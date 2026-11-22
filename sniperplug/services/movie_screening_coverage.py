from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import unquote, urljoin, urlparse

import aiohttp

from sniperplug.services.movie_ticket_drops import MovieTicketDrop, clean_text, normalize_text
from sniperplug.services.seeitearly_screenings import (
    SEEITEARLY_ALLOWED_HOSTS,
    SEEITEARLY_HOME_URL,
    SEEITEARLY_MAX_RESPONSE_BYTES,
    SEEITEARLY_REQUEST_TIMEOUT_SECONDS,
    SEEITEARLY_USER_AGENT,
)


COVERAGE_CACHE_SECONDS = 10 * 60
GOFOBO_PROVIDER_HOST = "gofobo.com"
_CITY_STATE_RE = re.compile(r"^(?P<city>.+)-(?P<state>[a-z]{2})$", re.IGNORECASE)
_SLUG_RE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True, slots=True)
class MovieScreeningCity:
    city: str
    state: str
    city_slug: str
    provider_hosts: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MovieScreeningCoverage:
    movie_slug: str
    cities: tuple[MovieScreeningCity, ...]
    document_valid: bool


_COVERAGE_CACHE: dict[str, tuple[float, MovieScreeningCoverage | None]] = {}
_COVERAGE_CACHE_LOCK = asyncio.Lock()


def movie_slug_for_drop(drop: MovieTicketDrop) -> str:
    parsed = urlparse(str(drop.offer_url or ""))
    host = (parsed.hostname or "").lower()
    parts = [unquote(part) for part in (parsed.path or "").split("/") if part]
    if host in SEEITEARLY_ALLOWED_HOSTS and len(parts) >= 2 and parts[0].lower() == "movies":
        return _normalize_slug(parts[1])
    return _normalize_slug(drop.title)


def parse_seeitearly_movie_coverage_html(
    html_text: str,
    *,
    movie_slug: str,
) -> MovieScreeningCoverage:
    normalized_slug = _normalize_slug(movie_slug)
    parser = _SeeItEarlyMovieCoverageParser(normalized_slug)
    try:
        parser.feed(html_text)
        parser.close()
    except Exception:
        return MovieScreeningCoverage(normalized_slug, (), False)

    page_text = normalize_text(html_text)
    document_valid = (
        bool(normalized_slug)
        and parser.movie_city_link_count > 0
        and "screening" in page_text
        and "seeitearly" in page_text
    )
    return MovieScreeningCoverage(
        movie_slug=normalized_slug,
        cities=tuple(parser.cities) if document_valid else (),
        document_valid=document_valid,
    )


async def fetch_seeitearly_movie_coverage(
    session: aiohttp.ClientSession,
    drop: MovieTicketDrop,
) -> MovieScreeningCoverage | None:
    movie_slug = movie_slug_for_drop(drop)
    if not movie_slug:
        return None
    url = f"{SEEITEARLY_HOME_URL.rstrip('/')}/movies/{movie_slug}"
    now = time.monotonic()
    async with _COVERAGE_CACHE_LOCK:
        cached = _COVERAGE_CACHE.get(url)
        if cached and cached[0] > now:
            return cached[1]

    coverage: MovieScreeningCoverage | None = None
    try:
        timeout = aiohttp.ClientTimeout(
            total=SEEITEARLY_REQUEST_TIMEOUT_SECONDS,
            connect=5,
        )
        async with session.get(
            url,
            headers={
                "Accept": "text/html,application/xhtml+xml",
                "User-Agent": SEEITEARLY_USER_AGENT,
            },
            timeout=timeout,
            allow_redirects=True,
        ) as response:
            final = urlparse(str(response.url))
            if (
                response.status == 200
                and final.scheme == "https"
                and (final.hostname or "").lower() in SEEITEARLY_ALLOWED_HOSTS
            ):
                payload = await response.read()
                if len(payload) <= SEEITEARLY_MAX_RESPONSE_BYTES:
                    charset = response.charset or "utf-8"
                    try:
                        html_text = payload.decode(charset, errors="replace")
                    except LookupError:
                        html_text = payload.decode("utf-8", errors="replace")
                    parsed = parse_seeitearly_movie_coverage_html(
                        html_text,
                        movie_slug=movie_slug,
                    )
                    if parsed.document_valid:
                        coverage = parsed
    except Exception:  # noqa: BLE001 - coverage is supplemental; caller fails closed.
        coverage = None

    async with _COVERAGE_CACHE_LOCK:
        _COVERAGE_CACHE[url] = (now + COVERAGE_CACHE_SECONDS, coverage)
    return coverage


def coverage_cities_for_source(
    coverage: MovieScreeningCoverage,
    *,
    source_key: str,
) -> tuple[MovieScreeningCity, ...]:
    if source_key == "gofobo_official_upcoming":
        return tuple(
            city
            for city in coverage.cities
            if GOFOBO_PROVIDER_HOST in city.provider_hosts
        )
    return coverage.cities


def coverage_city_matches_place(city: MovieScreeningCity, *, place_city: str, place_state: str) -> bool:
    return (
        normalize_text(city.city) == normalize_text(place_city)
        and city.state.upper() == str(place_state or "").upper()
    )


class _SeeItEarlyMovieCoverageParser(HTMLParser):
    """Capture free city/provider coverage from one public SeeItEarly movie page.

    SeeItEarly's movie page renders each city's screenings first and then an
    internal ``/movies/<slug>/in/<city-state>`` link. We retain only provider
    hosts from explicit ``Get free pass`` links seen in that city block. The
    city/state identity comes from the internal city URL, avoiding guesses from
    display names such as Franklin or Springfield.
    """

    def __init__(self, movie_slug: str) -> None:
        super().__init__(convert_charrefs=True)
        self.movie_slug = movie_slug
        self.cities: list[MovieScreeningCity] = []
        self.movie_city_link_count = 0
        self._active_href = ""
        self._active_parts: list[str] = []
        self._free_provider_hosts: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        data = {str(key).lower(): str(value or "") for key, value in attrs}
        self._active_href = urljoin(SEEITEARLY_HOME_URL, data.get("href", ""))
        self._active_parts = []

    def handle_data(self, data: str) -> None:
        if self._active_href:
            self._active_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or not self._active_href:
            return

        href = self._active_href
        text = clean_text(" ".join(self._active_parts))
        self._active_href = ""
        self._active_parts = []

        parsed = urlparse(href)
        host = (parsed.hostname or "").lower().removeprefix("www.")
        normalized_text = normalize_text(text)

        if "get free pass" in normalized_text:
            if parsed.scheme == "https" and host:
                self._free_provider_hosts.add(host)
            return

        city = _city_from_movie_city_url(href, movie_slug=self.movie_slug)
        if city is None:
            return
        self.movie_city_link_count += 1

        # A city link closes the city block. Only explicit free-pass providers
        # make that city part of free screening coverage.
        if self._free_provider_hosts:
            candidate = MovieScreeningCity(
                city=city.city,
                state=city.state,
                city_slug=city.city_slug,
                provider_hosts=tuple(sorted(self._free_provider_hosts)),
            )
            if candidate not in self.cities:
                self.cities.append(candidate)
        self._free_provider_hosts.clear()


def _city_from_movie_city_url(url: str, *, movie_slug: str) -> MovieScreeningCity | None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in SEEITEARLY_ALLOWED_HOSTS:
        return None
    parts = [unquote(part) for part in (parsed.path or "").split("/") if part]
    if len(parts) != 4 or parts[0].lower() != "movies" or parts[2].lower() != "in":
        return None
    if _normalize_slug(parts[1]) != _normalize_slug(movie_slug):
        return None

    city_state_slug = _normalize_slug(parts[3])
    match = _CITY_STATE_RE.fullmatch(city_state_slug)
    if not match:
        return None
    city_slug = match.group("city")
    state = match.group("state").upper()
    city = " ".join(part.capitalize() for part in city_slug.split("-") if part)
    if not city or len(state) != 2:
        return None
    return MovieScreeningCity(
        city=city,
        state=state,
        city_slug=city_state_slug,
        provider_hosts=(),
    )


def _normalize_slug(value: str) -> str:
    return _SLUG_RE.sub("-", str(value or "").lower()).strip("-")
