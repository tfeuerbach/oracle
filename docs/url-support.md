# URL Video Support

Oracle doesn't just index file attachments — it also detects and transcribes video links posted in messages.

## Supported platforms

| Platform | Domains |
|----------|---------|
| YouTube | `youtube.com`, `youtu.be`, `m.youtube.com` |
| Reddit | `reddit.com`, `v.redd.it` |
| Instagram | `instagram.com`, `kkinstagram.com`, `ddinstagram.com` |
| Vimeo | `vimeo.com` |
| Facebook | `facebook.com`, `fb.watch` |
| Twitter / X | `twitter.com`, `x.com` |
| TikTok | `tiktok.com`, `vm.tiktok.com`, `tnktok.com` |
| Twitch | `twitch.tv`, `clips.twitch.tv` |
| Dailymotion | `dailymotion.com` |
| Streamable | `streamable.com` |

Under the hood, Oracle uses [yt-dlp](https://github.com/yt-dlp/yt-dlp) which supports [over 1,000 sites](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md). The list above covers the domains Oracle actively looks for in messages. Additional domains can be added in `config.py`.

## How it works

1. When a message is posted (or scanned during backfill), Oracle extracts all URLs
2. URLs matching a known video platform are queued for processing
3. yt-dlp downloads the audio track only (as compressed m4a at 64kbps — no video data is stored)
4. The audio is sent to Whisper for transcription
5. The video **title** (from the platform), **duration**, **source type**, and **original URL** are stored alongside the transcription

## Long videos

By default, Oracle skips videos longer than **1 hour** (3600 seconds). This limit is configurable via the `MAX_VIDEO_DURATION` environment variable — set it to `0` to disable the limit entirely.

For videos within the limit, Oracle handles large files automatically:

- Audio is downloaded as compressed m4a (64kbps), so a 1-hour video is only ~28 MB
- The OpenAI Whisper API has a 25 MB file size limit per request
- If the file exceeds 24 MB, Oracle splits it into chunks using ffmpeg
- Each chunk is transcribed separately and the results are concatenated
- The full transcription is stored and searchable — no truncation

## Search results

When a URL video matches a search, the result includes:

- The video **title** as a link to the original URL (e.g., the YouTube page)
- A **source tag** (`youtube`, `reddit`, etc.)
- A link to the **Discord message** where it was posted
- The matching transcript snippet

## Backfill

URL videos in historical messages are picked up during backfill, just like file attachments. Run `/backfill` to index everything.
