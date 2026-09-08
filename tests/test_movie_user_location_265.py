from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

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
