#!/usr/bin/env python3
"""CLI tool to inspect the Oracle database.

Usage:
    python -m oracle.cli guilds
    python -m oracle.cli recent -n 20
    python -m oracle.cli search "hello world"
"""

import argparse
import asyncio
import sqlite3
import sys

from .config import DB_PATH


def db_conn():
    if not DB_PATH.exists():
        print(f"Database not found at {DB_PATH}", file=sys.stderr)
        sys.exit(1)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def cmd_guilds(_args):
    rows = db_conn().execute("""
        SELECT guild_id, COUNT(*) as videos,
               COUNT(DISTINCT channel_id) as channels,
               COUNT(DISTINCT author_id) as authors,
               COALESCE(SUM(file_size), 0) as total_bytes,
               COALESCE(SUM(duration), 0) as total_seconds,
               MIN(sent_at) as earliest, MAX(sent_at) as latest
        FROM transcriptions GROUP BY guild_id ORDER BY videos DESC
    """).fetchall()

    if not rows:
        print("No data in database.")
        return

    for r in rows:
        mb = r["total_bytes"] / (1024 * 1024)
        mins = r["total_seconds"] / 60
        print(f"\nGuild: {r['guild_id']}")
        print(f"  Videos:    {r['videos']}")
        print(f"  Channels:  {r['channels']}")
        print(f"  Authors:   {r['authors']}")
        print(f"  Size:      {mb:.1f} MB processed")
        print(f"  Duration:  {mins:.1f} min total")
        print(f"  Range:     {r['earliest'] or '?'} -> {r['latest'] or '?'}")


def cmd_recent(args):
    query = "SELECT * FROM transcriptions"
    params = []
    if args.guild:
        query += " WHERE guild_id = ?"
        params.append(args.guild)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(args.limit)

    rows = db_conn().execute(query, params).fetchall()
    if not rows:
        print("No records found.")
        return

    for r in rows:
        mb = (r["file_size"] or 0) / (1024 * 1024)
        dur = r["duration"]
        dur_str = f"{int(dur // 60)}:{int(dur % 60):02d}" if dur else "?"
        transcript = r["transcription"] or "(none)"
        preview = (transcript[:100] + "...") if len(transcript) > 100 else transcript
        print(f"\n{'=' * 70}")
        print(f"  ID:       {r['id']}")
        print(f"  File:     {r['filename']} ({mb:.2f} MB, {dur_str})")
        print(f"  Author:   {r['author_name']} ({r['author_id']})")
        print(f"  Channel:  {r['channel_id']}")
        print(f"  Message:  {r['message_id']}")
        print(f"  Sent:     {r['sent_at']}")
        print(f"  Indexed:  {r['created_at']}")
        print(f"  Text:     {preview}")


def cmd_search(args):
    query = """
        SELECT t.*, snippet(transcriptions_fts, 0, '>>>', '<<<', '...', 40) AS snippet
        FROM transcriptions_fts fts
        JOIN transcriptions t ON t.id = fts.rowid
        WHERE fts.transcription MATCH ?
    """
    params = [args.query]
    if args.guild:
        query += " AND t.guild_id = ?"
        params.append(args.guild)
    query += " ORDER BY rank LIMIT ?"
    params.append(args.limit)

    rows = db_conn().execute(query, params).fetchall()
    if not rows:
        print(f'No results for "{args.query}".')
        return

    print(f'{len(rows)} result(s) for "{args.query}":\n')
    for i, r in enumerate(rows, 1):
        dur = r["duration"]
        dur_str = f"{int(dur // 60)}:{int(dur % 60):02d}" if dur else "?"
        print(f"  {i}. {r['filename']} ({dur_str}) -- by {r['author_name']} -- {r['sent_at'][:10] if r['sent_at'] else '?'}")
        print(f"     ...{r['snippet']}...")
        print()


def cmd_channels(args):
    rows = db_conn().execute("""
        SELECT channel_id, COUNT(*) as videos,
               COUNT(DISTINCT author_id) as authors,
               COALESCE(SUM(duration), 0) as total_seconds
        FROM transcriptions WHERE guild_id = ?
        GROUP BY channel_id ORDER BY videos DESC
    """, (args.guild,)).fetchall()

    if not rows:
        print(f"No data for guild {args.guild}.")
        return

    print(f"Channels in guild {args.guild}:\n")
    for r in rows:
        mins = r["total_seconds"] / 60
        print(f"  {r['channel_id']}: {r['videos']} videos, {r['authors']} authors, {mins:.1f} min total")


def cmd_authors(args):
    rows = db_conn().execute("""
        SELECT author_id, author_name, COUNT(*) as videos,
               COALESCE(SUM(file_size), 0) as total_bytes,
               COALESCE(SUM(duration), 0) as total_seconds
        FROM transcriptions WHERE guild_id = ?
        GROUP BY author_id ORDER BY videos DESC
    """, (args.guild,)).fetchall()

    if not rows:
        print(f"No data for guild {args.guild}.")
        return

    print(f"Authors in guild {args.guild}:\n")
    for r in rows:
        mb = r["total_bytes"] / (1024 * 1024)
        mins = r["total_seconds"] / 60
        print(f"  {r['author_name']} ({r['author_id']}): {r['videos']} videos, {mb:.1f} MB, {mins:.1f} min")


def cmd_full(args):
    row = db_conn().execute("SELECT * FROM transcriptions WHERE id = ?", (args.id,)).fetchone()
    if not row:
        print(f"No record with id {args.id}.")
        return
    for key in row.keys():
        val = row[key]
        if key == "file_size" and val:
            val = f"{val} ({val / (1024*1024):.2f} MB)"
        if key == "duration" and val:
            val = f"{val:.1f}s ({int(val // 60)}:{int(val % 60):02d})"
        print(f"  {key:15s}: {val}")


def cmd_count(args):
    conn = db_conn()
    params = []
    where = []
    if args.guild:
        where.append("guild_id = ?")
        params.append(args.guild)

    clause = f"WHERE {' AND '.join(where)}" if where else ""
    total = conn.execute(f"SELECT COUNT(*) as n FROM transcriptions {clause}", params).fetchone()
    with_text = conn.execute(
        f"SELECT COUNT(*) as n FROM transcriptions {clause} "
        f"{'AND' if where else 'WHERE'} transcription IS NOT NULL AND transcription != ''",
        params,
    ).fetchone()
    without = conn.execute(
        f"SELECT COUNT(*) as n FROM transcriptions {clause} "
        f"{'AND' if where else 'WHERE'} (transcription IS NULL OR transcription = '')",
        params,
    ).fetchone()

    print(f"Total records:            {total['n']}")
    print(f"With transcription:       {with_text['n']}")
    print(f"Without transcription:    {without['n']}")


def cmd_embed_backfill(args):
    from .database import get_rows_without_embeddings, update_embedding
    from .embeddings import generate_embeddings_batch

    batch_size = args.batch
    total = 0

    async def run():
        nonlocal total
        while True:
            rows = get_rows_without_embeddings(limit=batch_size)
            if not rows:
                break
            ids, texts = zip(*rows)
            embeddings = await generate_embeddings_batch(list(texts))
            stored = 0
            for row_id, emb in zip(ids, embeddings):
                if emb:
                    update_embedding(row_id, emb)
                    stored += 1
                else:
                    update_embedding(row_id, b"")
            total += stored
            print(f"  Embedded {stored}/{len(rows)} transcriptions (total: {total})")
            if stored == 0:
                print(f"  Skipped {len(rows)} rows (text too short or API error)")
                break

    asyncio.run(run())
    print(f"\nDone. {total} embeddings generated.")


def main():
    parser = argparse.ArgumentParser(description="Inspect the Oracle database")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("guilds", help="List all guilds and their stats")

    p = sub.add_parser("recent", help="Show most recent transcriptions")
    p.add_argument("-g", "--guild", help="Filter by guild ID")
    p.add_argument("-n", "--limit", type=int, default=10)

    p = sub.add_parser("search", help="Full-text search transcriptions")
    p.add_argument("query")
    p.add_argument("-g", "--guild", help="Filter by guild ID")
    p.add_argument("-n", "--limit", type=int, default=10)

    p = sub.add_parser("channels", help="List channels with video counts")
    p.add_argument("guild", help="Guild ID")

    p = sub.add_parser("authors", help="List authors with video counts")
    p.add_argument("guild", help="Guild ID")

    p = sub.add_parser("full", help="Show full details for a record by ID")
    p.add_argument("id", type=int)

    p = sub.add_parser("count", help="Count records")
    p.add_argument("-g", "--guild", help="Filter by guild ID")

    p = sub.add_parser("embed-backfill", help="Generate embeddings for existing transcriptions")
    p.add_argument("-b", "--batch", type=int, default=50, help="Batch size per API call")

    args = parser.parse_args()
    {
        "guilds": cmd_guilds,
        "recent": cmd_recent,
        "search": cmd_search,
        "channels": cmd_channels,
        "authors": cmd_authors,
        "full": cmd_full,
        "count": cmd_count,
        "embed-backfill": cmd_embed_backfill,
    }[args.command](args)


if __name__ == "__main__":
    main()
