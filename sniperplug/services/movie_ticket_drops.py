from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse

import aiohttp


log = logging.getLogger("sniperplug.movie_tickets")

ATOM_PROMOTIONS_URL = "https://www.atomtickets.com/promotions"
ATOM_SOURCE_KEY = "atom_official_promotions"
ATOM_SOURCE_LABEL = "Official Atom Promotions Hub"
ATOM_ALLOWED_HOSTS = frozenset({"atomtickets.com", "www.atomtickets.com"})
ATOM_USER_AGENT = "SniperPlug/1.0 MovieTicketMonitor (+https://sniperplug.com)"
ATOM_MAX_RESPONSE_BYTES = 2_000_000
ATOM_REQUEST_TIMEOUT_SECONDS = 15
DELIVERY_RESERVATION_TTL = timedelta(minutes=10)

_CODE_PATTERNS = (
    re.compile(r"\benter\s+(?:the\s+)?promo(?:tion)?\s+code\s+([A-Z0-9][A-Z0-9-]{3,39})\b", re.I),
    re.compile(r"\buse\s+(?:the\s+)?promo(?:tion)?\s+code\s+([A-Z0-9][A-Z0-9-]{3,39})\b", re.I),
    re.compile(r"\bpromo(?:tion)?\s+code\s*(?:is|:)?\s*([A-Z0-9][A-Z0-9-]{3,39})\b", re.I),
)
_GENERIC_CODE_WORDS = frozenset(
    {
        "CHECKOUT",
        "DIRECTLY",
        "FIELD",
        "GIVEN",
        "PROMO",
        "PROMOTION",
        "RECEIVED",
        "REDEMPTION",
        "REQUIRED",
    }
)


@dataclass(frozen=True, slots=True)
class MovieTicketDrop:
    drop_id: str
    source_key: str
    source_label: str
    title: str
    code: str
    classification: str
    ticket_limit: int
    offer_url: str
    validity_text: str
    restrictions: tuple[str, ...]
    raw_text: str
    first_seen_at: str = ""
    last_seen_at: str = ""
    active: bool = True

    @property
    def value_label(self) -> str:
        noun = "ticket" if self.ticket_limit == 1 else "tickets"
        return f"Up to {self.ticket_limit} free {noun}"


@dataclass(frozen=True, slots=True)
class AtomPromotionParseResult:
    drops: tuple[MovieTicketDrop, ...]
    document_valid: bool
    film_section_found: bool
    partner_section_found: bool


@dataclass(frozen=True, slots=True)
class AtomFetchResult:
    not_modified: bool
    html: str = ""
    etag: str = ""
    last_modified: str = ""
    final_url: str = ATOM_PROMOTIONS_URL


@dataclass(slots=True)
class MovieTicketConfig:
    guild_id: int
    alert_channel_id: int | None = None
    enabled: bool = False


@dataclass(slots=True)
class MovieTicketSourceState:
    source_key: str = ATOM_SOURCE_KEY
    etag: str = ""
    last_modified: str = ""
    last_checked_at: str = ""
    last_success_at: str = ""
    last_error: str = ""
    active_drop_count: int = 0


@dataclass(frozen=True, slots=True)
class _RawPromotionBlock:
    heading: str
    href: str
    terms: tuple[str, ...]


class _AtomPromotionDocumentParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.section = ""
        self.film_section_found = False
        self.partner_section_found = False
        self.blocks: list[_RawPromotionBlock] = []
        self._current_heading = ""
        self._current_href = ""
        self._current_terms: list[str] = []
        self._capture_root = ""
        self._capture_parts: list[str] = []
        self._capture_href = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized_tag = tag.lower()
        if not self._capture_root and normalized_tag in {"h2", "h3", "li", "p"}:
            self._capture_root = normalized_tag
            self._capture_parts = []
            self._capture_href = ""
        if self._capture_root and normalized_tag == "a":
            href = dict(attrs).get("href")
            if href:
                self._capture_href = str(href)
        if self._capture_root and normalized_tag == "br":
            self._capture_parts.append(" ")

    def handle_data(self, data: str) -> None:
        if self._capture_root:
            self._capture_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if not self._capture_root or tag.lower() != self._capture_root:
            return
        text = clean_text(" ".join(self._capture_parts))
        capture_root = self._capture_root
        href = self._capture_href
        self._capture_root = ""
        self._capture_parts = []
        self._capture_href = ""
        if not text:
            return
        if capture_root in {"h2", "h3"}:
            self._handle_heading(text, href)
        elif self.section == "film" and self._current_heading:
            self._current_terms.append(text)

    def close(self) -> None:
        super().close()
        self._finish_current()

    def _handle_heading(self, text: str, href: str) -> None:
        normalized = normalize_text(text)
        if normalized == "film promotions":
            self._finish_current()
            self.section = "film"
            self.film_section_found = True
            return
        if normalized == "partner promotions":
            self._finish_current()
            self.section = "partner"
            self.partner_section_found = True
            return

        self._finish_current()
        if self.section == "film" and "free ticket" in normalized:
            self._current_heading = text
            self._current_href = href
            self._current_terms = []

    def _finish_current(self) -> None:
        if not self._current_heading:
            return
        self.blocks.append(
            _RawPromotionBlock(
                heading=self._current_heading,
                href=self._current_href,
                terms=tuple(self._current_terms),
            )
        )
        self._current_heading = ""
        self._current_href = ""
        self._current_terms = []


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def clean_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalize_text(value: str | None) -> str:
    return clean_text(value).lower()


def parse_atom_promotions_html(html_text: str) -> AtomPromotionParseResult:
    parser = _AtomPromotionDocumentParser()
    try:
        parser.feed(html_text)
        parser.close()
    except Exception:
        log.exception("Atom promotions HTML parsing failed")
        return AtomPromotionParseResult((), False, parser.film_section_found, parser.partner_section_found)

    page_text = normalize_text(html_text)
    document_valid = (
        parser.film_section_found
        and parser.partner_section_found
        and "atom tickets promotions" in page_text
    )
    if not document_valid:
        return AtomPromotionParseResult((), False, parser.film_section_found, parser.partner_section_found)

    drops: list[MovieTicketDrop] = []
    seen_codes: set[str] = set()
    for block in parser.blocks:
        drop = _drop_from_block(block)
        if drop is None or drop.code in seen_codes:
            continue
        seen_codes.add(drop.code)
        drops.append(drop)

    drops.sort(key=lambda item: (item.title.lower(), item.code))
    return AtomPromotionParseResult(tuple(drops), True, True, True)


def _drop_from_block(block: _RawPromotionBlock) -> MovieTicketDrop | None:
    combined = clean_text(" ".join((block.heading, *block.terms)))
    normalized = normalize_text(combined)
    if "free ticket" not in normalized:
        return None
    if any(term in normalized for term in ("sweepstakes", "chance to win", "enter to win")):
        return None

    code = extract_public_code(combined)
    if not code:
        return None

    heading_normalized = normalize_text(block.heading)
    if not any(term in heading_normalized for term in ("use your promo code", "promo code", "free ticket")):
        return None

    title = clean_text(re.split(r"\s+[–—-]\s+(?=use|get|receive)", block.heading, maxsplit=1, flags=re.I)[0])
    if not title:
        return None

    ticket_limit = extract_ticket_limit(block.heading, combined)
    validity_text = next(
        (term for term in block.terms if "valid from" in normalize_text(term) or "valid through" in normalize_text(term)),
        "While supplies last; see official terms.",
    )
    restrictions = summarize_restrictions(block.terms)
    offer_url = safe_atom_url(block.href) or ATOM_PROMOTIONS_URL
    drop_id = movie_drop_id(title=title, code=code, validity_text=validity_text)

    return MovieTicketDrop(
        drop_id=drop_id,
        source_key=ATOM_SOURCE_KEY,
        source_label=ATOM_SOURCE_LABEL,
        title=title[:180],
        code=code,
        classification="public_reusable",
        ticket_limit=ticket_limit,
        offer_url=offer_url,
        validity_text=validity_text[:500],
        restrictions=restrictions,
        raw_text=combined[:8000],
    )


def extract_public_code(text: str) -> str:
    for pattern in _CODE_PATTERNS:
        for match in pattern.finditer(text):
            candidate = re.sub(r"[^A-Z0-9-]", "", match.group(1).upper())
            if candidate and candidate not in _GENERIC_CODE_WORDS and any(char.isalpha() for char in candidate):
                return candidate
    return ""


def extract_ticket_limit(heading: str, combined: str) -> int:
    for text in (heading, combined):
        match = re.search(r"\bup\s+to\s+(\d{1,2})\s+free\s+tickets?\b", text, flags=re.I)
        if not match:
            match = re.search(r"\b(\d{1,2})\s+free\s+tickets?\b", text, flags=re.I)
        if match:
            return max(1, min(10, int(match.group(1))))
    return 1


def summarize_restrictions(terms: tuple[str, ...]) -> tuple[str, ...]:
    preferred_markers = (
        "one time use",
        "while supplies last",
        "valid from",
        "valid through",
        "valid in the united states",
        "not for resale",
        "cannot be redeemed for cash",
        "eligible theater",
        "select theater",
        "new atom",
    )
    selected: list[str] = []
    for term in terms:
        normalized = normalize_text(term)
        if any(marker in normalized for marker in preferred_markers):
            cleaned = clean_text(term)
            if cleaned and cleaned not in selected:
                selected.append(cleaned[:500])
        if len(selected) >= 6:
            break
    if not selected:
        selected.append("One-time use per customer, while supplies last; see official Atom terms.")
    return tuple(selected)


def movie_drop_id(*, title: str, code: str, validity_text: str) -> str:
    body = "|".join((ATOM_SOURCE_KEY, normalize_text(title), code.upper(), normalize_text(validity_text)))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:32]


def safe_atom_url(value: str | None) -> str:
    if not value:
        return ""
    absolute = urljoin(ATOM_PROMOTIONS_URL, value)
    parsed = urlparse(absolute)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in ATOM_ALLOWED_HOSTS:
        return ""
    return absolute


class AtomPromotionsClient:
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session

    async def fetch(self, state: MovieTicketSourceState | None = None) -> AtomFetchResult:
        headers = {
            "Accept": "text/html,application/xhtml+xml",
            "User-Agent": ATOM_USER_AGENT,
        }
        if state and state.etag:
            headers["If-None-Match"] = state.etag
        if state and state.last_modified:
            headers["If-Modified-Since"] = state.last_modified

        timeout = aiohttp.ClientTimeout(total=ATOM_REQUEST_TIMEOUT_SECONDS, connect=5)
        async with self.session.get(
            ATOM_PROMOTIONS_URL,
            headers=headers,
            timeout=timeout,
            allow_redirects=True,
        ) as response:
            final_url = str(response.url)
            if not safe_atom_url(final_url):
                raise RuntimeError("Atom promotions request redirected outside the official Atom allowlist.")
            if response.status == 304:
                return AtomFetchResult(
                    not_modified=True,
                    etag=response.headers.get("ETag", state.etag if state else ""),
                    last_modified=response.headers.get("Last-Modified", state.last_modified if state else ""),
                    final_url=final_url,
                )
            if response.status != 200:
                raise RuntimeError(f"Official Atom promotions page returned HTTP {response.status}.")
            payload = await response.read()
            if len(payload) > ATOM_MAX_RESPONSE_BYTES:
                raise RuntimeError("Official Atom promotions page exceeded the safe response-size limit.")
            charset = response.charset or "utf-8"
            try:
                html_text = payload.decode(charset, errors="replace")
            except LookupError:
                html_text = payload.decode("utf-8", errors="replace")
            return AtomFetchResult(
                not_modified=False,
                html=html_text,
                etag=response.headers.get("ETag", ""),
                last_modified=response.headers.get("Last-Modified", ""),
                final_url=final_url,
            )


class MovieTicketStore:
    def __init__(self, db: Any):
        self.db = db
        self._schema_ready = False
        self._schema_lock = asyncio.Lock()

    async def ensure_schema(self) -> None:
        if self._schema_ready:
            return
        async with self._schema_lock:
            if self._schema_ready:
                return
            conn = self.db.require_conn()
            await conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS movie_ticket_config (
                    guild_id INTEGER PRIMARY KEY,
                    alert_channel_id INTEGER,
                    enabled INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS movie_ticket_drops (
                    drop_id TEXT PRIMARY KEY,
                    source_key TEXT NOT NULL,
                    source_label TEXT NOT NULL,
                    title TEXT NOT NULL,
                    code TEXT NOT NULL,
                    classification TEXT NOT NULL,
                    ticket_limit INTEGER NOT NULL DEFAULT 1,
                    offer_url TEXT NOT NULL,
                    validity_text TEXT NOT NULL DEFAULT '',
                    restrictions_json TEXT NOT NULL DEFAULT '[]',
                    raw_text TEXT NOT NULL DEFAULT '',
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS movie_ticket_source_state (
                    source_key TEXT PRIMARY KEY,
                    etag TEXT NOT NULL DEFAULT '',
                    last_modified TEXT NOT NULL DEFAULT '',
                    last_checked_at TEXT NOT NULL DEFAULT '',
                    last_success_at TEXT NOT NULL DEFAULT '',
                    last_error TEXT NOT NULL DEFAULT '',
                    active_drop_count INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS movie_ticket_deliveries (
                    guild_id INTEGER NOT NULL,
                    drop_id TEXT NOT NULL,
                    channel_id INTEGER NOT NULL,
                    state TEXT NOT NULL DEFAULT 'reserved',
                    reserved_at TEXT NOT NULL,
                    posted_at TEXT,
                    message_id INTEGER,
                    last_error TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (guild_id, drop_id)
                );

                CREATE INDEX IF NOT EXISTS idx_movie_ticket_drops_active
                    ON movie_ticket_drops(source_key, active, last_seen_at DESC);
                CREATE INDEX IF NOT EXISTS idx_movie_ticket_deliveries_guild_state
                    ON movie_ticket_deliveries(guild_id, state, posted_at DESC);
                """
            )
            await conn.commit()
            self._schema_ready = True

    async def get_config(self, guild_id: int) -> MovieTicketConfig:
        await self.ensure_schema()
        conn = self.db.require_conn()
        cursor = await conn.execute(
            "SELECT guild_id, alert_channel_id, enabled FROM movie_ticket_config WHERE guild_id = ?",
            (int(guild_id),),
        )
        row = await cursor.fetchone()
        if not row:
            return MovieTicketConfig(guild_id=int(guild_id))
        return MovieTicketConfig(
            guild_id=int(_row_value(row, "guild_id") or guild_id),
            alert_channel_id=_optional_int(_row_value(row, "alert_channel_id")),
            enabled=bool(_row_value(row, "enabled")),
        )

    async def save_config(self, config: MovieTicketConfig) -> None:
        await self.ensure_schema()
        conn = self.db.require_conn()
        now = utc_now_iso()
        await conn.execute(
            """
            INSERT INTO movie_ticket_config (guild_id, alert_channel_id, enabled, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                alert_channel_id = excluded.alert_channel_id,
                enabled = excluded.enabled,
                updated_at = excluded.updated_at
            """,
            (
                int(config.guild_id),
                int(config.alert_channel_id) if config.alert_channel_id else None,
                1 if config.enabled else 0,
                now,
                now,
            ),
        )
        await conn.commit()

    async def list_enabled_configs(self) -> list[MovieTicketConfig]:
        await self.ensure_schema()
        conn = self.db.require_conn()
        cursor = await conn.execute(
            "SELECT guild_id, alert_channel_id, enabled FROM movie_ticket_config WHERE enabled = 1 AND alert_channel_id IS NOT NULL"
        )
        rows = await cursor.fetchall()
        return [
            MovieTicketConfig(
                guild_id=int(_row_value(row, "guild_id")),
                alert_channel_id=_optional_int(_row_value(row, "alert_channel_id")),
                enabled=True,
            )
            for row in rows
        ]

    async def get_source_state(self, source_key: str = ATOM_SOURCE_KEY) -> MovieTicketSourceState:
        await self.ensure_schema()
        conn = self.db.require_conn()
        cursor = await conn.execute(
            """
            SELECT source_key, etag, last_modified, last_checked_at, last_success_at, last_error, active_drop_count
            FROM movie_ticket_source_state WHERE source_key = ?
            """,
            (source_key,),
        )
        row = await cursor.fetchone()
        if not row:
            return MovieTicketSourceState(source_key=source_key)
        return MovieTicketSourceState(
            source_key=str(_row_value(row, "source_key") or source_key),
            etag=str(_row_value(row, "etag") or ""),
            last_modified=str(_row_value(row, "last_modified") or ""),
            last_checked_at=str(_row_value(row, "last_checked_at") or ""),
            last_success_at=str(_row_value(row, "last_success_at") or ""),
            last_error=str(_row_value(row, "last_error") or ""),
            active_drop_count=int(_row_value(row, "active_drop_count") or 0),
        )

    async def record_source_success(
        self,
        *,
        source_key: str,
        etag: str,
        last_modified: str,
        active_drop_count: int,
    ) -> None:
        await self.ensure_schema()
        conn = self.db.require_conn()
        now = utc_now_iso()
        await conn.execute(
            """
            INSERT INTO movie_ticket_source_state (
                source_key, etag, last_modified, last_checked_at, last_success_at, last_error, active_drop_count
            ) VALUES (?, ?, ?, ?, ?, '', ?)
            ON CONFLICT(source_key) DO UPDATE SET
                etag = excluded.etag,
                last_modified = excluded.last_modified,
                last_checked_at = excluded.last_checked_at,
                last_success_at = excluded.last_success_at,
                last_error = '',
                active_drop_count = excluded.active_drop_count
            """,
            (source_key, etag, last_modified, now, now, max(0, int(active_drop_count))),
        )
        await conn.commit()

    async def record_source_error(self, source_key: str, error: str) -> None:
        await self.ensure_schema()
        conn = self.db.require_conn()
        now = utc_now_iso()
        await conn.execute(
            """
            INSERT INTO movie_ticket_source_state (
                source_key, last_checked_at, last_error, active_drop_count
            ) VALUES (?, ?, ?, 0)
            ON CONFLICT(source_key) DO UPDATE SET
                last_checked_at = excluded.last_checked_at,
                last_error = excluded.last_error
            """,
            (source_key, now, clean_text(error)[:1000]),
        )
        await conn.commit()

    async def replace_active_drops(self, source_key: str, drops: tuple[MovieTicketDrop, ...]) -> None:
        await self.ensure_schema()
        conn = self.db.require_conn()
        now = utc_now_iso()
        active_ids: list[str] = []
        for drop in drops:
            if drop.source_key != source_key or drop.classification != "public_reusable":
                continue
            active_ids.append(drop.drop_id)
            await conn.execute(
                """
                INSERT INTO movie_ticket_drops (
                    drop_id, source_key, source_label, title, code, classification, ticket_limit,
                    offer_url, validity_text, restrictions_json, raw_text,
                    first_seen_at, last_seen_at, active
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(drop_id) DO UPDATE SET
                    source_label = excluded.source_label,
                    title = excluded.title,
                    code = excluded.code,
                    classification = excluded.classification,
                    ticket_limit = excluded.ticket_limit,
                    offer_url = excluded.offer_url,
                    validity_text = excluded.validity_text,
                    restrictions_json = excluded.restrictions_json,
                    raw_text = excluded.raw_text,
                    last_seen_at = excluded.last_seen_at,
                    active = 1
                """,
                (
                    drop.drop_id,
                    drop.source_key,
                    drop.source_label,
                    drop.title,
                    drop.code,
                    drop.classification,
                    max(1, int(drop.ticket_limit)),
                    drop.offer_url,
                    drop.validity_text,
                    json.dumps(list(drop.restrictions), ensure_ascii=False),
                    drop.raw_text,
                    now,
                    now,
                ),
            )

        if active_ids:
            placeholders = ",".join("?" for _ in active_ids)
            await conn.execute(
                f"UPDATE movie_ticket_drops SET active = 0 WHERE source_key = ? AND drop_id NOT IN ({placeholders})",
                (source_key, *active_ids),
            )
        else:
            await conn.execute("UPDATE movie_ticket_drops SET active = 0 WHERE source_key = ?", (source_key,))
        await conn.commit()

    async def list_active_drops(self, *, limit: int = 25) -> list[MovieTicketDrop]:
        await self.ensure_schema()
        conn = self.db.require_conn()
        cursor = await conn.execute(
            """
            SELECT drop_id, source_key, source_label, title, code, classification, ticket_limit,
                   offer_url, validity_text, restrictions_json, raw_text,
                   first_seen_at, last_seen_at, active
            FROM movie_ticket_drops
            WHERE active = 1 AND classification = 'public_reusable'
            ORDER BY last_seen_at DESC, title ASC
            LIMIT ?
            """,
            (max(1, min(100, int(limit))),),
        )
        rows = await cursor.fetchall()
        return [_drop_from_row(row) for row in rows]

    async def reserve_delivery(self, *, guild_id: int, drop_id: str, channel_id: int) -> bool:
        await self.ensure_schema()
        conn = self.db.require_conn()
        cursor = await conn.execute(
            """
            SELECT state, reserved_at FROM movie_ticket_deliveries
            WHERE guild_id = ? AND drop_id = ?
            """,
            (int(guild_id), drop_id),
        )
        row = await cursor.fetchone()
        now = datetime.now(UTC)
        if row:
            state = str(_row_value(row, "state") or "")
            reserved_at = _parse_iso(str(_row_value(row, "reserved_at") or ""))
            if state == "sent":
                return False
            if state == "reserved" and reserved_at and now - reserved_at < DELIVERY_RESERVATION_TTL:
                return False

        now_text = now.isoformat()
        await conn.execute(
            """
            INSERT INTO movie_ticket_deliveries (
                guild_id, drop_id, channel_id, state, reserved_at, posted_at, message_id, last_error
            ) VALUES (?, ?, ?, 'reserved', ?, NULL, NULL, '')
            ON CONFLICT(guild_id, drop_id) DO UPDATE SET
                channel_id = excluded.channel_id,
                state = 'reserved',
                reserved_at = excluded.reserved_at,
                posted_at = NULL,
                message_id = NULL,
                last_error = ''
            """,
            (int(guild_id), drop_id, int(channel_id), now_text),
        )
        await conn.commit()
        return True

    async def mark_delivery_sent(
        self,
        *,
        guild_id: int,
        drop_id: str,
        channel_id: int,
        message_id: int | None,
    ) -> None:
        await self.ensure_schema()
        conn = self.db.require_conn()
        await conn.execute(
            """
            UPDATE movie_ticket_deliveries
            SET state = 'sent', channel_id = ?, posted_at = ?, message_id = ?, last_error = ''
            WHERE guild_id = ? AND drop_id = ?
            """,
            (int(channel_id), utc_now_iso(), int(message_id) if message_id else None, int(guild_id), drop_id),
        )
        await conn.commit()

    async def mark_delivery_failed(self, *, guild_id: int, drop_id: str, error: str) -> None:
        await self.ensure_schema()
        conn = self.db.require_conn()
        await conn.execute(
            """
            UPDATE movie_ticket_deliveries
            SET state = 'failed', last_error = ?
            WHERE guild_id = ? AND drop_id = ?
            """,
            (clean_text(error)[:1000], int(guild_id), drop_id),
        )
        await conn.commit()

    async def count_sent_for_guild(self, guild_id: int) -> int:
        await self.ensure_schema()
        conn = self.db.require_conn()
        cursor = await conn.execute(
            "SELECT COUNT(*) AS count FROM movie_ticket_deliveries WHERE guild_id = ? AND state = 'sent'",
            (int(guild_id),),
        )
        row = await cursor.fetchone()
        return int(_row_value(row, "count") or 0) if row else 0


def _drop_from_row(row: Any) -> MovieTicketDrop:
    restrictions_value = str(_row_value(row, "restrictions_json") or "[]")
    try:
        parsed_restrictions = json.loads(restrictions_value)
    except (TypeError, ValueError):
        parsed_restrictions = []
    restrictions = tuple(clean_text(item) for item in parsed_restrictions if clean_text(item))
    return MovieTicketDrop(
        drop_id=str(_row_value(row, "drop_id") or ""),
        source_key=str(_row_value(row, "source_key") or ""),
        source_label=str(_row_value(row, "source_label") or ""),
        title=str(_row_value(row, "title") or ""),
        code=str(_row_value(row, "code") or ""),
        classification=str(_row_value(row, "classification") or ""),
        ticket_limit=max(1, int(_row_value(row, "ticket_limit") or 1)),
        offer_url=str(_row_value(row, "offer_url") or ATOM_PROMOTIONS_URL),
        validity_text=str(_row_value(row, "validity_text") or ""),
        restrictions=restrictions,
        raw_text=str(_row_value(row, "raw_text") or ""),
        first_seen_at=str(_row_value(row, "first_seen_at") or ""),
        last_seen_at=str(_row_value(row, "last_seen_at") or ""),
        active=bool(_row_value(row, "active")),
    )


def _row_value(row: Any, key: str) -> Any:
    if row is None:
        return None
    if isinstance(row, dict):
        return row.get(key)
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return getattr(row, key, None)


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
