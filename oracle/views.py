import asyncio
import logging

import discord
from discord import ui

from .database import add_watched_channels, mark_setup_complete, get_guild_settings, update_guild_setting
from .backfill import backfill_channel

log = logging.getLogger("oracle.views")


class BackfillConfirmView(ui.View):
    """Follow-up asking whether to backfill the newly watched channels."""

    def __init__(self, guild: discord.Guild, channel_ids: list[str], *, timeout=120):
        super().__init__(timeout=timeout)
        self.guild = guild
        self.channel_ids = channel_ids

    @ui.button(label="Yes, backfill now", style=discord.ButtonStyle.green)
    async def confirm(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.edit_message(
            content="Starting backfill for selected channels...", view=None,
        )
        report_channel = interaction.channel
        for cid in self.channel_ids:
            ch = self.guild.get_channel(int(cid))
            if ch and isinstance(ch, discord.TextChannel):
                embed = discord.Embed(
                    title=f"Backfill in progress -- #{ch.name}",
                    color=0x5865F2,
                )
                embed.add_field(name="Starting", value="Scanning messages...", inline=False)
                try:
                    status_msg = await report_channel.send(embed=embed)
                except discord.HTTPException:
                    status_msg = None
                asyncio.create_task(
                    backfill_channel(ch, report_channel, status_msg=status_msg),
                    name=f"backfill-setup-{ch.id}",
                )

    @ui.button(label="No, skip backfill", style=discord.ButtonStyle.grey)
    async def skip(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.edit_message(
            content="Setup complete -- backfill skipped. Use `/backfill` any time to index past videos.",
            view=None,
        )


class ResponseModeSetupView(ui.View):
    """Shown during setup after channel selection — asks how search results should be delivered."""

    def __init__(self, guild: discord.Guild, channel_ids: list[str], *, timeout=120):
        super().__init__(timeout=timeout)
        self.guild = guild
        self.channel_ids = channel_ids
        self.guild_id = str(guild.id)

    @ui.select(
        placeholder="How should Oracle respond to queries?",
        options=[
            discord.SelectOption(
                label="Public", value="public",
                description="Everyone in the channel sees the response",
            ),
            discord.SelectOption(
                label="Private", value="private",
                description="Only the person who ran the command sees it",
            ),
            discord.SelectOption(
                label="DM", value="dm",
                description="Oracle sends the response as a direct message",
            ),
        ],
    )
    async def mode_select(self, interaction: discord.Interaction, select: ui.Select):
        value = select.values[0]
        update_guild_setting(self.guild_id, "response_mode", value)
        label = RESPONSE_MODE_LABELS[value]

        await interaction.response.edit_message(
            content=f"Response mode set to **{label}**.\nYou can change this any time with `/settings`.",
            view=None,
        )

        backfill_view = BackfillConfirmView(self.guild, self.channel_ids)
        await interaction.followup.send(
            "Would you like to backfill these channels now? "
            "This will index all past videos.",
            view=backfill_view,
        )


class SetupView(ui.View):
    """Channel selection + confirm for initial server setup."""

    def __init__(self, *, timeout=300):
        super().__init__(timeout=timeout)
        self._selected_channels: list[discord.app_commands.AppCommandChannel] = []

    @ui.select(
        cls=ui.ChannelSelect,
        placeholder="Pick channels to monitor for videos...",
        min_values=1,
        max_values=25,
        channel_types=[discord.ChannelType.text],
    )
    async def channel_select(self, interaction: discord.Interaction, select: ui.ChannelSelect):
        self._selected_channels = select.values
        names = ", ".join(f"#{ch.name}" for ch in select.values)
        await interaction.response.edit_message(
            content=f"Selected: {names}\n\nClick **Confirm** to save.",
        )

    @ui.button(label="Confirm", style=discord.ButtonStyle.green, row=1)
    async def confirm(self, interaction: discord.Interaction, button: ui.Button):
        if not self._selected_channels:
            await interaction.response.send_message(
                "Please select at least one channel first.", ephemeral=True,
            )
            return

        guild_id = str(interaction.guild_id)
        channel_ids = [str(ch.id) for ch in self._selected_channels]
        names = ", ".join(f"#{ch.name}" for ch in self._selected_channels)

        add_watched_channels(guild_id, channel_ids)
        mark_setup_complete(guild_id)

        mode_view = ResponseModeSetupView(interaction.guild, channel_ids)
        await interaction.response.edit_message(
            content=(
                f"Monitoring: {names}\n\n"
                "One more thing — when someone uses `/search`, how should Oracle respond?"
            ),
            view=mode_view,
        )
        self.stop()


RESPONSE_MODE_LABELS = {
    "public": "Public -- visible to everyone in the channel",
    "private": "Private -- only you can see the response",
    "dm": "DM -- sent to your direct messages",
}


def build_settings_embed(guild_id: str, guild_name: str):
    settings = get_guild_settings(guild_id)
    mode = settings["response_mode"]

    embed = discord.Embed(title="Oracle Settings", color=0x5865F2)
    embed.set_footer(text=guild_name)
    embed.add_field(
        name="Response mode",
        value=RESPONSE_MODE_LABELS.get(mode, mode),
        inline=False,
    )
    return embed


class SettingsView(ui.View):
    """Dropdowns for configuring guild settings."""

    def __init__(self, guild_id: str, guild_name: str, *, timeout=180):
        super().__init__(timeout=timeout)
        self.guild_id = guild_id
        self.guild_name = guild_name

    @ui.select(
        placeholder="Response mode...",
        options=[
            discord.SelectOption(label="Public", value="public", description="Visible to everyone in the channel"),
            discord.SelectOption(label="Private", value="private", description="Only you can see the response"),
            discord.SelectOption(label="DM", value="dm", description="Sent to your direct messages"),
        ],
        row=0,
    )
    async def response_mode_select(self, interaction: discord.Interaction, select: ui.Select):
        value = select.values[0]
        update_guild_setting(self.guild_id, "response_mode", value)
        embed = build_settings_embed(self.guild_id, self.guild_name)
        await interaction.response.edit_message(embed=embed, view=self)
