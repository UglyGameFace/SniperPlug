from __future__ import annotations

import logging
import re
from html.parser import HTMLParser
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import aiohttp

from sniperplug.services.movie_ticket_drops import (
    ATOM_MAX_RESPONSE_BYTES,
    ATOM_PROMOTIONS_URL,
    ATOM_REQUEST_TIMEOUT_SECONDS,
    ATOM_USER_AGENT,
    clean_text,
    safe_atom_url,
)


log = logging.getLogger("sniperplug.movie_tickets.artwork")

ATOM_IMAGE_ALLOWED_HOSTS = frozenset(
    {
        "atom-tickets-res.cloudinary.com",
        "images.atomtickets.com",
    }
)
SEEITEARLY_PAGE_ALLOWED_HOSTS = frozenset({"seeitearly.com", "www.seeitearly.com"})
TMDB_IMAGE_ALLOWED_HOSTS = frozenset({"image.tmdb.org"})


class _AtomMovieImageParser(HTMLParser):
    """Extract movie artwork candidates from an Atom or SeeItEarly movie page."""

    META_IMAGE_KEYS = frozenset(
        {
            "og:image",
            "og:image:secure_url",
            "twitter:image",
            "twitter:image:src",
        }
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta_candidates: list[str] = []
        self.poster_candidates: list[str] = []
        self.other_candidates: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized_tag = tag.lower()
        data = {str(key).lower(): str(value or "") for key, value in attrs}

        if normalized_tag == "meta":
            key = (data.get("property") or data.get("name") or "").strip().lower()
            content = data.get("content", "").strip()
            if key in self.META_IMAGE_KEYS and content:
                self.meta_candidates.append(content)
            return

        if normalized_tag == "link":
            rel = data.get("rel", "").strip().lower()
            href = data.get("href", "").strip()
            if href and "image_src" in rel:
                self.meta_candidates.append(href)
            return

        if normalized_tag != "img":
            return

        src = (
            data.get("src", "").strip()
            or data.get("data-src", "").strip()
            or data.get("data-lazy-src", "").strip()
        )
        if not src:
            return

        alt = clean_text(data.get("alt")).lower()
        src_lower = src.lower()
        if (
            "movie poster" in alt
            or "poster" in alt
            or "ingestion-images" in src_lower
            or "_cops_" in src_lower
            or "image.tmdb.org" in src_lower
        ):
            self.poster_candidates.append(src)
        else:
            self.other_candidates.append(src)

    def candidates(self) -> tuple[str, ...]:
        return tuple(self.meta_candidates + self.poster_candidates + self.other_candidates)


def normalize_public_code(value: str | None) -> str:
    """Return only the redeemable code, without Markdown or quote wrappers."""

    stripped = clean_text(value).strip("`'\"“”‘’")
    return re.sub(r"[^A-Z0-9-]", "", stripped.upper())


def safe_atom_image_url(value: str | None) -> str:
    if not value:
        return ""
    absolute = urljoin(ATOM_PROMOTIONS_URL, value)
    parsed = urlparse(absolute)
    if parsed.scheme != "https":
        return ""
    if (parsed.hostname or "").lower() not in ATOM_IMAGE_ALLOWED_HOSTS:
        return ""
    return absolute


def safe_seeitearly_movie_url(value: str | None) -> str:
    if not value:
        return ""
    parsed = urlparse(str(value))
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in SEEITEARLY_PAGE_ALLOWED_HOSTS:
        return ""
    path = (parsed.path or "").rstrip("/")
    if not path.startswith("/movies/") or len(path.split("/")) < 3:
        return ""
    return str(value)


def safe_movie_image_url(value: str | None, *, page_url: str = "") -> str:
    """Allow only known Atom/TMDB poster assets and unwrap SeeItEarly's image proxy."""

    if not value:
        return ""
    absolute = urljoin(page_url or ATOM_PROMOTIONS_URL, value)
    parsed = urlparse(absolute)
    if parsed.scheme != "https":
        return ""
    host = (parsed.hostname or "").lower()
    if host in ATOM_IMAGE_ALLOWED_HOSTS or host in TMDB_IMAGE_ALLOWED_HOSTS:
        return absolute
    if host in SEEITEARLY_PAGE_ALLOWED_HOSTS and parsed.path == "/_next/image":
        proxied = unquote((parse_qs(parsed.query).get("url") or [""])[0])
        proxied_parsed = urlparse(proxied)
        if (
            proxied_parsed.scheme == "https"
            and (proxied_parsed.hostname or "").lower() in TMDB_IMAGE_ALLOWED_HOSTS
        ):
            return proxied
    return ""


def extract_atom_movie_image_url(html_text: str, *, page_url: str = ATOM_PROMOTIONS_URL) -> str:
    parser = _AtomMovieImageParser()
    try:
        parser.feed(html_text)
        parser.close()
    except Exception:
        log.exception("Movie artwork HTML parsing failed")
        return ""

    atom_page = bool(safe_atom_url(page_url))
    for candidate in parser.candidates():
        safe = safe_atom_image_url(candidate) if atom_page else safe_movie_image_url(candidate, page_url=page_url)
        if safe:
            return safe
    return ""


async def fetch_atom_movie_image(
    session: aiohttp.ClientSession,
    movie_url: str,
) -> str:
    """Fetch poster artwork from an Atom or SeeItEarly movie detail page.

    The public name is retained for compatibility with the existing movie runtime.
    Atom pages keep their strict Atom-only URL checks. SeeItEarly pages are accepted
    only for ``/movies/...`` detail URLs and only poster assets from Atom's image
    hosts or TMDB's image CDN can escape the parser.
    """

    atom_target = safe_atom_url(movie_url)
    seeitearly_target = safe_seeitearly_movie_url(movie_url)
    target = atom_target or seeitearly_target
    if not target or target == ATOM_PROMOTIONS_URL:
        return ""

    headers = {
        "Accept": "text/html,application/xhtml+xml",
        "User-Agent": ATOM_USER_AGENT,
    }
    timeout = aiohttp.ClientTimeout(total=ATOM_REQUEST_TIMEOUT_SECONDS, connect=5)
    async with session.get(
        target,
        headers=headers,
        timeout=timeout,
        allow_redirects=True,
    ) as response:
        final_url = str(response.url)
        if atom_target:
            if not safe_atom_url(final_url):
                raise RuntimeError("Atom movie page redirected outside the official Atom allowlist.")
        elif not safe_seeitearly_movie_url(final_url):
            raise RuntimeError("SeeItEarly movie page redirected outside the public movie-detail allowlist.")
        if response.status != 200:
            return ""

        payload = await response.read()
        if len(payload) > ATOM_MAX_RESPONSE_BYTES:
            raise RuntimeError("Movie artwork page exceeded the safe response-size limit.")

        charset = response.charset or "utf-8"
        try:
            html_text = payload.decode(charset, errors="replace")
        except LookupError:
            html_text = payload.decode("utf-8", errors="replace")
        return extract_atom_movie_image_url(html_text, page_url=final_url)
