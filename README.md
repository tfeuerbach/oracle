<p align="center">
  <img src="docs/assets/logo.png" alt="Oracle" width="128">
</p>

# Oracle

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Docker](https://img.shields.io/badge/docker-ready-blue.svg)](Dockerfile)

A Discord bot that transcribes videos and makes them searchable by what was said.

Supports file attachments (MP4, WebM, MOV, etc.) and URL videos (YouTube, Reddit, Vimeo, TikTok, Instagram, Twitter/X, and more). Transcriptions are stored per-server with full-text search.

[Add to your server](https://oracle.tfeuerbach.dev/getting-started/) | [Documentation](https://oracle.tfeuerbach.dev) | [Self-Hosting Guide](https://oracle.tfeuerbach.dev/self-hosting/)

<!-- [top.gg](https://top.gg/bot/TODO) -->

## Quick start

```bash
git clone https://github.com/tfeuerbach/oracle.git
cd oracle
cp .env.example .env
# Fill in DISCORD_TOKEN (OPENAI_API_KEY only needed for API mode)
docker compose up -d
```

## Commands

| Command | Description |
|---------|-------------|
| `/search <query>` | Semantic search -- finds videos by meaning, not just keywords |
| `/preference` | Set how Oracle responds to you (public, private, DM) |
| `/stats` | Show indexing statistics |
| `/setup` | Initial channel selection |
| `/settings` | Configure server-wide response mode and bot preferences |
| `/watch <channel>` | Add a channel to the monitoring list |
| `/unwatch <channel>` | Remove a channel |
| `/watching` | List monitored channels |
| `/backfill [channel]` | Index all past videos in a channel |
| `/backfill_server` | Index all past videos server-wide |
| `/stop_backfill` | Cancel all running backfills in this server |

## Project structure

```
oracle/
  __main__.py      Entry point (python -m oracle)
  bot.py           Discord client and event handlers
  commands.py      Slash command definitions
  backfill.py      Channel history scanning and batch processing
  transcriber.py   Video download, audio extraction, Whisper
  embeddings.py    Semantic search (OpenAI API or local)
  database.py      SQLite schema, FTS5 search, channel cache
  views.py         Discord UI components (setup flow)
  config.py        Environment variables and constants
  cli.py           Database inspection tool (python -m oracle.cli)
```

## Running locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m oracle
```

Requires `ffmpeg` on PATH. Database inspection:

```bash
python -m oracle.cli recent -n 5
python -m oracle.cli search "some phrase"
```

## Running fully local (no OpenAI API)

Oracle can run entirely on your own hardware with no API costs or external calls.

**Transcription** -- Install [openai-whisper](https://github.com/openai/whisper) and set `WHISPER_MODE=local`. A CUDA GPU is strongly recommended.

**Semantic search** -- Set `EMBEDDING_PROVIDER=local` and install [sentence-transformers](https://www.sbert.net/). Oracle uses `all-MiniLM-L6-v2` by default (~80 MB, runs on CPU or GPU). Set `LOCAL_EMBEDDING_MODEL` to use a different model from Hugging Face.

Both features work without an OpenAI API key. See the [self-hosting guide](https://oracle.tfeuerbach.dev/self-hosting/#running-fully-local) for model options, hardware requirements, and Docker GPU passthrough.

## Scaling

Oracle runs as a single container with SQLite and handles dozens of servers comfortably. Concurrency is managed with asyncio semaphores, and the real throughput bottleneck is the Whisper API rate limit.

If demand grows beyond what a single process can handle, the migration path is: PostgreSQL (shared state), Redis (distributed task queue), and separate worker containers. See the [architecture docs](https://oracle.tfeuerbach.dev/architecture/) for details.

## License

[MIT](LICENSE)
