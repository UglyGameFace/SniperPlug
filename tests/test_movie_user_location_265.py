from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import sniperplug.cogs.movie_result_actions as movie_actions
import sniperplug.services.movie_user_location as movie_location
from sniperplug.cogs.movie_result_actions import build_movie_result_actions_view
from sniperplug.services.gofobo_screenings import GOFOBO_SOURCE_KEY
from sniperplug.services.movie_ticket_drops import ATOM_SOURCE_KEY, MovieTicketDrop
from sniperplug.services.movie_user_location import (
    MovieUserLocationPreference,
    MovieZipPlace,
    clear_movie_user_location,
    filter_movie_drops_for_location,
    get_movie_user_location,
    normalize_movie_radius,
    normalize_movie_zip,
    save_movie_user_location,
)
from sniperplug.services.seeitearly_screenings import SEEITEARLY_SOURCE_KEY
from sniperplug.storage.db import Database


USER_ID = 1530990344404205792


def _drop(
    *,
    source_key: str,
    title: str,
    classification: str,
    url: str,
) -> MovieTicketDrop:
    return MovieTicketDrop(
        drop_id=f"{source_key}:{title}",
        source_key=source_key,
        source_label=source_key,
        title=title,
        code="FREE" if classification == "public_reusable" else "",
        classification=classification,
        ticket_limit=1,
        offer_url=url,
        validity_text="Current public listing",
        restrictions=(),
        raw_text=title,
    )


def test_movie_zip_normalization_and_radius_are_strict() -> None:
    assert normalize_movie_zip("06484") == "06484"
    assert normalize_movie_zip("06484-1234") == "06484"
    for radius in (10, 25, 50, 100):
        assert normalize_movie_radius(radius) == radius

    for bad_zip in ("6484", "0648A", "06484 extra", ""):
        with pytest.raises(ValueError):
            normalize_movie_zip(bad_zip)
    for bad_radius in (0, 5, 20, 60, 200, "nearby"):
        with pytest.raises(ValueError):
            normalize_movie_radius(bad_radius)


def test_movie_location_round_trips_large_user_id_as_text(tmp_path: Path) -> None:
    asyncio.run(_exercise_location_round_trip(tmp_path / "movie-location.sqlite3"))


async def _exercise_location_round_trip(path: Path) -> None:
    db = Database(str(path))
    await db.connect()
    await db.init()
    try:
        saved = await save_movie_user_location(
            db,
            user_id=USER_ID,
            zip_code="06484",
            radius_miles=25,
        )
        assert saved == MovieUserLocationPreference(USER_ID, "06484", 25)
        assert await get_movie_user_location(db, user_id=USER_ID) == saved

        conn = db.require_conn()
        cursor = await conn.execute(
            "SELECT typeof(user_id) AS id_type, zip_code, radius_miles FROM movie_ticket_user_location_v2"
        )
        row = await cursor.fetchone()
        assert row["id_type"] == "text"
        assert row["zip_code"] == "06484"
        assert row["radius_miles"] == 25

        await clear_movie_user_location(db, user_id=USER_ID)
        assert await get_movie_user_location(db, user_id=USER_ID) is None
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_local_mode_keeps_global_promos_and_only_location_confirmed_screenings(monkeypatch: pytest.MonkeyPatch) -> None:
    shelton = MovieZipPlace("Shelton", "CT", 41.3047, -73.1294, 0.0)
    bridgeport = MovieZipPlace("Greater Bridgeport", "CT", 41.1792, -73.1894, 9.0)
    new_york = MovieZipPlace("New York", "NY", 40.7128, -74.0060, 60.9)
    preference = MovieUserLocationPreference(USER_ID, "06484", 25)

    async def fake_resolve(_session, _preference):
        return shelton, (shelton, bridgeport, new_york)

    called_cities: list[str] = []

    async def fake_city_slugs(_session, place):
        called_cities.append(place.city)
        if place.city == "Greater Bridgeport":
            return frozenset({"practical-magic-2"})
        if place.city == "New York":
            return frozenset({"far-away-movie"})
        return frozenset()

    monkeypatch.setattr(movie_location, "_resolve_zip_places", fake_resolve)
    monkeypatch.setattr(movie_location, "_fetch_seeitearly_city_movie_slugs", fake_city_slugs)

    atom = _drop(
        source_key=ATOM_SOURCE_KEY,
        title="National Atom Promo",
        classification="public_reusable",
        url="https://www.atomtickets.com/promotions",
    )
    local = _drop(
        source_key=SEEITEARLY_SOURCE_KEY,
        title="Practical Magic 2",
        classification="local_screening",
        url="https://seeitearly.com/movies/practical-magic-2",
    )
    far = _drop(
        source_key=SEEITEARLY_SOURCE_KEY,
        title="Far Away Movie",
        classification="local_screening",
        url="https://seeitearly.com/movies/far-away-movie",
    )
    unverified_gofobo = _drop(
        source_key=GOFOBO_SOURCE_KEY,
        title="Title-only Gofobo Lead",
        classification="local_screening",
        url="https://gofobo.com/main/local_screenings/",
    )

    result = await filter_movie_drops_for_location(
        object(),
        [atom, local, far, unverified_gofobo],
        preference,
    )

    assert result.drops == (atom, local)
    assert result.matched_screening_count == 1
    assert result.hidden_screening_count == 2
    assert "Greater Bridgeport" in called_cities
    assert "New York" not in called_cities


@pytest.mark.asyncio
async def test_local_mode_fails_closed_when_city_pages_cannot_be_confirmed(monkeypatch: pytest.MonkeyPatch) -> None:
    shelton = MovieZipPlace("Shelton", "CT", 41.3047, -73.1294, 0.0)
    preference = MovieUserLocationPreference(USER_ID, "06484", 25)

    async def fake_resolve(_session, _preference):
        return shelton, (shelton,)

    async def unavailable_city(_session, _place):
        return None

    monkeypatch.setattr(movie_location, "_resolve_zip_places", fake_resolve)
    monkeypatch.setattr(movie_location, "_fetch_seeitearly_city_movie_slugs", unavailable_city)

    screening = _drop(
        source_key=SEEITEARLY_SOURCE_KEY,
        title="Unknown Distance",
        classification="local_screening",
        url="https://seeitearly.com/movies/unknown-distance",
    )
    result = await filter_movie_drops_for_location(object(), [screening], preference)

    assert result.drops == ()
    assert result.hidden_screening_count == 1
    assert "distance-unverified screenings are hidden" in result.note


def test_haversine_rejects_the_sixty_mile_new_york_example() -> None:
    distance = movie_location._haversine_miles(41.3047, -73.1294, 40.7128, -74.0060)
    assert distance > 50
    assert distance < 80


def test_zippopotam_place_rows_accept_primary_and_list_shapes() -> None:
    primary = {
        "places": [
            {
                "place name": "Shelton",
                "state abbreviation": "CT",
                "latitude": "41.3047",
                "longitude": "-73.1294",
            }
        ]
    }
    nearby = [
        {
            "place name": "New Haven",
            "state abbreviation": "CT",
            "latitude": "41.3083",
            "longitude": "-72.9279",
        }
    ]

    assert movie_location._place_from_zip_payload(primary).city == "Shelton"
    rows = movie_location._iter_place_rows(nearby)
    assert rows[0]["place name"] == "New Haven"


def test_result_view_exposes_optional_location_controls_without_forcing_zip() -> None:
    view = build_movie_result_actions_view([])
    labels = [getattr(item, "label", "") for item in view.children]

    assert labels == ["Set / Change ZIP", "Use Local", "Show All", "Clear ZIP"]
    assert all(getattr(item, "row", None) == 4 for item in view.children)


def test_location_slice_does_not_mutate_server_movie_alert_config() -> None:
    source = Path("sniperplug/cogs/movie_result_actions.py").read_text(encoding="utf-8")
    service = Path("sniperplug/services/movie_user_location.py").read_text(encoding="utf-8")

    assert "MovieTicketConfig" not in source
    assert "save_config(" not in source
    assert "movie_ticket_config_v2" not in service
    assert "guild_id" not in service


@pytest.mark.asyncio
async def test_set_zip_button_opens_modal_without_database_io(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeUser:
        id = USER_ID

    class FakeResponse:
        def __init__(self) -> None:
            self.modal = None

        async def send_message(self, *_args, **_kwargs) -> None:
            raise AssertionError("Set ZIP should open the modal, not send a fallback message.")

        async def send_modal(self, modal) -> None:
            self.modal = modal

    class FakeInteraction:
        user = FakeUser()

        def __init__(self) -> None:
            self.response = FakeResponse()

    fake_cog = object()
    monkeypatch.setattr(movie_actions, "_movie_cog", lambda _interaction: fake_cog)

    async def forbidden_database_read(*_args, **_kwargs):
        raise AssertionError("Set ZIP must not wait on Turso before opening its modal.")

    monkeypatch.setattr(movie_actions, "get_movie_user_location", forbidden_database_read)

    interaction = FakeInteraction()
    await movie_actions._SetMovieZipButton().callback(interaction)

    assert isinstance(interaction.response.modal, movie_actions.MovieZipModal)


@pytest.mark.asyncio
async def test_use_local_defers_before_loading_database_or_network_results(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []

    class FakeUser:
        id = USER_ID

    class FakeResponse:
        async def send_message(self, *_args, **_kwargs) -> None:
            raise AssertionError("Use Local should defer before doing slow work.")

        async def defer(self, *, ephemeral: bool, thinking: bool) -> None:
            assert ephemeral is True
            assert thinking is True
            events.append("defer")

    class FakeInteraction:
        user = FakeUser()
        response = FakeResponse()

    fake_cog = object()
    monkeypatch.setattr(movie_actions, "_movie_cog", lambda _interaction: fake_cog)

    async def fake_send_results(_interaction, _cog, *, show_all: bool, require_saved_location: bool = False) -> None:
        assert events == ["defer"]
        assert show_all is False
        assert require_saved_location is True
        events.append("results")

    monkeypatch.setattr(movie_actions, "_send_personal_movie_results", fake_send_results)

    await movie_actions._UseLocalMovieButton().callback(FakeInteraction())

    assert events == ["defer", "results"]


@pytest.mark.asyncio
async def test_personal_results_fail_closed_when_local_proof_refresh_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    national = _drop(
        source_key=ATOM_SOURCE_KEY,
        title="National Promo",
        classification="public_reusable",
        url="https://www.atomtickets.com/promotions",
    )
    screening = _drop(
        source_key=SEEITEARLY_SOURCE_KEY,
        title="Unverified Local Screening",
        classification="local_screening",
        url="https://seeitearly.com/movies/unverified-local-screening",
    )
    preference = MovieUserLocationPreference(USER_ID, "06484", 25)
    rendered: list[MovieTicketDrop] = []
    sent: list[dict[str, object]] = []

    class FakeStore:
        async def list_active_drops(self, *, limit: int):
            assert limit == movie_actions.MAX_MANUAL_RESULT_ACTIONS
            return [national, screening]

    class FakeBot:
        db = object()

    class FakeSession:
        closed = False

    class FakeCog:
        store = FakeStore()
        bot = FakeBot()
        _session = FakeSession()

        async def _build_drop_embeds(self, selected):
            rendered.extend(selected)
            return []

    class FakeUser:
        id = USER_ID

    class FakeFollowup:
        async def send(self, *args, **kwargs) -> None:
            sent.append({"args": args, **kwargs})

    class FakeInteraction:
        user = FakeUser()
        followup = FakeFollowup()

    async def fake_get_location(_db, *, user_id: int):
        assert user_id == USER_ID
        return preference

    async def broken_local_filter(_session, _drops, _preference):
        raise RuntimeError("source unavailable")

    monkeypatch.setattr(movie_actions, "get_movie_user_location", fake_get_location)
    monkeypatch.setattr(movie_actions, "filter_movie_drops_for_location", broken_local_filter)

    await movie_actions._send_personal_movie_results(
        FakeInteraction(),
        FakeCog(),
        show_all=False,
    )

    assert rendered == [national]
    assert len(sent) == 1
    assert "Local screening proof could not be refreshed" in str(sent[0].get("content", ""))
