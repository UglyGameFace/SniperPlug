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


log = logging.getLogger("sniperplug.movie_guide")

GUIDE_COLOR = discord.Color.gold()
GUIDE_SELECT_ID = "movies:guide:section"
GUIDE_LATEST_ID = "movies:guide:latest"
GUIDE_STATUS_ID = "movies:guide:status"
GUIDE_ENABLE_HERE_ID = "movies:guide:enable_here"
MAX_LATEST_EMBEDS = 8


class MovieGuideSectionSelect(discord.ui.Select):
    def __init__(self, cog: "MovieCommandGuideCog") -> None:
        self.cog = cog
        super().__init__(
            placeholder="Choose what you need help with…",
            min_values=1,
            max_values=1,
            custom_id=GUIDE_SELECT_ID,
            options=[
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
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
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
    def __init__(self, cog: "MovieCommandGuideCog") -> None:
        super().__init__(timeout=None)
        self.cog = cog
        self.add_item(MovieGuideSectionSelect(cog))
        self.add_item(
            discord.ui.Button(
                label="Atom",
                style=discord.ButtonStyle.link,
                url=ATOM_PROMOTIONS_URL,
                emoji="⚛️",
            )
        )
        self.add_item(
            discord.ui.Button(
                label="Fandango",
                style=discord.ButtonStyle.link,
                url=FANDANGO_OFFERS_URL,
                emoji="🎟️",
            )
        )
        self.add_item(
            discord.ui.Button(
                label="Gofobo",
                style=discord.ButtonStyle.link,
                url=GOFOBO_HOME_URL,
                emoji="🎬",
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
    """Private one-click setup action attached to the status result."""

    def __init__(self, cog: "MovieCommandGuideCog") -> None:
        super().__init__(timeout=300)
        self.cog = cog

    @discord.ui.button(
        label="Use This Channel for Alerts",
        style=discord.ButtonStyle.success,
        emoji="🔔",
        custom_id=GUIDE_ENABLE_HERE_ID,
    )
    async def enable_here(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        if not interaction.guild or not interaction.guild_id:
            await interaction.response.send_message("Use this button inside a server.", ephemeral=True)
            return
        if not isinstance(interaction.user, discord.Member) or not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message(
                "You need **Manage Server** to change the movie-alert destination.",
                ephemeral=True,
            )
            return
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message(
                "Open the movie panel in the text channel you want to use, then press this button again.",
                ephemeral=True,
            )
            return

        movie_cog = self.cog._movie_cog()
        if movie_cog is None:
            await interaction.response.send_message(
                "The movie-ticket monitor is unavailable on this build.",
                ephemeral=True,
            )
            return

        missing = missing_channel_permissions(interaction.guild, interaction.channel, self.cog.bot)
        if missing:
            await interaction.response.send_message(
                f"I cannot post movie alerts in {interaction.channel.mention}. Missing: **{', '.join(missing)}**.",
                ephemeral=True,
            )
            return

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
                f"Automatic monitoring is enabled. Recently confirmed offers/screenings: **{outcome.active_count}** • "
                f"new alerts posted: **{outcome.delivered_count}**."
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
                "Automatic monitoring is enabled, but no source could be confirmed during this immediate refresh. "
                "SniperPlug will retry automatically."
            )

        await interaction.followup.send(
            f"✅ Movie alerts will post in {channel.mention}.\n{scan_line}",
            ephemeral=True,
        )


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
        try:
            movie_cog = self._movie_cog()
            if movie_cog is None:
                await interaction.followup.send(
                    "The movie-ticket monitor is unavailable. Run `/movies latest`; "
                    "if that command works, restart the bot on the newest build.",
                    ephemeral=True,
                )
                return

            refresh_error = ""
            try:
                await movie_cog._scan_official_source(  # noqa: SLF001 - intentional UI bridge.
                    target_guild_id=int(interaction.guild_id) if interaction.guild_id else None,
                )
            except Exception as error:  # noqa: BLE001 - verified cache remains usable.
                refresh_error = clean_text(str(error))[:500]
                log.exception("Movie guide live-drop refresh failed guild=%s", interaction.guild_id)

            drops = await movie_cog.store.list_active_drops(limit=MAX_LATEST_EMBEDS)
            if not drops:
                message = "No recently confirmed official free-ticket offers or screening opportunities are available right now."
                if refresh_error:
                    message += f"\nLive refresh failed safely: `{refresh_error}`"
                await interaction.followup.send(message, ephemeral=True)
                return

            embeds = await movie_cog._build_drop_embeds(drops)  # noqa: SLF001
            content = (
                f"Found **{len(drops)}** recently confirmed official ticket offer(s) or screening lead(s). "
                "Claim quickly because code, pass, and local inventory can run out early."
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
            movie_cog = self._movie_cog()
            if movie_cog is None:
                await interaction.followup.send(
                    "The movie-ticket monitor is unavailable. Run `/movies status`; "
                    "if that command works, restart the bot on the newest build.",
                    ephemeral=True,
                )
                return

            guild_id = int(interaction.guild_id)
            config = await movie_cog.store.get_config(guild_id)
            active = await movie_cog.store.list_active_drops(limit=100)
            sent_count = await movie_cog.store.count_sent_for_guild(guild_id)
            public_codes = sum(1 for drop in active if drop.classification == "public_reusable")
            local_screenings = sum(1 for drop in active if drop.classification == "local_screening")
            states = await movie_cog._connected_source_states()  # noqa: SLF001 - shared status bridge.
            successful_times = [state.last_success_at for state in states.values() if state.last_success_at]
            latest_success = max(successful_times) if successful_times else ""
            source_lines = movie_cog._source_health_lines(states)  # noqa: SLF001
            await interaction.followup.send(
                embed=build_movie_status_embed(
                    enabled=config.enabled,
                    alert_channel_id=config.alert_channel_id,
                    active_count=public_codes,
                    local_screening_count=local_screenings,
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
        title="🎬 SniperPlug Movie Ticket Guide",
        description=(
            "Use this panel to check official free-ticket offers and screenings without memorizing commands.\n\n"
            "**Fastest route:** press **Check Latest Drops**. For automatic alerts, press **Setup Status** and then **Use This Channel for Alerts**."
        ),
        color=GUIDE_COLOR,
    )
    embed.add_field(
        name="👤 Everyone can use",
        value=(
            "**Check Latest Drops** — refresh and show current ticket codes and screening leads.\n"
            "**Setup Status** — show source health and this server's alert destination.\n"
            "`/movies sources` — explain Atom, Fandango, Gofobo, and unconnected private sources.\n"
            "`/movies-help` — reopen this guide privately."
        ),
        inline=False,
    )
    embed.add_field(
        name="🛠️ Server managers",
        value=(
            "**Setup Status → Use This Channel for Alerts** — one-tap automatic setup for the current text channel.\n"
            "`/movies setup alert_channel:#channel` — choose a different alert channel manually.\n"
            "`/movies scan` — check every connected official source immediately.\n"
            "`/movies test-alert` — verify that the configured channel works.\n"
            "`/movies disable` — stop automatic movie alerts."
        ),
        inline=False,
    )
    embed.add_field(
        name="👇 Pick a guide section",
        value="Use the dropdown for details, examples, and safety limits.",
        inline=False,
    )
    embed.set_footer(text="Official codes and screening passes can still expire, fill, or run out early.")
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
        description="No Atom, Fandango, or Gofobo API key is needed to use SniperPlug's public movie alerts.",
        color=GUIDE_COLOR,
    )
    embed.add_field(
        name="1. Check what is live now",
        value="Press **Check Latest Drops** on the panel.",
        inline=False,
    )
    embed.add_field(
        name="2. Read the classification",
        value=(
            "A result may be a reusable Atom code, a Fandango offer requiring a qualifying purchase, "
            "or a Gofobo screening that depends on ZIP and pass availability."
        ),
        inline=False,
    )
    embed.add_field(
        name="3. Claim immediately",
        value="Open the matching official-source button, follow its restrictions, and claim before inventory disappears.",
        inline=False,
    )
    embed.add_field(
        name="For automatic alerts",
        value="A server manager presses **Setup Status → Use This Channel for Alerts** once.",
        inline=False,
    )
    return embed


def _build_user_section() -> discord.Embed:
    embed = discord.Embed(title="🎟️ Commands Anyone Can Use", color=GUIDE_COLOR)
    embed.add_field(
        name="Check Latest Drops",
        value="Refreshes Atom, Fandango, and Gofobo, then privately shows recently confirmed results with artwork and restrictions.",
        inline=False,
    )
    embed.add_field(
        name="Setup Status",
        value="Shows this server's alert channel, health for every connected source, code/screening counts, and delivery total.",
        inline=False,
    )
    embed.add_field(
        name="`/movies sources`",
        value="Explains which official sources are automated and which private/account sources are not connected yet.",
        inline=False,
    )
    embed.add_field(
        name="`/movies-help`",
        value="Opens this interactive guide privately without posting in the channel.",
        inline=False,
    )
    return embed


def _build_admin_section() -> discord.Embed:
    embed = discord.Embed(title="⚙️ Server Setup", color=GUIDE_COLOR)
    embed.add_field(
        name="One-tap setup",
        value="Press **Setup Status**, then **Use This Channel for Alerts**. Requires **Manage Server**.",
        inline=False,
    )
    embed.add_field(
        name="`/movies setup alert_channel:#movie-tickets`",
        value="Use this only when you want alerts in a different channel than the one containing the panel.",
        inline=False,
    )
    embed.add_field(
        name="`/movies scan`",
        value="Checks Atom, Fandango, and Gofobo immediately and delivers newly discovered results.",
        inline=False,
    )
    embed.add_field(
        name="`/movies test-alert`",
        value="Posts a clearly labeled fake alert to verify permissions and appearance safely.",
        inline=False,
    )
    embed.add_field(
        name="`/movies disable`",
        value="Stops automatic delivery without deleting the saved channel configuration.",
        inline=False,
    )
    return embed


def _build_safety_section() -> discord.Embed:
    embed = discord.Embed(title="🛡️ Sources, Accuracy & Limits", color=GUIDE_COLOR)
    embed.add_field(
        name="Automated official sources",
        value=(
            f"[Atom Promotions]({ATOM_PROMOTIONS_URL}) for public reusable codes.\n"
            f"[Fandango Offers]({FANDANGO_OFFERS_URL}) for free-ticket promotions, with purchase requirements labeled.\n"
            f"[Gofobo]({GOFOBO_HOME_URL}) for upcoming local screening announcements and public short-code links."
        ),
        inline=False,
    )
    embed.add_field(
        name="Filtered or labeled separately",
        value=(
            "Private/account codes, sweepstakes, ordinary discounts, paid memberships, concessions, "
            "purchase-required offers, and ZIP-local screenings are never presented as the same thing."
        ),
        inline=False,
    )
    embed.add_field(
        name="What cannot be guaranteed",
        value=(
            "A verified code may expire or hit its limit. Gofobo often reveals the actual theater/date/time only after a ZIP lookup, "
            "and a pass can still fill before you claim or before admission."
        ),
        inline=False,
    )
    embed.add_field(
        name="Best practice",
        value="Claim immediately, read every restriction, and use Worked/Didn't work to help other members.",
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
    embed.add_field(name="Local screening leads", value=str(local_screening_count), inline=True)
    embed.add_field(name="Delivered to this server", value=str(sent_count), inline=True)
    embed.add_field(name="Latest successful check", value=_discord_time(last_success_at), inline=True)
    if source_health_lines:
        health = "\n".join(source_health_lines)[:1024]
    else:
        health = "Healthy" if last_success_at and not last_error else "Waiting for first successful check"
        if last_error:
            health = f"Last error: `{clean_text(last_error)[:700]}`"
    embed.add_field(name="Official source health", value=health, inline=False)
    embed.add_field(
        name="Alert destination",
        value=(
            "Managers can press **Use This Channel for Alerts** below to enable alerts here or move the existing destination here. "
            "No slash-command syntax required."
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


async def _send_component_error(interaction: discord.Interaction, error: Exception) -> None:
    message = (
        "That movie panel action hit an error, but it was acknowledged safely. "
        "Try reopening `/movies-help` while the error is logged."
    )
    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
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
