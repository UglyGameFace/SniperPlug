from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import aiohttp

from sniperplug.services.gofobo_screenings import (
    GOFOBO_ALLOWED_HOSTS,
    GOFOBO_HOME_URL,
    GOFOBO_MAX_RESPONSE_BYTES,
    GOFOBO_REQUEST_TIMEOUT_SECONDS,
    GOFOBO_USER_AGENT,
)
from sniperplug.services.movie_ticket_drops import clean_text, normalize_text


log = logging.getLogger("sniperplug.movie_tickets.gofobo_details")
US_ZIP_RE = re.compile(r"\b(\d{5})(?:-\d{4})?\b")
_STOP_LOCATION_MARKERS = frozenset(
    {
        "cast",
        "rated",
        "release date",
        "special instructions",
        "synopsis",
        "watch trailer",
        "event partner",
    }
)
_GENERIC_TITLES = frozenset(
    {
        "gofobo",
        "screening missed",
        "this screening has passed",
        "login or create an account",
    }
)


@dataclass(frozen=True, slots=True)
class GofoboScreeningDetail:
    offer_url: str
    title: str = ""
    date_text: str = ""
    time_text: str = ""
    theater_name: str = ""
    address_text: str = ""
    zip_code: str = ""
    availability: str = "unknown"
    active: bool = False
    document_valid: bool = False

    @property
    def location_label(self) -> str:
        parts = [part for part in (self.theater_name, self.address_text) if part]
        return "\n".join(parts)


class _DetailTextParser(HTMLParser):
    CAPTURE_TAGS = frozenset({"h1", "h2", "h3", "h4", "li", "p", "a", "button"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[str] = []
        self.headings: list[str] = []
        self._capture_tag = ""
        self._capture_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized = tag.lower()
        if not self._capture_tag and normalized in self.CAPTURE_TAGS:
            self._capture_tag = normalized
            self._capture_parts = []
        elif self._capture_tag and normalized == "br":
            self._capture_parts.append(" ")

    def handle_data(self, data: str) -> None:
        if self._capture_tag:
            self._capture_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.lower()
        if not self._capture_tag or normalized != self._capture_tag:
            return
        text = clean_text(" ".join(self._capture_parts))
        capture_tag = self._capture_tag
        self._capture_tag = ""
        self._capture_parts = []
        if not text:
            return
        if not self.blocks or normalize_text(self.blocks[-1]) != normalize_text(text):
            self.blocks.append(text)
        if capture_tag in {"h1", "h2"}:
            self.headings.append(text)


def parse_gofobo_screening_detail_html(html_text: str, *, final_url: str) -> GofoboScreeningDetail:
    parser = _DetailTextParser()
    try:
        parser.feed(html_text)
        parser.close()
    except Exception:
        log.exception("Gofobo screening detail parsing failed")
        return GofoboScreeningDetail(offer_url=safe_gofobo_detail_url(final_url))

    page_text = normalize_text(html_text)
    blocks = parser.blocks
    normalized_blocks = [normalize_text(block) for block in blocks]
    date_index = _first_exact_index(normalized_blocks, "date and time")
    location_index = _first_exact_index(normalized_blocks, "location")
    passed = any(
        marker in page_text
        for marker in (
            "this screening has passed",
            "screening has passed",
            "event has ended",
        )
    )
    waitlist = "wait list" in page_text or "waitlist" in page_text
    sold_out = any(marker in page_text for marker in ("sold out", "all passes have been redeemed", "event is full"))
    claimable = any(marker in page_text for marker in ("get my passes", "get passes", "claim passes", "rsvp"))

    date_text = ""
    time_text = ""
    if date_index is not None:
        candidates = _section_values(blocks, date_index + 1, stop_markers={"location"}, limit=4)
        if candidates:
            date_text = candidates[0]
        if len(candidates) > 1:
            time_text = candidates[1]

    theater_name = ""
    address_text = ""
    location_values: list[str] = []
    if location_index is not None:
        location_values = _section_values(
            blocks,
            location_index + 1,
            stop_markers=_STOP_LOCATION_MARKERS,
            limit=5,
        )
        location_values = [
            value
            for value in location_values
            if normalize_text(value) not in {"get my passes", "get passes", "claim passes", "rsvp"}
        ]
        if location_values:
            theater_name = location_values[0]
        if len(location_values) > 1:
            address_text = clean_text(" ".join(location_values[1:]))

    zip_code = extract_us_zip(" ".join(location_values))
    title = _best_title(parser.headings)
    document_valid = bool(
        safe_gofobo_detail_url(final_url)
        and "gofobo" in page_text
        and location_index is not None
        and (date_index is not None or passed)
        and zip_code
    )
    if passed:
        availability = "passed"
    elif sold_out:
        availability = "sold_out"
    elif waitlist:
        availability = "waitlist"
    elif claimable:
        availability = "open"
    else:
        availability = "unknown"
    active = document_valid and not passed and availability not in {"sold_out"}
    return GofoboScreeningDetail(
        offer_url=safe_gofobo_detail_url(final_url),
        title=title,
        date_text=date_text,
        time_text=time_text,
        theater_name=theater_name,
        address_text=address_text,
        zip_code=zip_code,
        availability=availability,
        active=active,
        document_valid=document_valid,
    )


def extract_us_zip(value: str | None) -> str:
    match = US_ZIP_RE.search(clean_text(value))
    return match.group(1) if match else ""


def safe_gofobo_detail_url(value: str | None) -> str:
    if not value:
        return ""
    absolute = urljoin(GOFOBO_HOME_URL, value)
    parsed = urlparse(absolute)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in GOFOBO_ALLOWED_HOSTS:
        return ""
    return absolute


class GofoboScreeningDetailClient:
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session

    async def fetch(self, offer_url: str) -> GofoboScreeningDetail:
        target = safe_gofobo_detail_url(offer_url)
        if not target:
            return GofoboScreeningDetail(offer_url="")
        timeout = aiohttp.ClientTimeout(total=GOFOBO_REQUEST_TIMEOUT_SECONDS, connect=5)
        async with self.session.get(
            target,
            headers={
                "Accept": "text/html,application/xhtml+xml",
                "User-Agent": GOFOBO_USER_AGENT,
            },
            timeout=timeout,
            allow_redirects=True,
        ) as response:
            final_url = str(response.url)
            if not safe_gofobo_detail_url(final_url):
                raise RuntimeError("Gofobo screening detail redirected outside the official allowlist.")
            if response.status != 200:
                return GofoboScreeningDetail(offer_url=final_url)
            payload = await response.read()
            if len(payload) > GOFOBO_MAX_RESPONSE_BYTES:
                raise RuntimeError("Gofobo screening detail exceeded the safe response-size limit.")
            charset = response.charset or "utf-8"
            try:
                html_text = payload.decode(charset, errors="replace")
            except LookupError:
                html_text = payload.decode("utf-8", errors="replace")
            return parse_gofobo_screening_detail_html(html_text, final_url=final_url)


def _first_exact_index(values: list[str], target: str) -> int | None:
    try:
        return values.index(target)
    except ValueError:
        return None


def _section_values(
    blocks: list[str],
    start: int,
    *,
    stop_markers: set[str] | frozenset[str],
    limit: int,
) -> list[str]:
    values: list[str] = []
    for block in blocks[start:]:
        normalized = normalize_text(block)
        if normalized in stop_markers:
            break
        if normalized in {"get my passes", "get passes", "claim passes", "rsvp"}:
            if values:
                break
            continue
        if normalized and block not in values:
            values.append(block)
        if len(values) >= limit:
            break
    return values


def _best_title(headings: list[str]) -> str:
    for heading in headings:
        normalized = normalize_text(heading)
        if normalized and normalized not in _GENERIC_TITLES and len(heading) <= 180:
            return heading
    return ""
