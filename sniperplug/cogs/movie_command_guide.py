from __future__ import annotations

import logging
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from sniperplug.services.movie_ticket_drops import ATOM_PROMOTIONS_URL, clean_text


log = logging.getLogger("sniperplug.movie_guide")

GUIDE_COLOR = discord.Color.gold()
GUIDE_SELECT_ID = "movies:guide:section"
GUIDE_LATEST_ID = "movies:guide:latest"
GUIDE_STATUS_ID = "movies:guide:status"
MAX_LATEST_EMBEDS = 8


class MovieGuideSectionSelect(discord.ui.Select):
    def __init__(self, cog: "MovieCommandGuideCog") -> None:
        self.cog = cog
        options = [
            discord.SelectOption(
                label="Start Here",
                value="start",
                emoji="🎬",
                description="The quickest way to start finding free tickets.",
            ),
            discord.SelectOption(
                label="Find Free Tickets",
                value="users",
                emoji="🎟️",
                description="Commands anyone can use to check drops and sources.",
            ),
            discord.SelectOption(
                label="Server Setup",
                value="admins",
                emoji="⚙️",
                description="Commands for server owners and managers.",
            ),
            discord.SelectOption(
                label="Safety & Sources",
                value="safety",
                emoji="🛡️",
                description="What SniperPlug verifies and what it cannot guarantee.",
            ),
        ]
        super().__init__(
            placeholder="Choose what you need help with…",
            min_values=1,
            max_values=1,
            options=options,
            custom_id=GUIDE_SELECT_ID,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        section = self.values[0] if self.values else "start"
        await interaction.response.send_message(
            embed=build_movie_guide_section_embed(section),
            ephemeral=True,
        )


class MovieGuidePanelView(discord.ui.View):
    def __init__(self, cog: "MovieCommandGuideCog") -> None:
        super().__init__(timeout=None)
        self.cog = cog
        self.add_item(MovieGuideSectionSelect(cog))
        self.add_item(
            discord.ui.Button(
                label="Official Atom Promotions",
                style=discord.ButtonStyle.link,
                url=ATOM_PROMOTIONS_URL,
                emoji="🔗",
            )
        )

    @discord.ui.button(
        label="Check Latest Drops",
        style=discord.ButtonStyle.success,
        emoji="🎟️",
        custom_id=GUIDE_LATEST_ID,
    )
    async def latest_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.cog.send_latest_from_panel(interaction)

    @discord.ui.button(
        label="Setup Status",
        style=discord.ButtonStyle.secondary,
        emoji="📡",
        custom_id=GUIDE_STATUS_ID,
    )
    async def status_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.cog.send_status_from_panel(interaction)


class MovieCommandGuideCog(commands.Cog):
    """Interactive help panel for the `/movies` command group."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.bot.add_view(MovieGuidePanelView(self))
        log.info("Persistent movie command guide panel registered")

    @app_commands.command(
        name="movies-help",
        description="Open a private guide explaining every movie-ticket command.",
    )
    async def movies_help(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            embed=build_movie_guide_home_embed(),
            view=MovieGuidePanelView(self),
            ephemeral=True,
        )

    @app_commands.command(
        name="movies-panel",
        description="Post the interactive movie-ticket command guide for your server.",
    )
    @app_commands.describe(
        target_channel="Channel where the permanent movie-ticket guide should be posted. Defaults to this channel.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def movies_panel(
        self,
        interaction: discord.Interaction,
        target_channel: discord.TextChannel | None = None,
    ) -> None:
        if not interaction.guild or not interaction.guild_id:
            await interaction.response.send_message("Use this command inside a server.", ephemeral=True)
            return

        channel = target_channel
        if channel is None and isinstance(interaction.channel, discord.TextChannel):
            channel = interaction.channel
        if channel is None:
            await interaction.response.send_message(
                "Choose a normal text channel with the `target_channel` option.",
                ephemeral=True,
            )
            return

        missing = missing_channel_permissions(interaction.guild, channel, self.bot)
        if missing:
            await interaction.response.send_message(
                f"I cannot post the guide in {channel.mention}. Missing: **{', '.join(missing)}**.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        message = await channel.send(
            embed=build_movie_guide_home_embed(),
            view=MovieGuidePanelView(self),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        await interaction.followup.send(
            f"Movie-ticket command guide posted successfully: {message.jump_url}",
            ephemeral=True,
        )

    async def send_latest_from_panel(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        movie_cog = self._movie_cog()
        if movie_cog is None:
            await interaction.followup.send(
                "The movie-ticket monitor is still starting. Try again in a moment or run `/movies latest`.",
                ephemeral=True,
            )
            return

        refresh_error = ""
        try:
            await movie_cog._scan_official_source(  # noqa: SLF001 - intentional cross-cog UI bridge.
                target_guild_id=int(interaction.guild_id) if interaction.guild_id else None,
            )
        except Exception as exc:  # noqa: BLE001 - cached verified drops remain usable.
            refresh_error = clean_text(str(exc))[:500]
            log.exception("Movie guide live-drop refresh failed guild=%s", interaction.guild_id)

        drops = await movie_cog.store.list_active_drops(limit=MAX_LATEST_EMBEDS)
        if not drops:
            message = "No public reusable free-ticket codes are currently cached from Atom's official promotions page."
            if refresh_error:
                message += f"\nLive refresh failed safely: `{refresh_error}`"
            await interaction.followup.send(message, ephemeral=True)
            return

        embeds = await movie_cog._build_drop_embeds(drops)  # noqa: SLF001 - shared verified embed builder.
        content = (
            f"Found **{len(drops)}** active official free-ticket drop(s). "
            "Claim quickly because promotional inventory can run out early."
        )
        if refresh_error:
            content += f"\nShowing the last verified cache because the live refresh failed: `{refresh_error}`"
        await interaction.followup.send(content=content, embeds=embeds, ephemeral=True)

    async def send_status_from_panel(self, interaction: discord.Interaction) -> None:
        if not interaction.guild_id:
            await interaction.response.send_message("Use this panel inside a server.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        movie_cog = self._movie_cog()
        if movie_cog is None:
            await interaction.followup.send(
                "The movie-ticket monitor is still starting. Try again in a moment.",
                ephemeral=True,
            )
            return

        config = await movie_cog.store.get_config(int(interaction.guild_id))
        state = await movie_cog.store.get_source_state()
        active = await movie_cog.store.list_active_drops(limit=100)
        sent_count = await movie_cog.store.count_sent_for_guild(int(interaction.guild_id))
        await interaction.followup.send(
            embed=build_movie_status_embed(
                enabled=config.enabled,
                alert_channel_id=config.alert_channel_id,
                active_count=len(active),
                sent_count=sent_count,
                last_success_at=state.last_success_at,
                last_error=state.last_error,
            ),
            ephemeral=True,
        )

    def _movie_cog(self) -> Any | None:
        return self.bot.get_cog("MovieTicketsCog")


def build_movie_guide_home_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🎬 SniperPlug Movie Ticket Guide",
        description=(
            "Use this panel to learn every movie-ticket command and check official free-ticket drops.\n\n"
            "**Fastest route:** press **Check Latest Drops** below, or run `/movies latest`."
        ),
        color=GUIDE_COLOR,
    )
    embed.add_field(
        name="👤 Everyone can use",
        value=(
            "`/movies latest` — refresh and show active free-ticket codes.\n"
            "`/movies status` — show alert status and source health for this server.\n"
            "`/movies sources` — explain which official sources are connected.\n"
            "`/movies-help` — reopen this guide privately."
        ),
        inline=False,
    )
    embed.add_field(
        name="🛠️ Server managers",
        value=(
            "`/movies setup alert_channel:#channel` — enable automatic alerts.\n"
            "`/movies scan` — check the official source immediately.\n"
            "`/movies test-alert` — verify that the configured channel works.\n"
            "`/movies disable` — stop automatic movie alerts.\n"
            "`/movies-panel target_channel:#channel` — post this permanent guide."
        ),
        inline=False,
    )
    embed.add_field(
        name="👇 Pick a guide section",
        value="Use the dropdown for step-by-step instructions, examples, and safety details.",
        inline=False,
    )
    embed.set_footer(text="SniperPlug checks official public sources; codes can still expire or run out early.")
    return embed


def build_movie_guide_section_embed(section: str) -> discord.Embed:
    builders = {
        "start": _build_start_section,
        "users": _build_user_section,
        "admins": _build_admin_section,
        "safety": _build_safety_section,
    }
    return builders.get(section, _build_start_section)()


def _build_start_section() -> discord.Embed:
    embed = discord.Embed(
        title="🎬 Start Here",
        description="You do not need an Atom API or an Atom login to use SniperPlug's public movie alerts.",
        color=GUIDE_COLOR,
    )
    embed.add_field(
        name="1. Check what is live now",
        value="Press **Check Latest Drops** on the panel or run `/movies latest`.",
        inline=False,
    )
    embed.add_field(
        name="2. Copy the code",
        value="Tap and hold the plain promo code in the result. SniperPlug removes quote and Markdown wrappers.",
        inline=False,
    )
    embed.add_field(
        name="3. Claim immediately",
        value="Open the official Atom offer, add the eligible ticket(s), and enter the code during checkout.",
        inline=False,
    )
    embed.add_field(
        name="For automatic alerts",
        value="A server manager runs `/movies setup alert_channel:#movie-tickets` once.",
        inline=False,
    )
    return embed


def _build_user_section() -> discord.Embed:
    embed = discord.Embed(title="🎟️ Commands Anyone Can Use", color=GUIDE_COLOR)
    embed.add_field(
        name="`/movies latest`",
        value="Checks Atom's official promotions page, then privately shows active verified codes with movie posters and restrictions.",
        inline=False,
    )
    embed.add_field(
        name="`/movies status`",
        value="Shows whether this server enabled alerts, the selected channel, source health, active-code count, and delivery total.",
        inline=False,
    )
    embed.add_field(
        name="`/movies sources`",
        value="Explains which official sources are automated and which sources still require a future safe connection.",
        inline=False,
    )
    embed.add_field(
        name="`/movies-help`",
        value="Opens this same interactive guide privately without posting anything to the channel.",
        inline=False,
    )
    return embed


def _build_admin_section() -> discord.Embed:
    embed = discord.Embed(title="⚙️ Server Setup Commands", color=GUIDE_COLOR)
    embed.add_field(
        name="`/movies setup alert_channel:#movie-tickets`",
        value="Enables automatic alerts and saves the selected destination. Requires **Manage Server**.",
        inline=False,
    )
    embed.add_field(
        name="`/movies scan`",
        value="Forces an immediate official-source refresh and delivers any newly discovered codes. Requires **Manage Server**.",
        inline=False,
    )
    embed.add_field(
        name="`/movies test-alert`",
        value="Posts a clearly labeled fake alert so you can verify channel permissions and appearance safely.",
        inline=False,
    )
    embed.add_field(
        name="`/movies disable`",
        value="Stops automatic delivery without deleting the server's saved configuration.",
        inline=False,
    )
    embed.add_field(
        name="`/movies-panel target_channel:#channel`",
        value="Posts this restart-safe public guide. Omit `target_channel` to use the current text channel.",
        inline=False,
    )
    return embed


def _build_safety_section() -> discord.Embed:
    embed = discord.Embed(title="🛡️ Sources, Accuracy & Limits", color=GUIDE_COLOR)
    embed.add_field(
        name="Automated source",
        value=(
            f"[Official Atom Promotions Hub]({ATOM_PROMOTIONS_URL}) is checked every minute. "
            "SniperPlug extracts public reusable free-ticket codes, terms, dates, and official movie artwork."
        ),
        inline=False,
    )
    embed.add_field(
        name="Filtered out",
        value="Private partner codes, account-specific codes, sweepstakes, normal discounts, concessions, and BOGO offers are not labeled as free public drops.",
        inline=False,
    )
    embed.add_field(
        name="What cannot be guaranteed",
        value="A verified code may expire, reach its redemption limit, exclude a theater/date, or run out before the listed end time.",
        inline=False,
    )
    embed.add_field(
        name="Best practice",
        value="Claim immediately and read the restrictions shown in the alert before selecting seats.",
        inline=False,
    )
    return embed


def build_movie_status_embed(
    *,
    enabled: bool,
    alert_channel_id: int | None,
    active_count: int,
    sent_count: int,
    last_success_at: str,
    last_error: str,
) -> discord.Embed:
    embed = discord.Embed(title="📡 Movie Alert Setup Status", color=GUIDE_COLOR)
    embed.add_field(name="Automatic alerts", value="Enabled" if enabled else "Disabled", inline=True)
    embed.add_field(
        name="Destination",
        value=f"<#{alert_channel_id}>" if alert_channel_id else "Not configured",
        inline=True,
    )
    embed.add_field(name="Active public codes", value=str(active_count), inline=True)
    embed.add_field(name="Delivered to this server", value=str(sent_count), inline=True)
    embed.add_field(name="Last successful check", value=_discord_time(last_success_at), inline=True)
    health = "Healthy" if last_success_at and not last_error else "Waiting for first successful check"
    if last_error:
        health = f"Last error: `{clean_text(last_error)[:700]}`"
    embed.add_field(name="Official source health", value=health, inline=False)
    if not enabled:
        embed.add_field(
            name="Enable alerts",
            value="Run `/movies setup alert_channel:#movie-tickets` with **Manage Server** permission.",
            inline=False,
        )
    return embed


def missing_channel_permissions(
    guild: discord.Guild,
    channel: discord.TextChannel,
    bot: commands.Bot,
) -> list[str]:
    member = guild.me
    if member is None and bot.user is not None:
        member = guild.get_member(bot.user.id)
    if member is None:
        return ["View Channel", "Send Messages", "Embed Links"]
    permissions = channel.permissions_for(member)
    required = {
        "view_channel": "View Channel",
        "send_messages": "Send Messages",
        "embed_links": "Embed Links",
    }
    return [label for attribute, label in required.items() if not getattr(permissions, attribute, False)]


def _discord_time(value: str) -> str:
    if not value:
        return "Never"
    try:
        parsed = discord.utils.parse_time(value)
    except (TypeError, ValueError):
        parsed = None
    if parsed is None:
        return clean_text(value)[:100]
    return f"<t:{int(parsed.timestamp())}:R>"
