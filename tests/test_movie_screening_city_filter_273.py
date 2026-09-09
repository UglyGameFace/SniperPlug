from __future__ import annotations

import pytest

import sniperplug.services.movie_user_location as movie_location
from sniperplug.services.gofobo_screenings import GOFOBO_SOURCE_KEY
from sniperplug.services.movie_screening_coverage import (
    MovieScreeningCity,
    MovieScreeningCoverage,
)
from sniperplug.services.movie_ticket_drops import MovieTicketDrop
from sniperplug.services.movie_user_location import (
    MovieUserLocationPreference,
    MovieZipPlace,
    filter_movie_drops_for_location,
)
from sniperplug.services.seeitearly_screenings import SEEITEARLY_SOURCE_KEY


USER_ID = 1530990344404205792


def _drop(*, source_key: str, title: str, url: str) -> MovieTicketDrop:
    return MovieTicketDrop(
        drop_id=f"{source_key}:{title}",
        source_key=source_key,
        source_label=source_key,
        title=title,
        code="",
        classification="local_screening",
        ticket_limit=1,
        offer_url=url,
        validity_text="Current public screening",
        restrictions=(),
        raw_text=title,
    )


def _city(city: str, state: str, *, providers: tuple[str, ...]) -> MovieScreeningCity:
    slug = f"{city.lower().replace(' ', '-')}-{state.lower()}"
    return MovieScreeningCity(city, state, slug, providers)


@pytest.mark.asyncio
async def test_gofobo_resident_evil_miami_only_is_hidden_for_connecticut_with_explicit_note(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shelton = MovieZipPlace("Shelton", "CT", 41.3047, -73.1294, 0.0)
    preference = MovieUserLocationPreference(USER_ID, "06484", 25)
    drop = _drop(
        source_key=GOFOBO_SOURCE_KEY,
        title="Resident Evil",
        url="https://gofobo.com/main/local_movie_screenings/7000",
    )

    async def fake_resolve(_session, _preference):
        return shelton, (shelton,)

    async def fake_city_slugs(_session, _place):
        return frozenset()

    async def fake_coverage(_session, _drop):
        return MovieScreeningCoverage(
            "resident-evil",
            (_city("Miami", "FL", providers=("gofobo.com",)),),
            True,
        )

    monkeypatch.setattr(movie_location, "_resolve_zip_places", fake_resolve)
    monkeypatch.setattr(movie_location, "_fetch_seeitearly_city_movie_slugs", fake_city_slugs)
    monkeypatch.setattr(movie_location, "fetch_seeitearly_movie_coverage", fake_coverage)

    result = await filter_movie_drops_for_location(object(), [drop], preference)

    assert result.drops == ()
    assert result.matched_screening_count == 0
    assert result.hidden_screening_count == 1
    assert "Resident Evil: Miami, FL only" in result.note
    assert "ZIP 06484" in result.note


@pytest.mark.asyncio
async def test_gofobo_resident_evil_is_local_for_matching_miami_city_and_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    miami = MovieZipPlace("Miami", "FL", 25.7617, -80.1918, 0.0)
    preference = MovieUserLocationPreference(USER_ID, "33101", 25)
    drop = _drop(
        source_key=GOFOBO_SOURCE_KEY,
        title="Resident Evil",
        url="https://gofobo.com/main/local_movie_screenings/7000",
    )

    async def fake_resolve(_session, _preference):
        return miami, (miami,)

    async def fake_city_slugs(_session, _place):
        return frozenset({"resident-evil"})

    async def fake_coverage(_session, _drop):
        return MovieScreeningCoverage(
            "resident-evil",
            (_city("Miami", "FL", providers=("gofobo.com",)),),
            True,
        )

    monkeypatch.setattr(movie_location, "_resolve_zip_places", fake_resolve)
    monkeypatch.setattr(movie_location, "_fetch_seeitearly_city_movie_slugs", fake_city_slugs)
    monkeypatch.setattr(movie_location, "fetch_seeitearly_movie_coverage", fake_coverage)

    result = await filter_movie_drops_for_location(object(), [drop], preference)

    assert result.drops == (drop,)
    assert result.matched_screening_count == 1
    assert result.hidden_screening_count == 0


@pytest.mark.asyncio
async def test_gofobo_requires_gofobo_provider_proof_not_just_same_title_in_city(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    boston = MovieZipPlace("Boston", "MA", 42.3601, -71.0589, 0.0)
    preference = MovieUserLocationPreference(USER_ID, "02108", 25)
    drop = _drop(
        source_key=GOFOBO_SOURCE_KEY,
        title="Forgotten Island",
        url="https://gofobo.com/main/local_movie_screenings/6483",
    )

    async def fake_resolve(_session, _preference):
        return boston, (boston,)

    async def fake_city_slugs(_session, _place):
        return frozenset({"forgotten-island"})

    async def fake_coverage(_session, _drop):
        return MovieScreeningCoverage(
            "forgotten-island",
            (_city("Boston", "MA", providers=("meetup.com",)),),
            True,
        )

    monkeypatch.setattr(movie_location, "_resolve_zip_places", fake_resolve)
    monkeypatch.setattr(movie_location, "_fetch_seeitearly_city_movie_slugs", fake_city_slugs)
    monkeypatch.setattr(movie_location, "fetch_seeitearly_movie_coverage", fake_coverage)

    result = await filter_movie_drops_for_location(object(), [drop], preference)

    assert result.drops == ()
    assert result.hidden_screening_count == 1


@pytest.mark.asyncio
async def test_seeitearly_keeps_existing_city_page_fallback_when_detail_coverage_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridgeport = MovieZipPlace("Greater Bridgeport", "CT", 41.1792, -73.1894, 0.0)
    preference = MovieUserLocationPreference(USER_ID, "06604", 25)
    drop = _drop(
        source_key=SEEITEARLY_SOURCE_KEY,
        title="Practical Magic 2",
        url="https://seeitearly.com/movies/practical-magic-2",
    )

    async def fake_resolve(_session, _preference):
        return bridgeport, (bridgeport,)

    async def fake_city_slugs(_session, _place):
        return frozenset({"practical-magic-2"})

    async def unavailable_coverage(_session, _drop):
        return None

    monkeypatch.setattr(movie_location, "_resolve_zip_places", fake_resolve)
    monkeypatch.setattr(movie_location, "_fetch_seeitearly_city_movie_slugs", fake_city_slugs)
    monkeypatch.setattr(movie_location, "fetch_seeitearly_movie_coverage", unavailable_coverage)

    result = await filter_movie_drops_for_location(object(), [drop], preference)

    assert result.drops == (drop,)
    assert result.matched_screening_count == 1
    assert result.hidden_screening_count == 0
