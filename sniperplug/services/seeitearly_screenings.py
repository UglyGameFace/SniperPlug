from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import unquote, urljoin, urlparse

import aiohttp

from sniperplug.services.movie_ticket_drops import (
    MovieTicketDrop,
    MovieTicketSourceState,
    clean_text,
    normalize_text,
)


log = logging.getLogger("sniperplug.movie_tickets.seeitearly")

SEEITEARLY_UPCOMING_URL = "https://seeitearly.com/upcoming"
SEEITEARLY_HOME_URL = "https://seeitearly.com/"
SEEITEARLY_SOURCE_KEY = "seeitearly_public_discovery"
SEEITEARLY_SOURCE_LABEL = "SeeItEarly Public Screening Discovery"
SEEITEARLY_ALLOWED_HOSTS = frozenset({"seeitearly.com", "www.seeitearly.com"})
SEEITEARLY_USER_AGENT = "SniperPlug/1.0 MovieTicketMonitor (+https://sniperplug.com)"
SEEITEARLY_REQUEST_TIMEOUT_SECONDS = 15
SEEITEARLY_MAX_RESPONSE_BYTES = 3_000_000
# Keep supplemental discovery intentionally small so direct sources remain the
# primary results and the first successful baseline cannot flood a guild.
SEEITEARLY_MAX_ACTIVE_DROPS = 3

# Extra public screening destinations surfaced by the permanent panel. They are
# links only until each site has a stable fail-closed automated parsing contract.
ADVANCE_SCREENINGS_URL = "https://www.advancescreenings.com/"
ONE_IOTA_URL = "https://1iota.com/"

_FREE_PASS_RE = re.compile(r"\b(?P<count>\d{1,4})\s+free\s+pass(?:es)?\b", re.IGNORECASE)
_CARD_PREFIX_RE = re.compile(
    r"^\s*\d{1,4}\s+free\s+pass(?:es)?\s+[A-Za-z]{3}\s+\d{1,2}\s+",
    re.IGNORECASE,
)
_GENERIC_TITLES = frozenset(
    {
        "all months",
        "all",
        "major releases",
        "track",
        "view all upcoming",
        "load more",
        "see screenings",
    }
)


@dataclass(frozen=True, slots=True)
class SeeItEarlyParseResult:
    drops: tuple[MovieTicketDrop, ...]
    document_valid: bool
    candidate_count: int


@dataclass(frozen=True, slots=True)
class SeeItEarlyFetchResult:
    not_modified: bool
    html: str = ""
    etag: str = ""
    last_modified: str = ""
    final_url: str = SEEITEARLY_UPCOMING_URL


@dataclass(frozen=True, slots=True)
class _RawCandidate:
    title: str
    href: str
    context: str
    free_count: int


class _SeeItEarlyUpcomingParser(HTMLParser):
    """Extract bounded title-level free-pass leads from the public upcoming page.

    The page is a national discovery index, not an original pass issuer. We only
    accept movie-detail links paired with explicit ``N free pass(es)`` evidence.
    Paid-only/release-only cards are ignored.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.candidates: list[_RawCandidate] = []
        self._recent_text: list[str] = []
        self._active_href = ""
        self._active_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        data = {str(key).lower(): str(value or "") for key, value in attrs}
        href = safe_seeitearly_url(data.get("href", ""))
        if href and _is_movie_detail_url(href):
            self._active_href = href
            self._active_parts = []

    def handle_data(self, data: str) -> None:
        text = clean_text(data)
        if not text:
            return
        self._recent_text.append(text)
        if len(self._recent_text) > 16:
            self._recent_text = self._recent_text[-16:]
        if self._active_href:
            self._active_parts.append(text)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or not self._active_href:
            return
        raw_link_text = clean_text(" ".join(self._active_parts))
        title = extract_seeitearly_card_title(raw_link_text, self._active_href)
        context = clean_text(" ".join(self._recent_text[-10:]))
        match = _FREE_PASS_RE.search(raw_link_text) or _FREE_PASS_RE.search(context)
        if title and match:
            free_count = int(match.group("count"))
            if free_count > 0:
                candidate = _RawCandidate(
                    title=title,
                    href=self._active_href,
                    context=context,
                    free_count=free_count,
                )
                if candidate not in self.candidates:
                    self.candidates.append(candidate)
        self._active_href = ""
        self._active_parts = []
        # A free-pass count belongs to the movie card/link we just consumed.
        # Clear nearby context so one title cannot donate "free" proof to the
        # next paid/release-only movie.
        self._recent_text = []


def parse_seeitearly_upcoming_html(html_text: str) -> SeeItEarlyParseResult:
    parser = _SeeItEarlyUpcomingParser()
    try:
        parser.feed(html_text)
        parser.close()
    except Exception:
        log.exception("SeeItEarly upcoming-page parsing failed")
        return SeeItEarlyParseResult((), False, len(parser.candidates))

    page_text = normalize_text(html_text)
    document_valid = (
        "releasing soon" in page_text
        and "seeitearly" in page_text
        and ("free pass" in page_text or "/movies/" in html_text.lower())
    )
    if not document_valid:
        return SeeItEarlyParseResult((), False, len(parser.candidates))

    by_title: dict[str, MovieTicketDrop] = {}
    for candidate in parser.candidates:
        key = normalize_text(candidate.title)
        if not key or key in by_title:
            continue
        by_title[key] = _drop_from_candidate(candidate)
        if len(by_title) >= SEEITEARLY_MAX_ACTIVE_DROPS:
            break

    return SeeItEarlyParseResult(tuple(by_title.values()), True, len(parser.candidates))


def extract_seeitearly_card_title(raw_link_text: str, href: str) -> str:
    """Extract exact title text from SeeItEarly's current combined card link.

    Current links can render as ``32 free passes SEP 10 The Uprising Drama ·
    Action · History``. The safe movie URL provides the title slug, so use its
    word count to slice only the title portion after the free-pass/date prefix.
    This preserves page capitalization/punctuation without guessing genre text
    into the title or issuing another HTTP request per movie.
    """

    raw = clean_text(raw_link_text)
    safe_href = safe_seeitearly_url(href)
    if not raw or not safe_href:
        return ""

    without_prefix = _CARD_PREFIX_RE.sub("", raw, count=1).strip()
    slug = unquote((urlparse(safe_href).path or "").rstrip("/").split("/")[-1])
    slug_words = [part for part in slug.split("-") if part]

    if _CARD_PREFIX_RE.match(raw) and slug_words:
        visible_words = without_prefix.split()
        if len(visible_words) >= len(slug_words):
            candidate = " ".join(visible_words[: len(slug_words)])
            # Match punctuation-insensitively, then keep the page's exact visible
            # punctuation/casing rather than converting the SEO slug into a title.
            if _title_identity(candidate) == _title_identity(" ".join(slug_words)):
                return clean_seeitearly_title(candidate)
        # SEO slugs remain safer than treating genres/card chrome as title.
        return clean_seeitearly_title(" ".join(slug_words).title())

    # Test/legacy card shape where the anchor contains only the title.
    return clean_seeitearly_title(raw)


def _title_identity(value: str) -> str:
    return re.sub(
        r"\s+",
        " ",
        re.sub(r"[^a-z0-9]+", " ", clean_text(value).lower()),
    ).strip()


def clean_seeitearly_title(value: str) -> str:
    title = clean_text(value)
    normalized = normalize_text(title)
    if not title or normalized in _GENERIC_TITLES:
        return ""
    if len(title) < 2 or len(title) > 180:
        return ""
    if _FREE_PASS_RE.fullmatch(title):
        return ""
    return title


def _drop_from_candidate(candidate: _RawCandidate) -> MovieTicketDrop:
    count = max(1, candidate.free_count)
    return MovieTicketDrop(
        drop_id=seeitearly_drop_id(title=candidate.title, offer_url=candidate.href),
        source_key=SEEITEARLY_SOURCE_KEY,
        source_label=SEEITEARLY_SOURCE_LABEL,
        title=candidate.title[:180],
        code="",
        classification="local_screening",
        ticket_limit=1,
        offer_url=candidate.href,
        validity_text=(
            f"SeeItEarly currently reports {count} free screening pass"
            f"{'es' if count != 1 else ''} for this title across its public index. "
            "Open the listing for current cities, venues, dates, and the original claim provider."
        ),
        restrictions=(
            "Supplemental discovery only: SeeItEarly aggregates multiple screening providers and is not the original pass issuer.",
            "Verify the city, venue, date/time, eligibility, and remaining pass inventory on the linked listing and its original provider before traveling.",
        ),
        raw_text=(
            "Supplemental aggregated discovery; no gated code republished.\n"
            f"FREE_PASS_COUNT={count}\n"
            f"Context: {candidate.context[:1200]}"
        )[:8000],
    )


def seeitearly_drop_id(*, title: str, offer_url: str) -> str:
    body = "|".join((SEEITEARLY_SOURCE_KEY, normalize_text(title), safe_seeitearly_url(offer_url)))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:32]


def safe_seeitearly_url(value: str | None) -> str:
    if not value:
        return ""
    absolute = urljoin(SEEITEARLY_HOME_URL, value)
    parsed = urlparse(absolute)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in SEEITEARLY_ALLOWED_HOSTS:
        return ""
    return absolute


def _is_movie_detail_url(url: str) -> bool:
    parsed = urlparse(url)
    path = (parsed.path or "").rstrip("/")
    return path.startswith("/movies/") and len(path.split("/")) >= 3


class SeeItEarlyUpcomingClient:
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session

    async def fetch(self, state: MovieTicketSourceState | None = None) -> SeeItEarlyFetchResult:
        headers = {
            "Accept": "text/html,application/xhtml+xml",
            "User-Agent": SEEITEARLY_USER_AGENT,
        }
        if state and state.etag:
            headers["If-None-Match"] = state.etag
        if state and state.last_modified:
            headers["If-Modified-Since"] = state.last_modified

        timeout = aiohttp.ClientTimeout(total=SEEITEARLY_REQUEST_TIMEOUT_SECONDS, connect=5)
        async with self.session.get(
            SEEITEARLY_UPCOMING_URL,
            headers=headers,
            timeout=timeout,
            allow_redirects=True,
        ) as response:
            final_url = str(response.url)
            if not safe_seeitearly_url(final_url):
                raise RuntimeError("SeeItEarly request redirected outside the public SeeItEarly allowlist.")
            if response.status == 304:
                return SeeItEarlyFetchResult(
                    not_modified=True,
                    etag=response.headers.get("ETag", state.etag if state else ""),
                    last_modified=response.headers.get("Last-Modified", state.last_modified if state else ""),
                    final_url=final_url,
                )
            if response.status != 200:
                raise RuntimeError(f"SeeItEarly public upcoming page returned HTTP {response.status}.")

            payload = await response.read()
            if len(payload) > SEEITEARLY_MAX_RESPONSE_BYTES:
                raise RuntimeError("SeeItEarly public upcoming page exceeded the safe response-size limit.")
            charset = response.charset or "utf-8"
            try:
                html_text = payload.decode(charset, errors="replace")
            except LookupError:
                html_text = payload.decode("utf-8", errors="replace")
            return SeeItEarlyFetchResult(
                not_modified=False,
                html=html_text,
                etag=response.headers.get("ETag", ""),
                last_modified=response.headers.get("Last-Modified", ""),
                final_url=final_url,
            )
