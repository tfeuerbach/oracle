# Getting Started

## Add Oracle to your server

[:fontawesome-brands-discord: Add Oracle to your server](https://discord.com/oauth2/authorize?client_id=1485459728560689312&permissions=84992&scope=bot+applications.commands){ .md-button .discord-btn }

<!-- [:material-arrow-up-bold: Vote on top.gg](https://top.gg/bot/TODO){ .md-button } -->

Click the button above to invite Oracle. You'll be asked to select a server and confirm the following permissions:

| Permission | Why it's needed |
|------------|-----------------|
| View Channels | See which channels exist and read messages in them |
| Send Messages | Post backfill status updates and setup prompts |
| Read Message History | Scan past messages during backfill |
| Embed Links | Format search results as rich embeds |

Slash commands are included automatically via the `applications.commands` scope.

## Setup flow

Here's what happens from the moment Oracle joins your server to when it's fully operational.

### 1. Initial channel selection

When Oracle joins your server, it sends a **setup prompt** in the system channel.

1. Use the channel dropdown to pick which text channels Oracle should monitor for videos
2. Click **Confirm**
3. Oracle asks how it should respond to queries like `/search`:
    - **Public** -- everyone in the channel sees the response
    - **Private** -- only the person who ran the command sees it
    - **DM** -- Oracle sends the response as a direct message
4. Oracle asks if you want to **backfill** those channels (index all past videos):
    - **Yes, backfill now** -- starts scanning historical messages immediately
    - **No, skip backfill** -- Oracle only watches for new videos going forward

### 2. Adding channels later

You can add channels to the monitoring list at any time with `/watch`:

```
/watch channel:#videos
```

Oracle will confirm the channel is being monitored and then ask if you'd like to backfill it -- same prompt as during initial setup.

To remove a channel: `/unwatch channel:#videos`

To see what's currently monitored: `/watching`

### 3. Configure settings

Use `/settings` to customize how Oracle behaves in your server. This opens an ephemeral panel (only you can see it) with dropdown menus:

| Setting | Options | Default |
|---------|---------|---------|
| **Response mode** | Public, Private, DM | Public |

**Response mode** controls how Oracle replies to query commands (`/search`, `/stats`, `/watching`):

- **Public** -- response is visible to everyone in the channel
- **Private** -- only the person who ran the command can see the response (ephemeral)
- **DM** -- Oracle sends the response to your direct messages

Individual users can override this with `/preference` to set their own mode without affecting the rest of the server.

Admin/operational commands (`/setup`, `/watch`, `/unwatch`, `/backfill`, `/stop_backfill`) always respond in the channel regardless of this setting.

!!! tip
    You can re-run `/setup` at any time to reconfigure channels from scratch, or use `/settings` to adjust preferences.

### 4. Live monitoring begins

Once setup is complete, Oracle automatically monitors all watched channels. Any video posted -- whether a file upload or a URL from YouTube, Reddit, TikTok, etc. -- is transcribed in the background. No further action is needed.

### 5. Backfilling past videos

To index videos posted before Oracle joined, use `/backfill`:

```
/backfill channel:#videos
```

Progress is shown via a live-updating status embed. For an entire server at once, admins can use `/backfill_server`.

## Searching for videos

Once Oracle has indexed some videos, any user in the server can search:

```
/search query:something about cats
```

Oracle returns the top matches with:

- Video filename or title (linked to the original)
- Duration
- Who posted it and when
- A snippet of the matching transcript

Results appear in two sections: **keyword matches** (exact text) and **similar results** (semantic meaning). This means you can find videos even if you don't remember the exact words.

## What gets transcribed

Oracle processes two kinds of video content:

| Source | Examples | How it's detected |
|--------|----------|-------------------|
| **File attachments** | `.mp4`, `.webm`, `.mov`, `.avi`, `.mkv` | Uploaded directly to Discord |
| **URL videos** | YouTube, Reddit, Vimeo, TikTok, Instagram, etc. | Links posted in message text |

Both are transcribed identically -- audio is extracted, sent to Whisper (OpenAI API or a local model, depending on configuration), and the full text is stored and indexed for search.

## Privacy

- Each server's transcription data is **completely isolated** -- users can only search videos from their own server
- Oracle does **not** store video files -- it downloads temporarily for transcription, then deletes them
- The only thing stored permanently is metadata (who posted it, when, filename) and the transcription text
