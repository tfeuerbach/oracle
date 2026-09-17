import asyncio
import logging
from datetime import datetime

import discord
from discord import app_commands

from .config import SEARCH_RESULTS_LIMIT, MAX_VIDEO_DURATION, EMBED_MAX_FILE_BYTES
from .database import (
    search_transcriptions,
    get_guild_stats,
    get_guild_embeddings,
    get_transcriptions_by_ids,
    is_setup_complete,
    is_channel_watched,
    add_watched_channels,
    remove_watched_channel,
    get_watched_channels,
    mark_setup_complete,
    get_guild_settings,
    resolve_response_mode,
    set_user_response_mode,
    clear_user_response_mode,
    get_user_response_mode,
)
from .embeddings import generate_embedding, semantic_search
from .backfill import backfill_channel, backfill_guild, cancel_backfills, active_backfill_keys
from .views import SetupView, SettingsView, build_settings_embed, RESPONSE_MODE_LABELS
from .transcriber import download_for_embed, TooLong

log = logging.getLogger("oracle.commands")


async def send_response(interaction: discord.Interaction, content: str = None, embed: discord.Embed = None):
    """Send a command response: user preference > guild setting > public."""
    mode = resolve_response_mode(str(interaction.user.id), str(interaction.guild_id))

    kwargs = {}
    if content:
        kwargs["content"] = content
    if embed:
        kwargs["embed"] = embed

    if mode == "dm":
        ack_text = "Sent to your DMs."
        try:
            await interaction.user.send(**kwargs)
        except discord.Forbidden:
            ack_text = "I can't DM you -- check your privacy settings."

        if not interaction.response.is_done():
            await interaction.response.send_message(ack_text, ephemeral=True)
        else:
            await interaction.followup.send(ack_text, ephemeral=True)
        return

    ephemeral = mode == "private"
    if not interaction.response.is_done():
        await interaction.response.send_message(**kwargs, ephemeral=ephemeral)
    else:
        await interaction.followup.send(**kwargs, ephemeral=ephemeral)


def register(tree: app_commands.CommandTree):

    @tree.command(name="setup", description="Configure Oracle for this server (pick channels to monitor)")
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def cmd_setup(interaction: discord.Interaction):
        embed = discord.Embed(
            title="Oracle Setup",
            description=(
                "Pick which channels to monitor for videos.\n\n"
                "Use the dropdown below to select channels, then click **Confirm**.\n"
                "You can change this later with `/watch` and `/unwatch`."
            ),
            color=0x5865F2,
        )
        await interaction.response.send_message(embed=embed, view=SetupView())

    @tree.command(name="settings", description="Configure bot preferences for this server")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def cmd_settings(interaction: discord.Interaction):
        guild_id = str(interaction.guild_id)
        embed = build_settings_embed(guild_id, interaction.guild.name)
        view = SettingsView(guild_id, interaction.guild.name)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @tree.command(name="watch", description="Add a channel to the video monitoring list")
    @app_commands.describe(channel="Channel to start monitoring")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def cmd_watch(interaction: discord.Interaction, channel: discord.TextChannel):
        guild_id = str(interaction.guild_id)
        if is_channel_watched(guild_id, str(channel.id)):
            await interaction.response.send_message(
                f"{channel.mention} is already being monitored.", ephemeral=True)
            return
        add_watched_channels(guild_id, [str(channel.id)])
        if not is_setup_complete(guild_id):
            mark_setup_complete(guild_id)

        from .views import BackfillConfirmView
        await interaction.response.send_message(
            f"Now monitoring {channel.mention} for videos.")
        view = BackfillConfirmView(interaction.guild, [str(channel.id)])
        await interaction.followup.send(
            f"Would you like to backfill {channel.mention} now? This will index all past videos.",
            view=view,
        )

    @tree.command(name="unwatch", description="Remove a channel from the video monitoring list")
    @app_commands.describe(channel="Channel to stop monitoring")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def cmd_unwatch(interaction: discord.Interaction, channel: discord.TextChannel):
        guild_id = str(interaction.guild_id)
        if not is_channel_watched(guild_id, str(channel.id)):
            await interaction.response.send_message(
                f"{channel.mention} is not being monitored.", ephemeral=True)
            return
        remove_watched_channel(guild_id, str(channel.id))
        await interaction.response.send_message(f"Stopped monitoring {channel.mention}.")

    @tree.command(name="watching", description="List all channels being monitored for videos")
    @app_commands.guild_only()
    async def cmd_watching(interaction: discord.Interaction):
        channel_ids = get_watched_channels(str(interaction.guild_id))
        if not channel_ids:
            await interaction.response.send_message(
                "No channels are being monitored. Use `/setup` or `/watch` to add some.",
                ephemeral=True)
            return
        mentions = []
        for cid in sorted(channel_ids):
            ch = interaction.guild.get_channel(int(cid))
            mentions.append(ch.mention if ch else f"<#{cid}>")
        await send_response(interaction, content=f"**Monitored channels:** {', '.join(mentions)}")

    @tree.command(name="search", description="Search video transcriptions by what was said")
    @app_commands.describe(query="Words or phrase you remember hearing in a video")
    @app_commands.guild_only()
    async def cmd_search(interaction: discord.Interaction, query: str):
        mode = resolve_response_mode(str(interaction.user.id), str(interaction.guild_id))
        ephemeral = mode == "private"

        if not interaction.response.is_done():
            await interaction.response.defer(thinking=True, ephemeral=ephemeral)

        guild_id = str(interaction.guild_id)

        fts_results = search_transcriptions(guild_id, query, limit=SEARCH_RESULTS_LIMIT)

        semantic_results = []
        fts_ids = {r.id for r in fts_results}
        query_emb = await generate_embedding(query)
        if query_emb:
            candidates = get_guild_embeddings(guild_id)
            if candidates:
                ranked = semantic_search(query_emb, candidates, top_k=5)
                ranked_ids = [row_id for row_id, _ in ranked if row_id not in fts_ids]
                if ranked_ids:
                    semantic_results = get_transcriptions_by_ids(ranked_ids)

        if not fts_results and not semantic_results:
            embed = discord.Embed(
                title=f'Search: "{query}"',
                description="Nothing turned up this time. Try different words or a broader phrase.",
                color=0x5865F2,
            )
            await send_response(interaction, embed=embed)
            return

        def format_result(i: int, r):
            sent = ""
            if r.sent_at:
                try:
                    dt = datetime.fromisoformat(r.sent_at)
                    sent = f" -- <t:{int(dt.timestamp())}:R>"
                except ValueError:
                    sent = f" -- {r.sent_at[:10]}"
            dur = ""
            if r.duration:
                m, s = divmod(int(r.duration), 60)
                dur = f" ({m}:{s:02d})"
            jump = f"https://discord.com/channels/{r.guild_id}/{r.channel_id}/{r.message_id}"
            if r.source_url:
                name_part = f"[{r.filename}]({r.source_url})"
                source_tag = f" `{r.source}`"
            else:
                name_part = f"[{r.filename}]({jump})"
                source_tag = ""
            preview = r.transcription or ""
            if len(preview) > 120:
                preview = preview[:120] + "..."
            return (
                f"**{i}.** {name_part}{dur}{source_tag}{sent}\n"
                f"   by <@{r.author_id}> — [jump to message]({jump})\n"
                f"   *\"{preview}\"*"
            )

        sections = []

        if fts_results:
            lines = [format_result(i, r) for i, r in enumerate(fts_results, 1)]
            sections.append("🔤 __**Keyword matches**__\n\n" + "\n\n".join(lines))
        else:
            sections.append("🔤 __**Keyword matches**__\n*No exact matches found.*")

        if semantic_results:
            lines = [format_result(i, r) for i, r in enumerate(semantic_results, 1)]
            sections.append("🧠 __**Similar results**__\n\n" + "\n\n".join(lines))

        description = "\n\n---\n\n".join(sections)
        if len(description) > 4000:
            description = description[:3997] + "..."

        total = len(fts_results) + len(semantic_results)
        embed = discord.Embed(
            title=f'Search: "{query}"',
            description=description,
            color=0x5865F2,
        )
        embed.set_footer(text=f"{total} result{'s' if total != 1 else ''}")
        await send_response(interaction, embed=embed)

    @tree.command(name="stats", description="Show video indexing statistics for this server")
    @app_commands.guild_only()
    async def cmd_stats(interaction: discord.Interaction):
        stats = get_guild_stats(str(interaction.guild_id))
        total_mb = stats["total_bytes_processed"] / (1024 * 1024)
        total_min = stats["total_duration_seconds"] / 60

        embed = discord.Embed(title="Oracle Stats", color=0x5865F2)
        embed.add_field(name="Videos Indexed", value=f"{stats['total_videos']:,}", inline=True)
        embed.add_field(name="Total Size Processed", value=f"{total_mb:,.1f} MB", inline=True)
        embed.add_field(name="Total Duration", value=f"{total_min:,.1f} min", inline=True)
        embed.add_field(name="Unique Posters", value=f"{stats['unique_authors']:,}", inline=True)
        embed.add_field(name="Channels Indexed", value=f"{stats['channels_indexed']:,}", inline=True)
        await send_response(interaction, embed=embed)

    @tree.command(name="backfill", description="Index all past videos in a channel")
    @app_commands.describe(channel="Channel to backfill (defaults to current)")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def cmd_backfill(interaction: discord.Interaction, channel: discord.TextChannel | None = None):
        target = channel or interaction.channel
        report_channel = interaction.channel
        status_msg = None
        try:
            if not interaction.response.is_done():
                await interaction.response.defer(thinking=True)
            embed = discord.Embed(
                title=f"Backfill in progress -- #{target.name}",
                color=0x5865F2,
            )
            embed.add_field(name="Status", value="Starting...", inline=False)
            status_msg = await interaction.followup.send(embed=embed, wait=True)
        except (discord.NotFound, discord.HTTPException):
            pass
        asyncio.create_task(
            backfill_channel(target, report_channel, status_msg=status_msg),
            name=f"backfill-{target.id}",
        )

    @tree.command(name="stop_backfill", description="Stop all running backfills in this server")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def cmd_stop_backfill(interaction: discord.Interaction):
        guild_id = str(interaction.guild_id)
        keys = active_backfill_keys(guild_id)
        if not keys:
            await interaction.response.send_message("No backfills are currently running.", ephemeral=True)
            return
        count = cancel_backfills(guild_id)
        await interaction.response.send_message(
            f"Stopping {count} running backfill{'s' if count != 1 else ''}. "
            f"In-progress transcriptions will finish but no new ones will start.")

    @tree.command(name="preference", description="Set how Oracle responds to you in this server")
    @app_commands.describe(mode="How Oracle should send results to you")
    @app_commands.choices(mode=[
        app_commands.Choice(name="Public -- visible to everyone", value="public"),
        app_commands.Choice(name="Private -- only you can see it", value="private"),
        app_commands.Choice(name="DM -- sent to your direct messages", value="dm"),
        app_commands.Choice(name="Use server default", value="default"),
    ])
    @app_commands.guild_only()
    async def cmd_preference(interaction: discord.Interaction, mode: app_commands.Choice[str]):
        user_id = str(interaction.user.id)
        guild_id = str(interaction.guild_id)

        if mode.value == "default":
            clear_user_response_mode(user_id, guild_id)
            server_mode = get_guild_settings(guild_id)["response_mode"]
            await interaction.response.send_message(
                f"Preference cleared — using server default (**{server_mode}**).",
                ephemeral=True,
            )
        else:
            set_user_response_mode(user_id, guild_id, mode.value)
            label = RESPONSE_MODE_LABELS[mode.value]
            await interaction.response.send_message(
                f"Your preference is now **{label}**.\n"
                "This overrides the server default for your queries.",
                ephemeral=True,
            )

    @tree.command(name="embed", description="Download Instagram, TikTok, Reddit, or YouTube Shorts and post them here")
    @app_commands.describe(url="Instagram, TikTok, Reddit, or YouTube Shorts link")
    @app_commands.guild_only()
    async def cmd_embed(interaction: discord.Interaction, url: str):
        await interaction.response.defer(thinking=True)

        video_path = None
        try:
            video_path, title, source = await download_for_embed(url)
        except ValueError as e:
            await interaction.followup.send(str(e), ephemeral=True)
            return
        except TooLong as e:
            m, s = divmod(int(e.duration), 60)
            await interaction.followup.send(
                f"Video is too long ({m}:{s:02d}). Max is {MAX_VIDEO_DURATION // 60} minutes.",
                ephemeral=True,
            )
            return
        except asyncio.TimeoutError:
            await interaction.followup.send("Download timed out. Try again later.", ephemeral=True)
            return
        except Exception as e:
            log.warning("Embed failed for %s: %s", url, e)
            await interaction.followup.send(
                "Couldn't download that video. It may be private, deleted, or region-locked.",
                ephemeral=True,
            )
            return

        try:
            size = video_path.stat().st_size
            if size > EMBED_MAX_FILE_BYTES:
                await interaction.followup.send(
                    f"Downloaded video is too large "
                    f"({size / 1e6:.1f} MB, limit {EMBED_MAX_FILE_BYTES / 1e6:.0f} MB).",
                    ephemeral=True,
                )
                return

            safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in title)[:80]
            if not safe_name:
                safe_name = "video"
            if not video_path.suffix:
                safe_name += ".mp4"
            else:
                safe_name += video_path.suffix

            file = discord.File(video_path, filename=safe_name)
            await interaction.followup.send(
                content=f"**{title}**\n`{source}` · requested by {interaction.user.mention}",
                file=file,
            )
            log.info("Embedded %s (%s, %.1f MB) in #%s", title, source, size / 1e6, interaction.channel)
        except discord.HTTPException as e:
            log.warning("Failed to upload embed for %s: %s", url, e)
            await interaction.followup.send(
                "Downloaded the video but Discord rejected the upload "
                f"(file may exceed this server's upload limit).",
                ephemeral=True,
            )
        finally:
            if video_path:
                video_path.unlink(missing_ok=True)
                for leftover in video_path.parent.glob(f"{video_path.stem}.*"):
                    leftover.unlink(missing_ok=True)

    @tree.command(name="backfill_server", description="Index all past videos across the entire server")
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def cmd_backfill_server(interaction: discord.Interaction):
        report_channel = interaction.channel
        try:
            if not interaction.response.is_done():
                await interaction.response.defer(thinking=True)
            await interaction.followup.send(
                f"\u23f3 Server-wide backfill starting for **{interaction.guild.name}**..."
            )
        except (discord.NotFound, discord.HTTPException):
            pass
        asyncio.create_task(
            backfill_guild(interaction.guild, report_channel),
            name=f"backfill-guild-{interaction.guild_id}",
        )
