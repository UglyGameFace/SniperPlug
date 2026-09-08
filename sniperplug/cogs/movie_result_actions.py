from __future__ import annotations

from urllib.parse import urlparse

import discord

from sniperplug.services.fandango_movie_offers import FANDANGO_OFFERS_URL, FANDANGO_SOURCE_KEY
from sniperplug.services.gofobo_screenings import GOFOBO_HOME_URL, GOFOBO_SOURCE_KEY
from sniperplug.services.movie_ticket_drops import (
    ATOM_PROMOTIONS_URL,
    ATOM_SOURCE_KEY,
    MovieTicketDrop,
    clean_text,
)
from sniperplug.services.seeitearly_screenings import SEEITEARLY_SOURCE_KEY, SEEITEARLY_UPCOMING_URL


MAX_MANUAL_RESULT_ACTIONS = 8


def _safe_http_url(value: str) -> str:
    cleaned = clean_text(value)
    if not cleaned:
        return ""
    parsed = urlparse(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return ""
    return cleaned


def movie_result_action(drop: MovieTicketDrop, index: int) -> tuple[str, str, str]:
    """Return a short visible CTA, destination URL, and emoji for one result card."""

    number = max(1, int(index))
    offer_url = _safe_http_url(drop.offer_url)

    if drop.source_key == SEEITEARLY_SOURCE_KEY:
        return f"#{number} Find Pass", offer_url or SEEITEARLY_UPCOMING_URL, "🔎"
    if drop.source_key == GOFOBO_SOURCE_KEY:
        return f"#{number} Check ZIP / Claim", offer_url or GOFOBO_HOME_URL, "🎬"
    if drop.source_key == FANDANGO_SOURCE_KEY:
        return f"#{number} Open Offer", offer_url or FANDANGO_OFFERS_URL, "🎟️"
    if drop.source_key == ATOM_SOURCE_KEY:
        return f"#{number} Open Atom Promo", offer_url or ATOM_PROMOTIONS_URL, "⚛️"

    return f"#{number} Open Result", offer_url, "🎟️"


def build_movie_result_actions_view(drops: list[MovieTicketDrop]) -> discord.ui.View:
    """Build obvious numbered link buttons matching the manual result cards."""

    view = discord.ui.View(timeout=900)
    button_offset = 0
    for result_number, drop in enumerate(drops[:MAX_MANUAL_RESULT_ACTIONS], start=1):
        label, url, emoji = movie_result_action(drop, result_number)
        if not url:
            continue
        view.add_item(
            discord.ui.Button(
                label=label[:80],
                style=discord.ButtonStyle.link,
                url=url,
                emoji=emoji,
                row=button_offset // 2,
            )
        )
        button_offset += 1
    return view


def annotate_movie_result_embeds(
    embeds: list[discord.Embed],
    drops: list[MovieTicketDrop],
) -> list[discord.Embed]:
    """Make the card/button relationship obvious without relying on title links."""

    for result_number, (embed, drop) in enumerate(
        zip(embeds, drops[:MAX_MANUAL_RESULT_ACTIONS], strict=False),
        start=1,
    ):
        label, url, _emoji = movie_result_action(drop, result_number)
        if url:
            value = f"Tap **{label}** below. You do not need to tap the card title."
        else:
            value = "No safe direct result link was provided for this card; use the source buttons on the permanent panel."
        embed.insert_field_at(
            0,
            name=f"👇 Result #{result_number} action",
            value=value,
            inline=False,
        )
    return embeds
