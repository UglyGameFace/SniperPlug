from __future__ import annotations

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


def movie_result_action(drop: MovieTicketDrop, index: int) -> tuple[str, str, str]:
    """Return a short visible CTA, destination URL, and emoji for one result card."""

    number = max(1, int(index))
    offer_url = clean_text(drop.offer_url)

    if drop.source_key == SEEITEARLY_SOURCE_KEY:
        return f"#{number} Find Pass", offer_url or SEEITEARLY_UPCOMING_URL, "🔎"
    if drop.source_key == GOFOBO_SOURCE_KEY:
        return f"#{number} Check ZIP / Claim", offer_url or GOFOBO_HOME_URL, "🎬"
    if drop.source_key == FANDANGO_SOURCE_KEY:
        return f"#{number} Open Offer", offer_url or FANDANGO_OFFERS_URL, "🎟️"
    if drop.source_key == ATOM_SOURCE_KEY:
        return f"#{number} Open Atom Promo", offer_url or ATOM_PROMOTIONS_URL, "⚛️"

    return f"#{number} Open Result", offer_url or ATOM_PROMOTIONS_URL, "🎟️"


def build_movie_result_actions_view(drops: list[MovieTicketDrop]) -> discord.ui.View:
    """Build obvious numbered link buttons matching the manual result cards."""

    view = discord.ui.View(timeout=900)
    for offset, drop in enumerate(drops[:MAX_MANUAL_RESULT_ACTIONS], start=1):
        label, url, emoji = movie_result_action(drop, offset)
        view.add_item(
            discord.ui.Button(
                label=label[:80],
                style=discord.ButtonStyle.link,
                url=url,
                emoji=emoji,
                row=(offset - 1) // 2,
            )
        )
    return view


def annotate_movie_result_embeds(
    embeds: list[discord.Embed],
    drops: list[MovieTicketDrop],
) -> list[discord.Embed]:
    """Make the card/button relationship obvious without relying on title links."""

    for offset, (embed, drop) in enumerate(
        zip(embeds, drops[:MAX_MANUAL_RESULT_ACTIONS], strict=False),
        start=1,
    ):
        label, _url, _emoji = movie_result_action(drop, offset)
        embed.insert_field_at(
            0,
            name=f"👇 Result #{offset} action",
            value=f"Tap **{label}** below. You do not need to tap the card title.",
            inline=False,
        )
    return embeds
