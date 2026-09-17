import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

WHISPER_MODEL = os.getenv("WHISPER_MODEL", "whisper-1")
WHISPER_MODE = os.getenv("WHISPER_MODE", "api")
LOCAL_WHISPER_MODEL = os.getenv("LOCAL_WHISPER_MODEL", "base")

MAX_CONCURRENT_TRANSCRIPTIONS = int(os.getenv("MAX_CONCURRENT_TRANSCRIPTIONS", "5"))

VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".avi", ".mkv", ".flv", ".wmv", ".m4v"}

WHISPER_MAX_FILE_BYTES = 24 * 1024 * 1024
MAX_VIDEO_DURATION = int(os.getenv("MAX_VIDEO_DURATION", "3600"))

VIDEO_URL_DOMAINS: dict[str, str] = {
    "youtube.com": "youtube",
    "www.youtube.com": "youtube",
    "m.youtube.com": "youtube",
    "youtu.be": "youtube",
    "reddit.com": "reddit",
    "www.reddit.com": "reddit",
    "v.redd.it": "reddit",
    "vimeo.com": "vimeo",
    "www.vimeo.com": "vimeo",
    "facebook.com": "facebook",
    "www.facebook.com": "facebook",
    "fb.watch": "facebook",
    "twitter.com": "twitter",
    "www.twitter.com": "twitter",
    "x.com": "twitter",
    "tiktok.com": "tiktok",
    "www.tiktok.com": "tiktok",
    "vm.tiktok.com": "tiktok",
    "tnktok.com": "tiktok",
    "www.tnktok.com": "tiktok",
    "streamable.com": "link",
    "clips.twitch.tv": "twitch",
    "twitch.tv": "twitch",
    "www.twitch.tv": "twitch",
    "dailymotion.com": "dailymotion",
    "www.dailymotion.com": "dailymotion",
    "instagram.com": "instagram",
    "www.instagram.com": "instagram",
    "kkinstagram.com": "instagram",
    "www.kkinstagram.com": "instagram",
    "ddinstagram.com": "instagram",
    "www.ddinstagram.com": "instagram",
}

DATA_ROOT = Path(os.getenv("ORACLE_DATA_DIR", "."))
TEMP_DIR = DATA_ROOT / "tmp"
DB_DIR = DATA_ROOT / "data"

TEMP_DIR.mkdir(parents=True, exist_ok=True)
DB_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DB_DIR / "oracle.db"

SEARCH_RESULTS_LIMIT = 10

EMBEDDING_PROVIDER = os.getenv("EMBEDDING_PROVIDER", "openai")
LOCAL_EMBEDDING_MODEL = os.getenv("LOCAL_EMBEDDING_MODEL", "all-MiniLM-L6-v2")

SEMANTIC_SIMILARITY_THRESHOLD = float(os.getenv("SEMANTIC_SIMILARITY_THRESHOLD", "0.3"))

# Max size for /embed downloads and uploads (Discord guild limits may still apply)
EMBED_MAX_FILE_BYTES = int(os.getenv("EMBED_MAX_FILE_BYTES", str(200 * 1024 * 1024)))

YTDLP_COOKIES_FILE = os.getenv("YTDLP_COOKIES_FILE", "")
YTDLP_COOKIES_BROWSER = os.getenv("YTDLP_COOKIES_BROWSER", "")
