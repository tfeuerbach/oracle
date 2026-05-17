# Architecture

## Current design

Oracle runs as a single Python process inside a Docker container. Everything happens in one asyncio event loop — no external services are required beyond the optional OpenAI API.

### System overview

``` mermaid
flowchart TD
    D[Discord API] -->|message with video| B

    subgraph server["Oracle Server"]
        B[Oracle Bot]
        B -->|file attachment| DL1[Download from Discord CDN]
        B -->|video URL| DL2[Download via yt-dlp]
        DL1 --> FF[ffmpeg — extract audio]
        DL2 --> FF
        FF -->|audio chunks| W[Whisper — transcribe]
        W -->|transcription text| E[Embeddings — generate vector]
        E -->|text + vector + metadata| DB[(SQLite)]
        DB -.->|/search query| B
    end
```

### Processing pipeline

Every video follows the same six steps:

``` mermaid
flowchart LR
    A[1. Detect\nvideo or URL] --> B[2. Download\naudio]
    B --> C[3. ffmpeg\nextract audio]
    C --> D[4. Whisper\ntranscribe]
    D --> E[5. Generate\nembedding]
    E --> F[6. Store in\nSQLite]
```

- **Step 2**: File attachments download from Discord CDN. URLs use yt-dlp (YouTube, Reddit, TikTok, etc.)
- **Step 3**: Video file is deleted immediately after audio extraction
- **Step 4**: Uses OpenAI API or local Whisper model — audio file deleted after
- **Step 5**: Uses OpenAI API or local sentence-transformers model
- **Step 6**: Transcription, metadata, and embedding vector stored permanently

### Live monitoring vs backfill

``` mermaid
flowchart TD
    MSG[New message in\nwatched channel] --> LIVE[Live processing]
    CMD[/backfill command] --> BF[Backfill processing]
    LIVE -->|up to 5 concurrent| SEM[Shared\nsemaphore pool]
    BF -->|up to 3 concurrent| SEM
    SEM --> PIPE[Transcription pipeline]
```

Live video processing always has priority:

- **Live semaphore**: 5 slots total (configurable via `MAX_CONCURRENT_TRANSCRIPTIONS`)
- **Backfill semaphore**: 3 of those 5 slots max — 2 are always reserved for real-time traffic
- If 10 servers backfill at once, work queues up — nothing crashes, it just takes longer

### Search flow

``` mermaid
flowchart LR
    Q[/search query] --> K[FTS5 keyword\nmatch]
    Q --> S[Cosine similarity\nembedding match]
    K --> R[Combined\nresults]
    S --> R
```

Search runs both methods in parallel:

- **Keyword**: SQLite FTS5 finds exact word matches in transcriptions
- **Semantic**: Query is embedded, then compared against all stored vectors by cosine similarity (threshold configurable via `SEMANTIC_SIMILARITY_THRESHOLD`)

Results are deduplicated and shown in two labeled sections.

---

## Privacy — what gets sent where

OpenAI never sees your Discord messages, usernames, server names, or channel names.

| Data | Stays on your machine | Sent to OpenAI |
|------|:---------------------:|:--------------:|
| Discord messages & usernames | ✅ Always | ❌ Never |
| Server & channel names | ✅ Always | ❌ Never |
| Video files | ✅ Deleted after processing | ❌ Never |
| Extracted audio | ✅ Deleted after processing | ⚠️ Only if `WHISPER_MODE=api` |
| Transcription text | ✅ Stored in SQLite | ⚠️ Only if `EMBEDDING_PROVIDER=openai` |
| Embeddings & search index | ✅ Stored in SQLite | ❌ Never |

!!! tip "Fully local mode"
    Set `WHISPER_MODE=local` and `EMBEDDING_PROVIDER=local` to keep **everything** on your machine with zero external API calls. See the [self-hosting guide](self-hosting.md#running-fully-local).

---

## Where this design works well

- **Dozens of servers** with moderate video traffic
- **Backfills** of any size (they queue and process incrementally)
- **Low operational overhead** — one container, one SQLite file

The real throughput bottleneck is the OpenAI Whisper API rate limit, not compute.

## Future state — scaling up

At **hundreds of active servers**, three things become limiting:

1. **SQLite is single-writer** — no concurrent replicas
2. **In-memory state is per-process** — channel cache, backfill tracker, semaphores don't coordinate
3. **No work distribution** — transcription jobs can't spread across workers

### Scaled architecture

``` mermaid
flowchart TD
    D[Discord API] --> B1[Bot 1]
    D --> B2[Bot 2]
    D --> BN[Bot N]
    B1 --> R[(Redis queue)]
    B2 --> R
    BN --> R
    R --> W1[Worker 1]
    R --> W2[Worker 2]
    R --> WN[Worker N]
    W1 --> PG[(PostgreSQL\n+ pgvector)]
    W2 --> PG
    WN --> PG
    B1 -.->|search| PG
    B2 -.->|search| PG
    BN -.->|search| PG
```

### Migration phases

**Phase 1: PostgreSQL** — Replace SQLite for concurrent reads/writes, row-level locking, `tsvector`/`tsquery` search, and [pgvector](https://github.com/pgvector/pgvector) for native vector similarity.

**Phase 2: Redis task queue** — Replace in-process semaphores with a distributed job queue. Bots enqueue transcription jobs; workers pull and process independently.

**Phase 3: Separate worker containers** — Bot containers handle Discord events and search. Worker containers handle the heavy lifting. Scale workers up during backfill surges, back down during quiet periods.

### What stays the same

The core pipeline (detect → download → extract → transcribe → embed → store) doesn't change regardless of scale. The refactor is about *where* each step runs, not *what* it does.
