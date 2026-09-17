<p align="center">
  <img src="docs/assets/logo.png" alt="Oracle" width="128">
</p>

<h1 align="center">Oracle</h1>

<p align="center">
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.10+-blue.svg" alt="Python 3.10+"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green.svg" alt="MIT License"></a>
  <a href="Dockerfile"><img src="https://img.shields.io/badge/docker-ready-blue.svg" alt="Docker"></a>
  <!-- <a href="https://top.gg/bot/TODO"><img src="https://img.shields.io/badge/top.gg-vote-ff3366.svg" alt="top.gg"></a> -->
</p>

<p align="center">
  Discord bot that transcribes videos and makes them searchable by what was said.
</p>

<p align="center">
  <a href="https://oracle.tfeuerbach.dev/getting-started/">Add to your server</a> · <a href="https://oracle.tfeuerbach.dev">Docs</a> · <a href="https://oracle.tfeuerbach.dev/self-hosting/">Self-Host</a>
</p>

---

Supports file attachments and URL videos (YouTube, Reddit, TikTok, Instagram, Twitter/X, Vimeo, and more). Transcriptions are stored per-server with full-text and semantic search.

## Quick start

```bash
git clone https://github.com/tfeuerbach/oracle.git && cd oracle
cp .env.example .env   # add DISCORD_TOKEN, optionally OPENAI_API_KEY
docker compose up -d
```

## Commands

| Command | Description |
|---------|-------------|
| `/search <query>` | Find videos by what was said (keyword + semantic) |
| `/embed <url>` | Download Instagram/TikTok/Reddit/YouTube Shorts and post it here |
| `/preference` | Set how results are sent to you (public, private, DM) |
| `/stats` | Indexing stats for the server |
| `/setup` | Pick channels to monitor |
| `/settings` | Server-wide response mode (admin) |
| `/watch` / `/unwatch` | Add or remove a monitored channel |
| `/watching` | List monitored channels |
| `/backfill [channel]` | Index past videos in a channel |
| `/backfill_server` | Index past videos server-wide |
| `/stop_backfill` | Cancel running backfills |

## Project layout

```
oracle/
  __main__.py      python -m oracle
  bot.py           client + event handlers
  commands.py      slash commands
  backfill.py      history scanning + batch processing
  transcriber.py   download, ffmpeg, whisper
  embeddings.py    vector search (OpenAI or local)
  database.py      sqlite, fts5, cache
  views.py         discord UI (setup, settings)
  config.py        env vars + constants
  cli.py           database inspector (python -m oracle.cli)
```

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m oracle
```

Needs `ffmpeg` on PATH.

## Run fully local (no API)

Set `WHISPER_MODE=local` + install [openai-whisper](https://github.com/openai/whisper). CUDA GPU recommended.

Set `EMBEDDING_PROVIDER=local` + install [sentence-transformers](https://www.sbert.net/). Runs fine on CPU.

No OpenAI key needed. Details in the [self-hosting guide](https://oracle.tfeuerbach.dev/self-hosting/#running-fully-local).

## Scaling

Single container + SQLite handles dozens of servers. The bottleneck is Whisper API rate limits, not compute.

Growth path: PostgreSQL → Redis task queue → worker containers. See [architecture](https://oracle.tfeuerbach.dev/architecture/).

## License

[MIT](LICENSE)
