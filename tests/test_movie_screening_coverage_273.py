from __future__ import annotations

from sniperplug.services.gofobo_screenings import GOFOBO_SOURCE_KEY
from sniperplug.services.movie_screening_coverage import (
    GOFOBO_PROVIDER_HOST,
    coverage_cities_for_source,
    coverage_city_matches_place,
    movie_slug_for_drop,
    parse_seeitearly_movie_coverage_html,
)
from sniperplug.services.movie_ticket_drops import MovieTicketDrop


FIXTURE = """
<!doctype html>
<html>
<head><title>SeeItEarly</title></head>
<body>
<h1>Forgotten Island</h1>
<h2>29 screenings</h2>
<div>
  <span>Miami Free</span>
  <a href="https://gofobo.com/claim/miami">Get free pass (opens in new tab)</a>
  <a href="/movies/forgotten-island/in/miami-fl">View 1 screening in Miami→</a>
</div>
<div>
  <span>Boston Free</span>
  <a href="https://www.meetup.com/event/boston">Get free pass (opens in new tab)</a>
  <a href="/movies/forgotten-island/in/boston-ma">View 1 screening in Boston→</a>
</div>
<div>
  <span>Somerville Free</span>
  <a href="https://www.gofobo.com/claim/somerville">Get free pass (opens in new tab)</a>
  <a href="/movies/forgotten-island/in/somerville-ma">View 1 screening in Somerville→</a>
</div>
<div>
  <span>Los Angeles Paid</span>
  <a href="https://tickets.example.com/la">Buy tickets</a>
  <a href="/movies/forgotten-island/in/los-angeles-ca">View 1 screening in Los Angeles→</a>
</div>
</body>
</html>
"""


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


def test_movie_coverage_uses_city_state_urls_and_only_explicit_free_pass_blocks() -> None:
    coverage = parse_seeitearly_movie_coverage_html(FIXTURE, movie_slug="forgotten-island")

    assert coverage.document_valid is True
    by_slug = {city.city_slug: city for city in coverage.cities}
    assert set(by_slug) == {"miami-fl", "boston-ma", "somerville-ma"}
    assert by_slug["miami-fl"].provider_hosts == (GOFOBO_PROVIDER_HOST,)
    assert by_slug["boston-ma"].provider_hosts == ("meetup.com",)
    assert by_slug["somerville-ma"].provider_hosts == (GOFOBO_PROVIDER_HOST,)
    assert "los-angeles-ca" not in by_slug


def test_gofobo_coverage_keeps_only_cities_with_a_gofobo_free_pass_link() -> None:
    coverage = parse_seeitearly_movie_coverage_html(FIXTURE, movie_slug="forgotten-island")
    gofobo = coverage_cities_for_source(coverage, source_key=GOFOBO_SOURCE_KEY)

    assert [city.city_slug for city in gofobo] == ["miami-fl", "somerville-ma"]


def test_city_match_requires_both_city_and_state() -> None:
    coverage = parse_seeitearly_movie_coverage_html(FIXTURE, movie_slug="forgotten-island")
    miami = next(city for city in coverage.cities if city.city_slug == "miami-fl")

    assert coverage_city_matches_place(miami, place_city="Miami", place_state="FL") is True
    assert coverage_city_matches_place(miami, place_city="Miami", place_state="OH") is False


def test_gofobo_drop_uses_title_slug_instead_of_numeric_detail_url() -> None:
    drop = _drop(
        source_key=GOFOBO_SOURCE_KEY,
        title="FORGOTTEN ISLAND",
        url="https://gofobo.com/main/local_movie_screenings/6483",
    )
    assert movie_slug_for_drop(drop) == "forgotten-island"


def test_seeitearly_drop_uses_canonical_movie_slug() -> None:
    drop = _drop(
        source_key="seeitearly_public_discovery",
        title="Oasis: Don't Look Back in Anger",
        url="https://seeitearly.com/movies/oasis-dont-look-back-in-anger",
    )
    assert movie_slug_for_drop(drop) == "oasis-dont-look-back-in-anger"
