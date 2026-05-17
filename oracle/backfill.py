import asyncio
import logging
import time

import discord

from .config import VIDEO_EXTENSIONS, MAX_CONCURRENT_TRANSCRIPTIONS
from .database import insert_transcription, message_already_processed, url_already_processed
from .embeddings import generate_embeddings_batch
from .transcriber import process_video, process_url, extract_video_urls, APICredentialError

log = logging.getLogger("oracle.backfill")

active_backfills: dict[str, asyncio.Event] = {}


def cancel_backfills(guild_id: str):
    """Signal all running backfills for a guild to stop. Returns count cancelled."""
    cancelled = 0
    for key, stop_event in list(active_backfills.items()):
        if key == guild_id or key.startswith(f"{guild_id}:"):
            stop_event.set()
            cancelled += 1
    return cancelled


def active_backfill_keys(guild_id: str):
    """Return keys of running backfills for a guild."""
    return [k for k in active_backfills if k == guild_id or k.startswith(f"{guild_id}:")]


def fmt_size(b: int):
    if b < 1024:
        return f"{b} B"
    if b < 1024 * 1024:
        return f"{b / 1024:.1f} KB"
    return f"{b / (1024 * 1024):.1f} MB"


def fmt_duration(s: float | None):
    if s is None:
        return "?"
    m, sec = divmod(int(s), 60)
    return f"{m}:{sec:02d}"


def is_video_attachment(attachment: discord.Attachment):
    if attachment.content_type and attachment.content_type.startswith("video/"):
        return True
    suffix = "." + attachment.filename.rsplit(".", 1)[-1].lower() if "." in attachment.filename else ""
    return suffix in VIDEO_EXTENSIONS


MARKER_STATUSES = {
    "no_audio":    ("(no audio)",     "processed", "  [done] %s -- no audio stream"),
    "no_speech":   ("(no speech)",    "processed", "  [done] %s -- no speech detected"),
    "not_video":   ("(not a video)",  "skipped",   "  [skip] %s -- not a video post"),
    "too_long":    ("(too long)",     "skipped",   "  [skip] %s -- exceeds max duration"),
    "unavailable": ("(unavailable)",  "skipped",   "  [skip] %s -- unavailable (deleted/private/blocked)"),
}


class BatchItem:
    __slots__ = ("msg", "attachment", "url", "source")

    def __init__(self, msg, *, attachment=None, url=None, source=None):
        self.msg = msg
        self.attachment = attachment
        self.url = url
        self.source = source

    @property
    def label(self):
        return self.attachment.filename if self.attachment else self.url


async def process_batch(
    batch: list[BatchItem],
    guild_id: str,
    channel_id: str,
    counters: dict,
    retry_queue: list,
    error_log: list[str] | None = None,
):
    """Process a batch of items. Populates counters and retry_queue.

    If error_log is provided, short error descriptions are appended for display.
    """
    async def do_one(item: BatchItem):
        if item.attachment:
            text, duration, status = await process_video(
                item.attachment.url, item.attachment.filename, priority="backfill",
            )
            return item, text, duration, status, None
        else:
            text, duration, status, title = await process_url(
                item.url, item.source, priority="backfill",
            )
            return item, text, duration, status, title

    results = await asyncio.gather(
        *[do_one(item) for item in batch],
        return_exceptions=True,
    )

    for result in results:
        if isinstance(result, APICredentialError):
            raise result

    texts_to_embed = []
    for result in results:
        if isinstance(result, BaseException):
            texts_to_embed.append("")
        else:
            _, text, _, status, _ = result
            texts_to_embed.append(text if status == "ok" and text else "")
    embeddings = await generate_embeddings_batch(texts_to_embed)

    for idx, result in enumerate(results):
        if isinstance(result, BaseException):
            counters["failed"] += 1
            label = batch[idx].label.split("/")[-1][:40]
            err_str = str(result)[:60]
            log.warning("  [fail] %s -- %s", label, err_str)
            if error_log is not None:
                error_log.append(f"`{label}` -- {err_str}")
            continue

        item, text, duration, status, title = result
        msg = item.msg
        is_url = item.url is not None
        filename = title if is_url else item.attachment.filename
        file_size = None if is_url else item.attachment.size
        source = item.source if is_url else "file"
        source_url = item.url if is_url else None

        if status in MARKER_STATUSES:
            marker, counter_key, log_msg = MARKER_STATUSES[status]
            insert_transcription(
                guild_id=guild_id, channel_id=channel_id,
                message_id=str(msg.id), author_id=str(msg.author.id),
                author_name=str(msg.author), filename=filename,
                file_size=file_size, duration=duration,
                sent_at=msg.created_at, transcription=marker,
                source=source, source_url=source_url,
            )
            counters[counter_key] += 1
            log.info(log_msg, filename)

        elif status == "error":
            counters["failed"] += 1
            retry_queue.append(item)
            short_name = (filename or "unknown").split("/")[-1][:40]
            log.warning("  [fail] %s -- queued for retry", filename)
            if error_log is not None:
                error_log.append(f"`{short_name}` -- download/transcription failed")

        elif text:
            row_id = insert_transcription(
                guild_id=guild_id, channel_id=channel_id,
                message_id=str(msg.id), author_id=str(msg.author.id),
                author_name=str(msg.author), filename=filename,
                file_size=file_size, duration=duration,
                sent_at=msg.created_at, transcription=text,
                source=source, source_url=source_url,
                embedding=embeddings[idx],
            )
            if row_id:
                counters["processed"] += 1
                preview = (text[:80] + "...") if len(text) > 80 else text
                log.info('  [done] %s -- %s -- "%s"', filename, fmt_duration(duration), preview)
            else:
                counters["failed"] += 1


async def backfill_channel(
    channel: discord.TextChannel,
    report_channel: discord.TextChannel | None = None,
    status_msg: discord.Message | None = None,
):
    guild_id = str(channel.guild.id)
    key = f"{guild_id}:{channel.id}"

    if key in active_backfills:
        log.info("Backfill already running for #%s in %s", channel.name, channel.guild.name)
        return

    stop = asyncio.Event()
    active_backfills[key] = stop
    counters = {"processed": 0, "skipped": 0, "failed": 0}
    total_found = 0
    messages_scanned = 0
    start_time = time.monotonic()
    retry_queue: list[BatchItem] = []
    batch: list[BatchItem] = []
    recent_errors: list[str] = []

    if status_msg:
        log.info("Using existing status message (msg_id=%s)", status_msg.id)
    elif report_channel:
        try:
            status_msg = await report_channel.send(
                f"\u23f3 **Backfill starting** -- #{channel.name}\nScanning messages..."
            )
            log.info("Status message created in #%s (msg_id=%s)", report_channel.name, status_msg.id)
        except discord.HTTPException as e:
            log.warning("Could not send status message to #%s: %s", report_channel.name, e)

    async def status(msg, *args, **kwargs):
        return await update_status(msg, *args, report_channel=report_channel, **kwargs)

    async def error_embed(msg, *args, **kwargs):
        return await edit_error_embed(msg, *args, report_channel=report_channel, **kwargs)

    try:
        log.info("Backfill started: #%s in %s", channel.name, channel.guild.name)
        await asyncio.sleep(2)

        last_message_id = None
        scan_exhausted = False
        while not scan_exhausted and not stop.is_set():
            retries = 0
            try:
                kwargs = {"limit": None, "oldest_first": True}
                if last_message_id:
                    kwargs["after"] = discord.Object(id=last_message_id)
                async for message in channel.history(**kwargs):
                    if stop.is_set():
                        log.info("Backfill cancelled for #%s", channel.name)
                        break

                    last_message_id = message.id
                    retries = 0
                    messages_scanned += 1

                    if status_msg and messages_scanned % 200 == 0:
                        status_msg = await status(
                            status_msg, channel, total_found, counters, start_time,
                            note=f"Scanning messages... ({messages_scanned:,} scanned)",
                        )

                    for attachment in message.attachments:
                        if not is_video_attachment(attachment):
                            continue
                        total_found += 1
                        if message_already_processed(guild_id, str(message.id), attachment.filename):
                            counters["skipped"] += 1
                            if counters["skipped"] <= 5 or counters["skipped"] % 25 == 0:
                                log.info("  [skip %d] %s (%s) -- already indexed",
                                         counters["skipped"], attachment.filename, fmt_size(attachment.size))
                            if status_msg and counters["skipped"] % 10 == 0:
                                status_msg = await status(status_msg, channel, total_found, counters, start_time)
                            continue
                        log.info("  [%d] Queued: %s (%s) from %s -- %s",
                                 total_found, attachment.filename, fmt_size(attachment.size),
                                 message.author, message.created_at.strftime("%Y-%m-%d"))
                        batch.append(BatchItem(message, attachment=attachment))

                    if message.content:
                        for url, source in extract_video_urls(message.content):
                            total_found += 1
                            if url_already_processed(guild_id, str(message.id), url):
                                counters["skipped"] += 1
                                continue
                            log.info("  [%d] Queued URL: %s [%s] from %s -- %s",
                                     total_found, url, source,
                                     message.author, message.created_at.strftime("%Y-%m-%d"))
                            batch.append(BatchItem(message, url=url, source=source))

                    if len(batch) >= MAX_CONCURRENT_TRANSCRIPTIONS:
                        log.info("  -- processing batch of %d --", len(batch))
                        if status_msg:
                            status_msg = await status(
                                status_msg, channel, total_found, counters, start_time,
                                note=f"Processing batch of {len(batch)}...",
                            )
                        await process_batch(batch, guild_id, str(channel.id), counters, retry_queue, recent_errors)
                        batch.clear()
                        if status_msg:
                            status_msg = await status(
                                status_msg, channel, total_found, counters, start_time,
                                errors=recent_errors,
                            )
                else:
                    scan_exhausted = True

            except discord.HTTPException as e:
                if e.status == 429:
                    wait = getattr(e, "retry_after", 10) or 10
                    log.warning("Rate limited scanning #%s, waiting %.1fs...", channel.name, wait)
                    await asyncio.sleep(wait)
                elif e.status >= 500 and retries < 5:
                    retries += 1
                    log.warning("Discord %d scanning #%s (attempt %d/5), retrying in 15s...",
                                e.status, channel.name, retries)
                    if status_msg:
                        status_msg = await status(
                            status_msg, channel, total_found, counters, start_time,
                            note=f"Discord API error ({e.status}) -- retrying...",
                        )
                    await asyncio.sleep(15)
                else:
                    raise

        if not stop.is_set() and batch:
            log.info("  -- processing final batch of %d --", len(batch))
            if status_msg:
                status_msg = await status(
                    status_msg, channel, total_found, counters, start_time,
                    note=f"Processing final batch of {len(batch)}...",
                )
            await process_batch(batch, guild_id, str(channel.id), counters, retry_queue, recent_errors)
            batch.clear()

        if not stop.is_set() and retry_queue:
            log.info("Retrying %d failed items for #%s", len(retry_queue), channel.name)
            if status_msg:
                status_msg = await status(
                    status_msg, channel, total_found, counters, start_time,
                    note=f"Retrying {len(retry_queue)} failed item{'s' if len(retry_queue) != 1 else ''}...",
                    errors=recent_errors,
                )
            await asyncio.sleep(5)

            retry_batch = []
            for item in retry_queue:
                if item.attachment:
                    if message_already_processed(guild_id, str(item.msg.id), item.attachment.filename):
                        continue
                else:
                    if url_already_processed(guild_id, str(item.msg.id), item.url):
                        continue
                log.info("  [retry] %s", item.label)
                retry_batch.append(item)

            if retry_batch:
                retry_counters = {"processed": 0, "skipped": 0, "failed": 0}
                still_failed: list[BatchItem] = []
                await process_batch(retry_batch, guild_id, str(channel.id), retry_counters, still_failed, recent_errors)
                counters["processed"] += retry_counters["processed"]
                counters["failed"] -= retry_counters["processed"]

                for item in still_failed:
                    msg = item.msg
                    is_url = item.url is not None
                    insert_transcription(
                        guild_id=guild_id, channel_id=str(channel.id),
                        message_id=str(msg.id), author_id=str(msg.author.id),
                        author_name=str(msg.author),
                        filename=item.url if is_url else item.attachment.filename,
                        file_size=None if is_url else item.attachment.size,
                        duration=None, sent_at=msg.created_at,
                        transcription="(unavailable)",
                        source=item.source if is_url else "file",
                        source_url=item.url if is_url else None,
                    )
                    log.info("  [indexed] %s -- marked unavailable", item.label)

        elapsed = time.monotonic() - start_time
        m, s = divmod(int(elapsed), 60)

        if stop.is_set():
            log.info("Backfill stopped: #%s -- %d transcribed, %d already indexed, %d failed (%dm%ds)",
                     channel.name, counters["processed"], counters["skipped"], counters["failed"], m, s)
            if status_msg:
                status_msg = await status(
                    status_msg, channel, total_found, counters, start_time,
                    note="Stopped by user.", errors=recent_errors,
                )
        else:
            log.info("Backfill complete: #%s -- %d scanned, %d found, %d transcribed, %d already indexed, %d failed (%dm%ds)",
                     channel.name, messages_scanned, total_found,
                     counters["processed"], counters["skipped"], counters["failed"], m, s)
            if status_msg:
                status_msg = await status(
                    status_msg, channel, total_found, counters, start_time,
                    done=True, errors=recent_errors,
                )

    except APICredentialError as e:
        log.error("Backfill halted for #%s: %s", channel.name, e)
        await error_embed(status_msg, channel,
                          f"**API error:** {e}\nFix the issue and run `/backfill` again.")
    except discord.Forbidden:
        log.warning("Missing permissions to read history in #%s", channel.name)
        await error_embed(status_msg, channel,
                          "Missing permissions to read message history.\n"
                          "Grant the bot **Read Message History** and **View Channel**, then try again.")
    except discord.HTTPException as e:
        log.exception("HTTP error during backfill of #%s (status %d)", channel.name, e.status)
        await error_embed(status_msg, channel,
                          f"Discord HTTP error ({e.status}): {e.text[:200] if e.text else 'unknown'}")
    except Exception as e:
        log.exception("Backfill failed for #%s", channel.name)
        await error_embed(status_msg, channel,
                          f"Unexpected error: `{type(e).__name__}: {str(e)[:200]}`\n"
                          "Check the bot logs for details.")
    finally:
        active_backfills.pop(key, None)


async def backfill_guild(guild: discord.Guild, report_channel: discord.TextChannel | None = None):
    guild_id = str(guild.id)
    if guild_id in active_backfills:
        return

    stop = asyncio.Event()
    active_backfills[guild_id] = stop
    accessible = []

    try:
        for channel in guild.text_channels:
            perms = channel.permissions_for(guild.me)
            if perms.read_message_history and perms.read_messages:
                accessible.append(channel)

        log.info("Server backfill: %s -- %d/%d channels accessible",
                 guild.name, len(accessible), len(guild.text_channels))

        if report_channel:
            try:
                await report_channel.send(
                    f"Starting server-wide backfill for **{guild.name}** -- "
                    f"{len(accessible)}/{len(guild.text_channels)} channels accessible...")
            except discord.HTTPException:
                pass

        for i, channel in enumerate(accessible, 1):
            if stop.is_set():
                log.info("Server backfill cancelled for %s at channel %d/%d", guild.name, i, len(accessible))
                break
            log.info("Channel %d/%d: #%s", i, len(accessible), channel.name)
            await backfill_channel(channel, report_channel)

        if stop.is_set():
            log.info("Server backfill stopped: %s", guild.name)
            if report_channel:
                try:
                    await report_channel.send(f"Server-wide backfill stopped for **{guild.name}**.")
                except discord.HTTPException:
                    pass
        else:
            log.info("Server backfill complete: %s", guild.name)
            if report_channel:
                try:
                    await report_channel.send(f"Server-wide backfill complete for **{guild.name}**.")
                except discord.HTTPException:
                    pass
    finally:
        active_backfills.pop(guild_id, None)


async def edit_error_embed(msg, channel, description, color=0xED4245, report_channel=None):
    if not msg:
        return
    channel_name = channel.name if hasattr(channel, "name") else str(channel)
    embed = discord.Embed(
        title=f"Backfill failed -- #{channel_name}",
        description=description,
        color=color,
    )
    try:
        await msg.edit(content="", embed=embed)
    except discord.NotFound:
        pass
    except discord.HTTPException:
        try:
            await msg.delete()
        except discord.HTTPException:
            pass
        try:
            dest = report_channel or msg.channel
            await dest.send(embed=embed)
        except discord.HTTPException:
            pass


def build_status_embed(channel, found, counters, start_time, done=False, note=None, errors=None):
    elapsed = time.monotonic() - start_time
    m, s = divmod(int(elapsed), 60)
    h, m = divmod(m, 60)
    elapsed_str = f"{h}h {m}m {s}s" if h else f"{m}m {s}s"

    if done:
        title = f"Backfill complete -- #{channel.name}"
        color = 0x57F287
    elif note and "stop" in note.lower():
        title = f"Backfill stopped -- #{channel.name}"
        color = 0xED4245
    else:
        title = f"Backfill in progress -- #{channel.name}"
        color = 0x5865F2

    embed = discord.Embed(title=title, color=color)
    embed.add_field(name="Transcribed", value=str(counters["processed"]), inline=True)
    embed.add_field(name="Already indexed", value=str(counters["skipped"]), inline=True)
    embed.add_field(name="Failed", value=str(counters["failed"]), inline=True)
    embed.add_field(name="Found", value=str(found), inline=True)
    embed.add_field(name="Elapsed", value=elapsed_str, inline=True)

    if errors:
        shown = errors[:3]
        error_text = "\n".join(f"- {e}" for e in shown)
        if len(errors) > 3:
            error_text += f"\n- ...and {len(errors) - 3} more"
        embed.add_field(name="Recent errors", value=error_text, inline=False)

    return embed


WEBHOOK_TOKEN_LIFETIME = 14 * 60

async def update_status(msg, channel, found, counters, start_time, done=False, note=None, errors=None, report_channel=None):
    embed = build_status_embed(channel, found, counters, start_time, done=done, note=note, errors=errors)
    dest = report_channel or msg.channel

    if not getattr(msg, "_bot_owned", False) and (time.monotonic() - start_time) >= WEBHOOK_TOKEN_LIFETIME:
        try:
            await msg.delete()
        except discord.HTTPException:
            pass
        try:
            new_msg = await dest.send(embed=embed)
            new_msg._bot_owned = True
            log.debug("Swapped webhook status message to bot-owned message in #%s", dest.name)
            return new_msg
        except discord.HTTPException:
            return msg

    try:
        await msg.edit(content="", embed=embed)
    except discord.HTTPException:
        pass
    return msg
