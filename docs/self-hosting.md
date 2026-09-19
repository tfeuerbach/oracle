# Self-Hosting

Oracle is designed to run 24/7 on a server. You can deploy it with Docker (recommended) or run it directly with Python.

## Prerequisites

- A [Discord bot application](https://discord.com/developers/applications) with:
    - **Bot** scope and **applications.commands** scope
    - **Message Content Intent** enabled (under Bot → Privileged Gateway Intents)
- An [OpenAI API key](https://platform.openai.com/api-keys) with billing enabled (only if using API mode -- not needed for local Whisper)
- **ffmpeg** installed on the host (included in the Docker image)

## Discord bot setup

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications)
2. Click **New Application**, name it "Oracle" (or whatever you like)
3. Go to **Bot** → click **Reset Token** → copy the token
4. Enable **Message Content Intent** under Privileged Gateway Intents
5. Go to **OAuth2** → **URL Generator**:
    - Scopes: `bot`, `applications.commands`
    - Bot Permissions: `Send Messages`, `Read Message History`, `View Channels`, `Embed Links`, `Use Slash Commands`
6. Copy the generated URL — this is your invite link

## Deploy with Docker

=== "Docker Compose (recommended)"

    ```bash
    git clone https://github.com/tfeuerbach/oracle.git
    cd oracle
    cp .env.example .env
    # Edit .env with your tokens
    docker compose up -d
    ```

    View logs:
    ```bash
    docker compose logs -f bot
    ```

    Update to latest version:
    ```bash
    git pull
    docker compose up -d --build
    ```

=== "Docker run"

    ```bash
    docker build -t oracle .
    docker run -d \
      --name oracle \
      --restart unless-stopped \
      --env-file .env \
      -v oracle-data:/app/data \
      oracle
    ```

## Deploy without Docker

### Requirements

- Python 3.10+
- ffmpeg
- pip

### Installation

```bash
git clone https://github.com/tfeuerbach/oracle.git
cd oracle
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Configuration

```bash
cp .env.example .env
```

Edit `.env` with your Discord token and OpenAI API key.

### Running

```bash
source .venv/bin/activate
python -m oracle
```

For production, use a process manager to keep it running:

```ini title="/etc/systemd/system/oracle.service"
[Unit]
Description=Oracle Discord Bot
After=network.target

[Service]
Type=simple
User=oracle
WorkingDirectory=/opt/oracle
ExecStart=/opt/oracle/.venv/bin/python -m oracle
Restart=always
RestartSec=10
EnvironmentFile=/opt/oracle/.env

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable oracle
sudo systemctl start oracle
```

## Configuration reference

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DISCORD_TOKEN` | Yes | — | Your Discord bot token |
| `OPENAI_API_KEY` | Conditional | — | OpenAI API key (not needed if both Whisper and embeddings are local) |
| `WHISPER_MODEL` | No | `whisper-1` | Whisper model name (API mode) |
| `WHISPER_MODE` | No | `api` | `api` (OpenAI) or `local` (self-hosted) |
| `LOCAL_WHISPER_MODEL` | No | `base` | Local Whisper model size |
| `EMBEDDING_PROVIDER` | No | `openai` | `openai` (API) or `local` (sentence-transformers) |
| `LOCAL_EMBEDDING_MODEL` | No | `all-MiniLM-L6-v2` | Hugging Face model for local embeddings |
| `SEMANTIC_SIMILARITY_THRESHOLD` | No | `0.3` | Minimum cosine similarity for semantic results (0.0–1.0) |
| `EMBED_MAX_FILE_BYTES` | No | `209715200` (200 MB) | Hard max download size for `/embed` |
| `EMBED_COMPRESS_THRESHOLD_BYTES` | No | `10485760` (10 MB) | Compress/scale `/embed` videos larger than this |
| `MAX_CONCURRENT_TRANSCRIPTIONS` | No | `5` | Max videos processed simultaneously |
| `MAX_VIDEO_DURATION` | No | `3600` | Max video length in seconds (longer videos are skipped) |
| `ORACLE_DATA_DIR` | No | `.` | Root directory for `data/` and `tmp/` |
| `YTDLP_COOKIES_FILE` | No | — | Path to Netscape-format cookies file for age-restricted videos |
| `YTDLP_COOKIES_BROWSER` | No | — | Browser to extract cookies from (`firefox`, `chrome`, `brave`, `edge`, `safari`) |

## Running fully local

Oracle can run entirely on your own hardware with **zero API calls and no OpenAI account**. There are two components to configure: transcription (Whisper) and semantic search (embeddings).

### Local Whisper (transcription)

Oracle can use a locally-hosted Whisper model instead of the OpenAI API. This eliminates the per-minute API cost entirely -- you just need a machine with enough compute.

#### Install Whisper

```bash
pip install openai-whisper
```

This installs the `whisper` CLI from OpenAI's open-source release. It requires:

- **Python 3.8+**
- **ffmpeg** on PATH
- A **CUDA-capable GPU** is strongly recommended. CPU-only works but is very slow (expect 5-10x real-time for the `base` model, worse for larger models).

!!! note
    The `openai-whisper` package is separate from the `openai` API client. You don't need an OpenAI **API key** when running fully local, but the `openai` Python package is still installed as a dependency of Oracle (used for the API code path).

#### Configure

Set these in your `.env`:

```bash
WHISPER_MODE=local
LOCAL_WHISPER_MODEL=base
```

#### Model sizes

| Model | Parameters | VRAM | Relative speed | Quality |
|-------|-----------|------|----------------|---------|
| `tiny` | 39M | ~1 GB | Fastest | Usable for short clips |
| `base` | 74M | ~1 GB | Fast | Good for most content |
| `small` | 244M | ~2 GB | Moderate | Better accuracy |
| `medium` | 769M | ~5 GB | Slow | High accuracy |
| `large` | 1550M | ~10 GB | Slowest | Best accuracy, multilingual |

The `base` model is the default and a good starting point. Use `small` or `medium` if you need better accuracy and have the GPU memory. The `large` model is best for non-English content.

Models are downloaded automatically on first use and cached in `~/.cache/whisper/`. On a typical system the `base` model is ~140 MB. You don't need to move or configure anything — just set `LOCAL_WHISPER_MODEL` and Oracle handles the rest.

!!! info "Docker users"
    Inside a container, the cache lives at `/root/.cache/whisper/`. Models are re-downloaded if the container is recreated. To persist them across rebuilds, mount a volume:
    ```yaml
    volumes:
      - whisper-cache:/root/.cache/whisper
    ```

### Local embeddings (semantic search)

Oracle's semantic search (`/search`) uses embeddings to find videos by meaning, not just exact keywords. By default this uses the OpenAI API, but you can run it locally.

#### Install sentence-transformers

```bash
pip install sentence-transformers
```

This pulls in PyTorch and the Hugging Face model hub. The default model (`all-MiniLM-L6-v2`) is ~80 MB and runs well on CPU.

Models are downloaded on first use and cached in `~/.cache/huggingface/hub/`. You can override this location by setting the `HF_HOME` environment variable.

!!! info "Docker users"
    Inside a container, the cache lives at `/root/.cache/huggingface/`. To persist models across container rebuilds, mount a volume:
    ```yaml
    volumes:
      - hf-cache:/root/.cache/huggingface
    ```

#### Configure

Set these in your `.env`:

```bash
EMBEDDING_PROVIDER=local
LOCAL_EMBEDDING_MODEL=all-MiniLM-L6-v2
```

#### Embedding model options

| Model | Size | Speed | Quality | Notes |
|-------|------|-------|---------|-------|
| `all-MiniLM-L6-v2` | 80 MB | Fast | Good | Default, great for CPU |
| `all-mpnet-base-v2` | 420 MB | Moderate | Better | Higher accuracy, still runs on CPU |
| `BAAI/bge-small-en-v1.5` | 130 MB | Fast | Good | Optimized for retrieval tasks |
| `BAAI/bge-large-en-v1.5` | 1.3 GB | Slow | Best | Best accuracy, benefits from GPU |

You can use any model from the [MTEB leaderboard](https://huggingface.co/spaces/mteb/leaderboard) or the [sentence-transformers docs](https://www.sbert.net/docs/pretrained_models.html) by setting `LOCAL_EMBEDDING_MODEL` to its Hugging Face identifier.

!!! tip
    If you switch embedding models after already indexing videos, re-run the embedding backfill to regenerate all vectors with the new model:
    ```bash
    python -m oracle.cli embed-backfill
    ```

### Fully local `.env` example

To run Oracle with no external API calls at all:

```bash
DISCORD_TOKEN=your-discord-token
# No OPENAI_API_KEY needed

WHISPER_MODE=local
LOCAL_WHISPER_MODEL=base

EMBEDDING_PROVIDER=local
LOCAL_EMBEDDING_MODEL=all-MiniLM-L6-v2

MAX_CONCURRENT_TRANSCRIPTIONS=3
MAX_VIDEO_DURATION=3600
```

### Docker with GPU passthrough

The default Dockerfile uses `python:3.11-slim` which doesn't include CUDA or local models. To run fully local in Docker:

1. **Use an NVIDIA CUDA base image** -- replace the `FROM` line in the Dockerfile:

    ```dockerfile
    FROM nvidia/cuda:12.2.0-runtime-ubuntu22.04
    ```

2. **Install additional dependencies**:

    ```dockerfile
    RUN pip install openai-whisper sentence-transformers
    ```

3. **Pass through your GPU** in `docker-compose.yml`:

    ```yaml
    services:
      bot:
        build: .
        deploy:
          resources:
            reservations:
              devices:
                - driver: nvidia
                  count: 1
                  capabilities: [gpu]
    ```

4. **Mount model cache volumes** to avoid re-downloading on container rebuild:

    ```yaml
    volumes:
      - oracle-data:/app/data
      - whisper-cache:/root/.cache/whisper
      - hf-cache:/root/.cache/huggingface
    ```

    Or with `docker run`:

    ```bash
    docker run --gpus all --env-file .env \
      -v oracle-data:/app/data \
      -v whisper-cache:/root/.cache/whisper \
      -v hf-cache:/root/.cache/huggingface \
      oracle
    ```

5. **Install the NVIDIA Container Toolkit** on the host:

    ```bash
    # Ubuntu/Debian
    curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
    curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
      sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
      sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
    sudo apt-get update && sudo apt-get install -y nvidia-container-toolkit
    sudo nvidia-ctk runtime configure --runtime=docker
    sudo systemctl restart docker
    ```

For simpler setups, run Oracle directly on bare metal (without Docker) when using local models.

### Mixing API and local

You can mix providers independently:

| Whisper | Embeddings | API key needed? |
|---------|-----------|-----------------|
| `api` | `openai` | Yes |
| `api` | `local` | Yes (for Whisper) |
| `local` | `openai` | Yes (for embeddings) |
| `local` | `local` | No |

Change `WHISPER_MODE` and `EMBEDDING_PROVIDER` in `.env` and restart.

## Hardware recommendations

=== "API mode (default)"

    Minimum requirements for a small deployment (1-5 servers):

    - **1 CPU core** (2 recommended)
    - **512 MB RAM** (1 GB recommended)
    - **1 GB disk** for the database (grows with usage)
    - Decent network bandwidth for downloading videos

    The main cost is the **OpenAI Whisper API** (~$0.006/min of audio) and **Embeddings API** (~$0.02/1M tokens). A server with 1,000 videos averaging 30 seconds each would cost about $3 to backfill transcription and pennies for embeddings.

=== "Fully local"

    Running everything on your own hardware:

    - **NVIDIA GPU** with CUDA support (strongly recommended for Whisper)
    - **4-12 GB VRAM** depending on Whisper model size
    - **4+ CPU cores** (CPU-only Whisper is slow but works)
    - **8 GB+ RAM** (16 GB recommended with large models)
    - **3 GB+ disk** for model weights + database
    - Embeddings run fine on CPU -- no GPU required for that part alone

    No API costs. Transcription speed depends entirely on your hardware.

## Cookies for age-restricted videos

Some videos on YouTube (age-gated) and TikTok (region-locked) require browser cookies to download. Oracle passes these to yt-dlp when configured.

### Option 1: Export a cookies file

Use a browser extension like [Get cookies.txt LOCALLY](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc) to export your cookies in Netscape format, then point Oracle to the file:

```bash
YTDLP_COOKIES_FILE=/path/to/cookies.txt
```

When running in Docker, mount the file:

```yaml
services:
  bot:
    volumes:
      - ./cookies.txt:/app/cookies.txt:ro
    environment:
      - YTDLP_COOKIES_FILE=/app/cookies.txt
```

### Option 2: Extract from browser directly

If running on bare metal (not Docker), yt-dlp can read cookies from an installed browser:

```bash
YTDLP_COOKIES_BROWSER=firefox
```

Supported browsers: `firefox`, `chrome`, `brave`, `edge`, `safari`, `opera`, `vivaldi`.

!!! warning
    Cookies expire. If you start seeing age-restriction errors again, re-export fresh cookies. Never share your cookies file -- it grants access to your logged-in sessions.

## Database

Oracle uses SQLite stored at `data/oracle.db`. The database is portable -- you can back it up by copying this single file.

To inspect the database from the command line:

```bash
python -m oracle.cli recent             # last 10 transcriptions
python -m oracle.cli search "cats"      # search transcriptions
python -m oracle.cli guilds             # list all indexed servers
python -m oracle.cli count              # total record count
python -m oracle.cli embed-backfill     # generate embeddings for existing transcriptions
```
