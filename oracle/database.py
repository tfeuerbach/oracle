import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime

from .config import DB_PATH


@dataclass
class TranscriptionRecord:
    id: int
    guild_id: str
    channel_id: str
    message_id: str
    author_id: str
    author_name: str
    filename: str
    file_size: int | None
    duration: float | None
    sent_at: str | None
    transcription: str | None
    created_at: str
    source: str = "file"
    source_url: str | None = None

    @classmethod
    def from_row(cls, r, transcript_key="transcription"):
        return cls(
            id=r["id"], guild_id=r["guild_id"], channel_id=r["channel_id"],
            message_id=r["message_id"], author_id=r["author_id"],
            author_name=r["author_name"], filename=r["filename"],
            file_size=r["file_size"], duration=r["duration"],
            sent_at=r["sent_at"], transcription=r[transcript_key],
            created_at=r["created_at"],
            source=r["source"] or "file", source_url=r["source_url"],
        )


tls = threading.local()


def get_connection():
    if not hasattr(tls, "conn") or tls.conn is None:
        conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        tls.conn = conn
    return tls.conn


@contextmanager
def get_db():
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


DEFAULT_SETTINGS = {"response_mode": "public"}


class GuildCache:
    """In-memory cache of watched channels and guild settings for O(1) lookups."""

    def __init__(self):
        self._channels: dict[str, set[str]] = {}
        self._setup: set[str] = set()
        self._settings: dict[str, dict] = {}

    def load(self):
        with get_db() as conn:
            self._channels.clear()
            for row in conn.execute("SELECT guild_id, channel_id FROM watched_channels"):
                self._channels.setdefault(row["guild_id"], set()).add(row["channel_id"])
            self._setup = set()
            self._settings.clear()
            for row in conn.execute("SELECT guild_id, setup_complete, response_mode FROM guild_settings"):
                gid = row["guild_id"]
                if row["setup_complete"]:
                    self._setup.add(gid)
                self._settings[gid] = {
                    "response_mode": row["response_mode"] or DEFAULT_SETTINGS["response_mode"],
                }

    def is_watched(self, guild_id: str, channel_id: str):
        return channel_id in self._channels.get(guild_id, set())

    def is_setup_complete(self, guild_id: str):
        return guild_id in self._setup

    def add_channels(self, guild_id: str, channel_ids: list[str]):
        self._channels.setdefault(guild_id, set()).update(channel_ids)

    def remove_channel(self, guild_id: str, channel_id: str):
        if guild_id in self._channels:
            self._channels[guild_id].discard(channel_id)

    def mark_setup(self, guild_id: str):
        self._setup.add(guild_id)

    def get_channels(self, guild_id: str):
        return self._channels.get(guild_id, set()).copy()

    def get_settings(self, guild_id: str):
        return {**DEFAULT_SETTINGS, **self._settings.get(guild_id, {})}

    def set_setting(self, guild_id: str, key: str, value):
        if guild_id not in self._settings:
            self._settings[guild_id] = dict(DEFAULT_SETTINGS)
        self._settings[guild_id][key] = value


guild_cache = GuildCache()

SCHEMA = """
CREATE TABLE IF NOT EXISTS transcriptions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id      TEXT NOT NULL,
    channel_id    TEXT NOT NULL,
    message_id    TEXT NOT NULL,
    author_id     TEXT NOT NULL,
    author_name   TEXT NOT NULL,
    filename      TEXT NOT NULL,
    file_size     INTEGER,
    duration      REAL,
    sent_at       TEXT,
    transcription TEXT,
    created_at    TEXT DEFAULT (datetime('now')),
    UNIQUE(guild_id, message_id, filename)
);

CREATE INDEX IF NOT EXISTS idx_transcriptions_guild
    ON transcriptions(guild_id);
CREATE INDEX IF NOT EXISTS idx_transcriptions_channel
    ON transcriptions(guild_id, channel_id);
CREATE INDEX IF NOT EXISTS idx_transcriptions_author
    ON transcriptions(guild_id, author_id);
CREATE INDEX IF NOT EXISTS idx_transcriptions_message
    ON transcriptions(message_id);

CREATE VIRTUAL TABLE IF NOT EXISTS transcriptions_fts USING fts5(
    transcription, content='transcriptions', content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS trg_fts_insert
AFTER INSERT ON transcriptions BEGIN
    INSERT INTO transcriptions_fts(rowid, transcription)
    VALUES (new.id, new.transcription);
END;

CREATE TRIGGER IF NOT EXISTS trg_fts_delete
AFTER DELETE ON transcriptions BEGIN
    INSERT INTO transcriptions_fts(transcriptions_fts, rowid, transcription)
    VALUES ('delete', old.id, old.transcription);
END;

CREATE TRIGGER IF NOT EXISTS trg_fts_update
AFTER UPDATE OF transcription ON transcriptions BEGIN
    INSERT INTO transcriptions_fts(transcriptions_fts, rowid, transcription)
    VALUES ('delete', old.id, old.transcription);
    INSERT INTO transcriptions_fts(rowid, transcription)
    VALUES (new.id, new.transcription);
END;

CREATE TABLE IF NOT EXISTS guild_settings (
    guild_id       TEXT PRIMARY KEY,
    setup_complete INTEGER DEFAULT 0,
    response_mode  TEXT DEFAULT 'public',
    created_at     TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS watched_channels (
    guild_id   TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    added_at   TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (guild_id, channel_id)
);

CREATE TABLE IF NOT EXISTS user_preferences (
    user_id        TEXT NOT NULL,
    guild_id       TEXT NOT NULL,
    response_mode  TEXT,
    PRIMARY KEY (user_id, guild_id)
);
"""


def init_db():
    with get_db() as conn:
        conn.executescript(SCHEMA)

        existing = {row[1] for row in conn.execute("PRAGMA table_info(transcriptions)").fetchall()}
        if "source" not in existing:
            conn.execute("ALTER TABLE transcriptions ADD COLUMN source TEXT DEFAULT 'file'")
        if "source_url" not in existing:
            conn.execute("ALTER TABLE transcriptions ADD COLUMN source_url TEXT")
        if "embedding" not in existing:
            conn.execute("ALTER TABLE transcriptions ADD COLUMN embedding BLOB")

        gs_cols = {row[1] for row in conn.execute("PRAGMA table_info(guild_settings)").fetchall()}
        if "response_mode" not in gs_cols:
            conn.execute("ALTER TABLE guild_settings ADD COLUMN response_mode TEXT DEFAULT 'public'")

    guild_cache.load()


def insert_transcription(
    guild_id: str,
    channel_id: str,
    message_id: str,
    author_id: str,
    author_name: str,
    filename: str,
    file_size: int | None = None,
    duration: float | None = None,
    sent_at: datetime | None = None,
    transcription: str | None = None,
    source: str = "file",
    source_url: str | None = None,
    embedding: bytes | None = None,
):
    sent_str = sent_at.isoformat() if sent_at else None
    with get_db() as conn:
        try:
            cur = conn.execute(
                """INSERT INTO transcriptions
                   (guild_id, channel_id, message_id, author_id, author_name,
                    filename, file_size, duration, sent_at, transcription,
                    source, source_url, embedding)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (guild_id, channel_id, message_id, author_id, author_name,
                 filename, file_size, duration, sent_str, transcription,
                 source, source_url, embedding),
            )
            return cur.lastrowid
        except sqlite3.IntegrityError:
            return None


def message_already_processed(guild_id: str, message_id: str, filename: str):
    with get_db() as conn:
        row = conn.execute(
            "SELECT 1 FROM transcriptions WHERE guild_id=? AND message_id=? AND filename=?",
            (guild_id, message_id, filename),
        ).fetchone()
        return row is not None


def url_already_processed(guild_id: str, message_id: str, source_url: str):
    with get_db() as conn:
        row = conn.execute(
            "SELECT 1 FROM transcriptions WHERE guild_id=? AND message_id=? AND source_url=?",
            (guild_id, message_id, source_url),
        ).fetchone()
        return row is not None


def search_transcriptions(guild_id: str, query: str, limit: int = 10):
    safe_query = '"' + query.replace('"', '""') + '"'
    with get_db() as conn:
        rows = conn.execute(
            """SELECT t.*, snippet(transcriptions_fts, 0, '**', '**', '...', 32) AS snippet
               FROM transcriptions_fts fts
               JOIN transcriptions t ON t.id = fts.rowid
               WHERE fts.transcription MATCH ? AND t.guild_id = ?
               ORDER BY rank LIMIT ?""",
            (safe_query, guild_id, limit),
        ).fetchall()

        return [TranscriptionRecord.from_row(r, "snippet") for r in rows]


def get_guild_embeddings(guild_id: str):
    """Return (id, embedding) for all transcriptions in a guild that have embeddings."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, embedding FROM transcriptions WHERE guild_id = ? AND embedding IS NOT NULL AND length(embedding) > 0",
            (guild_id,),
        ).fetchall()
        return [(r["id"], r["embedding"]) for r in rows]


def get_transcriptions_by_ids(ids: list[int]):
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    with get_db() as conn:
        rows = conn.execute(
            f"""SELECT *, transcription AS snippet FROM transcriptions
                WHERE id IN ({placeholders})""",
            ids,
        ).fetchall()
        by_id = {r["id"]: TranscriptionRecord.from_row(r, "snippet") for r in rows}
        return [by_id[i] for i in ids if i in by_id]


def get_rows_without_embeddings(limit: int = 100):
    """Return (id, transcription) for rows that need embeddings."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT id, transcription FROM transcriptions
               WHERE embedding IS NULL AND transcription IS NOT NULL
                 AND transcription NOT IN ('(no audio)', '(no speech)', '(not a video)', '(too long)', '(unavailable)')
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return [(r["id"], r["transcription"]) for r in rows]


def update_embedding(row_id: int, embedding: bytes):
    with get_db() as conn:
        conn.execute("UPDATE transcriptions SET embedding = ? WHERE id = ?", (embedding, row_id))


def get_guild_stats(guild_id: str):
    with get_db() as conn:
        row = conn.execute(
            """SELECT COUNT(*) as total_videos,
                      COALESCE(SUM(file_size), 0) as total_bytes_processed,
                      COALESCE(SUM(duration), 0) as total_duration_seconds,
                      COUNT(DISTINCT author_id) as unique_authors,
                      COUNT(DISTINCT channel_id) as channels_indexed
               FROM transcriptions WHERE guild_id = ?""",
            (guild_id,),
        ).fetchone()
        return dict(row)


def mark_setup_complete(guild_id: str):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO guild_settings (guild_id, setup_complete) VALUES (?, 1) "
            "ON CONFLICT(guild_id) DO UPDATE SET setup_complete = 1",
            (guild_id,),
        )
    guild_cache.mark_setup(guild_id)


def is_setup_complete(guild_id: str):
    return guild_cache.is_setup_complete(guild_id)


def add_watched_channels(guild_id: str, channel_ids: list[str]):
    with get_db() as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO watched_channels (guild_id, channel_id) VALUES (?, ?)",
            [(guild_id, cid) for cid in channel_ids],
        )
    guild_cache.add_channels(guild_id, channel_ids)


def remove_watched_channel(guild_id: str, channel_id: str):
    with get_db() as conn:
        conn.execute(
            "DELETE FROM watched_channels WHERE guild_id=? AND channel_id=?",
            (guild_id, channel_id),
        )
    guild_cache.remove_channel(guild_id, channel_id)


def get_watched_channels(guild_id: str):
    return guild_cache.get_channels(guild_id)


def is_channel_watched(guild_id: str, channel_id: str):
    return guild_cache.is_watched(guild_id, channel_id)


def get_guild_settings(guild_id: str):
    return guild_cache.get_settings(guild_id)


ALLOWED_SETTINGS = {"response_mode"}


def update_guild_setting(guild_id: str, key: str, value):
    if key not in ALLOWED_SETTINGS:
        raise ValueError(f"Unknown setting: {key}")
    db_value = value
    if isinstance(value, bool):
        db_value = 1 if value else 0
    with get_db() as conn:
        conn.execute(
            f"INSERT INTO guild_settings (guild_id, {key}) VALUES (?, ?) "
            f"ON CONFLICT(guild_id) DO UPDATE SET {key} = ?",
            (guild_id, db_value, db_value),
        )
    guild_cache.set_setting(guild_id, key, value)


def get_user_response_mode(user_id: str, guild_id: str):
    with get_db() as conn:
        row = conn.execute(
            "SELECT response_mode FROM user_preferences WHERE user_id = ? AND guild_id = ?",
            (user_id, guild_id),
        ).fetchone()
        return row["response_mode"] if row else None


def set_user_response_mode(user_id: str, guild_id: str, mode: str):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO user_preferences (user_id, guild_id, response_mode) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id, guild_id) DO UPDATE SET response_mode = ?",
            (user_id, guild_id, mode, mode),
        )


def clear_user_response_mode(user_id: str, guild_id: str):
    with get_db() as conn:
        conn.execute(
            "DELETE FROM user_preferences WHERE user_id = ? AND guild_id = ?",
            (user_id, guild_id),
        )


def resolve_response_mode(user_id: str, guild_id: str):
    """User preference wins, otherwise fall back to guild setting."""
    user_mode = get_user_response_mode(user_id, guild_id)
    if user_mode:
        return user_mode
    return get_guild_settings(guild_id)["response_mode"]
