from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from typing import Any, Iterable

from sniperplug.models.deal import utc_now_iso
from sniperplug.target_watcher.parser import (
    TargetProductSeed,
    canonical_target_product_url,
    normalize_tcin,
)


LOCATION_TABLE = "target_location_profiles"
CATALOG_TABLE = "target_global_catalog"
CURSOR_TABLE = "target_location_scan_cursors"
MIGRATION_TABLE = "sniperplug_data_migrations"
REMOVE_UNSAFE_TARGET_MIGRATION = "20260802_remove_target_without_location_v1"


@dataclass(frozen=True)
class TargetLocationContext:
    scope_type: str
    scope_id: str
    zip_code: str
    store_id: str
    store_name: str
    address_line: str
    city: str
    state: str
    postal_code: str
    latitude: str
    longitude: str
    enabled: bool = True

    @property
    def location_key(self) -> str:
        return target_location_key(self.store_id, self.zip_code)

    @property
    def display_name(self) -> str:
        location = ", ".join(
            piece for piece in (self.city, self.state) if piece
        )
        return self.store_name or location or f"Target {self.store_id}"


async def ensure_target_location_tables(db: Any) -> None:
    if getattr(db, "_target_location_tables_ready", False):
        return
    conn = db.require_conn()
    await conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {LOCATION_TABLE} (
            scope_type TEXT NOT NULL,
            scope_id TEXT NOT NULL,
            zip_code TEXT NOT NULL,
            store_id TEXT NOT NULL,
            store_name TEXT NOT NULL,
            address_line TEXT NOT NULL DEFAULT '',
            city TEXT NOT NULL DEFAULT '',
            state TEXT NOT NULL,
            postal_code TEXT NOT NULL DEFAULT '',
            latitude TEXT NOT NULL,
            longitude TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(scope_type, scope_id)
        )
        """
    )
    await conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{LOCATION_TABLE}_active_location "
        f"ON {LOCATION_TABLE} (enabled, store_id, zip_code)"
    )
    await conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {CATALOG_TABLE} (
            tcin TEXT PRIMARY KEY,
            product_url TEXT NOT NULL,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL
        )
        """
    )
    await conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{CATALOG_TABLE}_tcin "
        f"ON {CATALOG_TABLE} (tcin)"
    )
    await conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {CURSOR_TABLE} (
            location_key TEXT PRIMARY KEY,
            cursor_tcin TEXT NOT NULL DEFAULT '',
            next_scan_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    await conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{CURSOR_TABLE}_due "
        f"ON {CURSOR_TABLE} (next_scan_at, location_key)"
    )
    await conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {MIGRATION_TABLE} (
            migration_key TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
        """
    )
    await conn.commit()
    await _remove_unsafe_target_enrollment(db)
    try:
        setattr(db, "_target_location_tables_ready", True)
    except Exception:
        pass


async def save_target_location(
    db: Any,
    *,
    scope_type: str,
    scope_id: int | str,
    zip_code: str,
    store_id: str,
    store_name: str,
    address_line: str,
    city: str,
    state: str,
    postal_code: str,
    latitude: str | float,
    longitude: str | float,
) -> TargetLocationContext:
    await ensure_target_location_tables(db)
    normalized = _validated_location(
        scope_type=scope_type,
        scope_id=scope_id,
        zip_code=zip_code,
        store_id=store_id,
        store_name=store_name,
        address_line=address_line,
        city=city,
        state=state,
        postal_code=postal_code,
        latitude=latitude,
        longitude=longitude,
    )
    conn = db.require_conn()
    now = utc_now_iso()
    await conn.execute(
        f"""
        INSERT INTO {LOCATION_TABLE} (
            scope_type, scope_id, zip_code, store_id, store_name,
            address_line, city, state, postal_code, latitude, longitude,
            enabled, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
        ON CONFLICT(scope_type, scope_id) DO UPDATE SET
            zip_code = excluded.zip_code,
            store_id = excluded.store_id,
            store_name = excluded.store_name,
            address_line = excluded.address_line,
            city = excluded.city,
            state = excluded.state,
            postal_code = excluded.postal_code,
            latitude = excluded.latitude,
            longitude = excluded.longitude,
            enabled = 1,
            updated_at = excluded.updated_at
        """,
        (
            normalized.scope_type,
            normalized.scope_id,
            normalized.zip_code,
            normalized.store_id,
            normalized.store_name,
            normalized.address_line,
            normalized.city,
            normalized.state,
            normalized.postal_code,
            normalized.latitude,
            normalized.longitude,
            now,
            now,
        ),
    )
    await conn.execute(
        f"""
        INSERT INTO {CURSOR_TABLE} (location_key, cursor_tcin, next_scan_at, updated_at)
        VALUES (?, '', ?, ?)
        ON CONFLICT(location_key) DO UPDATE SET next_scan_at = excluded.next_scan_at,
            updated_at = excluded.updated_at
        """,
        (normalized.location_key, now, now),
    )
    await conn.commit()
    return normalized


async def save_guild_target_location(
    db: Any,
    *,
    guild_id: int,
    **location: Any,
) -> TargetLocationContext:
    return await save_target_location(
        db,
        scope_type="guild",
        scope_id=guild_id,
        **location,
    )


async def save_user_target_location(
    db: Any,
    *,
    user_id: int,
    **location: Any,
) -> TargetLocationContext:
    return await save_target_location(
        db,
        scope_type="user",
        scope_id=user_id,
        **location,
    )


async def get_target_location(
    db: Any,
    *,
    scope_type: str,
    scope_id: int | str,
) -> TargetLocationContext | None:
    await ensure_target_location_tables(db)
    conn = db.require_conn()
    cursor = await conn.execute(
        f"""
        SELECT scope_type, scope_id, zip_code, store_id, store_name,
               address_line, city, state, postal_code, latitude, longitude,
               enabled
        FROM {LOCATION_TABLE}
        WHERE scope_type = ? AND scope_id = ?
        LIMIT 1
        """,
        (_scope_type(scope_type), str(scope_id)),
    )
    row = await cursor.fetchone()
    return _location_from_row(row) if row is not None else None


async def get_guild_target_location(
    db: Any,
    guild_id: int,
) -> TargetLocationContext | None:
    return await get_target_location(
        db,
        scope_type="guild",
        scope_id=guild_id,
    )


async def get_user_target_location(
    db: Any,
    user_id: int,
) -> TargetLocationContext | None:
    return await get_target_location(
        db,
        scope_type="user",
        scope_id=user_id,
    )


async def clear_target_location(
    db: Any,
    *,
    scope_type: str,
    scope_id: int | str,
) -> bool:
    await ensure_target_location_tables(db)
    conn = db.require_conn()
    cursor = await conn.execute(
        f"UPDATE {LOCATION_TABLE} SET enabled = 0, updated_at = ? "
        "WHERE scope_type = ? AND scope_id = ? AND enabled = 1",
        (utc_now_iso(), _scope_type(scope_type), str(scope_id)),
    )
    await conn.commit()
    return int(getattr(cursor, "rowcount", 0) or 0) > 0


async def list_unique_active_target_locations(db: Any) -> list[TargetLocationContext]:
    await ensure_target_location_tables(db)
    conn = db.require_conn()
    cursor = await conn.execute(
        f"""
        SELECT MIN(scope_type) AS scope_type, MIN(scope_id) AS scope_id,
               zip_code, store_id, MIN(store_name) AS store_name,
               MIN(address_line) AS address_line, MIN(city) AS city,
               MIN(state) AS state, MIN(postal_code) AS postal_code,
               MIN(latitude) AS latitude, MIN(longitude) AS longitude,
               1 AS enabled
        FROM {LOCATION_TABLE}
        WHERE enabled = 1
        GROUP BY store_id, zip_code
        ORDER BY store_id, zip_code
        """
    )
    return [_location_from_row(row) for row in await cursor.fetchall()]


async def get_active_target_location_by_key(
    db: Any,
    *,
    store_id: str,
    zip_code: str,
) -> TargetLocationContext | None:
    await ensure_target_location_tables(db)
    conn = db.require_conn()
    cursor = await conn.execute(
        f"""
        SELECT scope_type, scope_id, zip_code, store_id, store_name,
               address_line, city, state, postal_code, latitude, longitude,
               enabled
        FROM {LOCATION_TABLE}
        WHERE enabled = 1 AND store_id = ? AND zip_code = ?
        ORDER BY CASE scope_type WHEN 'guild' THEN 0 ELSE 1 END, scope_id
        LIMIT 1
        """,
        (str(store_id), str(zip_code)),
    )
    row = await cursor.fetchone()
    return _location_from_row(row) if row is not None else None


async def upsert_target_catalog_seeds(
    db: Any,
    seeds: Iterable[TargetProductSeed],
    *,
    now: datetime | None = None,
) -> int:
    await ensure_target_location_tables(db)
    conn = db.require_conn()
    now_iso = _utc(now).isoformat()
    unique: dict[str, TargetProductSeed] = {}
    for seed in seeds:
        tcin = normalize_tcin(seed.tcin)
        if tcin:
            unique[tcin] = TargetProductSeed(
                tcin=tcin,
                product_url=seed.product_url or canonical_target_product_url(tcin),
            )
    if not unique:
        return 0
    existing: set[str] = set()
    tcins = list(unique)
    for start in range(0, len(tcins), 250):
        batch = tcins[start : start + 250]
        placeholders = ",".join("?" for _ in batch)
        cursor = await conn.execute(
            f"SELECT tcin FROM {CATALOG_TABLE} WHERE tcin IN ({placeholders})",
            tuple(batch),
        )
        existing.update(
            str(_row_get(row, "tcin", 0) or "") for row in await cursor.fetchall()
        )
        values = ",".join("(?, ?, ?, ?)" for _ in batch)
        params: list[Any] = []
        for tcin in batch:
            seed = unique[tcin]
            params.extend((tcin, seed.product_url, now_iso, now_iso))
        await conn.execute(
            f"""
            INSERT INTO {CATALOG_TABLE} (tcin, product_url, first_seen_at, last_seen_at)
            VALUES {values}
            ON CONFLICT(tcin) DO UPDATE SET
                product_url = excluded.product_url,
                last_seen_at = excluded.last_seen_at
            """,
            tuple(params),
        )
    await conn.commit()
    return sum(1 for tcin in unique if tcin not in existing)


async def stage_due_target_location_batches(
    db: Any,
    *,
    locations_per_cycle: int,
    products_per_location: int,
    scan_spacing_seconds: int,
    now: datetime | None = None,
) -> tuple[int, int]:
    """Materialize bounded catalog slices for due unique Target locations.

    The global catalog remains one row per TCIN. Only a rotating bounded slice is
    copied into the exact location-state table, so adding more guilds does not
    create an immediate catalog × guild explosion.
    """

    await ensure_target_location_tables(db)
    from sniperplug.target_watcher.storage import upsert_product_seeds

    conn = db.require_conn()
    now_dt = _utc(now)
    now_iso = now_dt.isoformat()
    locations = await list_unique_active_target_locations(db)
    active = {location.location_key: location for location in locations}
    for location in locations:
        await conn.execute(
            f"""
            INSERT INTO {CURSOR_TABLE} (location_key, cursor_tcin, next_scan_at, updated_at)
            VALUES (?, '', ?, ?)
            ON CONFLICT(location_key) DO NOTHING
            """,
            (location.location_key, now_iso, now_iso),
        )
    if active:
        placeholders = ",".join("?" for _ in active)
        await conn.execute(
            f"DELETE FROM {CURSOR_TABLE} WHERE location_key NOT IN ({placeholders})",
            tuple(active),
        )
    else:
        await conn.execute(f"DELETE FROM {CURSOR_TABLE}")
    await conn.commit()
    if not active:
        return 0, 0

    cursor = await conn.execute(
        f"""
        SELECT location_key, cursor_tcin
        FROM {CURSOR_TABLE}
        WHERE next_scan_at <= ?
        ORDER BY next_scan_at, location_key
        LIMIT ?
        """,
        (now_iso, max(1, int(locations_per_cycle))),
    )
    due = await cursor.fetchall()
    staged_locations = staged_products = 0
    for row in due:
        location_key = str(_row_get(row, "location_key", 0) or "")
        last_tcin = str(_row_get(row, "cursor_tcin", 1) or "")
        location = active.get(location_key)
        if location is None:
            continue
        catalog_cursor = await conn.execute(
            f"""
            SELECT tcin, product_url
            FROM {CATALOG_TABLE}
            WHERE tcin > ?
            ORDER BY tcin
            LIMIT ?
            """,
            (last_tcin, max(1, int(products_per_location))),
        )
        rows = await catalog_cursor.fetchall()
        if not rows and last_tcin:
            catalog_cursor = await conn.execute(
                f"SELECT tcin, product_url FROM {CATALOG_TABLE} ORDER BY tcin LIMIT ?",
                (max(1, int(products_per_location)),),
            )
            rows = await catalog_cursor.fetchall()
        seeds = [
            TargetProductSeed(
                tcin=str(_row_get(item, "tcin", 0) or ""),
                product_url=str(_row_get(item, "product_url", 1) or ""),
            )
            for item in rows
            if normalize_tcin(_row_get(item, "tcin", 0))
        ]
        if seeds:
            await upsert_product_seeds(
                db,
                seeds,
                store_id=location.store_id,
                zip_code=location.zip_code,
                now=now_dt,
            )
            staged_products += len(seeds)
        next_scan = now_dt + timedelta(seconds=max(10, int(scan_spacing_seconds)))
        await conn.execute(
            f"""
            UPDATE {CURSOR_TABLE}
            SET cursor_tcin = ?, next_scan_at = ?, updated_at = ?
            WHERE location_key = ?
            """,
            (
                seeds[-1].tcin if seeds else "",
                next_scan.isoformat(),
                now_iso,
                location_key,
            ),
        )
        staged_locations += 1
    await conn.commit()
    return staged_locations, staged_products


async def prune_orphan_target_product_rows(db: Any) -> int:
    await ensure_target_location_tables(db)
    from sniperplug.target_watcher.storage import PRODUCT_TABLE, ensure_target_watcher_tables

    await ensure_target_watcher_tables(db)
    conn = db.require_conn()
    active = {
        location.location_key for location in await list_unique_active_target_locations(db)
    }
    cursor = await conn.execute(
        f"SELECT DISTINCT store_id, zip_code FROM {PRODUCT_TABLE}"
    )
    removed = 0
    for row in await cursor.fetchall():
        store_id = str(_row_get(row, "store_id", 0) or "")
        zip_code = str(_row_get(row, "zip_code", 1) or "")
        if target_location_key(store_id, zip_code) in active:
            continue
        result = await conn.execute(
            f"DELETE FROM {PRODUCT_TABLE} WHERE store_id = ? AND zip_code = ?",
            (store_id, zip_code),
        )
        removed += max(0, int(getattr(result, "rowcount", 0) or 0))
    await conn.commit()
    return removed


async def target_card_matches_guild_location(
    db: Any,
    *,
    guild_id: int,
    card: Any,
) -> bool:
    return _card_matches_location(
        card,
        await get_guild_target_location(db, guild_id),
    )


async def target_card_matches_user_location(
    db: Any,
    *,
    user_id: int,
    card: Any,
) -> bool:
    return _card_matches_location(
        card,
        await get_user_target_location(db, user_id),
    )


def target_location_key(store_id: str, zip_code: str) -> str:
    return f"{str(store_id).strip()}:{str(zip_code).strip()}"


def _card_matches_location(
    card: Any,
    location: TargetLocationContext | None,
) -> bool:
    attrs = dict(getattr(card, "variant_attributes", None) or {})
    if str(attrs.get("targetLocationScope") or "local").lower() == "national":
        return True
    if location is None or not location.enabled:
        return False
    return bool(
        str(attrs.get("targetStoreId") or "") == location.store_id
        and str(attrs.get("targetZip") or "") == location.zip_code
    )


async def _remove_unsafe_target_enrollment(db: Any) -> None:
    """Remove the original global Target enrollment unless a guild chose a store."""

    conn = db.require_conn()
    marker = await conn.execute(
        f"SELECT 1 FROM {MIGRATION_TABLE} WHERE migration_key = ? LIMIT 1",
        (REMOVE_UNSAFE_TARGET_MIGRATION,),
    )
    if await marker.fetchone() is not None:
        return
    table = await conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' "
        "AND name = 'guild_public_alert_settings' LIMIT 1"
    )
    if await table.fetchone() is not None:
        configured_cursor = await conn.execute(
            f"SELECT scope_id FROM {LOCATION_TABLE} "
            "WHERE scope_type = 'guild' AND enabled = 1"
        )
        configured = {
            str(_row_get(row, "scope_id", 0) or "")
            for row in await configured_cursor.fetchall()
        }
        cursor = await conn.execute(
            "SELECT guild_id, retailers_json FROM guild_public_alert_settings"
        )
        for row in await cursor.fetchall():
            guild_id = str(_row_get(row, "guild_id", 0) or "")
            if guild_id in configured:
                continue
            try:
                retailers = [
                    str(value).strip().lower().replace(" ", "_").replace("-", "_")
                    for value in json.loads(
                        str(_row_get(row, "retailers_json", 1) or "[]")
                    )
                ]
            except Exception:
                continue
            cleaned = [value for value in retailers if value not in {"target", "target_store", "target.com"}]
            if cleaned == retailers:
                continue
            await conn.execute(
                "UPDATE guild_public_alert_settings SET retailers_json = ?, updated_at = ? "
                "WHERE CAST(guild_id AS TEXT) = ?",
                (json.dumps(list(dict.fromkeys(cleaned))), utc_now_iso(), guild_id),
            )
    await conn.execute(
        f"INSERT INTO {MIGRATION_TABLE} (migration_key, applied_at) VALUES (?, ?) "
        "ON CONFLICT(migration_key) DO NOTHING",
        (REMOVE_UNSAFE_TARGET_MIGRATION, utc_now_iso()),
    )
    await conn.commit()


def _validated_location(
    *,
    scope_type: str,
    scope_id: int | str,
    zip_code: str,
    store_id: str,
    store_name: str,
    address_line: str,
    city: str,
    state: str,
    postal_code: str,
    latitude: str | float,
    longitude: str | float,
) -> TargetLocationContext:
    scope = _scope_type(scope_type)
    identifier = str(scope_id).strip()
    zip_value = "".join(character for character in str(zip_code) if character.isdigit())
    store_value = str(store_id).strip()
    state_value = str(state).strip().upper()
    try:
        lat = float(latitude)
        lon = float(longitude)
    except (TypeError, ValueError) as error:
        raise ValueError("Target store coordinates were missing or invalid") from error
    if not identifier:
        raise ValueError("Target location scope ID is required")
    if len(zip_value) != 5:
        raise ValueError("Target ZIP code must contain exactly five digits")
    if not store_value.isdigit():
        raise ValueError("Target store ID must be numeric")
    if len(state_value) != 2 or not state_value.isalpha():
        raise ValueError("Target store state must be a two-letter code")
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("Target store coordinates were outside valid bounds")
    name = " ".join(str(store_name or "").split())
    if not name:
        raise ValueError("Target store name is required")
    return TargetLocationContext(
        scope_type=scope,
        scope_id=identifier,
        zip_code=zip_value,
        store_id=store_value,
        store_name=name[:200],
        address_line=" ".join(str(address_line or "").split())[:300],
        city=" ".join(str(city or "").split())[:120],
        state=state_value,
        postal_code=" ".join(str(postal_code or zip_value).split())[:20],
        latitude=f"{lat:.6f}".rstrip("0").rstrip("."),
        longitude=f"{lon:.6f}".rstrip("0").rstrip("."),
        enabled=True,
    )


def _scope_type(value: str) -> str:
    scope = str(value or "").strip().lower()
    if scope not in {"guild", "user"}:
        raise ValueError("Target location scope must be guild or user")
    return scope


def _location_from_row(row: Any) -> TargetLocationContext:
    return TargetLocationContext(
        scope_type=str(_row_get(row, "scope_type", 0) or ""),
        scope_id=str(_row_get(row, "scope_id", 1) or ""),
        zip_code=str(_row_get(row, "zip_code", 2) or ""),
        store_id=str(_row_get(row, "store_id", 3) or ""),
        store_name=str(_row_get(row, "store_name", 4) or ""),
        address_line=str(_row_get(row, "address_line", 5) or ""),
        city=str(_row_get(row, "city", 6) or ""),
        state=str(_row_get(row, "state", 7) or ""),
        postal_code=str(_row_get(row, "postal_code", 8) or ""),
        latitude=str(_row_get(row, "latitude", 9) or ""),
        longitude=str(_row_get(row, "longitude", 10) or ""),
        enabled=bool(int(_row_get(row, "enabled", 11) or 0)),
    )


def _utc(value: datetime | None) -> datetime:
    result = value or datetime.now(timezone.utc)
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _row_get(row: Any, key: str, index: int) -> Any:
    if row is None:
        return None
    try:
        return row[key]
    except Exception:
        pass
    try:
        return row[index]
    except Exception:
        pass
    if isinstance(row, dict):
        return row.get(key)
    return getattr(row, key, None)
