import asyncio
import logging

import discord
from discord import app_commands

from .config import DISCORD_TOKEN
from .database import (
    init_db,
    insert_transcription,
    message_already_processed,
    url_already_processed,
    is_setup_complete,
    is_channel_watched,
)
from .transcriber import process_video, process_url, extract_video_urls, APICredentialError
from .backfill import is_video_attachment
from .embeddings import generate_embedding
from .views import SetupView
from . import commands

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)-20s %(levelname)-8s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logging.getLogger("discord.http").setLevel(logging.ERROR)
logging.getLogger("discord.gateway").setLevel(logging.WARNING)
logging.getLogger("discord.webhook.async_").setLevel(logging.ERROR)
logging.getLogger("discord.client").setLevel(logging.WARNING)
log = logging.getLogger("oracle")

intents = discord.Intents.default()
intents.message_content = True
intents.messages = True
intents.guilds = True

bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)
commands.register(tree)


@bot.event
async def on_ready():
    init_db()
    await tree.sync()
    log.info("Oracle is online as %s (guilds: %d)", bot.user, len(bot.guilds))


@tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error.__cause__, discord.NotFound) and error.__cause__.code == 10062:
        log.warning("Interaction expired before response for /%s", interaction.command.name)
        return
    log.exception("Unhandled command error in /%s", interaction.command.name, exc_info=error)


@bot.event
async def on_guild_join(guild: discord.Guild):
    log.info("Joined new guild: %s (id=%s)", guild.name, guild.id)
    target = guild.system_channel
    if target is None:
        for ch in guild.text_channels:
            if ch.permissions_for(guild.me).send_messages:
                target = ch
                break
    if target is None:
        log.warning("No sendable channel found in %s", guild.name)
        return

    embed = discord.Embed(
        title="Hey, Oracle here.",
        description=(
            "I'll keep an eye on your video channels and make them searchable "
            "— every clip, transcribed and indexed so nothing gets lost.\n\n"
            "Pick the channels you want me to watch below, then hit **Confirm**.\n"
            "You can always add or remove channels later with `/watch` and `/unwatch`."
        ),
        color=0x5865F2,
    )
    await target.send(embed=embed, view=SetupView())


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return

    guild_id = str(message.guild.id)
    if not is_setup_complete(guild_id):
        return
    if not is_channel_watched(guild_id, str(message.channel.id)):
        return

    for att in message.attachments:
        if not is_video_attachment(att):
            continue
        if message_already_processed(guild_id, str(message.id), att.filename):
            continue
        asyncio.create_task(
            handle_new_video(message, att),
            name=f"transcribe-{message.id}-{att.filename}",
        )

    if message.content:
        for url, source in extract_video_urls(message.content):
            if url_already_processed(guild_id, str(message.id), url):
                continue
            asyncio.create_task(
                handle_new_url(message, url, source),
                name=f"transcribe-url-{message.id}",
            )


async def notify_api_error(message, error):
    log.error("API credential error: %s", error)
    try:
        await message.channel.send(
            f"**Oracle error:** {error}\n"
            "Transcription is paused until this is resolved."
        )
    except discord.HTTPException:
        pass


async def handle_new_video(message: discord.Message, attachment: discord.Attachment):
    try:
        text, duration, status = await process_video(attachment.url, attachment.filename)
    except APICredentialError as e:
        return await notify_api_error(message, e)

    if status == "no_audio":
        text = "(no audio)"
    elif status == "no_speech":
        text = "(no speech)"
    elif not text:
        log.warning("Transcription failed for %s -- not storing", attachment.filename)
        return

    embedding = await generate_embedding(text)
    insert_transcription(
        guild_id=str(message.guild.id),
        channel_id=str(message.channel.id),
        message_id=str(message.id),
        author_id=str(message.author.id),
        author_name=str(message.author),
        filename=attachment.filename,
        file_size=attachment.size,
        duration=duration,
        sent_at=message.created_at,
        transcription=text,
        embedding=embedding,
    )
    log.info("Indexed %s from %s in #%s", attachment.filename, message.author, message.channel)


async def handle_new_url(message: discord.Message, url: str, source: str):
    try:
        text, duration, status, title = await process_url(url, source)
    except APICredentialError as e:
        return await notify_api_error(message, e)

    if status in ("not_video", "too_long", "unavailable"):
        return
    if status == "no_speech":
        text = "(no speech)"
    elif not text:
        log.warning("Transcription failed for URL %s -- not storing", url)
        return

    embedding = await generate_embedding(text)
    insert_transcription(
        guild_id=str(message.guild.id),
        channel_id=str(message.channel.id),
        message_id=str(message.id),
        author_id=str(message.author.id),
        author_name=str(message.author),
        filename=title,
        duration=duration,
        sent_at=message.created_at,
        transcription=text,
        source=source,
        source_url=url,
        embedding=embedding,
    )
    log.info("Indexed URL [%s] %s from %s in #%s", source, title, message.author, message.channel)


def main():
    if not DISCORD_TOKEN:
        raise SystemExit("DISCORD_TOKEN not set. Copy .env.example to .env and fill it in.")
    bot.run(DISCORD_TOKEN)
