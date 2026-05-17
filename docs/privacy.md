# Privacy Policy

**Last updated:** March 24, 2026

## What data Oracle collects

When Oracle is added to a Discord server and configured to monitor channels, it processes video content in those channels and stores the following:

| Data | Purpose | Retention |
|------|---------|-----------|
| Guild (server) ID | Isolate data per server | Until the bot is removed |
| Channel ID | Track which channel a video was posted in | Until the bot is removed |
| Message ID | Link transcriptions back to the original message | Until the bot is removed |
| User ID and username | Attribute videos to the person who posted them | Until the bot is removed |
| Video filename or title | Identify the video in search results | Until the bot is removed |
| File size and duration | Display metadata in search results and stats | Until the bot is removed |
| Transcription text | Enable full-text search of video content | Until the bot is removed |
| Video source URL | Link back to the original video (for URL videos) | Until the bot is removed |

## What Oracle does NOT collect

- Message content (other than extracting video URLs)
- Direct messages
- User activity, presence, or status
- Data from channels not explicitly configured for monitoring
- Any data from servers where setup has not been completed

## Video file handling

Oracle downloads video and audio files **temporarily** for the sole purpose of transcription. Files are deleted immediately after processing. No video or audio content is stored permanently.

## Third-party services

By default, Oracle uses two OpenAI APIs:

- **Whisper API** — extracted audio is sent for transcription
- **Embeddings API** — transcription text is sent to generate semantic search vectors

Data sent to OpenAI is subject to [OpenAI's usage policies](https://openai.com/policies/usage-policies).

Both of these can be replaced with fully local alternatives (see the [self-hosting guide](self-hosting.md#running-fully-local)), in which case **no data leaves your server**.

For URL videos, Oracle uses **yt-dlp** to download audio directly from the source platform. This is equivalent to a user visiting the URL in a browser.

## Data isolation

Each Discord server's data is completely isolated. Users can only search and view transcriptions from their own server. There is no cross-server data access.

## Data deletion

When Oracle is removed from a server, it stops processing new content. Server administrators can request full deletion of their server's data by contacting the bot operator.

Self-hosted instances are fully controlled by the operator, who can delete the SQLite database at any time.

## Self-hosted instances

This privacy policy applies to the official hosted instance of Oracle. If you are using a self-hosted instance, the operator of that instance is responsible for their own data handling practices.

## Contact

For privacy questions or data deletion requests, contact the bot operator or open an issue on [GitHub](https://github.com/tfeuerbach/oracle).
