from __future__ import annotations

import asyncio
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote, urlparse

import aiohttp

from sniperplug.services.movie_ticket_drops import clean_text, utc_now_iso
from sniperplug.services.movie_ticket_snowflake_store import snowflake_int, snowflake_text


ZIP_RE = re.compile(r"^\d{5}$")
ZIP_LOOKUP_BASE_URL = "https://api.zippopotam.us"
ZIP_LOOKUP_ALLOWED_HOSTS = frozenset({"api.zippopotam.us"})
ZIP_LOOKUP_USER_AGENT = "SniperPlug/1.0 MovieLocalSetup (+https://sniperplug.com)"
ZIP_LOOKUP_TIMEOUT_SECONDS = 10
ZIP_LOOKUP_MAX_BYTES = 256_000
ZIP_CACHE_TTL = timedelta(days=180)
ALLOWED_RADII = frozenset({10, 25, 50})


@dataclass(slots=True)
class MovieServerSetup:
    guild_id: int
    alert_channel_id: int | None = None
    local_channel_id: int | None = None
    enabled: bool = False
    atom_enabled: bool = True
    fandango_enabled: bool = True
    gofobo_enabled: bool = True
    local_dm_enabled: bool = True
    local_channel_enabled: bool = False
    feedback_enabled: bool = True
    member_self_service_enabled: bool = True
    default_radius_miles: int = 25
    server_zip_code: str = ""
    server_place_name: str = ""
    server_state_code: str = ""
    server_latitude: float | None = None
    server_longitude: float | None = None

    @property
    def any_source_enabled(self) -> bool:
        return self.atom_enabled or self.fandango_enabled or self.gofobo_enabled


@dataclass(slots=True)
class MovieUserLocalProfile:
    guild_id: int
    user_id: int
    zip_code: str
    place_name: str
    state_code: str
    latitude: float
    longitude: float
    radius_miles: int = 25
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class ZipPlace:
    zip_code: str
    place_name: str
    state_name: str
    state_code: str
    latitude: float
    longitude: float
    fetched_at: str = ""

    @property
    def short_label(self) -> str:
        location = ", ".join(part for part in (self.place_name, self.state_code) if part)
        return f"{self.zip_code} — {location}" if location else self.zip_code


class MovieSetupStore:
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
                CREATE TABLE IF NOT EXISTS movie_server_setup_v1 (
                    guild_id TEXT PRIMARY KEY,
                    alert_channel_id TEXT,
                    local_channel_id TEXT,
                    enabled INTEGER NOT NULL DEFAULT 0,
                    atom_enabled INTEGER NOT NULL DEFAULT 1,
                    fandango_enabled INTEGER NOT NULL DEFAULT 1,
                    gofobo_enabled INTEGER NOT NULL DEFAULT 1,
                    local_dm_enabled INTEGER NOT NULL DEFAULT 1,
                    local_channel_enabled INTEGER NOT NULL DEFAULT 0,
                    feedback_enabled INTEGER NOT NULL DEFAULT 1,
                    member_self_service_enabled INTEGER NOT NULL DEFAULT 1,
                    default_radius_miles INTEGER NOT NULL DEFAULT 25,
                    server_zip_code TEXT NOT NULL DEFAULT '',
                    server_place_name TEXT NOT NULL DEFAULT '',
                    server_state_code TEXT NOT NULL DEFAULT '',
                    server_latitude REAL,
                    server_longitude REAL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS movie_user_local_profiles_v1 (
                    guild_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    zip_code TEXT NOT NULL,
                    place_name TEXT NOT NULL DEFAULT '',
                    state_code TEXT NOT NULL DEFAULT '',
                    latitude REAL NOT NULL,
                    longitude REAL NOT NULL,
                    radius_miles INTEGER NOT NULL DEFAULT 25,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (guild_id, user_id)
                );

                CREATE TABLE IF NOT EXISTS movie_zip_cache_v1 (
                    zip_code TEXT PRIMARY KEY,
                    place_name TEXT NOT NULL,
                    state_name TEXT NOT NULL DEFAULT '',
                    state_code TEXT NOT NULL DEFAULT '',
                    latitude REAL NOT NULL,
                    longitude REAL NOT NULL,
                    fetched_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS movie_user_local_deliveries_v1 (
                    guild_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    drop_id TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'reserved',
                    reserved_at TEXT NOT NULL,
                    sent_at TEXT,
                    last_error TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (guild_id, user_id, drop_id)
                );

                CREATE INDEX IF NOT EXISTS idx_movie_server_setup_enabled
                    ON movie_server_setup_v1(enabled, guild_id);
                CREATE INDEX IF NOT EXISTS idx_movie_user_local_profiles_enabled
                    ON movie_user_local_profiles_v1(guild_id, enabled, zip_code);
                CREATE INDEX IF NOT EXISTS idx_movie_user_local_deliveries_state
                    ON movie_user_local_deliveries_v1(guild_id, state, sent_at);
                """
            )
            await conn.commit()
            self._schema_ready = True

    async def get_server_setup(self, guild_id: int) -> MovieServerSetup:
        await self.ensure_schema()
        guild_text = snowflake_text(guild_id)
        conn = self.db.require_conn()
        cursor = await conn.execute(
            """
            SELECT guild_id, alert_channel_id, local_channel_id, enabled,
                   atom_enabled, fandango_enabled, gofobo_enabled,
                   local_dm_enabled, local_channel_enabled, feedback_enabled,
                   member_self_service_enabled, default_radius_miles,
                   server_zip_code, server_place_name, server_state_code,
                   server_latitude, server_longitude
            FROM movie_server_setup_v1
            WHERE guild_id = ?
            """,
            (guild_text,),
        )
        row = await cursor.fetchone()
        if not row:
            return MovieServerSetup(guild_id=int(guild_text))
        return MovieServerSetup(
            guild_id=snowflake_int(_row_value(row, "guild_id")),
            alert_channel_id=_optional_snowflake(_row_value(row, "alert_channel_id")),
            local_channel_id=_optional_snowflake(_row_value(row, "local_channel_id")),
            enabled=bool(_row_value(row, "enabled")),
            atom_enabled=bool(_row_value(row, "atom_enabled")),
            fandango_enabled=bool(_row_value(row, "fandango_enabled")),
            gofobo_enabled=bool(_row_value(row, "gofobo_enabled")),
            local_dm_enabled=bool(_row_value(row, "local_dm_enabled")),
            local_channel_enabled=bool(_row_value(row, "local_channel_enabled")),
            feedback_enabled=bool(_row_value(row, "feedback_enabled")),
            member_self_service_enabled=bool(_row_value(row, "member_self_service_enabled")),
            default_radius_miles=normalize_radius(_row_value(row, "default_radius_miles")),
            server_zip_code=normalize_zip(_row_value(row, "server_zip_code"), allow_blank=True),
            server_place_name=clean_text(_row_value(row, "server_place_name"))[:120],
            server_state_code=clean_text(_row_value(row, "server_state_code"))[:12].upper(),
            server_latitude=_optional_float(_row_value(row, "server_latitude")),
            server_longitude=_optional_float(_row_value(row, "server_longitude")),
        )

    async def save_server_setup(self, setup: MovieServerSetup) -> None:
        await self.ensure_schema()
        guild_text = snowflake_text(setup.guild_id)
        alert_channel_text = _optional_snowflake_text(setup.alert_channel_id)
        local_channel_text = _optional_snowflake_text(setup.local_channel_id)
        radius = normalize_radius(setup.default_radius_miles)
        server_zip = normalize_zip(setup.server_zip_code, allow_blank=True)
        now = utc_now_iso()
        conn = self.db.require_conn()
        await conn.execute(
            """
            INSERT INTO movie_server_setup_v1 (
                guild_id, alert_channel_id, local_channel_id, enabled,
                atom_enabled, fandango_enabled, gofobo_enabled,
                local_dm_enabled, local_channel_enabled, feedback_enabled,
                member_self_service_enabled, default_radius_miles,
                server_zip_code, server_place_name, server_state_code,
                server_latitude, server_longitude, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                alert_channel_id = excluded.alert_channel_id,
                local_channel_id = excluded.local_channel_id,
                enabled = excluded.enabled,
                atom_enabled = excluded.atom_enabled,
                fandango_enabled = excluded.fandango_enabled,
                gofobo_enabled = excluded.gofobo_enabled,
                local_dm_enabled = excluded.local_dm_enabled,
                local_channel_enabled = excluded.local_channel_enabled,
                feedback_enabled = excluded.feedback_enabled,
                member_self_service_enabled = excluded.member_self_service_enabled,
                default_radius_miles = excluded.default_radius_miles,
                server_zip_code = excluded.server_zip_code,
                server_place_name = excluded.server_place_name,
                server_state_code = excluded.server_state_code,
                server_latitude = excluded.server_latitude,
                server_longitude = excluded.server_longitude,
                updated_at = excluded.updated_at
            """,
            (
                guild_text,
                alert_channel_text,
                local_channel_text,
                1 if setup.enabled else 0,
                1 if setup.atom_enabled else 0,
                1 if setup.fandango_enabled else 0,
                1 if setup.gofobo_enabled else 0,
                1 if setup.local_dm_enabled else 0,
                1 if setup.local_channel_enabled else 0,
                1 if setup.feedback_enabled else 0,
                1 if setup.member_self_service_enabled else 0,
                radius,
                server_zip,
                clean_text(setup.server_place_name)[:120],
                clean_text(setup.server_state_code)[:12].upper(),
                setup.server_latitude,
                setup.server_longitude,
                now,
                now,
            ),
        )
        await conn.commit()
        verified = await self.get_server_setup(int(guild_text))
        if verified.guild_id != int(guild_text):
            raise RuntimeError("Movie setup guild ID failed its exact database round-trip check.")
        if verified.alert_channel_id != setup.alert_channel_id:
            raise RuntimeError("Movie setup alert channel failed its exact database round-trip check.")
        if verified.local_channel_id != setup.local_channel_id:
            raise RuntimeError("Movie setup local channel failed its exact database round-trip check.")

    async def list_enabled_server_setups(self) -> list[MovieServerSetup]:
        await self.ensure_schema()
        conn = self.db.require_conn()
        cursor = await conn.execute(
            "SELECT guild_id FROM movie_server_setup_v1 WHERE enabled = 1"
        )
        rows = await cursor.fetchall()
        setups: list[MovieServerSetup] = []
        for row in rows:
            try:
                setups.append(await self.get_server_setup(snowflake_int(_row_value(row, "guild_id"))))
            except (TypeError, ValueError):
                continue
        return setups

    async def get_user_profile(self, guild_id: int, user_id: int) -> MovieUserLocalProfile | None:
        await self.ensure_schema()
        conn = self.db.require_conn()
        cursor = await conn.execute(
            """
            SELECT guild_id, user_id, zip_code, place_name, state_code,
                   latitude, longitude, radius_miles, enabled
            FROM movie_user_local_profiles_v1
            WHERE guild_id = ? AND user_id = ?
            """,
            (snowflake_text(guild_id), snowflake_text(user_id)),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return MovieUserLocalProfile(
            guild_id=snowflake_int(_row_value(row, "guild_id")),
            user_id=snowflake_int(_row_value(row, "user_id")),
            zip_code=normalize_zip(_row_value(row, "zip_code")),
            place_name=clean_text(_row_value(row, "place_name"))[:120],
            state_code=clean_text(_row_value(row, "state_code"))[:12].upper(),
            latitude=float(_row_value(row, "latitude")),
            longitude=float(_row_value(row, "longitude")),
            radius_miles=normalize_radius(_row_value(row, "radius_miles")),
            enabled=bool(_row_value(row, "enabled")),
        )

    async def save_user_profile(self, profile: MovieUserLocalProfile) -> None:
        await self.ensure_schema()
        now = utc_now_iso()
        conn = self.db.require_conn()
        await conn.execute(
            """
            INSERT INTO movie_user_local_profiles_v1 (
                guild_id, user_id, zip_code, place_name, state_code,
                latitude, longitude, radius_miles, enabled, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, user_id) DO UPDATE SET
                zip_code = excluded.zip_code,
                place_name = excluded.place_name,
                state_code = excluded.state_code,
                latitude = excluded.latitude,
                longitude = excluded.longitude,
                radius_miles = excluded.radius_miles,
                enabled = excluded.enabled,
                updated_at = excluded.updated_at
            """,
            (
                snowflake_text(profile.guild_id),
                snowflake_text(profile.user_id),
                normalize_zip(profile.zip_code),
                clean_text(profile.place_name)[:120],
                clean_text(profile.state_code)[:12].upper(),
                float(profile.latitude),
                float(profile.longitude),
                normalize_radius(profile.radius_miles),
                1 if profile.enabled else 0,
                now,
                now,
            ),
        )
        await conn.commit()

    async def list_enabled_user_profiles(self, guild_id: int) -> list[MovieUserLocalProfile]:
        await self.ensure_schema()
        conn = self.db.require_conn()
        cursor = await conn.execute(
            """
            SELECT user_id FROM movie_user_local_profiles_v1
            WHERE guild_id = ? AND enabled = 1
            ORDER BY user_id
            """,
            (snowflake_text(guild_id),),
        )
        rows = await cursor.fetchall()
        profiles: list[MovieUserLocalProfile] = []
        for row in rows:
            try:
                profile = await self.get_user_profile(guild_id, snowflake_int(_row_value(row, "user_id")))
            except (TypeError, ValueError):
                continue
            if profile is not None and profile.enabled:
                profiles.append(profile)
        return profiles

    async def count_enabled_user_profiles(self, guild_id: int) -> int:
        await self.ensure_schema()
        conn = self.db.require_conn()
        cursor = await conn.execute(
            """
            SELECT COUNT(*) AS count FROM movie_user_local_profiles_v1
            WHERE guild_id = ? AND enabled = 1
            """,
            (snowflake_text(guild_id),),
        )
        row = await cursor.fetchone()
        return int(_row_value(row, "count") or 0) if row else 0

    async def set_user_profile_enabled(self, guild_id: int, user_id: int, enabled: bool) -> bool:
        profile = await self.get_user_profile(guild_id, user_id)
        if profile is None:
            return False
        profile.enabled = bool(enabled)
        await self.save_user_profile(profile)
        return True

    async def delete_user_profile(self, guild_id: int, user_id: int) -> None:
        await self.ensure_schema()
        conn = self.db.require_conn()
        await conn.execute(
            "DELETE FROM movie_user_local_profiles_v1 WHERE guild_id = ? AND user_id = ?",
            (snowflake_text(guild_id), snowflake_text(user_id)),
        )
        await conn.commit()

    async def get_cached_zip(self, zip_code: str) -> ZipPlace | None:
        await self.ensure_schema()
        cleaned_zip = normalize_zip(zip_code)
        conn = self.db.require_conn()
        cursor = await conn.execute(
            """
            SELECT zip_code, place_name, state_name, state_code,
                   latitude, longitude, fetched_at
            FROM movie_zip_cache_v1 WHERE zip_code = ?
            """,
            (cleaned_zip,),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        fetched_at = clean_text(_row_value(row, "fetched_at"))
        parsed = _parse_iso(fetched_at)
        if parsed is None or datetime.now(UTC) - parsed > ZIP_CACHE_TTL:
            return None
        return ZipPlace(
            zip_code=cleaned_zip,
            place_name=clean_text(_row_value(row, "place_name"))[:120],
            state_name=clean_text(_row_value(row, "state_name"))[:120],
            state_code=clean_text(_row_value(row, "state_code"))[:12].upper(),
            latitude=float(_row_value(row, "latitude")),
            longitude=float(_row_value(row, "longitude")),
            fetched_at=fetched_at,
        )

    async def save_cached_zip(self, place: ZipPlace) -> None:
        await self.ensure_schema()
        fetched_at = place.fetched_at or utc_now_iso()
        conn = self.db.require_conn()
        await conn.execute(
            """
            INSERT INTO movie_zip_cache_v1 (
                zip_code, place_name, state_name, state_code,
                latitude, longitude, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(zip_code) DO UPDATE SET
                place_name = excluded.place_name,
                state_name = excluded.state_name,
                state_code = excluded.state_code,
                latitude = excluded.latitude,
                longitude = excluded.longitude,
                fetched_at = excluded.fetched_at
            """,
            (
                normalize_zip(place.zip_code),
                clean_text(place.place_name)[:120],
                clean_text(place.state_name)[:120],
                clean_text(place.state_code)[:12].upper(),
                float(place.latitude),
                float(place.longitude),
                fetched_at,
            ),
        )
        await conn.commit()

    async def reserve_user_delivery(self, guild_id: int, user_id: int, drop_id: str) -> bool:
        await self.ensure_schema()
        conn = self.db.require_conn()
        cursor = await conn.execute(
            """
            SELECT state FROM movie_user_local_deliveries_v1
            WHERE guild_id = ? AND user_id = ? AND drop_id = ?
            """,
            (snowflake_text(guild_id), snowflake_text(user_id), clean_text(drop_id)),
        )
        row = await cursor.fetchone()
        if row and clean_text(_row_value(row, "state")) == "sent":
            return False
        now = utc_now_iso()
        await conn.execute(
            """
            INSERT INTO movie_user_local_deliveries_v1 (
                guild_id, user_id, drop_id, state, reserved_at, sent_at, last_error
            ) VALUES (?, ?, ?, 'reserved', ?, NULL, '')
            ON CONFLICT(guild_id, user_id, drop_id) DO UPDATE SET
                state = 'reserved', reserved_at = excluded.reserved_at,
                sent_at = NULL, last_error = ''
            """,
            (snowflake_text(guild_id), snowflake_text(user_id), clean_text(drop_id), now),
        )
        await conn.commit()
        return True

    async def mark_user_delivery_sent(self, guild_id: int, user_id: int, drop_id: str) -> None:
        await self.ensure_schema()
        conn = self.db.require_conn()
        await conn.execute(
            """
            UPDATE movie_user_local_deliveries_v1
            SET state = 'sent', sent_at = ?, last_error = ''
            WHERE guild_id = ? AND user_id = ? AND drop_id = ?
            """,
            (utc_now_iso(), snowflake_text(guild_id), snowflake_text(user_id), clean_text(drop_id)),
        )
        await conn.commit()

    async def mark_user_delivery_failed(self, guild_id: int, user_id: int, drop_id: str, error: str) -> None:
        await self.ensure_schema()
        conn = self.db.require_conn()
        await conn.execute(
            """
            UPDATE movie_user_local_deliveries_v1
            SET state = 'failed', last_error = ?
            WHERE guild_id = ? AND user_id = ? AND drop_id = ?
            """,
            (clean_text(error)[:1000], snowflake_text(guild_id), snowflake_text(user_id), clean_text(drop_id)),
        )
        await conn.commit()


class ZipLookupClient:
    def __init__(self, session: aiohttp.ClientSession, store: MovieSetupStore):
        self.session = session
        self.store = store
        self._locks: dict[str, asyncio.Lock] = {}

    async def lookup_us_zip(self, zip_code: str) -> ZipPlace:
        cleaned_zip = normalize_zip(zip_code)
        cached = await self.store.get_cached_zip(cleaned_zip)
        if cached is not None:
            return cached
        lock = self._locks.setdefault(cleaned_zip, asyncio.Lock())
        async with lock:
            cached = await self.store.get_cached_zip(cleaned_zip)
            if cached is not None:
                return cached
            url = f"{ZIP_LOOKUP_BASE_URL}/us/{quote(cleaned_zip)}"
            parsed = urlparse(url)
            if parsed.scheme != "https" or (parsed.hostname or "").lower() not in ZIP_LOOKUP_ALLOWED_HOSTS:
                raise RuntimeError("ZIP lookup URL failed the official host allowlist.")
            timeout = aiohttp.ClientTimeout(total=ZIP_LOOKUP_TIMEOUT_SECONDS, connect=5)
            async with self.session.get(
                url,
                headers={"Accept": "application/json", "User-Agent": ZIP_LOOKUP_USER_AGENT},
                timeout=timeout,
                allow_redirects=False,
            ) as response:
                if response.status == 404:
                    raise ValueError("That ZIP code was not found.")
                if response.status != 200:
                    raise RuntimeError(f"ZIP validation returned HTTP {response.status}.")
                payload = await response.read()
                if len(payload) > ZIP_LOOKUP_MAX_BYTES:
                    raise RuntimeError("ZIP validation response exceeded the safe size limit.")
                data = await _json_from_bytes(payload)
            places = data.get("places") if isinstance(data, dict) else None
            if not isinstance(places, list) or not places:
                raise ValueError("That ZIP code did not resolve to a supported place.")
            first = places[0] if isinstance(places[0], dict) else {}
            try:
                latitude = float(first.get("latitude"))
                longitude = float(first.get("longitude"))
            except (TypeError, ValueError) as error:
                raise ValueError("That ZIP code did not return usable coordinates.") from error
            place = ZipPlace(
                zip_code=cleaned_zip,
                place_name=clean_text(first.get("place name"))[:120],
                state_name=clean_text(first.get("state"))[:120],
                state_code=clean_text(first.get("state abbreviation"))[:12].upper(),
                latitude=latitude,
                longitude=longitude,
                fetched_at=utc_now_iso(),
            )
            await self.store.save_cached_zip(place)
            return place


def normalize_zip(value: Any, *, allow_blank: bool = False) -> str:
    text = clean_text(value)
    if allow_blank and not text:
        return ""
    if not ZIP_RE.fullmatch(text):
        raise ValueError("Enter a valid 5-digit US ZIP code.")
    return text


def normalize_radius(value: Any) -> int:
    try:
        radius = int(value)
    except (TypeError, ValueError):
        radius = 25
    if radius not in ALLOWED_RADII:
        return 25
    return radius


def distance_miles(latitude_a: float, longitude_a: float, latitude_b: float, longitude_b: float) -> float:
    earth_radius_miles = 3958.7613
    lat1 = math.radians(float(latitude_a))
    lon1 = math.radians(float(longitude_a))
    lat2 = math.radians(float(latitude_b))
    lon2 = math.radians(float(longitude_b))
    delta_lat = lat2 - lat1
    delta_lon = lon2 - lon1
    haversine = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    )
    return earth_radius_miles * 2 * math.asin(min(1.0, math.sqrt(haversine)))


def _optional_snowflake(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return snowflake_int(value)
    except (TypeError, ValueError):
        return None


def _optional_snowflake_text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return snowflake_text(value)


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _row_value(row: Any, key: str) -> Any:
    if row is None:
        return None
    if isinstance(row, dict):
        return row.get(key)
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return getattr(row, key, None)


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


async def _json_from_bytes(payload: bytes) -> dict[str, Any]:
    import json

    try:
        parsed = json.loads(payload.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("ZIP validation returned invalid JSON.") from error
    if not isinstance(parsed, dict):
        raise RuntimeError("ZIP validation returned an unexpected payload.")
    return parsed
