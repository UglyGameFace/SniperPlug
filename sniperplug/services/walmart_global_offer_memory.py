from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sniperplug.models.candidate import SourceCandidate


GLOBAL_OFFER_MEMORY_TABLE = "walmart_offer_price_memory"
IDENTITY_VERSION = "v1"
MIN_STABLE_CONFIRMATIONS = 2
MIN_CONFIRMATION_GAP_SECONDS = 4 * 60 * 60
STABLE_REFERENCE_MAX_AGE_DAYS = 30
STALE_ROW_RETENTION_DAYS = 90
MAX_GLOBAL_ROWS = 250_000
CLEANUP_INTERVAL_SECONDS = 6 * 60 * 60

_last_cleanup_monotonic = 0.0


@dataclass(frozen=True)
class ExactOfferIdentity:
    identity_key: str
    item_id: str
    offer_id: str
    seller_key: str
    variant_key: str
    condition_key: str
    fulfillment_key: str


@dataclass(frozen=True)
class GlobalOfferObservation:
    identity: ExactOfferIdentity
    status: str
    previous_price: float | None
    current_price: float | None
    stable_reference_price: float | None
    lowest_seen_price: float | None
    stable_seen_count: int = 0
    candidate_seen_count: int = 0
    drop_percent: float = 0.0
    drop_dollars: float = 0.0
    reason: str = ""

    @property
    def should_public_post(self) -> bool:
        return (
            self.status in {"lower_price", "new_low"}
            and self.stable_reference_price is not None
            and self.drop_percent > 0
            and self.drop_dollars > 0
        )


async def ensure_global_offer_memory_table(db: Any) -> None:
    conn = db.require_conn()
    await conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {GLOBAL_OFFER_MEMORY_TABLE} (
            identity_key TEXT PRIMARY KEY,
            identity_version TEXT NOT NULL,
            item_id TEXT NOT NULL,
            offer_id TEXT NOT NULL,
            seller_key TEXT NOT NULL,
            variant_key TEXT NOT NULL,
            condition_key TEXT NOT NULL,
            fulfillment_key TEXT NOT NULL,
            current_price_cents INTEGER NOT NULL,
            candidate_price_cents INTEGER NOT NULL,
            candidate_seen_count INTEGER NOT NULL DEFAULT 1,
            candidate_first_seen_at TEXT NOT NULL,
            candidate_last_seen_at TEXT NOT NULL,
            stable_price_cents INTEGER,
            stable_seen_count INTEGER NOT NULL DEFAULT 0,
            stable_first_seen_at TEXT,
            stable_last_confirmed_at TEXT,
            lowest_seen_cents INTEGER NOT NULL,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            last_status TEXT NOT NULL
        )
        """
    )
    await conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{GLOBAL_OFFER_MEMORY_TABLE}_item "
        f"ON {GLOBAL_OFFER_MEMORY_TABLE} (item_id, offer_id, seller_key)"
    )
    await conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{GLOBAL_OFFER_MEMORY_TABLE}_last_seen "
        f"ON {GLOBAL_OFFER_MEMORY_TABLE} (last_seen_at)"
    )
    await conn.commit()


def exact_offer_identity(candidate: SourceCandidate) -> ExactOfferIdentity | None:
    """Return a fail-closed exact Walmart offer fingerprint.

    Public observed-price proof is allowed only after the exact Walmart detail
    endpoint verified the item ID. Search-result identity alone is discovery,
    never historical price proof.
    """

    attrs = dict(getattr(candidate, "variant_attributes", None) or {})
    if _normalized(attrs.get("exactDetailPriceProof")) != "yes":
        return None

    item_id = _walmart_item_id(candidate)
    exact_item_id = _digits(attrs.get("exactDetailItemId"))
    if not item_id or not exact_item_id or item_id != exact_item_id:
        return None

    offer_id = _clean_id(getattr(candidate, "selected_offer_id", None))
    if not offer_id:
        return None

    seller_key = _seller_key(candidate, attrs)
    if not seller_key:
        return None

    variant_payload = {
        "variant_label": _normalized(getattr(candidate, "variant_label", None)),
        "pack_size": _normalized(getattr(candidate, "pack_size", None)),
        "color": _normalized(getattr(candidate, "color", None)),
        "platform": _normalized(getattr(candidate, "platform", None)),
        "model": _normalized(getattr(candidate, "model", None)),
        "size": _normalized(attrs.get("size")),
        "attr_color": _normalized(attrs.get("color")),
        "attr_platform": _normalized(attrs.get("platform")),
        "attr_model": _normalized(attrs.get("model") or attrs.get("modelNumber")),
        "attr_pack": _normalized(attrs.get("packSize")),
        "attr_unit": _normalized(attrs.get("unitSize")),
    }
    variant_key = _digest(variant_payload)

    condition_key = _normalized(
        getattr(candidate, "condition", None)
        or getattr(candidate, "api_condition", None)
        or attrs.get("condition")
        or "unspecified"
    )
    fulfillment_key = _normalized(
        getattr(candidate, "fulfillment_type", None)
        or attrs.get("fulfillment")
        or "unspecified"
    )

    identity_payload = {
        "version": IDENTITY_VERSION,
        "item_id": item_id,
        "offer_id": offer_id,
        "seller_key": seller_key,
        "variant_key": variant_key,
        "condition_key": condition_key,
        "fulfillment_key": fulfillment_key,
    }
    identity_key = f"walmart-offer:{IDENTITY_VERSION}:{_digest(identity_payload)}"
    return ExactOfferIdentity(
        identity_key=identity_key,
        item_id=item_id,
        offer_id=offer_id,
        seller_key=seller_key,
        variant_key=variant_key,
        condition_key=condition_key,
        fulfillment_key=fulfillment_key,
    )


async def observe_exact_offer(
    conn: Any,
    *,
    candidate: SourceCandidate,
    identity: ExactOfferIdentity,
    now: datetime | None = None,
    min_discount: int = 50,
    min_drop_dollars: float = 5.0,
) -> GlobalOfferObservation:
    now_dt = now or datetime.now(timezone.utc)
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=timezone.utc)
    now_iso = now_dt.astimezone(timezone.utc).isoformat()

    current_price = _positive_price(
        getattr(candidate, "api_current_price", None)
        or getattr(candidate, "current_price", None)
    )
    if current_price is None:
        return GlobalOfferObservation(
            identity=identity,
            status="missing_price",
            previous_price=None,
            current_price=None,
            stable_reference_price=None,
            lowest_seen_price=None,
            reason="exact Walmart detail current price missing",
        )
    current_cents = _price_to_cents(current_price)

    cursor = await conn.execute(
        f"SELECT * FROM {GLOBAL_OFFER_MEMORY_TABLE} WHERE identity_key = ?",
        (identity.identity_key,),
    )
    row = await cursor.fetchone()

    if row is not None and not _row_identity_matches(row, identity):
        return GlobalOfferObservation(
            identity=identity,
            status="identity_collision",
            previous_price=None,
            current_price=current_price,
            stable_reference_price=None,
            lowest_seen_price=None,
            reason="stored fingerprint components did not match; row was not used",
        )

    if row is None:
        await conn.execute(
            f"""
            INSERT INTO {GLOBAL_OFFER_MEMORY_TABLE} (
                identity_key, identity_version, item_id, offer_id, seller_key,
                variant_key, condition_key, fulfillment_key,
                current_price_cents, candidate_price_cents, candidate_seen_count,
                candidate_first_seen_at, candidate_last_seen_at,
                stable_price_cents, stable_seen_count, stable_first_seen_at,
                stable_last_confirmed_at, lowest_seen_cents,
                first_seen_at, last_seen_at, last_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, NULL, 0, NULL, NULL, ?, ?, ?, 'learning')
            """,
            (
                identity.identity_key,
                IDENTITY_VERSION,
                identity.item_id,
                identity.offer_id,
                identity.seller_key,
                identity.variant_key,
                identity.condition_key,
                identity.fulfillment_key,
                current_cents,
                current_cents,
                now_iso,
                now_iso,
                current_cents,
                now_iso,
                now_iso,
            ),
        )
        return GlobalOfferObservation(
            identity=identity,
            status="learning",
            previous_price=None,
            current_price=current_price,
            stable_reference_price=None,
            lowest_seen_price=current_price,
            candidate_seen_count=1,
            reason="first exact-offer observation; waiting for a separated confirmation",
        )

    previous_price = _cents_to_price(_row_get(row, "current_price_cents"))
    stable_price = _valid_stable_reference(row, now_dt)
    stable_seen_count = int(_row_get(row, "stable_seen_count") or 0)
    lowest_seen = _cents_to_price(_row_get(row, "lowest_seen_cents"))

    status = "same_or_higher"
    reason = "exact offer price is not below a fresh stable reference"
    drop_percent = 0.0
    drop_dollars = 0.0
    if stable_price is None:
        status = "learning"
        reason = "stable reference is not confirmed or is older than the trust window"
    elif current_price < stable_price:
        drop_dollars = round(stable_price - current_price, 2)
        drop_percent = round(drop_dollars / stable_price * 100.0, 2)
        if drop_percent >= max(1, int(min_discount)) and drop_dollars >= float(min_drop_dollars):
            status = "new_low" if lowest_seen is not None and current_price < lowest_seen else "lower_price"
            reason = "same exact item, offer, seller, variant, condition, and fulfillment is below a confirmed stable price"
        else:
            reason = "observed exact-offer drop is below the public threshold"

    candidate_price_cents = int(_row_get(row, "candidate_price_cents") or current_cents)
    candidate_seen_count = int(_row_get(row, "candidate_seen_count") or 1)
    candidate_first_seen_at = str(_row_get(row, "candidate_first_seen_at") or now_iso)
    candidate_last_seen_at = _parse_datetime(_row_get(row, "candidate_last_seen_at")) or now_dt

    confirmation_advanced = False
    if candidate_price_cents != current_cents:
        candidate_price_cents = current_cents
        candidate_seen_count = 1
        candidate_first_seen_at = now_iso
        candidate_last_seen_at = now_dt
    elif (now_dt - candidate_last_seen_at).total_seconds() >= MIN_CONFIRMATION_GAP_SECONDS:
        candidate_seen_count += 1
        candidate_last_seen_at = now_dt
        confirmation_advanced = True

    next_stable_cents = _int_or_none(_row_get(row, "stable_price_cents"))
    next_stable_seen_count = stable_seen_count
    next_stable_first_seen_at = _row_get(row, "stable_first_seen_at")
    next_stable_last_confirmed_at = _row_get(row, "stable_last_confirmed_at")
    if candidate_seen_count >= MIN_STABLE_CONFIRMATIONS and (
        next_stable_cents != current_cents or confirmation_advanced
    ):
        if next_stable_cents != current_cents:
            next_stable_cents = current_cents
            next_stable_seen_count = candidate_seen_count
            next_stable_first_seen_at = candidate_first_seen_at
        else:
            next_stable_seen_count = max(next_stable_seen_count, candidate_seen_count)
        next_stable_last_confirmed_at = now_iso

    lowest_cents = min(
        value
        for value in (
            _int_or_none(_row_get(row, "lowest_seen_cents")),
            current_cents,
        )
        if value is not None
    )

    await conn.execute(
        f"""
        UPDATE {GLOBAL_OFFER_MEMORY_TABLE}
        SET current_price_cents = ?,
            candidate_price_cents = ?,
            candidate_seen_count = ?,
            candidate_first_seen_at = ?,
            candidate_last_seen_at = ?,
            stable_price_cents = ?,
            stable_seen_count = ?,
            stable_first_seen_at = ?,
            stable_last_confirmed_at = ?,
            lowest_seen_cents = ?,
            last_seen_at = ?,
            last_status = ?
        WHERE identity_key = ?
        """,
        (
            current_cents,
            candidate_price_cents,
            candidate_seen_count,
            candidate_first_seen_at,
            candidate_last_seen_at.astimezone(timezone.utc).isoformat(),
            next_stable_cents,
            next_stable_seen_count,
            next_stable_first_seen_at,
            next_stable_last_confirmed_at,
            lowest_cents,
            now_iso,
            status,
            identity.identity_key,
        ),
    )

    return GlobalOfferObservation(
        identity=identity,
        status=status,
        previous_price=previous_price,
        current_price=current_price,
        stable_reference_price=stable_price,
        lowest_seen_price=lowest_seen,
        stable_seen_count=stable_seen_count,
        candidate_seen_count=candidate_seen_count,
        drop_percent=drop_percent,
        drop_dollars=drop_dollars,
        reason=reason,
    )


async def maybe_prune_global_offer_memory(conn: Any, *, now: datetime | None = None) -> None:
    global _last_cleanup_monotonic
    monotonic_now = time.monotonic()
    if monotonic_now - _last_cleanup_monotonic < CLEANUP_INTERVAL_SECONDS:
        return
    _last_cleanup_monotonic = monotonic_now

    now_dt = now or datetime.now(timezone.utc)
    cutoff = (now_dt - timedelta(days=STALE_ROW_RETENTION_DAYS)).isoformat()
    await conn.execute(
        f"DELETE FROM {GLOBAL_OFFER_MEMORY_TABLE} WHERE last_seen_at < ?",
        (cutoff,),
    )
    await conn.execute(
        f"""
        DELETE FROM {GLOBAL_OFFER_MEMORY_TABLE}
        WHERE identity_key IN (
            SELECT identity_key
            FROM {GLOBAL_OFFER_MEMORY_TABLE}
            ORDER BY last_seen_at DESC
            LIMIT -1 OFFSET ?
        )
        """,
        (MAX_GLOBAL_ROWS,),
    )


def _valid_stable_reference(row: Any, now: datetime) -> float | None:
    stable_cents = _int_or_none(_row_get(row, "stable_price_cents"))
    stable_count = int(_row_get(row, "stable_seen_count") or 0)
    confirmed_at = _parse_datetime(_row_get(row, "stable_last_confirmed_at"))
    if stable_cents is None or stable_count < MIN_STABLE_CONFIRMATIONS or confirmed_at is None:
        return None
    if now - confirmed_at > timedelta(days=STABLE_REFERENCE_MAX_AGE_DAYS):
        return None
    return _cents_to_price(stable_cents)


def _row_identity_matches(row: Any, identity: ExactOfferIdentity) -> bool:
    expected = {
        "identity_version": IDENTITY_VERSION,
        "item_id": identity.item_id,
        "offer_id": identity.offer_id,
        "seller_key": identity.seller_key,
        "variant_key": identity.variant_key,
        "condition_key": identity.condition_key,
        "fulfillment_key": identity.fulfillment_key,
    }
    return all(str(_row_get(row, key) or "") == str(value) for key, value in expected.items())


def _seller_key(candidate: SourceCandidate, attrs: dict[str, Any]) -> str:
    seller_id = _clean_id(attrs.get("sellerId"))
    if seller_id:
        return f"id:{seller_id.lower()}"
    walmart_seller = _normalized(attrs.get("walmartSeller")) == "yes"
    seller_name = _normalized(getattr(candidate, "seller_name", None) or attrs.get("seller"))
    if walmart_seller or seller_name in {
        "walmart",
        "walmart.com",
        "walmart stores inc",
        "walmart stores, inc.",
    }:
        return "id:walmart"
    if seller_name:
        return f"name:{seller_name}"
    return ""


def _walmart_item_id(candidate: SourceCandidate) -> str:
    for value in (
        getattr(candidate, "product_id", None),
        getattr(candidate, "sku", None),
        _item_id_from_url(getattr(candidate, "direct_product_url", None)),
        _item_id_from_url(getattr(candidate, "product_url", None)),
    ):
        parsed = _digits(value)
        if parsed:
            return parsed
    return ""


def _item_id_from_url(value: Any) -> str:
    match = re.search(r"/ip/(?:[^/?#]+/)?(\d+)", str(value or ""))
    return match.group(1) if match else ""


def _clean_id(value: Any) -> str:
    text = " ".join(str(value or "").split()).strip()
    return "" if text.lower() in {"", "none", "unknown", "null"} else text


def _digits(value: Any) -> str:
    text = _clean_id(value)
    return text if text.isdigit() else ""


def _normalized(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().replace("_", " ").replace("-", " ").split())


def _digest(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _positive_price(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        parsed = float(str(value).replace("$", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return round(parsed, 2) if parsed > 0 else None


def _price_to_cents(value: float) -> int:
    return int(round(float(value) * 100))


def _cents_to_price(value: Any) -> float | None:
    parsed = _int_or_none(value)
    return round(parsed / 100.0, 2) if parsed is not None else None


def _int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _row_get(row: Any, key: str) -> Any:
    try:
        return row[key]
    except Exception:
        if isinstance(row, dict):
            return row.get(key)
        return getattr(row, key, None)
