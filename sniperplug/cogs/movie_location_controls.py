from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import discord

from sniperplug.cogs.movie_result_actions import (
    MAX_MANUAL_RESULT_ACTIONS,
    movie_result_action,
)
from sniperplug.services.movie_ticket_drops import MovieTicketDrop
from sniperplug.services.movie_user_location import (
    DEFAULT_MOVIE_LOCATION_RADIUS,
    MovieLocalFilterResult,
    MovieUserLocationPreference,
    clear_movie_user_location,
    filter_movie_drops_for_location,
    get_movie_user_location,
    normalize_movie_radius,
    normalize_movie_zip,
    save_movie_user_location,
    validate_movie_zip_live,
)


@dataclass(frozen=True, slots=True)
class MovieBrowseSelection:
    drops: tuple[MovieTicketDrop, ...]
    preference: MovieUserLocationPreference | None
    local_result: MovieLocalFilterResult | None
    show_all: bool


async def select_movie_browse_drops(
    movie_cog: Any,
    *,
    user_id: int,
    drops: list[MovieTicketDrop],
    show_all: bool = False,
) -> MovieBrowseSelection:
    preference = await get_movie_user_location(movie_cog.bot.db, user_id=user_id)
    if show_all or preference is None:
        return MovieBrowseSelection(tuple(drops), preference, None, bool(show_all))

    session = getattr(movie_cog, "_session", None)
    if session is None or session.closed:
        return MovieBrowseSelection(
            tuple(drop for drop in drops if drop.classification != "local_screening"),
            preference,
            MovieLocalFilterResult(
                drops=tuple(drop for drop in drops if drop.classification != "local_screening"),
                matched_screening_count=0,
                hidden_screening_count=sum(1 for drop in drops if drop.classification == "local_screening"),
                searched_places=(),
                note="Local screening lookup is temporarily unavailable; distance-unverified screenings are hidden.",
            ),
            False,
        )

    local_result = await filter_movie_drops_for_location(session, drops, preference)
    return MovieBrowseSelection(local_result.drops, preference, local_result, False)


def movie_browse_content(selection: MovieBrowseSelection) -> str:
    preference = selection.preference
    if preference is None:
        return (
            "Browsing **all recently confirmed results**. Set a ZIP below whenever you want a local-first view."
        )
    if selection.show_all:
        return (
            f"Showing **all results**. Your saved local preference is ZIP `{preference.zip_code}` "
            f"within **{preference.radius_miles} mi**; press **Use Local** to filter again."
        )

    local_result = selection.local_result
    matched = local_result.matched_screening_count if local_result else 0
    hidden = local_result.hidden_screening_count if local_result else 0
    text = (
        f"📍 **Local mode:** ZIP `{preference.zip_code}` • within **{preference.radius_miles} mi** • "
        f"confirmed local screening titles **{matched}**"
    )
    if hidden:
        text += f" • distance-unverified screening leads hidden **{hidden}**"
    if local_result and local_result.note:
        text += f"\n{local_result.note}"
    text += "\nUse **Show All** whenever you want the national/unfiltered view."
    return text


class MovieLocationModal(discord.ui.Modal, title="Movie screening location"):
    def __init__(self, movie_cog: Any, *, owner_id: int, current: MovieUserLocationPreference | None):
        super().__init__(timeout=300)
        self.movie_cog = movie_cog
        self.owner_id = int(owner_id)
        self.zip_input = discord.ui.TextInput(
            label="US ZIP code",
            placeholder="06484",
            default=current.zip_code if current else "",
            min_length=5,
            max_length=10,
            required=True,
        )
        self.radius_input = discord.ui.TextInput(
            label="Search radius (10, 25, 50, or 100 miles)",
            placeholder="25",
            default=str(current.radius_miles if current else DEFAULT_MOVIE_LOCATION_RADIUS),
            min_length=2,
            max_length=3,
            required=True,
        )
        self.add_item(self.zip_input)
        self.add_item(self.radius_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if int(interaction.user.id) != self.owner_id:
            await interaction.response.send_message("This movie-location form belongs to another user.", ephemeral=True)
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
        except Exception as error:  # noqa: BLE001 - validation must fail visibly.
            await interaction.followup.send(
                f"I could not validate ZIP `{zip_code}`: `{str(error)[:300]}`. Nothing was saved.",
                ephemeral=True,
            )
            return

        preference = await save_movie_user_location(
            self.movie_cog.bot.db,
            user_id=self.owner_id,
            zip_code=zip_code,
            radius_miles=radius,
        )
        await interaction.followup.send(
            f"✅ Movie location saved privately for you: **{place.city}, {place.state}** • "
            f"ZIP `{preference.zip_code}` • **{preference.radius_miles} mi**.\n"
            "Your next **Check Now** or `/movies latest` uses Local mode automatically. "
            "You can still press **Show All** on the results whenever you want national discovery.",
            ephemeral=True,
        )


class _MovieLocationButton(discord.ui.Button):
    def __init__(self, view_ref: "MovieBrowseView") -> None:
        preference = view_ref.preference
        label = (
            f"ZIP {preference.zip_code} · {preference.radius_miles} mi"
            if preference
            else "Set ZIP"
        )
        super().__init__(
            label=label[:80],
            style=discord.ButtonStyle.secondary,
            emoji="📍",
            row=4,
        )
        self.view_ref = view_ref

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(
            MovieLocationModal(
                self.view_ref.movie_cog,
                owner_id=self.view_ref.owner_id,
                current=self.view_ref.preference,
            )
        )


class _MovieShowModeButton(discord.ui.Button):
    def __init__(self, view_ref: "MovieBrowseView") -> None:
        super().__init__(
            label="Use Local" if view_ref.show_all else "Show All",
            style=discord.ButtonStyle.primary,
            emoji="📍" if view_ref.show_all else "🌎",
            row=4,
        )
        self.view_ref = view_ref

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        drops = await self.view_ref.movie_cog.store.list_active_drops(limit=MAX_MANUAL_RESULT_ACTIONS)
        selection = await select_movie_browse_drops(
            self.view_ref.movie_cog,
            user_id=self.view_ref.owner_id,
            drops=drops,
            show_all=not self.view_ref.show_all,
        )
        await self.view_ref.movie_cog.send_personal_movie_results(
            interaction,
            selection=selection,
            heading="Movie results",
        )


class _MovieClearLocationButton(discord.ui.Button):
    def __init__(self, view_ref: "MovieBrowseView") -> None:
        super().__init__(
            label="Clear ZIP",
            style=discord.ButtonStyle.danger,
            emoji="🗑️",
            row=4,
        )
        self.view_ref = view_ref

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        await clear_movie_user_location(
            self.view_ref.movie_cog.bot.db,
            user_id=self.view_ref.owner_id,
        )
        await interaction.followup.send(
            "Your saved movie ZIP was cleared. Future checks return to the all-results view until you set another ZIP.",
            ephemeral=True,
        )


class MovieBrowseView(discord.ui.View):
    def __init__(
        self,
        movie_cog: Any,
        *,
        owner_id: int,
        drops: list[MovieTicketDrop],
        preference: MovieUserLocationPreference | None,
        show_all: bool,
    ) -> None:
        super().__init__(timeout=900)
        self.movie_cog = movie_cog
        self.owner_id = int(owner_id)
        self.preference = preference
        self.show_all = bool(show_all)

        button_offset = 0
        for result_number, drop in enumerate(drops[:MAX_MANUAL_RESULT_ACTIONS], start=1):
            label, url, emoji = movie_result_action(drop, result_number)
            if not url:
                continue
            self.add_item(
                discord.ui.Button(
                    label=label[:80],
                    style=discord.ButtonStyle.link,
                    url=url,
                    emoji=emoji,
                    row=button_offset // 2,
                )
            )
            button_offset += 1

        self.add_item(_MovieLocationButton(self))
        if preference is not None:
            self.add_item(_MovieShowModeButton(self))
            self.add_item(_MovieClearLocationButton(self))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) == self.owner_id:
            return True
        await interaction.response.send_message(
            "These movie-location controls belong to the user who opened this result view.",
            ephemeral=True,
        )
        return False


def build_movie_browse_view(
    movie_cog: Any,
    *,
    user_id: int,
    drops: list[MovieTicketDrop],
    preference: MovieUserLocationPreference | None,
    show_all: bool,
) -> MovieBrowseView:
    return MovieBrowseView(
        movie_cog,
        owner_id=user_id,
        drops=drops,
        preference=preference,
        show_all=show_all,
    )
