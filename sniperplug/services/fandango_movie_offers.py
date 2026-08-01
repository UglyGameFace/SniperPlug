from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import aiohttp

from sniperplug.services.movie_ticket_artwork import normalize_public_code
from sniperplug.services.movie_ticket_drops import (
    MovieTicketDrop,
    MovieTicketSourceState,
    clean_text,
    normalize_text,
)


log = logging.getLogger("sniperplug.movie_tickets.fandango")

FANDANGO_OFFERS_URL = "https://www.fandango.com/offers"
FANDANGO_SOURCE_KEY = "fandango_official_offers"
FANDANGO_SOURCE_LABEL = "Official Fandango Offers"
FANDANGO_ALLOWED_HOSTS = frozenset(
    {
        "fandango.com",
        "www.fandango.com",
        "www.fandangomovietickets.com",
    }
)
FANDANGO_IMAGE_ALLOWED_HOSTS = frozenset({"images.fandango.com"})
FANDANGO_USER_AGENT = "SniperPlug/1.0 MovieTicketMonitor (+https://sniperplug.com)"
FANDANGO_REQUEST_TIMEOUT_SECONDS = 15
FANDANGO_MAX_RESPONSE_BYTES = 3_000_000
FANDANGO_IMAGE_MARKER = "SNIPERPLUG_IMAGE_URL="
FANDANGO_PURCHASE_MARKER = "SNIPERPLUG_PURCHASE_REQUIRED=1"

_CODE_PATTERNS = (
    re.compile(r"\bwith\s+code\s*[:\-]?\s*([A-Z0-9][A-Z0-9-]{3,39})\b", re.I),
    re.compile(r"\buse\s+(?:the\s+)?(?:fandango\s+)?(?:promotional\s+)?code\s*[:\-]?\s*([A-Z0-9][A-Z0-9-]{3,39})\b", re.I),
    re.compile(r"\bpromo(?:tional)?\s+code\s*[:\-]?\s*([A-Z0-9][A-Z0-9-]{3,39})\b", re.I),
    re.compile(r"\bmust\s+use\s+code\s*[:\-]?\s*([A-Z0-9][A-Z0-9-]{3,39})\b", re.I),
)
_GENERIC_CODE_WORDS = frozenset(
    {
        "CHECKOUT",
        "DIRECTLY",
        "FIELD",
        "FANDANGO",
        "GIVEN",
        "PROMO",
        "PROMOTIONAL",
        "RECEIVED",
        "REDEMPTION",
        "REQUIRED",
        "TERMS",
    }
)
_FREE_TICKET_PATTERNS = (
    re.compile(r"\bbuy\s+(?:one|1|\d+)\s+tickets?.*?get\s+(?:one|1)\s+tickets?\s+free\b", re.I),
    re.compile(r"\bget\s+(?:one|1)\s+(?:kids?\s+)?tickets?\s+free\b", re.I),
    re.compile(r"\b(?:one|1)\s+free\s+(?:kids?\s+)?tickets?\b", re.I),
    re.compile(r"\bbogo\b", re.I),
    re.compile(r"\bb\d+g1\b", re.I),
)
_EXCLUDED_TERMS = (
    "chance to win",
    "enter to win",
    "sweepstakes",
    "join fanclub",
    "fanclub summer movie pass",
    "membership",
    "automatically applied",
)


@dataclass(frozen=True, slots=True)
class FandangoOfferParseResult:
    drops: tuple[MovieTicketDrop, ...]
    document_valid: bool
    offers_section_found: bool
    candidate_count: int


@dataclass(frozen=True, slots=True)
class FandangoFetchResult:
    not_modified: bool
    html: str = ""
    etag: str = ""
    last_modified: str = ""
    final_url: str = FANDANGO_OFFERS_URL


@dataclass(frozen=True, slots=True)
class _RawFandangoOffer:
    heading: str
    description: str
    href: str
    image_url: str


class _FandangoOffersParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.offers_section_found = False
        self.in_offers_section = False
        self.blocks: list[_RawFandangoOffer] = []
        self._capture_tag = ""
        self._capture_parts: list[str] = []
        self._capture_href = ""
        self._current_heading = ""
        self._current_description_parts: list[str] = []
        self._current_href = ""
        self._last_link = ""
        self._last_image = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized_tag = tag.lower()
        data = {str(key).lower(): str(value or "") for key, value in attrs}

        if normalized_tag == "a":
            href = data.get("href", "").strip()
            if href:
                self._last_link = href
                if self._capture_tag:
                    self._capture_href = href

        if normalized_tag == "img":
            src = (
                data.get("src", "").strip()
                or data.get("data-src", "").strip()
                or data.get("data-lazy-src", "").strip()
            )
            safe_image = safe_fandango_image_url(src)
            if safe_image:
                self._last_image = safe_image

        if not self._capture_tag and normalized_tag in {"h1", "h2", "h3", "p", "li"}:
            self._capture_tag = normalized_tag
            self._capture_parts = []
            self._capture_href = ""
        elif self._capture_tag and normalized_tag == "br":
            self._capture_parts.append(" ")

    def handle_data(self, data: str) -> None:
        if self._capture_tag:
            self._capture_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.lower()
        if not self._capture_tag or normalized_tag != self._capture_tag:
            return

        capture_tag = self._capture_tag
        text = clean_text(" ".join(self._capture_parts))
        href = self._capture_href
        self._capture_tag = ""
        self._capture_parts = []
        self._capture_href = ""
        if not text:
            return

        normalized = normalize_text(text)
        if capture_tag == "h2":
            if normalized == "special offers":
                self._finish_current()
                self.in_offers_section = True
                self.offers_section_found = True
                return
            if self.in_offers_section:
                self._finish_current()
                self.in_offers_section = False
                return

        if not self.in_offers_section:
            return

        if capture_tag == "h3":
            self._finish_current()
            self._current_heading = text
            self._current_href = href or self._last_link
            self._current_description_parts = []
            return

        if self._current_heading and capture_tag in {"p", "li"}:
            lowered = normalized
            if lowered not in {"learn more", "buy tickets", "get tickets", "join now", "watch now"}:
                self._current_description_parts.append(text)

    def close(self) -> None:
        super().close()
        self._finish_current()

    def _finish_current(self) -> None:
        if not self._current_heading:
            return
        description = clean_text(" ".join(self._current_description_parts))
        self.blocks.append(
            _RawFandangoOffer(
                heading=self._current_heading,
                description=description,
                href=self._current_href,
                image_url=self._last_image,
            )
        )
        self._current_heading = ""
        self._current_description_parts = []
        self._current_href = ""
        self._last_image = ""


def parse_fandango_offers_html(html_text: str) -> FandangoOfferParseResult:
    parser = _FandangoOffersParser()
    try:
        parser.feed(html_text)
        parser.close()
    except Exception:
        log.exception("Fandango offers HTML parsing failed")
        return FandangoOfferParseResult((), False, parser.offers_section_found, len(parser.blocks))

    page_text = normalize_text(html_text)
    document_valid = parser.offers_section_found and "fandango" in page_text and "special offers" in page_text
    if not document_valid:
        return FandangoOfferParseResult((), False, parser.offers_section_found, len(parser.blocks))

    drops: list[MovieTicketDrop] = []
    seen_codes: set[str] = set()
    for block in parser.blocks:
        drop = _drop_from_fandango_block(block)
        if drop is None or drop.code in seen_codes:
            continue
        seen_codes.add(drop.code)
        drops.append(drop)

    drops.sort(key=lambda item: (item.title.lower(), item.code))
    return FandangoOfferParseResult(tuple(drops), True, True, len(parser.blocks))


def _drop_from_fandango_block(block: _RawFandangoOffer) -> MovieTicketDrop | None:
    combined = clean_text(f"{block.heading} {block.description}")
    normalized = normalize_text(combined)
    if any(term in normalized for term in _EXCLUDED_TERMS):
        return None
    if not any(pattern.search(combined) for pattern in _FREE_TICKET_PATTERNS):
        return None

    code = extract_fandango_public_code(combined)
    if not code:
        return None

    purchase_required = any(
        phrase in normalized
        for phrase in (
            "with purchase",
            "buy one",
            "buy 1",
            "buy 2",
            "buy 3",
            "purchase one",
            "purchase two",
            "purchase three",
            "adult ticket",
            "same showtime",
        )
    )
    restrictions: list[str] = []
    if purchase_required:
        restrictions.append("Requires the qualifying ticket purchase described by Fandango; this is not a no-purchase free ticket.")
    restrictions.append(block.description[:700] or "While supplies last; see Fandango's official offer terms.")
    restrictions.append("Fandango offers can end when redemption inventory is exhausted, even before the displayed end date.")

    offer_url = safe_fandango_url(block.href) or FANDANGO_OFFERS_URL
    image_url = safe_fandango_image_url(block.image_url)
    marker_lines = [combined]
    if purchase_required:
        marker_lines.append(FANDANGO_PURCHASE_MARKER)
    if image_url:
        marker_lines.append(f"{FANDANGO_IMAGE_MARKER}{image_url}")

    drop_id = fandango_drop_id(title=block.heading, code=code, description=block.description)
    return MovieTicketDrop(
        drop_id=drop_id,
        source_key=FANDANGO_SOURCE_KEY,
        source_label=FANDANGO_SOURCE_LABEL,
        title=block.heading[:180],
        code=code,
        classification="public_reusable",
        ticket_limit=1,
        offer_url=offer_url,
        validity_text="While supplies last; verify the current dates and eligible showtime in Fandango's official terms.",
        restrictions=tuple(restrictions[:5]),
        raw_text="\n".join(marker_lines)[:8000],
    )


def extract_fandango_public_code(text: str) -> str:
    for pattern in _CODE_PATTERNS:
        for match in pattern.finditer(text):
            candidate = normalize_public_code(match.group(1))
            if candidate and candidate not in _GENERIC_CODE_WORDS and any(char.isalpha() for char in candidate):
                return candidate
    return ""


def fandango_drop_id(*, title: str, code: str, description: str) -> str:
    body = "|".join(
        (
            FANDANGO_SOURCE_KEY,
            normalize_text(title),
            normalize_public_code(code),
            normalize_text(description),
        )
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:32]


def safe_fandango_url(value: str | None) -> str:
    if not value:
        return ""
    absolute = urljoin(FANDANGO_OFFERS_URL, value)
    parsed = urlparse(absolute)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in FANDANGO_ALLOWED_HOSTS:
        return ""
    return absolute


def safe_fandango_image_url(value: str | None) -> str:
    if not value:
        return ""
    absolute = urljoin(FANDANGO_OFFERS_URL, value)
    parsed = urlparse(absolute)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in FANDANGO_IMAGE_ALLOWED_HOSTS:
        return ""
    return absolute


def extract_fandango_image_marker(raw_text: str) -> str:
    for line in str(raw_text or "").splitlines():
        if line.startswith(FANDANGO_IMAGE_MARKER):
            return safe_fandango_image_url(line[len(FANDANGO_IMAGE_MARKER) :])
    return ""


def fandango_purchase_required(raw_text: str) -> bool:
    return FANDANGO_PURCHASE_MARKER in str(raw_text or "")


class FandangoOffersClient:
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session

    async def fetch(self, state: MovieTicketSourceState | None = None) -> FandangoFetchResult:
        headers = {
            "Accept": "text/html,application/xhtml+xml",
            "User-Agent": FANDANGO_USER_AGENT,
        }
        if state and state.etag:
            headers["If-None-Match"] = state.etag
        if state and state.last_modified:
            headers["If-Modified-Since"] = state.last_modified

        timeout = aiohttp.ClientTimeout(total=FANDANGO_REQUEST_TIMEOUT_SECONDS, connect=5)
        async with self.session.get(
            FANDANGO_OFFERS_URL,
            headers=headers,
            timeout=timeout,
            allow_redirects=True,
        ) as response:
            final_url = str(response.url)
            if not safe_fandango_url(final_url):
                raise RuntimeError("Fandango offers request redirected outside the official Fandango allowlist.")
            if response.status == 304:
                return FandangoFetchResult(
                    not_modified=True,
                    etag=response.headers.get("ETag", state.etag if state else ""),
                    last_modified=response.headers.get("Last-Modified", state.last_modified if state else ""),
                    final_url=final_url,
                )
            if response.status != 200:
                raise RuntimeError(f"Official Fandango offers page returned HTTP {response.status}.")

            payload = await response.read()
            if len(payload) > FANDANGO_MAX_RESPONSE_BYTES:
                raise RuntimeError("Official Fandango offers page exceeded the safe response-size limit.")
            charset = response.charset or "utf-8"
            try:
                html_text = payload.decode(charset, errors="replace")
            except LookupError:
                html_text = payload.decode("utf-8", errors="replace")
            return FandangoFetchResult(
                not_modified=False,
                html=html_text,
                etag=response.headers.get("ETag", ""),
                last_modified=response.headers.get("Last-Modified", ""),
                final_url=final_url,
            )
