from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from sniperplug.cogs.registered_multi_source_movies import MovieTicketsCog
from sniperplug.services.fandango_movie_offers import FANDANGO_OFFERS_URL
from sniperplug.services.gofobo_screenings import GOFOBO_HOME_URL
from sniperplug.services.movie_ticket_drops import (
    ATOM_PROMOTIONS_URL,
    MovieTicketConfig,
    clean_text,
)
from sniperplug.services.seeitearly_screenings import (
    ADVANCE_SCREENINGS_URL,
    ONE_IOTA_URL,
    SEEITEARLY_UPCOMING_URL,
)


log = logging.getLogger("sniperplug.movie_guide")

GUIDE_COLOR = discord.Color.gold()
GUIDE_SELECT_ID = "movies:guide:section"
GUIDE_LATEST_ID = "movies:guide:latest"
GUIDE_STATUS_ID = "movies:guide:status"
GUIDE_ENABLE_HERE_ID = "movies:guide:enable_here"
GUIDE_ENABLE_HERE_PANEL_ID = "movies:guide:enable_here_panel"
MAX_LATEST_EMBEDS = 8


class MovieGuideSectionSelect(discord.ui.Select):
    def __init__(self, cog: "MovieCommandGuideCog") -> None:
        self.cog = cog
        super().__init__(
            placeholder="What do you want to do?",
            min_values=1,
            max_values=1,
            custom_id=GUIDE_SELECT_ID,
            row=0,
            options=[
                discord.SelectOption(
                    label="Start Here",
                    value="start",
                    emoji="🎬",
                    description="Quick tour of checking and claiming free tickets.",
                ),
                discord.SelectOption(
                    label="Find Tickets",
                    value="users",
                    emoji="🎟️",
                    description="Refresh current offers and screening leads.",
                ),
                discord.SelectOption(
                    label="Alert Settings",
                    value="admins",
                    emoji="🔔",
                    description="Enable, move, test, or disable automatic alerts.",
                ),
                discord.SelectOption(
                    label="Sources & Safety",
                    value="safety",
                    emoji="🛡️",
                    description="Direct sources, supplemental discovery, and limits.",
                ),
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            await self.cog.refresh_public_panel_from_interaction(interaction)
            section = self.values[0] if self.values else _selected_value(interaction)
            await interaction.followup.send(
                embed=build_movie_guide_section_embed(section or "start"),
                ephemeral=True,
            )
        except Exception as error:  # noqa: BLE001 - component must fail visibly.
            log.exception(
                "Movie guide section failed user=%s guild=%s",
                interaction.user.id if interaction.user else None,
                interaction.guild_id,
            )
            await _send_component_error(interaction, error)


class MovieGuidePanelView(discord.ui.View):
    """Restart-safe permanent panel with fixed rows and direct actions."""

    def __init__(self, cog: "MovieCommandGuideCog") -> None:
        super().__init__(timeout=None)
        self.cog = cog
        self.add_item(MovieGuideSectionSelect(cog))

        # Direct sources get their own row. Explicit rows prevent Discord.py's
        # automatic component packing from pushing core actions out of view.
        self.add_item(
            discord.ui.Button(
                label="Atom",
                style=discord.ButtonStyle.link,
                url=ATOM_PROMOTIONS_URL,
                emoji="⚛️",
                row=2,
            )
        )
        self.add_item(
            discord.ui.Button(
                label="Fandango",
                style=discord.ButtonStyle.link,
                url=FANDANGO_OFFERS_URL,
                emoji="🎟️",
                row=2,
            )
        )
        self.add_item(
            discord.ui.Button(
                label="Gofobo",
                style=discord.ButtonStyle.link,
                url=GOFOBO_HOME_URL,
                emoji="🎬",
                row=2,
            )
        )

        # Extra public discovery/directories. SeeItEarly is connected to the
        # runtime as bounded supplemental discovery; Advance Screenings and
        # 1iota remain manual/direct links until we have a stable fail-closed
        # machine-readable path for them.
        self.add_item(
            discord.ui.Button(
                label="SeeItEarly",
                style=discord.ButtonStyle.link,
                url=SEEITEARLY_UPCOMING_URL,
                emoji="🔎",
                row=3,
            )
        )
        self.add_item(
            discord.ui.Button(
                label="AdvanceScreenings",
                style=discord.ButtonStyle.link,
                url=ADVANCE_SCREENINGS_URL,
                emoji="🎞️",
                row=3,
            )
        )
        self.add_item(
            discord.ui.Button(
                label="1iota",
                style=discord.ButtonStyle.link,
                url=ONE_IOTA_URL,
                emoji="⭐",
                row=3,
            )
        )

    @discord.ui.button(
        label="Check Now",
        style=discord.ButtonStyle.success,
        emoji="🎟️",
        custom_id=GUIDE_LATEST_ID,
        row=1,
    )
    async def latest_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.cog.send_latest_from_panel(interaction)

    @discord.ui.button(
        label="Status",
        style=discord.ButtonStyle.secondary,
        emoji="📡",
        custom_id=GUIDE_STATUS_ID,
        row=1,
    )
    async def status_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.cog.send_status_from_panel(interaction)

    @discord.ui.button(
        label="Alerts Here",
        style=discord.ButtonStyle.primary,
        emoji="🔔",
        custom_id=GUIDE_ENABLE_HERE_PANEL_ID,
        row=1,
    )
    async def alerts_here_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.cog.refresh_public_panel_from_interaction(interaction)
        await self.cog.enable_alerts_here(interaction)

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item: discord.ui.Item[Any],
    ) -> None:
        log.error(
            "Movie guide component failed item=%s user=%s guild=%s",
            getattr(item, "custom_id", type(item).__name__),
            interaction.user.id if interaction.user else None,
            interaction.guild_id,
            exc_info=(type(error), error, error.__traceback__),
        )
        await _send_component_error(interaction, error)


class MovieStatusActionView(discord.ui.View):
    """Private backup for the permanent panel's Alerts Here action."""

    def __init__(self, cog: "MovieCommandGuideCog") -> None:
        super().__init__(timeout=900)
        self.cog = cog

    @discord.ui.button(
        label="Use This Channel for Alerts",
        style=discord.ButtonStyle.success,
        emoji="🔔",
        custom_id=GUIDE_ENABLE_HERE_ID,
        row=0,
    )
    async def enable_here(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await self.cog.enable_alerts_here(interaction)


class MovieCommandGuideCog(commands.Cog):
    """Interactive, persistent UI for the `/movies` command group."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.bot.add_view(MovieGuidePanelView(self))
        log.info("Persistent movie command guide panel registered")

    @app_commands.command(
        name="movies-help",
        description="Open the private free-movie-ticket guide.",
    )
    async def movies_help(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            embed=build_movie_guide_home_embed(),
            view=MovieGuidePanelView(self),
            ephemeral=True,
        )

    @app_commands.command(
        name="movies-panel",
        description="Post the permanent free-movie-ticket panel for your server.",
    )
    @app_commands.describe(
        target_channel="Channel where the permanent movie-ticket panel should be posted. Defaults to this channel.",
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
                f"I cannot post the panel in {channel.mention}. Missing: **{', '.join(missing)}**.",
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
            f"Movie-ticket panel posted successfully: {message.jump_url}",
            ephemeral=True,
        )

    async def refresh_public_panel_from_interaction(self, interaction: discord.Interaction) -> bool:
        """Upgrade an old permanent panel in place on its next interaction.

        Older panels were posted without saving their channel/message IDs. A
        component interaction still carries the exact message, which gives us a
        safe migration path without scanning channel history or asking admins to
        delete/repost months-old panels.
        """

        message = getattr(interaction, "message", None)
        if message is None or not interaction.guild_id:
            return False
        author = getattr(message, "author", None)
        if self.bot.user is not None and author is not None and getattr(author, "id", None) != self.bot.user.id:
            return False
        try:
            await message.edit(
                embed=build_movie_guide_home_embed(),
                view=MovieGuidePanelView(self),
            )
            return True
        except (discord.Forbidden, discord.NotFound, discord.HTTPException) as error:
            log.warning(
                "Could not self-refresh old movie panel guild=%s message=%s error=%s",
                interaction.guild_id,
                getattr(message, "id", None),
                clean_text(str(error))[:300],
            )
            return False

    async def enable_alerts_here(self, interaction: discord.Interaction) -> None:
        """Shared manager-gated current-channel setup used by both panel views."""

        if not interaction.guild or not interaction.guild_id:
            await _respond_ephemeral(interaction, "Use this button inside a server.")
            return
        if not isinstance(interaction.user, discord.Member) or not interaction.user.guild_permissions.manage_guild:
            await _respond_ephemeral(
                interaction,
                "You need **Manage Server** to change the movie-alert destination.",
            )
            return
        if not isinstance(interaction.channel, discord.TextChannel):
            await _respond_ephemeral(
                interaction,
                "Open the permanent movie panel in the text channel you want to use, then press **Alerts Here**.",
            )
            return

        movie_cog = self._movie_cog()
        if movie_cog is None:
            await _respond_ephemeral(interaction, "The movie-ticket monitor is unavailable on this build.")
            return

        missing = missing_channel_permissions(interaction.guild, interaction.channel, self.bot)
        if missing:
            await _respond_ephemeral(
                interaction,
                f"I cannot post movie alerts in {interaction.channel.mention}. Missing: **{', '.join(missing)}**.",
            )
            return

        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)
        guild_id = int(interaction.guild_id)
        channel = interaction.channel
        await movie_cog.store.save_config(
            MovieTicketConfig(
                guild_id=guild_id,
                alert_channel_id=int(channel.id),
                enabled=True,
            )
        )

        scan_line = "Automatic monitoring is enabled."
        try:
            outcome = await movie_cog._scan_official_source(target_guild_id=guild_id)  # noqa: SLF001
            scan_line = (
                f"Recently confirmed offers/screenings: **{outcome.active_count}** • "
                f"new direct-source alerts posted: **{outcome.delivered_count}**."
            )
            warning = movie_cog._source_warning_summary()  # noqa: SLF001
            if warning:
                scan_line += f"\n⚠️ {warning}"
        except Exception as error:  # noqa: BLE001 - config is still valid.
            log.warning(
                "Movie one-click setup saved but initial refresh failed guild=%s channel=%s error=%s",
                guild_id,
                channel.id,
                clean_text(str(error))[:300],
            )
            scan_line = (
                "The destination is saved, but no source could be confirmed during this immediate refresh. "
                "SniperPlug will retry automatically."
            )

        await interaction.followup.send(
            f"✅ Movie alerts are enabled in {channel.mention}.\n{scan_line}",
            ephemeral=True,
        )

    async def send_latest_from_panel(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await self.refresh_public_panel_from_interaction(interaction)
            movie_cog = self._movie_cog()
            if movie_cog is None:
                await interaction.followup.send(
                    "The movie-ticket monitor is unavailable. Try `/movies latest` after restarting the newest build.",
                    ephemeral=True,
                )
                return

            refresh_error = ""
            try:
                await movie_cog._scan_official_source(  # noqa: SLF001 - intentional UI bridge.
                    target_guild_id=int(interaction.guild_id) if interaction.guild_id else None,
                )
            except Exception as error:  # noqa: BLE001 - freshness-qualified cache remains usable.
                refresh_error = clean_text(str(error))[:500]
                log.exception("Movie guide live-drop refresh failed guild=%s", interaction.guild_id)

            drops = await movie_cog.store.list_active_drops(limit=MAX_LATEST_EMBEDS)
            if not drops:
                message = "No recently confirmed free-ticket offers or screening opportunities are available right now."
                if refresh_error:
                    message += f"\nLive refresh failed safely: `{refresh_error}`"
                await interaction.followup.send(message, ephemeral=True)
                return

            embeds = await movie_cog._build_drop_embeds(drops)  # noqa: SLF001
            content = (
                f"Found **{len(drops)}** recently confirmed ticket offer(s) or screening lead(s). "
                "Direct-source and supplemental discovery results are labeled separately."
            )
            if refresh_error:
                content += (
                    "\nSome sources could not be refreshed live; only freshness-qualified results are shown: "
                    f"`{refresh_error}`"
                )
            await interaction.followup.send(content=content, embeds=embeds, ephemeral=True)
        except Exception as error:  # noqa: BLE001 - always return a visible component result.
            log.exception("Movie guide latest button failed guild=%s", interaction.guild_id)
            await _send_component_error(interaction, error)

    async def send_status_from_panel(self, interaction: discord.Interaction) -> None:
        if not interaction.guild_id:
            await interaction.response.send_message("Use this panel inside a server.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        try:
            await self.refresh_public_panel_from_interaction(interaction)
            movie_cog = self._movie_cog()
            if movie_cog is None:
                await interaction.followup.send(
                    "The movie-ticket monitor is unavailable. Try `/movies status` after restarting the newest build.",
                    ephemeral=True,
                )
                return

            guild_id = int(interaction.guild_id)
            config = await movie_cog.store.get_config(guild_id)
            active = await movie_cog.store.list_active_drops(limit=100)
            sent_count = await movie_cog.store.count_sent_for_guild(guild_id)
            public_codes = sum(1 for drop in active if drop.classification == "public_reusable")
            screening_leads = sum(1 for drop in active if drop.classification == "local_screening")
            states = await movie_cog._connected_source_states()  # noqa: SLF001 - shared status bridge.
            successful_times = [state.last_success_at for state in states.values() if state.last_success_at]
            latest_success = max(successful_times) if successful_times else ""
            source_lines = movie_cog._source_health_lines(states)  # noqa: SLF001
            await interaction.followup.send(
                embed=build_movie_status_embed(
                    enabled=config.enabled,
                    alert_channel_id=config.alert_channel_id,
                    active_count=public_codes,
                    local_screening_count=screening_leads,
                    sent_count=sent_count,
                    last_success_at=latest_success,
                    source_health_lines=source_lines,
                ),
                view=MovieStatusActionView(self),
                ephemeral=True,
            )
        except Exception as error:  # noqa: BLE001
            log.exception("Movie guide status button failed guild=%s", interaction.guild_id)
            await _send_component_error(interaction, error)

    def _movie_cog(self) -> MovieTicketsCog | None:
        direct = self.bot.get_cog(MovieTicketsCog.__cog_name__)
        if isinstance(direct, MovieTicketsCog):
            return direct
        return next(
            (cog for cog in self.bot.cogs.values() if isinstance(cog, MovieTicketsCog)),
            None,
        )


def build_movie_guide_home_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🎬 Free Movie Tickets & Screenings",
        description=(
            "Find current free-ticket offers and early-screening leads without memorizing commands.\n\n"
            "**Check now. Turn on alerts. Open the source. Claim fast.**"
        ),
        color=GUIDE_COLOR,
    )
    embed.add_field(
        name="🎟️ Check now",
        value="Press **Check Now** to refresh every connected source and privately show current results.",
        inline=False,
    )
    embed.add_field(
        name="🔔 Automatic alerts",
        value=(
            "Managers: press **Alerts Here** to make this channel the alert destination immediately. "
            "Press **Status** anytime to see source health and delivery state."
        ),
        inline=False,
    )
    embed.add_field(
        name="🌐 Coverage",
        value=(
            "**Direct monitoring:** Atom • Fandango • Gofobo\n"
            "**Supplemental discovery:** SeeItEarly\n"
            "**Extra screening directories:** AdvanceScreenings • 1iota"
        ),
        inline=False,
    )
    embed.add_field(
        name="👇 More options",
        value="Use the dropdown for finding tickets, alert settings, source details, and safety limits.",
        inline=False,
    )
    embed.set_footer(text="Free passes can fill or expire early. Always verify the linked provider before traveling.")
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
        description="The permanent panel is the main interface. Commands are optional fallbacks.",
        color=GUIDE_COLOR,
    )
    embed.add_field(name="1. Find something", value="Press **Check Now**.", inline=False)
    embed.add_field(
        name="2. Read what kind of result it is",
        value=(
            "Atom/Fandango/Gofobo results come from direct public source pages. "
            "SeeItEarly results are clearly marked supplemental discovery and point you toward current screening locations/providers."
        ),
        inline=False,
    )
    embed.add_field(
        name="3. Claim from the linked provider",
        value="Open the result, verify your city/date/rules, and claim before free inventory fills.",
        inline=False,
    )
    embed.add_field(
        name="Want automatic alerts?",
        value="A server manager presses **Alerts Here** once in the channel that should receive them.",
        inline=False,
    )
    return embed


def _build_user_section() -> discord.Embed:
    embed = discord.Embed(title="🎟️ Find Tickets", color=GUIDE_COLOR)
    embed.add_field(
        name="Check Now",
        value="Refreshes Atom, Fandango, Gofobo, and bounded SeeItEarly discovery, then shows freshness-qualified results privately.",
        inline=False,
    )
    embed.add_field(
        name="Source buttons",
        value=(
            "Use the direct links on the permanent panel when you want to browse a provider yourself. "
            "AdvanceScreenings and 1iota are included as extra directories even though SniperPlug does not automate them yet."
        ),
        inline=False,
    )
    embed.add_field(
        name="Advanced fallback",
        value="`/movies latest` refreshes results and `/movies sources` explains source coverage.",
        inline=False,
    )
    return embed


def _build_admin_section() -> discord.Embed:
    embed = discord.Embed(title="🔔 Alert Settings", color=GUIDE_COLOR)
    embed.add_field(
        name="Enable or move alerts here",
        value="Press **Alerts Here** on the permanent panel. Requires **Manage Server**.",
        inline=False,
    )
    embed.add_field(
        name="Check configuration",
        value="Press **Status** to see destination, connected-source health, current result counts, and delivery total.",
        inline=False,
    )
    embed.add_field(
        name="Advanced fallbacks",
        value=(
            "`/movies setup alert_channel:#channel` chooses a different channel.\n"
            "`/movies scan` refreshes now.\n"
            "`/movies test-alert` verifies permissions/appearance.\n"
            "`/movies disable` stops automatic delivery."
        ),
        inline=False,
    )
    return embed


def _build_safety_section() -> discord.Embed:
    embed = discord.Embed(title="🛡️ Sources & Safety", color=GUIDE_COLOR)
    embed.add_field(
        name="Direct monitored sources",
        value=(
            f"[Atom Promotions]({ATOM_PROMOTIONS_URL}) — public reusable promotions.\n"
            f"[Fandango Offers]({FANDANGO_OFFERS_URL}) — ticket offers, with purchase requirements labeled.\n"
            f"[Gofobo]({GOFOBO_HOME_URL}) — local screening announcements and public pass/code links."
        ),
        inline=False,
    )
    embed.add_field(
        name="Supplemental discovery",
        value=(
            f"[SeeItEarly]({SEEITEARLY_UPCOMING_URL}) — bounded public discovery across many screening providers. "
            "SniperPlug labels it as an aggregator and never treats it as the original pass issuer."
        ),
        inline=False,
    )
    embed.add_field(
        name="More places to check",
        value=(
            f"[AdvanceScreenings]({ADVANCE_SCREENINGS_URL}) and [1iota]({ONE_IOTA_URL}) can contain screenings not present on Gofobo. "
            "They are linked from the permanent panel while their automated integration remains fail-closed."
        ),
        inline=False,
    )
    embed.add_field(
        name="What SniperPlug will not fake",
        value=(
            "No guessed theater/date/time, no account scraping, no CAPTCHA bypass, no paid event mislabeled free, "
            "and no aggregator result presented as if it came directly from a studio/pass issuer."
        ),
        inline=False,
    )
    embed.add_field(
        name="Best practice",
        value="Verify the city, date, venue, restrictions, and remaining pass inventory on the linked provider before traveling.",
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
    last_error: str = "",
    local_screening_count: int = 0,
    source_health_lines: list[str] | None = None,
) -> discord.Embed:
    embed = discord.Embed(title="📡 Movie Alert Status", color=GUIDE_COLOR)
    embed.add_field(name="Automatic alerts", value="Enabled" if enabled else "Disabled", inline=True)
    embed.add_field(
        name="Destination",
        value=f"<#{alert_channel_id}>" if alert_channel_id else "Not configured",
        inline=True,
    )
    embed.add_field(name="Reusable ticket codes", value=str(active_count), inline=True)
    embed.add_field(name="Screening/discovery leads", value=str(local_screening_count), inline=True)
    embed.add_field(name="Delivered to this server", value=str(sent_count), inline=True)
    embed.add_field(name="Latest successful check", value=_discord_time(last_success_at), inline=True)
    if source_health_lines:
        health = "\n".join(source_health_lines)[:1024]
    else:
        health = "Healthy" if last_success_at and not last_error else "Waiting for first successful check"
        if last_error:
            health = f"Last error: `{clean_text(last_error)[:700]}`"
    embed.add_field(name="Connected source health", value=health, inline=False)
    embed.add_field(
        name="Alert destination",
        value=(
            "Managers can press **Alerts Here** on the permanent movie panel to enable alerts in that channel or move the destination there. "
            "This private status card also includes the same setup action when Discord renders its controls."
        ),
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


def _selected_value(interaction: discord.Interaction) -> str:
    data = interaction.data if isinstance(interaction.data, dict) else {}
    values = data.get("values")
    if isinstance(values, list) and values:
        return clean_text(values[0])
    return "start"


async def _respond_ephemeral(interaction: discord.Interaction, message: str) -> None:
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


async def _send_component_error(interaction: discord.Interaction, error: Exception) -> None:
    message = (
        "That movie panel action hit an error, but it was acknowledged safely. "
        "Try **Check Now** again while the error is logged."
    )
    try:
        await _respond_ephemeral(interaction, message)
    except Exception:
        log.exception(
            "Could not send movie guide component failure response original=%s",
            clean_text(str(error))[:300],
        )


def _discord_time(value: str) -> str:
    if not value:
        return "Never"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return clean_text(value)[:100]
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return f"<t:{int(parsed.timestamp())}:R>"
