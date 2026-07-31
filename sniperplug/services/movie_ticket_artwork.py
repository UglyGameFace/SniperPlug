from __future__ import annotations

import logging
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

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


class _AtomMovieImageParser(HTMLParser):
    """Extract official movie artwork candidates from an Atom movie page."""

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


def extract_atom_movie_image_url(html_text: str) -> str:
    parser = _AtomMovieImageParser()
    try:
        parser.feed(html_text)
        parser.close()
    except Exception:
        log.exception("Atom movie artwork HTML parsing failed")
        return ""

    for candidate in parser.candidates():
        safe = safe_atom_image_url(candidate)
        if safe:
            return safe
    return ""


async def fetch_atom_movie_image(
    session: aiohttp.ClientSession,
    movie_url: str,
) -> str:
    """Fetch an official Atom movie page and return its official poster URL."""

    target = safe_atom_url(movie_url)
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
        if not safe_atom_url(final_url):
            raise RuntimeError("Atom movie page redirected outside the official Atom allowlist.")
        if response.status != 200:
            return ""

        payload = await response.read()
        if len(payload) > ATOM_MAX_RESPONSE_BYTES:
            raise RuntimeError("Official Atom movie page exceeded the safe response-size limit.")

        charset = response.charset or "utf-8"
        try:
            html_text = payload.decode(charset, errors="replace")
        except LookupError:
            html_text = payload.decode("utf-8", errors="replace")
        return extract_atom_movie_image_url(html_text)
