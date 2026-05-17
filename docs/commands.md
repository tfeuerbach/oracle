# Commands

All commands are Discord slash commands. Type `/` in any channel to see them.

## Everyone

### `/search`

Search video transcriptions by what was said.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `query` | string | Yes | Words or phrase you remember hearing |

Results are shown in two sections:

- **Keyword matches** — exact text matches from the transcription (FTS5 full-text search)
- **Similar results** — semantically related videos found via embedding similarity, even if the exact words weren't said

Duplicates between sections are automatically removed. The similarity threshold is configurable via `SEMANTIC_SIMILARITY_THRESHOLD` (default `0.3`).

### `/stats`

Show indexing statistics for the current server — total videos indexed, total duration processed, unique posters, and channels covered.

### `/watching`

List all channels currently being monitored for videos.

### `/preference`

Set how Oracle responds to **you** in this server. This overrides the server-wide response mode for your own queries.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `mode` | choice | Yes | Public, Private, DM, or Use server default |

Choosing **Use server default** clears your override and falls back to whatever the admin has configured via `/settings`.

## Server managers

These require the **Manage Server** permission.

### `/settings`

Open the server settings panel. Shows current configuration with dropdown menus to change:

- **Response mode** -- how Oracle replies to query commands (`/search`, `/stats`, `/watching`):
    - **Public** -- visible to everyone in the channel (default)
    - **Private** -- only the command invoker sees the response
    - **DM** -- sent to your direct messages

The settings panel is ephemeral (only you can see it). Changes take effect immediately.

### `/watch`

Add a channel to the monitoring list.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `channel` | text channel | Yes | Channel to start monitoring |

After adding the channel, Oracle prompts you with **Yes, backfill now** / **No, skip backfill** to optionally index all past videos in the channel.

If this is the first channel added (setup wasn't completed before), it also marks setup as complete so live monitoring begins.

### `/unwatch`

Remove a channel from the monitoring list.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `channel` | text channel | Yes | Channel to stop monitoring |

### `/backfill`

Index all past videos in a channel. Progress is reported via a live-updating status embed.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `channel` | text channel | No | Defaults to the current channel |

Videos are processed concurrently (up to 5 at a time). Failed videos are automatically retried once at the end.

### `/stop_backfill`

Stop all running backfills in the current server. In-progress transcriptions will finish, but no new videos will be queued.

## Administrators

These require the **Administrator** permission.

### `/setup`

Re-send the setup UI with the channel selector. Useful if the original setup message was missed or you want to reconfigure from scratch.

### `/backfill_server`

Index all past videos across every accessible text channel in the server. Channels are processed sequentially, but videos within each channel are processed concurrently.
