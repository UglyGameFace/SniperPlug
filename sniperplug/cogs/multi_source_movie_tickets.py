from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

import discord

from sniperplug.cogs.movie_ticket_feedback import (
    MovieTicketFeedbackCog,
    MovieTicketFeedbackView,
)
from sniperplug.cogs.movie_tickets import (
    MovieScanOutcome,
    MovieTicketsCog as AtomMovieTicketsCog,
)
from sniperplug.services.fandango_movie_offers import (
    FANDANGO_OFFERS_URL,
    FANDANGO_PURCHASE_MARKER,
    FANDANGO_SOURCE_KEY,
    FANDANGO_SOURCE_LABEL,
    FandangoOffersClient,
    extract_fandango_image_marker,
    fandango_purchase_required,
    parse_fandango_offers_html,
)
from sniperplug.services.movie_ticket_artwork import normalize_public_code
from sniperplug.services.movie_ticket_drops import (
    ATOM_PROMOTIONS_URL,
    ATOM_SOURCE_KEY,
    MovieTicketDrop,
    MovieTicketSourceState,
    clean_text,
)


log = logging.getLogger("sniperplug.movie_tickets.multi_source")


class MovieTicketsCog(AtomMovieTicketsCog):
    """The existing `/movies` command group with multiple official sources."""

    def __init__(self, bot):
        super().__init__(bot)
        self._fandango_lock = asyncio.Lock()

    async def _scan_official_source(self, *, target_guild_id: int | None = None) -> MovieScanOutcome:
        outcomes: list[MovieScanOutcome] = []
        errors: list[str] = []

        try:
            outcomes.append(await super()._scan_official_source(target_guild_id=target_guild_id))
        except Exception as error:  # noqa: BLE001 - other first-party sources must still run.
            errors.append(f"Atom: {clean_text(str(error))[:300]}")
            log.exception("Atom source failed while multi-source movie scan continued")

        try:
            outcomes.append(await self._scan_fandango_source(target_guild_id=target_guild_id))
        except Exception as error:  # noqa: BLE001 - Atom cache may still be usable.
            errors.append(f"Fandango: {clean_text(str(error))[:300]}")
            log.exception("Fandango source failed while multi-source movie scan continued")

        if not outcomes:
            raise RuntimeError("All official movie-ticket sources failed: " + " | ".join(errors))

        fandango_state = await self.store.get_source_state(FANDANGO_SOURCE_KEY)
        return MovieScanOutcome(
            modified=any(item.modified for item in outcomes),
            active_count=len(await self.store.list_active_drops(limit=100)),
            delivered_count=sum(item.delivered_count for item in outcomes),
            source_state=fandango_state if len(outcomes) == 1 and errors and errors[0].startswith("Atom:") else outcomes[0].source_state,
        )

    async def _scan_fandango_source(self, *, target_guild_id: int | None = None) -> MovieScanOutcome:
        async with self._fandango_lock:
            state = await self.store.get_source_state(FANDANGO_SOURCE_KEY)
            try:
                fetched = await FandangoOffersClient(self._require_session()).fetch(state)
                modified = not fetched.not_modified
                if fetched.not_modified:
                    drops = [
                        drop
                        for drop in await self.store.list_active_drops(limit=100)
                        if drop.source_key == FANDANGO_SOURCE_KEY
                    ]
                else:
                    parsed = parse_fandango_offers_html(fetched.html)
                    if not parsed.document_valid:
                        raise RuntimeError(
                            "The official Fandango offers page structure could not be verified, "
                            "so SniperPlug preserved its last verified Fandango cache."
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
                updated_state = await self.store.get_source_state(FANDANGO_SOURCE_KEY)
                log.info(
                    "Official Fandango source checked modified=%s active=%s delivered=%s target_guild=%s",
                    modified,
                    len(drops),
                    delivered,
                    target_guild_id,
                )
                return MovieScanOutcome(modified, len(drops), delivered, updated_state)
            except Exception as error:
                await self.store.record_source_error(FANDANGO_SOURCE_KEY, str(error))
                raise

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
                if drop.classification != "public_reusable" or not drop.code:
                    continue
                reserved = await self.store.reserve_delivery(
                    guild_id=config.guild_id,
                    drop_id=drop.drop_id,
                    channel_id=channel.id,
                )
                if not reserved:
                    continue
                try:
                    view = await self._public_drop_view(config.guild_id, drop)
                    message = await channel.send(
                        embed=build_multi_source_movie_drop_embed(
                            drop,
                            image_url=poster_urls.get(drop.drop_id, ""),
                        ),
                        view=view,
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
        return await super()._resolve_poster_url(drop)


def build_multi_source_movie_drop_embed(
    drop: MovieTicketDrop,
    *,
    image_url: str = "",
) -> discord.Embed:
    purchase_required = fandango_purchase_required(drop.raw_text)
    if drop.source_key == FANDANGO_SOURCE_KEY:
        title = "🎟️ FREE FANDANGO TICKET OFFER"
        description_value = "1 free ticket with a qualifying purchase" if purchase_required else drop.value_label
        classification = "Public code • qualifying purchase required" if purchase_required else "Public reusable code"
        claim_text = (
            "Open the official Fandango offer, choose the required eligible ticket(s), and enter the code at checkout. "
            "Read the purchase, theater, format, and same-showtime rules before paying."
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
    embed.add_field(
        name="Promo code • tap and hold to copy",
        value=normalize_public_code(drop.code) or "Unavailable",
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
            "not remaining redemption inventory"
        )
    )
    return embed


def official_source_url(drop: MovieTicketDrop) -> str:
    return FANDANGO_OFFERS_URL if drop.source_key == FANDANGO_SOURCE_KEY else ATOM_PROMOTIONS_URL


def official_offer_button_label(drop: MovieTicketDrop) -> str:
    if drop.source_key == FANDANGO_SOURCE_KEY:
        return "Open official Fandango offer"
    return "Open official Atom offer"


def source_health_line(label: str, state: MovieTicketSourceState, url: str) -> str:
    health = "Healthy" if state.last_success_at and not state.last_error else "Waiting for first success"
    if state.last_error:
        health = f"Error: `{clean_text(state.last_error)[:350]}`"
    return f"**{label}:** {health} • [Official page]({url})"
