from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import unquote, urljoin, urlparse

import aiohttp

from sniperplug.services.movie_ticket_artwork import normalize_public_code
from sniperplug.services.movie_ticket_drops import (
    MovieTicketDrop,
    MovieTicketSourceState,
    clean_text,
    normalize_text,
)


log = logging.getLogger("sniperplug.movie_tickets.gofobo")

GOFOBO_HOME_URL = "https://gofobo.com/"
GOFOBO_LOCAL_SCREENINGS_URL = "https://gofobo.com/main/local_screenings/"
GOFOBO_SOURCE_KEY = "gofobo_official_upcoming"
GOFOBO_SOURCE_LABEL = "Official Gofobo Upcoming Screenings"
GOFOBO_ALLOWED_HOSTS = frozenset({"gofobo.com", "www.gofobo.com"})
GOFOBO_IMAGE_ALLOWED_HOSTS = frozenset(
    {
        "gofobo.com",
        "www.gofobo.com",
        "dk2d6nav3mn9d.cloudfront.net",
    }
)
GOFOBO_USER_AGENT = "SniperPlug/1.0 MovieTicketMonitor (+https://sniperplug.com)"
GOFOBO_REQUEST_TIMEOUT_SECONDS = 15
GOFOBO_MAX_RESPONSE_BYTES = 3_000_000
GOFOBO_IMAGE_MARKER = "SNIPERPLUG_IMAGE_URL="
GOFOBO_PUBLIC_CODE_MARKER = "SNIPERPLUG_PUBLIC_CODE=1"

_RESERVED_PATHS = frozenset(
    {
        "about",
        "account",
        "contact",
        "faq",
        "index.php",
        "login",
        "main",
        "movies",
        "privacy",
        "redeem",
        "rewards",
        "screenings",
        "sweepstakes",
        "terms",
        "trailers",
    }
)
_GENERIC_TITLES = frozenset(
    {
        "find screenings",
        "find more movies and events",
        "login",
        "redeem",
        "screenings",
        "upcoming screenings & events",
    }
)


@dataclass(frozen=True, slots=True)
class GofoboParseResult:
    drops: tuple[MovieTicketDrop, ...]
    document_valid: bool
    upcoming_section_found: bool
    candidate_count: int


@dataclass(frozen=True, slots=True)
class GofoboFetchResult:
    not_modified: bool
    html: str = ""
    etag: str = ""
    last_modified: str = ""
    final_url: str = GOFOBO_HOME_URL


@dataclass(frozen=True, slots=True)
class _RawGofoboScreening:
    title: str
    href: str
    image_url: str


class _GofoboUpcomingParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.upcoming_section_found = False
        self.in_upcoming_section = False
        self.blocks: list[_RawGofoboScreening] = []
        self._active_link = ""
        self._active_link_parts: list[str] = []
        self._active_link_image = ""
        self._capture_heading = ""
        self._heading_parts: list[str] = []
        self._last_safe_link = ""
        self._last_safe_image = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized_tag = tag.lower()
        data = {str(key).lower(): str(value or "") for key, value in attrs}

        if normalized_tag == "a":
            href = safe_gofobo_url(data.get("href", ""))
            self._active_link = href
            self._active_link_parts = []
            self._active_link_image = ""
            if href:
                self._last_safe_link = href
            return

        if normalized_tag == "img":
            src = (
                data.get("src", "").strip()
                or data.get("data-src", "").strip()
                or data.get("data-lazy-src", "").strip()
            )
            safe_image = safe_gofobo_image_url(src)
            if safe_image:
                self._last_safe_image = safe_image
                if self._active_link:
                    self._active_link_image = safe_image
            alt = clean_text(data.get("alt"))
            if self._active_link and alt:
                self._active_link_parts.append(alt)
            return

        if not self._capture_heading and normalized_tag in {"h1", "h2", "h3"}:
            self._capture_heading = normalized_tag
            self._heading_parts = []

    def handle_data(self, data: str) -> None:
        if self._active_link:
            self._active_link_parts.append(data)
        if self._capture_heading:
            self._heading_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.lower()
        if normalized_tag == "a" and self._active_link:
            title = clean_text(" ".join(self._active_link_parts))
            if self.in_upcoming_section:
                self._add_candidate(
                    title=title,
                    href=self._active_link,
                    image_url=self._active_link_image or self._last_safe_image,
                )
            self._active_link = ""
            self._active_link_parts = []
            self._active_link_image = ""
            return

        if self._capture_heading and normalized_tag == self._capture_heading:
            title = clean_text(" ".join(self._heading_parts))
            self._capture_heading = ""
            self._heading_parts = []
            normalized = normalize_text(title)
            if normalized == "upcoming screenings & events":
                self.in_upcoming_section = True
                self.upcoming_section_found = True
                return
            if self.in_upcoming_section and normalized.startswith("browse our newly added"):
                self.in_upcoming_section = False
                return
            if self.in_upcoming_section and normalized_tag == "h3":
                self._add_candidate(
                    title=title,
                    href=self._last_safe_link,
                    image_url=self._last_safe_image,
                )

    def _add_candidate(self, *, title: str, href: str, image_url: str) -> None:
        cleaned_title = clean_text(title)
        normalized = normalize_text(cleaned_title)
        if not cleaned_title or normalized in _GENERIC_TITLES:
            return
        if len(cleaned_title) < 2 or len(cleaned_title) > 180:
            return
        if not href:
            return
        candidate = _RawGofoboScreening(
            title=cleaned_title,
            href=href,
            image_url=safe_gofobo_image_url(image_url),
        )
        if candidate not in self.blocks:
            self.blocks.append(candidate)


def parse_gofobo_home_html(html_text: str) -> GofoboParseResult:
    parser = _GofoboUpcomingParser()
    try:
        parser.feed(html_text)
        parser.close()
    except Exception:
        log.exception("Gofobo homepage parsing failed")
        return GofoboParseResult((), False, parser.upcoming_section_found, len(parser.blocks))

    page_text = normalize_text(html_text)
    document_valid = (
        parser.upcoming_section_found
        and "gofobo" in page_text
        and "upcoming screenings & events" in page_text
        and "sweepstakes" in page_text
    )
    if not document_valid:
        return GofoboParseResult((), False, parser.upcoming_section_found, len(parser.blocks))

    drops: list[MovieTicketDrop] = []
    seen_ids: set[str] = set()
    for block in parser.blocks:
        drop = _drop_from_gofobo_block(block)
        if drop is None or drop.drop_id in seen_ids:
            continue
        seen_ids.add(drop.drop_id)
        drops.append(drop)

    drops.sort(key=lambda item: item.title.lower())
    return GofoboParseResult(tuple(drops), True, True, len(parser.blocks))


def _drop_from_gofobo_block(block: _RawGofoboScreening) -> MovieTicketDrop | None:
    offer_url = safe_gofobo_url(block.href)
    if not offer_url:
        return None

    public_code = extract_gofobo_public_code(offer_url)
    raw_lines = [f"Official upcoming listing: {block.title}"]
    if public_code:
        raw_lines.append(GOFOBO_PUBLIC_CODE_MARKER)
    image_url = safe_gofobo_image_url(block.image_url)
    if image_url:
        raw_lines.append(f"{GOFOBO_IMAGE_MARKER}{image_url}")

    restrictions = (
        "Availability is local: open Gofobo and enter your ZIP/postal code before attempting to claim.",
        "A free Gofobo account and successful pass claim may be required; an announcement is not a guaranteed pass.",
        "Screenings can fill or move to a wait list when all passes are redeemed.",
        "A pass is valid only for its listed theater, date, and time and does not guarantee admission; seating is first come, first served.",
    )
    return MovieTicketDrop(
        drop_id=gofobo_drop_id(title=block.title, offer_url=offer_url),
        source_key=GOFOBO_SOURCE_KEY,
        source_label=GOFOBO_SOURCE_LABEL,
        title=block.title[:180],
        code=public_code,
        classification="local_screening",
        ticket_limit=1,
        offer_url=offer_url,
        validity_text="Local screening availability can change at any time; check your ZIP and claim immediately.",
        restrictions=restrictions,
        raw_text="\n".join(raw_lines)[:8000],
    )


def extract_gofobo_public_code(url: str | None) -> str:
    safe_url = safe_gofobo_url(url)
    if not safe_url:
        return ""
    path = unquote(urlparse(safe_url).path or "").strip("/")
    if not path or "/" in path:
        return ""
    normalized_path = path.lower()
    if normalized_path in _RESERVED_PATHS:
        return ""
    if not re.fullmatch(r"[A-Za-z0-9_-]{4,40}", path):
        return ""
    return normalize_public_code(path)


def gofobo_drop_id(*, title: str, offer_url: str) -> str:
    body = "|".join((GOFOBO_SOURCE_KEY, normalize_text(title), safe_gofobo_url(offer_url)))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:32]


def safe_gofobo_url(value: str | None) -> str:
    if not value:
        return ""
    absolute = urljoin(GOFOBO_HOME_URL, value)
    parsed = urlparse(absolute)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in GOFOBO_ALLOWED_HOSTS:
        return ""
    return absolute


def safe_gofobo_image_url(value: str | None) -> str:
    if not value:
        return ""
    absolute = urljoin(GOFOBO_HOME_URL, value)
    parsed = urlparse(absolute)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in GOFOBO_IMAGE_ALLOWED_HOSTS:
        return ""
    return absolute


def extract_gofobo_image_marker(raw_text: str) -> str:
    for line in str(raw_text or "").splitlines():
        if line.startswith(GOFOBO_IMAGE_MARKER):
            return safe_gofobo_image_url(line[len(GOFOBO_IMAGE_MARKER) :])
    return ""


def gofobo_has_public_code(raw_text: str) -> bool:
    return GOFOBO_PUBLIC_CODE_MARKER in str(raw_text or "")


class GofoboUpcomingClient:
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session

    async def fetch(self, state: MovieTicketSourceState | None = None) -> GofoboFetchResult:
        headers = {
            "Accept": "text/html,application/xhtml+xml",
            "User-Agent": GOFOBO_USER_AGENT,
        }
        if state and state.etag:
            headers["If-None-Match"] = state.etag
        if state and state.last_modified:
            headers["If-Modified-Since"] = state.last_modified

        timeout = aiohttp.ClientTimeout(total=GOFOBO_REQUEST_TIMEOUT_SECONDS, connect=5)
        async with self.session.get(
            GOFOBO_HOME_URL,
            headers=headers,
            timeout=timeout,
            allow_redirects=True,
        ) as response:
            final_url = str(response.url)
            if not safe_gofobo_url(final_url):
                raise RuntimeError("Gofobo request redirected outside the official Gofobo allowlist.")
            if response.status == 304:
                return GofoboFetchResult(
                    not_modified=True,
                    etag=response.headers.get("ETag", state.etag if state else ""),
                    last_modified=response.headers.get("Last-Modified", state.last_modified if state else ""),
                    final_url=final_url,
                )
            if response.status != 200:
                raise RuntimeError(f"Official Gofobo homepage returned HTTP {response.status}.")

            payload = await response.read()
            if len(payload) > GOFOBO_MAX_RESPONSE_BYTES:
                raise RuntimeError("Official Gofobo homepage exceeded the safe response-size limit.")
            charset = response.charset or "utf-8"
            try:
                html_text = payload.decode(charset, errors="replace")
            except LookupError:
                html_text = payload.decode("utf-8", errors="replace")
            return GofoboFetchResult(
                not_modified=False,
                html=html_text,
                etag=response.headers.get("ETag", ""),
                last_modified=response.headers.get("Last-Modified", ""),
                final_url=final_url,
            )
