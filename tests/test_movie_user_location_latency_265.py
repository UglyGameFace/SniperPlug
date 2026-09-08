from __future__ import annotations

import asyncio

import pytest

import sniperplug.services.movie_user_location as movie_location
from sniperplug.services.movie_user_location import MovieUserLocationPreference, MovieZipPlace


USER_ID = 1530990344404205792


@pytest.mark.asyncio
async def test_live_zip_validation_populates_cache_for_immediate_local_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary = MovieZipPlace("Shelton", "CT", 41.3047, -73.1294, 0.0)
    nearby = (
        primary,
        MovieZipPlace("Derby", "CT", 41.3207, -73.0890, 2.4),
    )
    calls = 0

    async def fake_lookup(_session, zip_code: str):
        nonlocal calls
        calls += 1
        assert zip_code == "06484"
        return primary, nearby

    monkeypatch.setattr(movie_location, "_lookup_zip_places_uncached", fake_lookup)
    movie_location._LOCATION_CACHE.clear()
    try:
        validated = await movie_location.validate_movie_zip_live(object(), "06484")
        preference = MovieUserLocationPreference(USER_ID, "06484", 25)
        resolved_primary, resolved_nearby = await movie_location._resolve_zip_places(
            object(),
            preference,
        )

        assert validated == primary
        assert resolved_primary == primary
        assert resolved_nearby == nearby
        assert calls == 1
    finally:
        movie_location._LOCATION_CACHE.clear()


@pytest.mark.asyncio
async def test_local_city_page_checks_are_concurrent_but_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    places = tuple(
        MovieZipPlace(f"City {index}", "CT", 41.0 + index / 1000, -73.0, float(index))
        for index in range(6)
    )
    preference = MovieUserLocationPreference(USER_ID, "06484", 100)
    active = 0
    peak = 0

    async def fake_resolve(_session, _preference):
        return places[0], places

    async def fake_city_slugs(_session, _place):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        try:
            await asyncio.sleep(0.02)
            return frozenset()
        finally:
            active -= 1

    monkeypatch.setattr(movie_location, "_resolve_zip_places", fake_resolve)
    monkeypatch.setattr(movie_location, "_fetch_seeitearly_city_movie_slugs", fake_city_slugs)

    result = await movie_location.filter_movie_drops_for_location(
        object(),
        [],
        preference,
    )

    assert result.drops == ()
    assert peak > 1
    assert peak <= movie_location.MAX_CITY_PAGE_CONCURRENCY
    assert peak <= movie_location.MAX_CITY_PAGES_PER_LOOKUP
