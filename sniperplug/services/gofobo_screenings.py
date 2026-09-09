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
        "d21ld3wfckjpu2.cloudfront.net",
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
        "find screening",
        "find more movies and events",
        "get passes",
        "get tickets",
        "learn more",
        "login",
        "more info",
        "more information",
        "redeem",
        "screenings",
        "upcoming screenings & events",
        "view details",
        "view more",
        "watch trailer",
        "watch the trailer",
    }
)
_CTA_SUFFIXES = (
    "learn more",
    "more info",
    "more information",
    "view details",
    "view more",
    "get passes",
    "get tickets",
    "find screenings",
    "find screening",
    "watch trailer",
    "watch the trailer",
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
        self._seen_title_specific_links: set[str] = set()
        self._resolved_title_specific_links: set[str] = set()
        self._active_link = ""
        self._active_link_parts: list[str] = []
        self._active_link_image = ""
        self._capture_heading = ""
        self._heading_parts: list[str] = []
        self._capture_paragraph = False
        self._paragraph_parts: list[str] = []
        self._last_safe_link = ""
        self._last_safe_image = ""
        self._pending_heading_title = ""
        self._pending_heading_image = ""
        self._pending_card_link = ""
        self._pending_card_image = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized_tag = tag.lower()
        data = {str(key).lower(): str(value or "") for key, value in attrs}

        if normalized_tag == "a":
            # A title-specific image card must resolve before another link starts.
            # Keep the earlier URL in the seen set so parse validation fails closed
            # instead of accidentally pairing it with unrelated later text.
            self._pending_card_link = ""
            self._pending_card_image = ""
            href = safe_gofobo_url(data.get("href", ""))
            self._active_link = href
            self._active_link_parts = []
            self._active_link_image = ""
            if href:
                self._last_safe_link = href
                if self.in_upcoming_section and gofobo_offer_is_title_specific(href):
                    self._seen_title_specific_links.add(href)
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

        if not self._capture_heading and normalized_tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._pending_card_link = ""
            self._pending_card_image = ""
            self._capture_heading = normalized_tag
            self._heading_parts = []
            return

        if self.in_upcoming_section and self._pending_card_link and normalized_tag == "p":
            self._capture_paragraph = True
            self._paragraph_parts = []

    def handle_data(self, data: str) -> None:
        if self._active_link:
            self._active_link_parts.append(data)
        if self._capture_heading:
            self._heading_parts.append(data)
        if self._capture_paragraph:
            self._paragraph_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.lower()
        if normalized_tag == "a" and self._active_link:
            raw_title = clean_text(" ".join(self._active_link_parts))
            cleaned_title = clean_gofobo_candidate_title(raw_title)
            if self.in_upcoming_section:
                if cleaned_title:
                    self._add_candidate(
                        title=cleaned_title,
                        href=self._active_link,
                        image_url=self._active_link_image or self._last_safe_image,
                    )
                    if normalize_text(cleaned_title) == normalize_text(self._pending_heading_title):
                        self._pending_heading_title = ""
                        self._pending_heading_image = ""
                elif self._pending_heading_title:
                    # Current Gofobo cards can expose the movie/event title in a
                    # heading and use a generic CTA such as "LEARN MORE" for the
                    # actual detail link. Pair those explicitly rather than
                    # turning the CTA into a fake movie title.
                    self._add_candidate(
                        title=self._pending_heading_title,
                        href=self._active_link,
                        image_url=(
                            self._active_link_image
                            or self._pending_heading_image
                            or self._last_safe_image
                        ),
                    )
                    self._pending_heading_title = ""
                    self._pending_heading_image = ""
                elif gofobo_offer_is_title_specific(self._active_link):
                    # Current production Gofobo uses an image-only detail anchor
                    # followed by a sibling <p> containing the title. Keep this
                    # one exact link pending until that immediate text boundary.
                    self._pending_card_link = self._active_link
                    self._pending_card_image = self._active_link_image or self._last_safe_image
            self._active_link = ""
            self._active_link_parts = []
            self._active_link_image = ""
            return

        if self._capture_paragraph and normalized_tag == "p":
            raw_title = clean_text(" ".join(self._paragraph_parts))
            self._capture_paragraph = False
            self._paragraph_parts = []
            if self.in_upcoming_section and self._pending_card_link:
                title = clean_gofobo_candidate_title(raw_title)
                if title:
                    self._add_candidate(
                        title=title,
                        href=self._pending_card_link,
                        image_url=self._pending_card_image or self._last_safe_image,
                    )
                self._pending_card_link = ""
                self._pending_card_image = ""
            return

        if self._capture_heading and normalized_tag == self._capture_heading:
            raw_title = clean_text(" ".join(self._heading_parts))
            title = clean_gofobo_candidate_title(raw_title)
            self._capture_heading = ""
            self._heading_parts = []
            normalized = normalize_text(raw_title)
            if normalized == "upcoming screenings & events":
                self.in_upcoming_section = True
                self.upcoming_section_found = True
                self._pending_heading_title = ""
                self._pending_heading_image = ""
                self._pending_card_link = ""
                self._pending_card_image = ""
                return
            if self.in_upcoming_section and normalized.startswith("browse our newly added"):
                self.in_upcoming_section = False
                self._pending_heading_title = ""
                self._pending_heading_image = ""
                self._pending_card_link = ""
                self._pending_card_image = ""
                return
            if self.in_upcoming_section and title:
                # Keep a safe title-only fallback even if the card-specific URL
                # is represented later by a generic CTA. The CTA handler above
                # will add the same title with the specific URL and parse-time
                # coalescing will prefer that version.
                self._pending_heading_title = title
                self._pending_heading_image = self._last_safe_image
                self._add_candidate(
                    title=title,
                    href="",
                    image_url=self._last_safe_image,
                )

    def _add_candidate(self, *, title: str, href: str, image_url: str) -> None:
        cleaned_title = clean_gofobo_candidate_title(title)
        normalized = normalize_text(cleaned_title)
        if not cleaned_title or normalized in _GENERIC_TITLES:
            return
        if len(cleaned_title) < 2 or len(cleaned_title) > 180:
            return
        candidate_href = safe_gofobo_url(href)
        if not candidate_href:
            candidate_href = GOFOBO_LOCAL_SCREENINGS_URL
        candidate = _RawGofoboScreening(
            title=cleaned_title,
            href=candidate_href,
            image_url=safe_gofobo_image_url(image_url),
        )
        if candidate not in self.blocks:
            self.blocks.append(candidate)
        if gofobo_offer_is_title_specific(candidate_href):
            self._resolved_title_specific_links.add(candidate_href)


def clean_gofobo_candidate_title(value: str) -> str:
    """Return a movie/event title without generic card CTA text."""

    cleaned = collapse_repeated_title(clean_text(value))
    if not cleaned:
        return ""
    if normalize_text(cleaned) in _GENERIC_TITLES:
        return ""

    for suffix in _CTA_SUFFIXES:
        cleaned = re.sub(
            rf"(?:\s*[-|•:]\s*|\s+){re.escape(suffix)}\s*$",
            "",
            cleaned,
            flags=re.IGNORECASE,
        ).strip()
    cleaned = collapse_repeated_title(cleaned)
    if normalize_text(cleaned) in _GENERIC_TITLES:
        return ""
    return cleaned


def collapse_repeated_title(value: str) -> str:
    """Collapse card text such as ``MUTINY MUTINY`` to one movie title."""

    cleaned = clean_text(value)
    words = cleaned.split()
    if len(words) >= 2 and len(words) % 2 == 0:
        midpoint = len(words) // 2
        if [part.casefold() for part in words[:midpoint]] == [
            part.casefold() for part in words[midpoint:]
        ]:
            return " ".join(words[:midpoint])
    return cleaned


def parse_gofobo_home_html(html_text: str) -> GofoboParseResult:
    parser = _GofoboUpcomingParser()
    try:
        parser.feed(html_text)
        parser.close()
    except Exception:
        log.exception("Gofobo homepage parsing failed")
        return GofoboParseResult((), False, parser.upcoming_section_found, len(parser.blocks))

    page_text = normalize_text(html_text)
    shell_valid = (
        parser.upcoming_section_found
        and "gofobo" in page_text
        and "sweepstakes" in page_text
    )
    extraction_consistent = parser._seen_title_specific_links.issubset(
        parser._resolved_title_specific_links
    )
    document_valid = shell_valid and extraction_consistent
    if not document_valid:
        return GofoboParseResult((), False, parser.upcoming_section_found, len(parser.blocks))

    by_title: dict[str, MovieTicketDrop] = {}
    for block in parser.blocks:
        drop = _drop_from_gofobo_block(block)
        if drop is None:
            continue
        key = normalize_text(drop.title)
        existing = by_title.get(key)
        if existing is None or (
            gofobo_offer_is_title_specific(drop.offer_url)
            and not gofobo_offer_is_title_specific(existing.offer_url)
        ):
            by_title[key] = drop

    drops = sorted(by_title.values(), key=lambda item: item.title.lower())
    return GofoboParseResult(tuple(drops), True, True, len(parser.blocks))


def _drop_from_gofobo_block(block: _RawGofoboScreening) -> MovieTicketDrop | None:
    title = clean_gofobo_candidate_title(block.title)
    if not title:
        return None
    offer_url = safe_gofobo_url(block.href) or GOFOBO_LOCAL_SCREENINGS_URL

    public_code = extract_gofobo_public_code(offer_url)
    raw_lines = [f"Official upcoming listing: {title}"]
    if public_code:
        raw_lines.append(GOFOBO_PUBLIC_CODE_MARKER)
    image_url = safe_gofobo_image_url(block.image_url)
    if image_url:
        raw_lines.append(f"{GOFOBO_IMAGE_MARKER}{image_url}")

    restrictions = (
        "Availability is ZIP/postal-code specific; Gofobo can show different theaters or no screening in your area.",
        "Pass inventory can fill quickly, and a claimed pass still does not guarantee admission after capacity is reached.",
    )
    return MovieTicketDrop(
        drop_id=gofobo_drop_id(title=title, offer_url=offer_url),
        source_key=GOFOBO_SOURCE_KEY,
        source_label=GOFOBO_SOURCE_LABEL,
        title=title[:180],
        code=public_code,
        classification="local_screening",
        ticket_limit=1,
        offer_url=offer_url,
        validity_text=(
            "Gofobo lists this title as an upcoming screening. Open the official listing and enter your ZIP/postal code "
            "for the actual theater, date, time, and remaining pass availability."
        ),
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


def gofobo_offer_is_title_specific(url: str | None) -> bool:
    safe_url = safe_gofobo_url(url)
    if not safe_url:
        return False
    path = unquote(urlparse(safe_url).path or "").strip("/")
    normalized_path = path.lower()
    if re.fullmatch(r"main/local_movie_screenings/\d+", normalized_path):
        return True
    if normalized_path.startswith("main/blp/") and len(path.split("/")) >= 3:
        return True
    if path and "/" not in path and normalized_path not in _RESERVED_PATHS:
        return bool(re.fullmatch(r"[A-Za-z0-9_-]{4,40}", path))
    return False


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
    # On the first successful Gofobo request of each process, intentionally omit
    # persisted validators. Parser upgrades must get one real response body so a
    # previously accepted bad durable row (for example title="LEARN MORE") can
    # be replaced immediately after deployment instead of being blessed forever
    # by a 304. Normal ETag/Last-Modified requests resume after that one body.
    _process_full_body_confirmed = False

    def __init__(self, session: aiohttp.ClientSession):
        self.session = session

    async def fetch(self, state: MovieTicketSourceState | None = None) -> GofoboFetchResult:
        force_full_body = not type(self)._process_full_body_confirmed
        headers = {
            "Accept": "text/html,application/xhtml+xml",
            "User-Agent": GOFOBO_USER_AGENT,
        }
        if not force_full_body and state and state.etag:
            headers["If-None-Match"] = state.etag
        if not force_full_body and state and state.last_modified:
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
                if force_full_body:
                    raise RuntimeError(
                        "Official Gofobo returned HTTP 304 to the required full-body cache refresh."
                    )
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
            type(self)._process_full_body_confirmed = True
            return GofoboFetchResult(
                not_modified=False,
                html=html_text,
                etag=response.headers.get("ETag", ""),
                last_modified=response.headers.get("Last-Modified", ""),
                final_url=final_url,
            )
