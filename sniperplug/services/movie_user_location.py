from __future__ import annotations

import asyncio
import math
import re
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any
from urllib.parse import quote, urljoin, urlparse

import aiohttp

from sniperplug.services.movie_ticket_drops import MovieTicketDrop, normalize_text
from sniperplug.services.movie_ticket_snowflake_store import snowflake_text
from sniperplug.services.seeitearly_screenings import (
    SEEITEARLY_ALLOWED_HOSTS,
    SEEITEARLY_HOME_URL,
    SEEITEARLY_SOURCE_KEY,
)


MOVIE_LOCATION_RADII = (10, 25, 50, 100)
DEFAULT_MOVIE_LOCATION_RADIUS = 25
ZIP_LOOKUP_BASE_URL = "https://api.zippopotam.us"
ZIP_LOOKUP_TIMEOUT_SECONDS = 8
SEEITEARLY_CITY_TIMEOUT_SECONDS = 10
MAX_NEARBY_POSTAL_PLACES = 11
MAX_CITY_PAGES_PER_LOOKUP = 8
LOCATION_CACHE_SECONDS = 30 * 60
CITY_PAGE_CACHE_SECONDS = 10 * 60

_ZIP_RE = re.compile(r"^\d{5}(?:-\d{4})?$")
_SLUG_RE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True, slots=True)
class MovieUserLocationPreference:
    user_id: int
    zip_code: str
    radius_miles: int = DEFAULT_MOVIE_LOCATION_RADIUS


@dataclass(frozen=True, slots=True)
class MovieZipPlace:
    city: str
    state: str
    latitude: float
    longitude: float
    distance_miles: float = 0.0


@dataclass(frozen=True, slots=True)
class MovieLocalFilterResult:
    drops: tuple[MovieTicketDrop, ...]
    matched_screening_count: int
    hidden_screening_count: int
    searched_places: tuple[MovieZipPlace, ...]
    note: str = ""


_LOCATION_CACHE: dict[str, tuple[float, MovieZipPlace, tuple[MovieZipPlace, ...]]] = {}
_CITY_PAGE_CACHE: dict[str, tuple[float, frozenset[str]]] = {}
_CACHE_LOCK = asyncio.Lock()
_SCHEMA_LOCK = asyncio.Lock()


async def ensure_movie_user_location_table(db: Any) -> None:
    if bool(getattr(db, "_movie_user_location_schema_ready", False)):
        return
    async with _SCHEMA_LOCK:
        if bool(getattr(db, "_movie_user_location_schema_ready", False)):
            return
        conn = db.require_conn()
        await conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS movie_ticket_user_location_v2 (
                user_id TEXT PRIMARY KEY,
                zip_code TEXT NOT NULL,
                radius_miles INTEGER NOT NULL DEFAULT 25,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        await conn.commit()
        setattr(db, "_movie_user_location_schema_ready", True)


async def get_movie_user_location(
    db: Any,
    *,
    user_id: int,
) -> MovieUserLocationPreference | None:
    await ensure_movie_user_location_table(db)
    conn = db.require_conn()
    cursor = await conn.execute(
        """
        SELECT user_id, zip_code, radius_miles
        FROM movie_ticket_user_location_v2
        WHERE user_id = ?
        """,
        (snowflake_text(user_id),),
    )
    row = await cursor.fetchone()
    if not row:
        return None
    return MovieUserLocationPreference(
        user_id=int(_row_value(row, "user_id")),
        zip_code=normalize_movie_zip(str(_row_value(row, "zip_code") or "")),
        radius_miles=normalize_movie_radius(_row_value(row, "radius_miles")),
    )


async def save_movie_user_location(
    db: Any,
    *,
    user_id: int,
    zip_code: str,
    radius_miles: int = DEFAULT_MOVIE_LOCATION_RADIUS,
) -> MovieUserLocationPreference:
    await ensure_movie_user_location_table(db)
    normalized_zip = normalize_movie_zip(zip_code)
    normalized_radius = normalize_movie_radius(radius_miles)
    preference = MovieUserLocationPreference(
        user_id=int(snowflake_text(user_id)),
        zip_code=normalized_zip,
        radius_miles=normalized_radius,
    )
    conn = db.require_conn()
    await conn.execute(
        """
        INSERT INTO movie_ticket_user_location_v2 (
            user_id, zip_code, radius_miles, created_at, updated_at
        ) VALUES (?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        ON CONFLICT(user_id) DO UPDATE SET
            zip_code = excluded.zip_code,
            radius_miles = excluded.radius_miles,
            updated_at = CURRENT_TIMESTAMP
        """,
        (snowflake_text(preference.user_id), preference.zip_code, preference.radius_miles),
    )
    await conn.commit()
    verified = await get_movie_user_location(db, user_id=preference.user_id)
    if verified != preference:
        raise RuntimeError("Movie ZIP preference failed its exact database round-trip check.")
    return preference


async def clear_movie_user_location(db: Any, *, user_id: int) -> None:
    await ensure_movie_user_location_table(db)
    conn = db.require_conn()
    await conn.execute(
        "DELETE FROM movie_ticket_user_location_v2 WHERE user_id = ?",
        (snowflake_text(user_id),),
    )
    await conn.commit()


def normalize_movie_zip(value: str) -> str:
    text = str(value or "").strip()
    if not _ZIP_RE.fullmatch(text):
        raise ValueError("Enter a valid 5-digit US ZIP code (ZIP+4 is also accepted).")
    return text[:5]


def normalize_movie_radius(value: Any) -> int:
    try:
        radius = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError("Radius must be 10, 25, 50, or 100 miles.") from error
    if radius not in MOVIE_LOCATION_RADII:
        raise ValueError("Radius must be 10, 25, 50, or 100 miles.")
    return radius


async def filter_movie_drops_for_location(
    session: aiohttp.ClientSession,
    drops: list[MovieTicketDrop],
    preference: MovieUserLocationPreference,
) -> MovieLocalFilterResult:
    """Return a conservative local view of manual movie results.

    Global/reusable promotions remain visible. A screening row requires positive
    free + local evidence. SeeItEarly can be matched against the free entries in
    its public city pages; title-only screening rows from other sources remain
    available through Show All when their current feed lacks distance-safe proof.
    """

    primary, nearby = await _resolve_zip_places(session, preference)
    candidate_places = tuple(
        place for place in nearby if place.distance_miles <= preference.radius_miles
    )[:MAX_CITY_PAGES_PER_LOOKUP]
    if not candidate_places:
        candidate_places = (primary,)

    local_movie_slugs: set[str] = set()
    successful_places: list[MovieZipPlace] = []
    for place in candidate_places:
        slugs = await _fetch_seeitearly_city_movie_slugs(session, place)
        if slugs is None:
            continue
        successful_places.append(place)
        local_movie_slugs.update(slugs)

    kept: list[MovieTicketDrop] = []
    matched_screenings = 0
    hidden_screenings = 0
    for drop in drops:
        if drop.classification != "local_screening":
            kept.append(drop)
            continue
        if drop.source_key == SEEITEARLY_SOURCE_KEY and _drop_movie_slug(drop) in local_movie_slugs:
            kept.append(drop)
            matched_screenings += 1
            continue
        hidden_screenings += 1

    note = ""
    if not successful_places:
        note = (
            "No SeeItEarly city page could be confirmed for the nearby postal places, "
            "so distance-unverified screenings are hidden in Local mode."
        )
    elif matched_screenings == 0:
        note = (
            "No connected free SeeItEarly screening title matched a confirmed city page "
            f"within {preference.radius_miles} miles of ZIP {preference.zip_code}."
        )

    return MovieLocalFilterResult(
        drops=tuple(kept),
        matched_screening_count=matched_screenings,
        hidden_screening_count=hidden_screenings,
        searched_places=tuple(successful_places),
        note=note,
    )


async def validate_movie_zip_live(
    session: aiohttp.ClientSession,
    zip_code: str,
) -> MovieZipPlace:
    normalized = normalize_movie_zip(zip_code)
    primary, _nearby = await _lookup_zip_places_uncached(session, normalized)
    return primary


async def _resolve_zip_places(
    session: aiohttp.ClientSession,
    preference: MovieUserLocationPreference,
) -> tuple[MovieZipPlace, tuple[MovieZipPlace, ...]]:
    now = time.monotonic()
    async with _CACHE_LOCK:
        cached = _LOCATION_CACHE.get(preference.zip_code)
        if cached and cached[0] > now:
            return cached[1], cached[2]

    primary, nearby = await _lookup_zip_places_uncached(session, preference.zip_code)
    async with _CACHE_LOCK:
        _LOCATION_CACHE[preference.zip_code] = (
            now + LOCATION_CACHE_SECONDS,
            primary,
            nearby,
        )
    return primary, nearby


async def _lookup_zip_places_uncached(
    session: aiohttp.ClientSession,
    zip_code: str,
) -> tuple[MovieZipPlace, tuple[MovieZipPlace, ...]]:
    primary_payload = await _get_json(session, f"{ZIP_LOOKUP_BASE_URL}/us/{quote(zip_code)}")
    primary = _place_from_zip_payload(primary_payload)
    if primary is None:
        raise ValueError("That ZIP code could not be resolved to a US city.")

    nearby_places: list[MovieZipPlace] = [primary]
    try:
        nearby_payload = await _get_json(
            session,
            f"{ZIP_LOOKUP_BASE_URL}/nearby/us/{quote(zip_code)}",
        )
    except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError, ValueError):
        nearby_payload = None

    seen = {_place_identity(primary)}
    for raw in _iter_place_rows(nearby_payload):
        place = _place_from_row(raw)
        if place is None:
            continue
        distance = _haversine_miles(
            primary.latitude,
            primary.longitude,
            place.latitude,
            place.longitude,
        )
        candidate = MovieZipPlace(
            city=place.city,
            state=place.state,
            latitude=place.latitude,
            longitude=place.longitude,
            distance_miles=distance,
        )
        identity = _place_identity(candidate)
        if identity not in seen:
            nearby_places.append(candidate)
            seen.add(identity)
        if len(nearby_places) >= MAX_NEARBY_POSTAL_PLACES + 1:
            break

    nearby_places.sort(key=lambda item: item.distance_miles)
    return primary, tuple(nearby_places)


async def _get_json(session: aiohttp.ClientSession, url: str) -> Any:
    timeout = aiohttp.ClientTimeout(total=ZIP_LOOKUP_TIMEOUT_SECONDS, connect=4)
    async with session.get(
        url,
        headers={"Accept": "application/json", "User-Agent": "SniperPlug/1.0 MovieLocation"},
        timeout=timeout,
        allow_redirects=True,
    ) as response:
        if response.status == 404:
            raise ValueError("That ZIP code could not be resolved.")
        if response.status != 200:
            raise RuntimeError(f"ZIP lookup returned HTTP {response.status}.")
        return await response.json(content_type=None)


def _place_from_zip_payload(payload: Any) -> MovieZipPlace | None:
    for row in _iter_place_rows(payload):
        place = _place_from_row(row)
        if place is not None:
            return place
    return None


def _iter_place_rows(payload: Any) -> tuple[dict[str, Any], ...]:
    if isinstance(payload, dict):
        for key in ("places", "nearby", "results", "data"):
            rows = payload.get(key)
            if isinstance(rows, list):
                return tuple(row for row in rows if isinstance(row, dict))
    if isinstance(payload, list):
        return tuple(row for row in payload if isinstance(row, dict))
    return ()


def _place_from_row(row: dict[str, Any]) -> MovieZipPlace | None:
    city = str(
        row.get("place name")
        or row.get("place_name")
        or row.get("name")
        or row.get("city")
        or ""
    ).strip()
    state = str(
        row.get("state abbreviation")
        or row.get("state_abbreviation")
        or row.get("state code")
        or row.get("state_code")
        or ""
    ).strip().upper()
    try:
        latitude = float(row.get("latitude", row.get("lat")))
        longitude = float(row.get("longitude", row.get("lon", row.get("lng"))))
    except (TypeError, ValueError):
        return None
    if not city or len(state) != 2:
        return None
    return MovieZipPlace(city=city, state=state, latitude=latitude, longitude=longitude)


class _SeeItEarlyCityParser(HTMLParser):
    """Collect only explicitly free movie links from the city's playing section."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.movie_slugs: set[str] = set()
        self._heading_tag = ""
        self._heading_parts: list[str] = []
        self._in_playing_section = False
        self._entry_free: bool | None = None
        self._active_movie_slug = ""
        self._active_free: bool | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized_tag = tag.lower()
        if not self._heading_tag and normalized_tag in {"h1", "h2", "h3", "h4"}:
            self._heading_tag = normalized_tag
            self._heading_parts = []

        if normalized_tag != "a" or not self._in_playing_section:
            return
        data = {str(key).lower(): str(value or "") for key, value in attrs}
        absolute = urljoin(SEEITEARLY_HOME_URL, data.get("href", ""))
        parsed = urlparse(absolute)
        if parsed.scheme != "https" or (parsed.hostname or "").lower() not in SEEITEARLY_ALLOWED_HOSTS:
            return
        parts = [part for part in (parsed.path or "").split("/") if part]
        if len(parts) == 2 and parts[0].lower() == "movies":
            self._active_movie_slug = _normalize_slug(parts[1])
            self._active_free = self._entry_free

    def handle_data(self, data: str) -> None:
        text = " ".join(str(data or "").split())
        if not text:
            return
        if self._heading_tag:
            self._heading_parts.append(text)
        if not self._in_playing_section:
            return

        normalized = normalize_text(text)
        if normalized == "free" or normalized.startswith("free pass"):
            self._entry_free = True
            if self._active_movie_slug:
                self._active_free = True
        elif normalized == "paid" or normalized.startswith("buy ticket"):
            self._entry_free = False
            if self._active_movie_slug:
                self._active_free = False

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.lower()
        if self._heading_tag and normalized_tag == self._heading_tag:
            heading = normalize_text(" ".join(self._heading_parts))
            heading_key = heading.replace("'", "").replace("’", "")
            self._heading_tag = ""
            self._heading_parts = []
            if heading_key.startswith("whats playing in"):
                self._in_playing_section = True
                self._entry_free = None
                return
            if self._in_playing_section and (
                heading_key.startswith("film events")
                or heading_key.startswith("where its happening")
                or heading_key.startswith("popular theaters")
                or heading_key.startswith("releasing soon")
            ):
                self._in_playing_section = False
                self._entry_free = None
                self._active_movie_slug = ""
                self._active_free = None
                return

        if normalized_tag == "a" and self._active_movie_slug:
            if self._in_playing_section and self._active_free is True:
                self.movie_slugs.add(self._active_movie_slug)
            self._active_movie_slug = ""
            self._active_free = None
            self._entry_free = None


async def _fetch_seeitearly_city_movie_slugs(
    session: aiohttp.ClientSession,
    place: MovieZipPlace,
) -> frozenset[str] | None:
    city_slug = _city_slug(place)
    if not city_slug:
        return None
    url = f"{SEEITEARLY_HOME_URL.rstrip('/')}/cities/{city_slug}"
    now = time.monotonic()
    async with _CACHE_LOCK:
        cached = _CITY_PAGE_CACHE.get(url)
        if cached and cached[0] > now:
            return cached[1]

    timeout = aiohttp.ClientTimeout(total=SEEITEARLY_CITY_TIMEOUT_SECONDS, connect=4)
    try:
        async with session.get(
            url,
            headers={
                "Accept": "text/html,application/xhtml+xml",
                "User-Agent": "SniperPlug/1.0 MovieTicketMonitor (+https://sniperplug.com)",
            },
            timeout=timeout,
            allow_redirects=True,
        ) as response:
            if response.status == 404:
                slugs = frozenset()
            elif response.status != 200:
                return None
            else:
                final = urlparse(str(response.url))
                if final.scheme != "https" or (final.hostname or "").lower() not in SEEITEARLY_ALLOWED_HOSTS:
                    return None
                html_text = await response.text(errors="replace")
                parser = _SeeItEarlyCityParser()
                parser.feed(html_text)
                parser.close()
                slugs = frozenset(parser.movie_slugs)
    except (aiohttp.ClientError, asyncio.TimeoutError, UnicodeError):
        return None

    async with _CACHE_LOCK:
        _CITY_PAGE_CACHE[url] = (now + CITY_PAGE_CACHE_SECONDS, slugs)
    return slugs


def _drop_movie_slug(drop: MovieTicketDrop) -> str:
    parsed = urlparse(str(drop.offer_url or ""))
    parts = [part for part in (parsed.path or "").split("/") if part]
    if len(parts) >= 2 and parts[-2].lower() == "movies":
        return _normalize_slug(parts[-1])
    return _normalize_slug(normalize_text(drop.title).replace(" ", "-"))


def _city_slug(place: MovieZipPlace) -> str:
    city = _SLUG_RE.sub("-", place.city.lower()).strip("-")
    state = _SLUG_RE.sub("", place.state.lower())
    if not city or len(state) != 2:
        return ""
    return f"{city}-{state}"


def _normalize_slug(value: str) -> str:
    return _SLUG_RE.sub("-", str(value or "").lower()).strip("-")


def _place_identity(place: MovieZipPlace) -> tuple[str, str]:
    return (normalize_text(place.city), place.state.upper())


def _haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    earth_radius_miles = 3958.7613
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    return earth_radius_miles * 2 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1 - a)))


def _row_value(row: Any, key: str) -> Any:
+    try:
+        return row[key]
+    except (KeyError, TypeError, IndexError):
+        return None