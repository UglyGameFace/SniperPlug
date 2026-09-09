from __future__ import annotations

from typing import Any
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
from sniperplug.services.movie_user_location import (
    DEFAULT_MOVIE_LOCATION_RADIUS,
    clear_movie_user_location,
    filter_movie_drops_for_location,
    get_movie_user_location,
    normalize_movie_radius,
    normalize_movie_zip,
    save_movie_user_location,
    validate_movie_zip_live,
)
from sniperplug.services.seeitearly_screenings import SEEITEARLY_SOURCE_KEY, SEEITEARLY_UPCOMING_URL


MAX_MANUAL_RESULT_ACTIONS = 8
MAX_BUTTON_LABEL_LENGTH = 80
DISCORD_MESSAGE_EMBED_CHAR_LIMIT = 6000
MOVIE_RESULT_EMBED_CHAR_BUDGET = 5800


def _safe_http_url(value: str) -> str:
    cleaned = clean_text(value)
    if not cleaned:
        return ""
    parsed = urlparse(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return ""
    return cleaned


def _movie_action_label(drop: MovieTicketDrop, number: int, action: str) -> str:
    """Keep the movie identity visible while respecting Discord's label limit."""

    prefix = f"#{number} "
    suffix = f" • {action}"
    title = clean_text(drop.title) or "Movie"
    available = MAX_BUTTON_LABEL_LENGTH - len(prefix) - len(suffix)
    if available <= 1:
        return f"{prefix}{action}"[:MAX_BUTTON_LABEL_LENGTH]
    if len(title) > available:
        title = title[: available - 1].rstrip() + "…"
    return f"{prefix}{title}{suffix}"


def movie_result_action(drop: MovieTicketDrop, index: int) -> tuple[str, str, str]:
    """Return a title-identifying CTA, destination URL, and emoji for one result card."""

    number = max(1, int(index))
    offer_url = _safe_http_url(drop.offer_url)

    if drop.source_key == SEEITEARLY_SOURCE_KEY:
        return _movie_action_label(drop, number, "Find Pass"), offer_url or SEEITEARLY_UPCOMING_URL, "🔎"
    if drop.source_key == GOFOBO_SOURCE_KEY:
        return _movie_action_label(drop, number, "Check ZIP / Claim"), offer_url or GOFOBO_HOME_URL, "🎬"
    if drop.source_key == FANDANGO_SOURCE_KEY:
        return _movie_action_label(drop, number, "Open Offer"), offer_url or FANDANGO_OFFERS_URL, "🎟️"
    if drop.source_key == ATOM_SOURCE_KEY:
        return _movie_action_label(drop, number, "Open Promo"), offer_url or ATOM_PROMOTIONS_URL, "⚛️"

    return _movie_action_label(drop, number, "Open Result"), offer_url, "🎟️"


class MovieZipModal(discord.ui.Modal, title="Movie screening location"):
    def __init__(self, movie_cog: Any, *, user_id: int, current: Any = None) -> None:
        super().__init__(timeout=300)
        self.movie_cog = movie_cog
        self.user_id = int(user_id)
        self.zip_input = discord.ui.TextInput(
            label="US ZIP code",
            placeholder="06484",
            default=getattr(current, "zip_code", "") or "",
            min_length=5,
            max_length=10,
            required=True,
        )
        self.radius_input = discord.ui.TextInput(
            label="Local radius: 10, 25, 50, or 100 miles",
            placeholder="25",
            default=str(getattr(current, "radius_miles", DEFAULT_MOVIE_LOCATION_RADIUS)),
            min_length=2,
            max_length=3,
            required=True,
        )
        self.add_item(self.zip_input)
        self.add_item(self.radius_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if int(interaction.user.id) != self.user_id:
            await interaction.response.send_message(
                "This movie-location form belongs to another user.",
                ephemeral=True,
            )
            return
        try:
            zip_code = normalize_movie_zip(str(self.zip_input.value))
            radius = normalize_movie_radius(str(self.radius_input.value))
        except ValueError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return

        session = getattr(self.movie_cog, "_session", None)
        if session is None or session.closed:
            await interaction.response.send_message(
                "Movie location lookup is temporarily unavailable. Try again after the movie monitor reconnects.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            place = await validate_movie_zip_live(session, zip_code)
        except Exception as error:  # noqa: BLE001 - location validation must fail visibly.
            await interaction.followup.send(
                f"I could not validate ZIP `{zip_code}`: `{clean_text(str(error))[:300]}`. Nothing was saved.",
                ephemeral=True,
            )
            return

        try:
            preference = await save_movie_user_location(
                self.movie_cog.bot.db,
                user_id=self.user_id,
                zip_code=zip_code,
                radius_miles=radius,
            )
        except Exception as error:  # noqa: BLE001 - persistence failures must not kill the interaction.
            await interaction.followup.send(
                "The ZIP was valid, but SniperPlug could not save your movie-location preference. "
                f"Nothing was changed. Error: `{clean_text(str(error))[:300]}`",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            f"✅ Saved privately for **your** movie browsing: **{place.city}, {place.state}** • "
            f"ZIP `{preference.zip_code}` • **{preference.radius_miles} mi**.\n"
            "Here is the conservative Local view now. Use **Show All** whenever you want national results.",
            ephemeral=True,
        )
        await _send_personal_movie_results(interaction, self.movie_cog, show_all=False)


class _SetMovieZipButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="Set / Change ZIP",
            style=discord.ButtonStyle.secondary,
            emoji="📍",
            row=4,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        movie_cog = _movie_cog(interaction)
        if movie_cog is None:
            await interaction.response.send_message("The movie-ticket monitor is unavailable.", ephemeral=True)
            return
        # A modal must be the interaction's first response. Do not wait on Turso
        # just to prefill fields; a slow database must never turn this button into
        # Discord's generic "Interaction failed" message.
        await interaction.response.send_modal(
            MovieZipModal(movie_cog, user_id=int(interaction.user.id))
        )


class _UseLocalMovieButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="Use Local",
            style=discord.ButtonStyle.success,
            emoji="📍",
            row=4,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        movie_cog = _movie_cog(interaction)
        if movie_cog is None:
            await interaction.response.send_message("The movie-ticket monitor is unavailable.", ephemeral=True)
            return
        # Acknowledge before any database or network I/O. Turso and screening
        # sources are allowed to be slow; Discord's interaction deadline is not.
        await interaction.response.defer(ephemeral=True, thinking=True)
        await _send_personal_movie_results(
            interaction,
            movie_cog,
            show_all=False,
            require_saved_location=True,
        )


class _ShowAllMoviesButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="Show All",
            style=discord.ButtonStyle.primary,
            emoji="🌎",
            row=4,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        movie_cog = _movie_cog(interaction)
        if movie_cog is None:
            await interaction.response.send_message("The movie-ticket monitor is unavailable.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        await _send_personal_movie_results(interaction, movie_cog, show_all=True)


class _ClearMovieZipButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="Clear ZIP",
            style=discord.ButtonStyle.danger,
            emoji="🗑️",
            row=4,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        movie_cog = _movie_cog(interaction)
        if movie_cog is None:
            await interaction.response.send_message("The movie-ticket monitor is unavailable.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            await clear_movie_user_location(movie_cog.bot.db, user_id=int(interaction.user.id))
        except Exception as error:  # noqa: BLE001 - persistence failures must be visible after defer.
            await interaction.followup.send(
                "SniperPlug could not clear your movie ZIP right now. "
                f"Error: `{clean_text(str(error))[:300]}`",
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            "Your personal movie ZIP was cleared. Nothing about the server's alert destination changed.",
            ephemeral=True,
        )


def build_movie_result_actions_view(drops: list[MovieTicketDrop]) -> discord.ui.View:
    """Build obvious result links plus optional personal location controls."""

    view = discord.ui.View(timeout=900)
    button_offset = 0
    for result_number, drop in enumerate(drops[:MAX_MANUAL_RESULT_ACTIONS], start=1):
        label, url, emoji = movie_result_action(drop, result_number)
        if not url:
            continue
        view.add_item(
            discord.ui.Button(
                label=label[:MAX_BUTTON_LABEL_LENGTH],
                style=discord.ButtonStyle.link,
                url=url,
                emoji=emoji,
                row=button_offset // 2,
            )
        )
        button_offset += 1

    # Manual result views are ephemeral, so these controls are naturally scoped
    # to the user who opened the result stack. A saved ZIP never changes a guild's
    # public alert configuration.
    view.add_item(_SetMovieZipButton())
    view.add_item(_UseLocalMovieButton())
    view.add_item(_ShowAllMoviesButton())
    view.add_item(_ClearMovieZipButton())
    return view


def _copy_result_embed_visuals(source: discord.Embed, target: discord.Embed) -> None:
    image_url = getattr(source.image, "url", None)
    if image_url:
        target.set_image(url=str(image_url))
    thumbnail_url = getattr(source.thumbnail, "url", None)
    if thumbnail_url:
        target.set_thumbnail(url=str(thumbnail_url))


def _compact_movie_result_embed(
    source: discord.Embed,
    drop: MovieTicketDrop,
    *,
    result_number: int,
    char_budget: int,
) -> discord.Embed:
    label, url, _emoji = movie_result_action(drop, result_number)
    description = clean_text(source.description or "")[:130]
    compact = discord.Embed(
        title=clean_text(source.title or "Movie result")[:120],
        description=description,
        url=source.url or None,
        color=source.color,
        timestamp=source.timestamp,
    )
    compact.add_field(
        name=f"👇 Result #{result_number} action",
        value=(
            f"Use **{label}** below."
            if url
            else "No safe direct result link was provided; use the source buttons on the permanent panel."
        )[:120],
        inline=False,
    )

    code = clean_text(drop.code)
    if code:
        compact.add_field(name="Promo code", value=code[:80], inline=True)

    classification = (
        "Public reusable code"
        if drop.classification == "public_reusable"
        else "Screening/discovery lead"
    )
    if drop.source_key == SEEITEARLY_SOURCE_KEY:
        classification += " • supplemental"
    compact.add_field(name="Classification", value=classification[:80], inline=True)

    verify_parts: list[str] = []
    validity = clean_text(drop.validity_text)
    if validity:
        verify_parts.append(validity[:100])
    if drop.restrictions:
        restriction = clean_text(drop.restrictions[0])
        if restriction:
            verify_parts.append(restriction[:100])
    verify_text = " • ".join(verify_parts) or "Verify availability and eligibility on the official source before claiming or traveling."
    compact.add_field(name="Verify before claiming", value=verify_text[:200], inline=False)
    compact.set_footer(text=f"Source: {clean_text(drop.source_label)[:80]}")
    _copy_result_embed_visuals(source, compact)

    if len(compact) <= char_budget:
        return compact

    # Emergency form is intentionally tiny but keeps identity, action, source,
    # and any actual public code. It is preferable to Discord rejecting the
    # entire result stack because multiple individually-valid embeds exceed the
    # 6000-character per-message aggregate limit.
    emergency = discord.Embed(
        title=clean_text(source.title or "Movie result")[:80],
        description=f"**{clean_text(drop.title)[:120] or 'Movie result'}**",
        url=source.url or None,
        color=source.color,
        timestamp=source.timestamp,
    )
    emergency.add_field(
        name=f"👇 Result #{result_number} action",
        value=(f"Use **{label}** below." if url else "Use the permanent panel source buttons.")[:110],
        inline=False,
    )
    if code:
        emergency.add_field(name="Promo code", value=code[:80], inline=True)
    emergency.add_field(
        name="Verify",
        value="Confirm current availability, eligibility, location, and terms on the linked source.",
        inline=False,
    )
    emergency.set_footer(text=f"Source: {clean_text(drop.source_label)[:60]}")
    _copy_result_embed_visuals(source, emergency)
    return emergency


def _fit_movie_result_embeds_to_discord_budget(
    embeds: list[discord.Embed],
    drops: list[MovieTicketDrop],
) -> list[discord.Embed]:
    if sum(len(embed) for embed in embeds) <= MOVIE_RESULT_EMBED_CHAR_BUDGET:
        return embeds

    paired_count = min(len(embeds), len(drops), MAX_MANUAL_RESULT_ACTIONS)
    if paired_count <= 0:
        return embeds

    per_embed_budget = max(400, MOVIE_RESULT_EMBED_CHAR_BUDGET // paired_count - 5)
    compacted = [
        _compact_movie_result_embed(
            embed,
            drop,
            result_number=index,
            char_budget=per_embed_budget,
        )
        for index, (embed, drop) in enumerate(
            zip(embeds[:paired_count], drops[:paired_count], strict=False),
            start=1,
        )
    ]

    # The emergency form above is smaller than the minimum per-card budget, so
    # this invariant should always hold. Fail closed in development if a future
    # card change violates it instead of rediscovering the limit in production.
    if sum(len(embed) for embed in compacted) > MOVIE_RESULT_EMBED_CHAR_BUDGET:
        raise ValueError("Movie result embeds still exceed Discord's aggregate embed character budget.")
    return compacted


def annotate_movie_result_embeds(
    embeds: list[discord.Embed],
    drops: list[MovieTicketDrop],
) -> list[discord.Embed]:
    """Make card/button mapping obvious and keep the whole Discord message valid."""

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

    if sum(len(embed) for embed in embeds) > DISCORD_MESSAGE_EMBED_CHAR_LIMIT:
        embeds[:] = _fit_movie_result_embeds_to_discord_budget(embeds, drops)
    return embeds


async def _send_personal_movie_results(
    interaction: discord.Interaction,
    movie_cog: Any,
    *,
    show_all: bool,
    require_saved_location: bool = False,
) -> None:
    try:
        all_drops = await movie_cog.store.list_active_drops(limit=MAX_MANUAL_RESULT_ACTIONS)
        preference = await get_movie_user_location(
            movie_cog.bot.db,
            user_id=int(interaction.user.id),
        )
    except Exception as error:  # noqa: BLE001 - manual UI should fail visibly, never vanish.
        await interaction.followup.send(
            "SniperPlug could not load your movie results/location preference right now. "
            f"Error: `{clean_text(str(error))[:300]}`",
            ephemeral=True,
        )
        return

    if require_saved_location and preference is None:
        await interaction.followup.send(
            "Set your ZIP first, then **Use Local** will hide distance-unverified screening leads.",
            view=build_movie_result_actions_view(list(all_drops)),
            ephemeral=True,
        )
        return

    if show_all or preference is None:
        selected = list(all_drops)
        if preference is None:
            location_line = (
                "🌎 **All-results mode.** Set a ZIP whenever you want a conservative local view."
            )
        else:
            location_line = (
                f"🌎 **All-results mode.** Saved local preference: ZIP `{preference.zip_code}` • "
                f"**{preference.radius_miles} mi**."
            )
    else:
        session = getattr(movie_cog, "_session", None)
        if session is None or session.closed:
            selected = [drop for drop in all_drops if drop.classification != "local_screening"]
            location_line = (
                f"📍 **Local mode:** ZIP `{preference.zip_code}` • **{preference.radius_miles} mi**. "
                "The location resolver is temporarily unavailable, so distance-unverified screening leads are hidden."
            )
        else:
            try:
                local = await filter_movie_drops_for_location(session, list(all_drops), preference)
            except Exception as error:  # noqa: BLE001 - fail closed when local proof cannot be built.
                selected = [drop for drop in all_drops if drop.classification != "local_screening"]
                location_line = (
                    f"📍 **Local mode:** ZIP `{preference.zip_code}` • **{preference.radius_miles} mi**. "
                    "Local screening proof could not be refreshed, so distance-unverified screenings are hidden. "
                    f"Error: `{clean_text(str(error))[:220]}`"
                )
            else:
                selected = list(local.drops)
                location_line = (
                    f"📍 **Local mode:** ZIP `{preference.zip_code}` • **{preference.radius_miles} mi** • "
                    f"confirmed local screening titles **{local.matched_screening_count}**"
                )
                if local.hidden_screening_count:
                    location_line += (
                        f" • distance-unverified screening leads hidden **{local.hidden_screening_count}**"
                    )
                if local.note:
                    location_line += f"\n{local.note}"

    if not selected:
        await interaction.followup.send(
            location_line
            + "\nNo results meet this view right now. Press **Show All** to inspect the broader national feed.",
            view=build_movie_result_actions_view([]),
            ephemeral=True,
        )
        return

    try:
        embeds = annotate_movie_result_embeds(
            await movie_cog._build_drop_embeds(selected),  # noqa: SLF001 - shared manual-result UI.
            selected,
        )
    except Exception as error:  # noqa: BLE001 - preserve a usable interaction if artwork/rendering fails.
        await interaction.followup.send(
            location_line
            + "\nSniperPlug found movie results but could not render their cards right now. "
            f"Error: `{clean_text(str(error))[:220]}`",
            view=build_movie_result_actions_view(selected),
            ephemeral=True,
        )
        return

    await interaction.followup.send(
        content=(
            location_line
            + f"\nShowing **{len(selected)}** recently confirmed result(s). "
            "Location filtering is personal and does not change automatic server alerts."
        ),
        embeds=embeds,
        view=build_movie_result_actions_view(selected),
        ephemeral=True,
    )


def _movie_cog(interaction: discord.Interaction) -> Any | None:
    client = interaction.client
    get_cog = getattr(client, "get_cog", None)
    if callable(get_cog):
        for name in ("movies", "MovieTicketsCog"):
            candidate = get_cog(name)
            if candidate is not None and hasattr(candidate, "store"):
                return candidate
    for candidate in getattr(client, "cogs", {}).values():
        if hasattr(candidate, "store") and hasattr(candidate, "_build_drop_embeds"):
            if candidate.__class__.__name__ == "MovieTicketsCog":
                return candidate
    return None
