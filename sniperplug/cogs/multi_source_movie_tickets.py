from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

import discord
from discord import app_commands

from sniperplug.cogs.movie_ticket_feedback import (
    MovieTicketFeedbackCog,
    MovieTicketFeedbackView,
)
from sniperplug.cogs.movie_tickets import (
    MAX_LATEST_EMBEDS,
    MOVIE_POLL_SECONDS,
    MovieScanOutcome,
    MovieTicketsCog as AtomMovieTicketsCog,
    _discord_time,
)
from sniperplug.services.fandango_movie_offers import (
    FANDANGO_OFFERS_URL,
    FANDANGO_SOURCE_KEY,
    FANDANGO_SOURCE_LABEL,
    FandangoOffersClient,
    extract_fandango_image_marker,
    fandango_purchase_required,
    parse_fandango_offers_html,
)
from sniperplug.services.gofobo_screenings import (
    GOFOBO_HOME_URL,
    GOFOBO_LOCAL_SCREENINGS_URL,
    GOFOBO_SOURCE_KEY,
    GOFOBO_SOURCE_LABEL,
    GofoboUpcomingClient,
    extract_gofobo_image_marker,
    parse_gofobo_home_html,
)
from sniperplug.services.movie_ticket_artwork import normalize_public_code
from sniperplug.services.movie_ticket_drops import (
    ATOM_PROMOTIONS_URL,
    ATOM_SOURCE_KEY,
    AtomPromotionsClient,
    MovieTicketConfig,
    MovieTicketDrop,
    MovieTicketSourceState,
    clean_text,
    parse_atom_promotions_html,
)


log = logging.getLogger("sniperplug.movie_tickets.multi_source")

DELIVERABLE_CLASSIFICATIONS = frozenset({"public_reusable", "local_screening"})
CONNECTED_SOURCE_KEYS = (
    ATOM_SOURCE_KEY,
    FANDANGO_SOURCE_KEY,
    GOFOBO_SOURCE_KEY,
)


class MovieTicketsCog(AtomMovieTicketsCog):
    """The existing `/movies` command group with multiple official sources."""

    def __init__(self, bot):
        super().__init__(bot)
        self._fandango_lock = asyncio.Lock()
        self._gofobo_lock = asyncio.Lock()

    async def cog_load(self) -> None:
        await super().cog_load()
        log.info(
            "Multi-source movie monitor loaded sources=atom,fandango,gofobo poll_seconds=%s",
            MOVIE_POLL_SECONDS,
        )

    @app_commands.command(name="setup", description="Enable official free movie-ticket and screening alerts in a channel.")
    @app_commands.describe(alert_channel="Channel where verified movie-ticket alerts should post.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def setup(self, interaction: discord.Interaction, alert_channel: discord.TextChannel) -> None:
        if not interaction.guild_id or not interaction.guild:
            await interaction.response.send_message("Use this command inside a server.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)

        missing = self._missing_bot_permissions(interaction.guild, alert_channel)
        if missing:
            await interaction.followup.send(self._missing_permissions_message(alert_channel, missing), ephemeral=True)
            return

        await self.store.save_config(
            MovieTicketConfig(
                guild_id=int(interaction.guild_id),
                alert_channel_id=int(alert_channel.id),
                enabled=True,
            )
        )

        try:
            outcome = await self._scan_official_source(target_guild_id=int(interaction.guild_id))
            scan_line = (
                f"All connected official sources checked. Active offers/screenings: **{outcome.active_count}**. "
                f"New alerts posted here: **{outcome.delivered_count}**."
            )
        except Exception as error:
            log.exception("Multi-source movie setup refresh failed guild=%s", interaction.guild_id)
            scan_line = (
                "The channel is saved and automatic monitoring is enabled, but the first source check failed: "
                f"`{clean_text(str(error))[:300]}`. SniperPlug will retry automatically."
            )

        await interaction.followup.send(
            f"🎬 Movie-ticket alerts are **enabled** in {alert_channel.mention}.\n{scan_line}\n"
            "SniperPlug now watches official Atom promotions, Fandango offers, and Gofobo upcoming screenings. "
            "It separates reusable codes, purchase-required offers, and ZIP-local screening opportunities instead of labeling them all the same.",
            ephemeral=True,
        )

    @app_commands.command(name="status", description="Show movie-ticket destination, every source's health, and delivery totals.")
    async def status(self, interaction: discord.Interaction) -> None:
        if not interaction.guild_id:
            await interaction.response.send_message("Use this command inside a server.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        guild_id = int(interaction.guild_id)
        config = await self.store.get_config(guild_id)
        active = await self.store.list_active_drops(limit=100)
        sent_count = await self.store.count_sent_for_guild(guild_id)
        public_codes = sum(1 for drop in active if drop.classification == "public_reusable")
        local_screenings = sum(1 for drop in active if drop.classification == "local_screening")
        states = await self._connected_source_states()
        successful_times = [state.last_success_at for state in states.values() if state.last_success_at]
        latest_success = max(successful_times) if successful_times else ""

        embed = discord.Embed(title="🎬 Movie Ticket Monitor", color=discord.Color.gold())
        embed.add_field(name="Alerts", value="Enabled" if config.enabled else "Disabled", inline=True)
        embed.add_field(
            name="Channel",
            value=f"<#{config.alert_channel_id}>" if config.alert_channel_id else "Not configured",
            inline=True,
        )
        embed.add_field(name="Reusable ticket codes", value=str(public_codes), inline=True)
        embed.add_field(name="Local screening leads", value=str(local_screenings), inline=True)
        embed.add_field(name="Delivered to this server", value=str(sent_count), inline=True)
        embed.add_field(name="Latest successful check", value=_discord_time(latest_success), inline=True)
        embed.add_field(name="Check interval", value=f"Every {MOVIE_POLL_SECONDS} seconds", inline=True)
        embed.add_field(
            name="Official source health",
            value="\n".join(self._source_health_lines(states))[:1024],
            inline=False,
        )
        embed.set_footer(
            text="Codes, purchase-required offers, and ZIP-local screenings are classified separately; availability can still run out."
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="latest", description="Refresh and show the latest official ticket offers and screenings.")
    async def latest(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        refresh_error = ""
        try:
            await self._scan_official_source(
                target_guild_id=int(interaction.guild_id) if interaction.guild_id else None
            )
        except Exception as error:
            refresh_error = clean_text(str(error))[:500]
            log.exception("Manual multi-source movie refresh failed guild=%s", interaction.guild_id)

        drops = await self.store.list_active_drops(limit=MAX_LATEST_EMBEDS)
        if not drops:
            message = "No verified public movie-ticket offers or screening opportunities are currently cached from connected official sources."
            if refresh_error:
                message += f"\nThe live refresh also failed: `{refresh_error}`"
            await interaction.followup.send(message, ephemeral=True)
            return

        content = (
            f"Found **{len(drops)}** active official movie-ticket offer(s) or screening lead(s). "
            "Claim immediately; codes, passes, and local inventory can disappear early."
        )
        if refresh_error:
            content += f"\nShowing the last verified cache because part of the live refresh failed: `{refresh_error}`"
        await interaction.followup.send(
            content=content,
            embeds=await self._build_drop_embeds(drops),
            ephemeral=True,
        )

    @app_commands.command(name="scan", description="Check every connected official movie-ticket source now.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def scan(self, interaction: discord.Interaction) -> None:
        if not interaction.guild_id:
            await interaction.response.send_message("Use this command inside a server.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            outcome = await self._scan_official_source(target_guild_id=int(interaction.guild_id))
        except Exception as error:
            log.exception("Manual multi-source movie scan failed guild=%s", interaction.guild_id)
            await interaction.followup.send(
                f"Every connected official source failed safely: `{clean_text(str(error))[:500]}`. Last verified caches were preserved.",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            f"Official multi-source scan complete. Any source changed: **{'yes' if outcome.modified else 'no'}** • "
            f"Active offers/screenings: **{outcome.active_count}** • "
            f"New alerts delivered to this server: **{outcome.delivered_count}**.",
            ephemeral=True,
        )

    @app_commands.command(name="sources", description="Show the official movie-ticket sources SniperPlug monitors.")
    async def sources(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(
            title="🎟️ Official Movie Ticket Sources",
            description=(
                "SniperPlug labels each source honestly so a local pass, purchase-required offer, or private code is never presented as a universal free ticket."
            ),
            color=discord.Color.gold(),
        )
        embed.add_field(
            name="✅ Automated now",
            value=(
                f"[Atom Promotions Hub]({ATOM_PROMOTIONS_URL}) — public reusable free-ticket codes, dates, restrictions, and official artwork.\n"
                f"[Fandango Special Offers]({FANDANGO_OFFERS_URL}) — public free-ticket codes; BOGO/B3G1 and kids-ticket offers are clearly marked **purchase required**.\n"
                f"[Gofobo Upcoming Screenings]({GOFOBO_HOME_URL}) — official upcoming screening announcements and public short-code links when exposed. Every alert tells users to verify their ZIP on [Gofobo's local search]({GOFOBO_LOCAL_SCREENINGS_URL})."
            ),
            inline=False,
        )
        embed.add_field(
            name="ℹ️ Official but not connected",
            value=(
                "Private Atom/Gofobo emails, SMS, app push notifications, account-only invitations, studio mailing lists, "
                "and partner-issued unique codes. These require a safe, consented inbox or account connection and are never guessed from public pages."
            ),
            inline=False,
        )
        embed.add_field(
            name="Not treated as original sources",
            value=(
                "Reddit and deal forums may eventually help as backup discovery, but they are repost sources and do not outrank Atom, Fandango, Gofobo, studios, or theaters."
            ),
            inline=False,
        )
        embed.set_footer(text="No Atom/Fandango/Gofobo API key or account login is used.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def _scan_official_source(self, *, target_guild_id: int | None = None) -> MovieScanOutcome:
        outcomes: list[MovieScanOutcome] = []
        errors: list[str] = []
        source_scans = (
            ("Atom", self._scan_atom_source),
            ("Fandango", self._scan_fandango_source),
            ("Gofobo", self._scan_gofobo_source),
        )
        for label, scanner in source_scans:
            try:
                outcomes.append(await scanner(target_guild_id=target_guild_id))
            except Exception as error:  # noqa: BLE001 - healthy sources must continue.
                errors.append(f"{label}: {clean_text(str(error))[:300]}")
                log.exception("%s source failed while multi-source movie scan continued", label)

        if not outcomes:
            raise RuntimeError("All official movie-ticket sources failed: " + " | ".join(errors))

        all_active = await self.store.list_active_drops(limit=100)
        return MovieScanOutcome(
            modified=any(item.modified for item in outcomes),
            active_count=len(all_active),
            delivered_count=sum(item.delivered_count for item in outcomes),
            source_state=outcomes[0].source_state,
        )

    async def _scan_atom_source(self, *, target_guild_id: int | None = None) -> MovieScanOutcome:
        async with self._source_lock:
            state = await self.store.get_source_state(ATOM_SOURCE_KEY)
            try:
                fetched = await AtomPromotionsClient(self._require_session()).fetch(state)
                modified = not fetched.not_modified
                if fetched.not_modified:
                    drops = await self._list_source_drops(ATOM_SOURCE_KEY)
                else:
                    parsed = parse_atom_promotions_html(fetched.html)
                    if not parsed.document_valid:
                        raise RuntimeError(
                            "The official Atom page structure could not be verified, so SniperPlug preserved the last verified Atom cache."
                        )
                    await self.store.replace_active_drops(ATOM_SOURCE_KEY, parsed.drops)
                    drops = list(parsed.drops)

                await self.store.record_source_success(
                    source_key=ATOM_SOURCE_KEY,
                    etag=fetched.etag or state.etag,
                    last_modified=fetched.last_modified or state.last_modified,
                    active_drop_count=len(drops),
                )
                delivered = await self._deliver_drops(drops, target_guild_id=target_guild_id)
                return MovieScanOutcome(
                    modified,
                    len(drops),
                    delivered,
                    await self.store.get_source_state(ATOM_SOURCE_KEY),
                )
            except Exception as error:
                await self.store.record_source_error(ATOM_SOURCE_KEY, str(error))
                raise

    async def _scan_fandango_source(self, *, target_guild_id: int | None = None) -> MovieScanOutcome:
        async with self._fandango_lock:
            state = await self.store.get_source_state(FANDANGO_SOURCE_KEY)
            try:
                fetched = await FandangoOffersClient(self._require_session()).fetch(state)
                modified = not fetched.not_modified
                if fetched.not_modified:
                    drops = await self._list_source_drops(FANDANGO_SOURCE_KEY)
                else:
                    parsed = parse_fandango_offers_html(fetched.html)
                    if not parsed.document_valid:
                        raise RuntimeError(
                            "The official Fandango page structure could not be verified, so SniperPlug preserved the last verified Fandango cache."
                        )
                    await self.store.replace_active_drops(FANDANGO_SOURCE_KEY, parsed.drops)
                    drops = list(parsed.drops)

                await self.store.record_source_success(
                    source_key=FANDANGO_SOURCE_KEY,
                    etag=fetched.etag or state.etag,
                    last_modified=fetched.last_modified or state.last_modified,
                    active_drop_count=len(drops),
                )
                delivered = await self._deliver_drops(drops, target_guild_id=target_guild_id)
                return MovieScanOutcome(
                    modified,
                    len(drops),
                    delivered,
                    await self.store.get_source_state(FANDANGO_SOURCE_KEY),
                )
            except Exception as error:
                await self.store.record_source_error(FANDANGO_SOURCE_KEY, str(error))
                raise

    async def _scan_gofobo_source(self, *, target_guild_id: int | None = None) -> MovieScanOutcome:
        async with self._gofobo_lock:
            state = await self.store.get_source_state(GOFOBO_SOURCE_KEY)
            try:
                fetched = await GofoboUpcomingClient(self._require_session()).fetch(state)
                modified = not fetched.not_modified
                if fetched.not_modified:
                    drops = await self._list_source_drops(GOFOBO_SOURCE_KEY)
                else:
                    parsed = parse_gofobo_home_html(fetched.html)
                    if not parsed.document_valid:
                        raise RuntimeError(
                            "The official Gofobo homepage structure could not be verified, so SniperPlug preserved the last verified Gofobo cache."
                        )
                    await self.store.replace_active_drops(GOFOBO_SOURCE_KEY, parsed.drops)
                    drops = list(parsed.drops)

                await self.store.record_source_success(
                    source_key=GOFOBO_SOURCE_KEY,
                    etag=fetched.etag or state.etag,
                    last_modified=fetched.last_modified or state.last_modified,
                    active_drop_count=len(drops),
                )
                delivered = await self._deliver_drops(drops, target_guild_id=target_guild_id)
                return MovieScanOutcome(
                    modified,
                    len(drops),
                    delivered,
                    await self.store.get_source_state(GOFOBO_SOURCE_KEY),
                )
            except Exception as error:
                await self.store.record_source_error(GOFOBO_SOURCE_KEY, str(error))
                raise

    async def _list_source_drops(self, source_key: str) -> list[MovieTicketDrop]:
        return [
            drop
            for drop in await self.store.list_active_drops(limit=250)
            if drop.source_key == source_key
        ]

    async def _connected_source_states(self) -> dict[str, MovieTicketSourceState]:
        return {
            source_key: await self.store.get_source_state(source_key)
            for source_key in CONNECTED_SOURCE_KEYS
        }

    def _source_health_lines(self, states: dict[str, MovieTicketSourceState]) -> list[str]:
        return [
            source_health_line("Atom", states[ATOM_SOURCE_KEY], ATOM_PROMOTIONS_URL),
            source_health_line("Fandango", states[FANDANGO_SOURCE_KEY], FANDANGO_OFFERS_URL),
            source_health_line("Gofobo", states[GOFOBO_SOURCE_KEY], GOFOBO_HOME_URL),
        ]

    async def _deliver_drops(
        self,
        drops: list[MovieTicketDrop] | tuple[MovieTicketDrop, ...],
        *,
        target_guild_id: int | None,
    ) -> int:
        poster_urls = await self._resolve_poster_urls(drops)
        if target_guild_id is None:
            configs = await self.store.list_enabled_configs()
        else:
            config = await self.store.get_config(int(target_guild_id))
            configs = [config] if config.enabled and config.alert_channel_id else []

        delivered = 0
        for config in configs:
            channel = await self._configured_channel(config)
            if channel is None:
                log.warning("Movie ticket destination missing guild=%s channel=%s", config.guild_id, config.alert_channel_id)
                continue
            missing = self._missing_bot_permissions(channel.guild, channel)
            if missing:
                log.warning(
                    "Movie ticket destination lacks permissions guild=%s channel=%s missing=%s",
                    channel.guild.id,
                    channel.id,
                    missing,
                )
                continue

            for drop in drops:
                if drop.classification not in DELIVERABLE_CLASSIFICATIONS:
                    continue
                if drop.classification == "public_reusable" and not drop.code:
                    continue
                reserved = await self.store.reserve_delivery(
                    guild_id=config.guild_id,
                    drop_id=drop.drop_id,
                    channel_id=channel.id,
                )
                if not reserved:
                    continue
                try:
                    message = await channel.send(
                        embed=build_multi_source_movie_drop_embed(
                            drop,
                            image_url=poster_urls.get(drop.drop_id, ""),
                        ),
                        view=await self._public_drop_view(config.guild_id, drop),
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                    await self.store.mark_delivery_sent(
                        guild_id=config.guild_id,
                        drop_id=drop.drop_id,
                        channel_id=channel.id,
                        message_id=message.id,
                    )
                    delivered += 1
                except Exception as error:
                    await self.store.mark_delivery_failed(
                        guild_id=config.guild_id,
                        drop_id=drop.drop_id,
                        error=str(error),
                    )
                    log.exception(
                        "Multi-source movie delivery failed guild=%s channel=%s drop=%s source=%s",
                        config.guild_id,
                        channel.id,
                        drop.drop_id,
                        drop.source_key,
                    )
        return delivered

    async def _public_drop_view(self, guild_id: int, drop: MovieTicketDrop) -> discord.ui.View:
        feedback_cog = self.bot.get_cog(MovieTicketFeedbackCog.__cog_name__)
        if isinstance(feedback_cog, MovieTicketFeedbackCog):
            counts = await feedback_cog.store.get_counts(guild_id=guild_id, drop_id=drop.drop_id)
            return MovieTicketFeedbackView(
                feedback_cog,
                offer_url=drop.offer_url,
                counts=counts,
            )

        view = discord.ui.View(timeout=None)
        view.add_item(
            discord.ui.Button(
                label=official_offer_button_label(drop),
                url=drop.offer_url or official_source_url(drop),
                emoji="🎟️",
            )
        )
        return view

    async def _build_drop_embeds(self, drops: list[MovieTicketDrop]) -> list[discord.Embed]:
        poster_urls = await self._resolve_poster_urls(drops)
        return [
            build_multi_source_movie_drop_embed(
                drop,
                image_url=poster_urls.get(drop.drop_id, ""),
            )
            for drop in drops
        ]

    async def _resolve_poster_url(self, drop: MovieTicketDrop) -> str:
        if drop.source_key == FANDANGO_SOURCE_KEY:
            return extract_fandango_image_marker(drop.raw_text)
        if drop.source_key == GOFOBO_SOURCE_KEY:
            return extract_gofobo_image_marker(drop.raw_text)
        return await super()._resolve_poster_url(drop)


def build_multi_source_movie_drop_embed(
    drop: MovieTicketDrop,
    *,
    image_url: str = "",
) -> discord.Embed:
    if drop.source_key == FANDANGO_SOURCE_KEY:
        purchase_required = fandango_purchase_required(drop.raw_text)
        title = "🎟️ FREE FANDANGO TICKET OFFER"
        description_value = "1 free ticket with a qualifying purchase" if purchase_required else drop.value_label
        classification = "Public code • qualifying purchase required" if purchase_required else "Public reusable code"
        claim_text = (
            "Open the official Fandango offer, choose the required eligible ticket(s), and enter the code at checkout. "
            "Read the purchase, theater, format, and same-showtime rules before paying."
        )
    elif drop.source_key == GOFOBO_SOURCE_KEY:
        title = "🎬 GOFOBO FREE SCREENING ALERT"
        description_value = "Official local screening opportunity"
        classification = "ZIP-local screening • account/pass availability required"
        claim_text = (
            "Open the official Gofobo listing immediately, enter your ZIP/postal code, and sign in or create an account if required to claim a pass. "
            "A listing is not a guaranteed pass or guaranteed theater admission."
        )
    else:
        title = "🎟️ FREE ATOM TICKET DROP"
        description_value = drop.value_label
        classification = "Public reusable code"
        claim_text = (
            "Open Atom, add the eligible ticket(s), and enter the code at checkout immediately. "
            "Supplies can run out before the listed end date."
        )

    embed = discord.Embed(
        title=title,
        description=f"**{drop.title}**\n{description_value}",
        url=drop.offer_url,
        color=discord.Color.green(),
        timestamp=datetime.now(UTC),
    )
    promo_code = normalize_public_code(drop.code)
    if promo_code:
        embed.add_field(
            name="Promo code • tap and hold to copy",
            value=promo_code,
            inline=True,
        )
    elif drop.source_key == GOFOBO_SOURCE_KEY:
        embed.add_field(
            name="Public code",
            value="No public code exposed — use the official listing and verify your ZIP.",
            inline=True,
        )
    embed.add_field(name="Classification", value=classification, inline=True)
    embed.add_field(
        name="Validity",
        value=clean_text(drop.validity_text)[:1024] or "See official terms.",
        inline=False,
    )
    restriction_text = "\n".join(f"• {clean_text(item)}" for item in drop.restrictions[:5])
    if restriction_text:
        embed.add_field(name="Important restrictions", value=restriction_text[:1024], inline=False)
    embed.add_field(name="Claim", value=claim_text, inline=False)
    if image_url:
        embed.set_image(url=image_url)
    embed.set_footer(
        text=(
            f"Source: {drop.source_label} • SniperPlug verifies the official source, "
            "not remaining redemption/pass inventory"
        )
    )
    return embed


def official_source_url(drop: MovieTicketDrop) -> str:
    if drop.source_key == FANDANGO_SOURCE_KEY:
        return FANDANGO_OFFERS_URL
    if drop.source_key == GOFOBO_SOURCE_KEY:
        return GOFOBO_HOME_URL
    return ATOM_PROMOTIONS_URL


def official_offer_button_label(drop: MovieTicketDrop) -> str:
    if drop.source_key == FANDANGO_SOURCE_KEY:
        return "Open official Fandango offer"
    if drop.source_key == GOFOBO_SOURCE_KEY:
        return "Open official Gofobo screening"
    return "Open official Atom offer"


def source_health_line(label: str, state: MovieTicketSourceState, url: str) -> str:
    health = "Healthy" if state.last_success_at and not state.last_error else "Waiting for first success"
    if state.last_error:
        health = f"Error: `{clean_text(state.last_error)[:250]}`"
    return f"**{label}:** {health} • [Official page]({url})"
