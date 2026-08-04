from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from sniperplug.models.candidate import SourceCandidate
from sniperplug.services.walmart_exact_price_enrichment import (
    _percent_off,
    _positive_number,
    _trusted_reference,
)
from sniperplug.services.walmart_exact_queue_drain import tiered_recheck_delay
from sniperplug.services.walmart_exact_verification_queue import (
    QUEUE_RETRY_BASE_SECONDS,
    QUEUE_RETRY_MAX_SECONDS,
    QUEUE_TABLE,
    _candidate_snapshot,
    _classify_exact_candidate,
    _compact_text,
    _price_to_cents,
)
from sniperplug.services.walmart_global_offer_memory import (
    GLOBAL_OFFER_MEMORY_TABLE,
    IDENTITY_VERSION,
    MIN_CONFIRMATION_GAP_SECONDS,
    MIN_STABLE_CONFIRMATIONS,
    ExactOfferIdentity,
    _int_or_none,
    _parse_datetime,
    _positive_price,
    _row_get,
    _row_identity_matches,
    _valid_stable_reference,
    exact_offer_identity,
)


@dataclass(frozen=True)
class ExactQueuePersistenceOutcome:
    claim: Any
    candidate: SourceCandidate | None
    status: str
    error: str
    observed_at: datetime


@dataclass(frozen=True)
class ExactQueuePersistenceResult:
    queue_rows: int = 0
    offer_rows: int = 0
    sql_statements: int = 0


@dataclass(frozen=True)
class _OfferInput:
    candidate: SourceCandidate
    identity: ExactOfferIdentity
    observed_at: datetime
    min_discount: int


_QUEUE_UPDATE_COLUMNS = (
    "item_id",
    "expected_lease_token",
    "status",
    "write_exact",
    "verified_at",
    "exact_current_cents",
    "exact_reference_cents",
    "exact_discount_bps",
    "exact_reference_source",
    "exact_offer_id",
    "exact_seller_key",
    "exact_variant_key",
    "exact_condition_key",
    "exact_fulfillment_key",
    "snapshot_json",
    "last_attempt_at",
    "next_attempt_at",
    "last_error",
)

_OFFER_COLUMNS = (
    "identity_key",
    "identity_version",
    "item_id",
    "offer_id",
    "seller_key",
    "variant_key",
    "condition_key",
    "fulfillment_key",
    "current_price_cents",
    "candidate_price_cents",
    "candidate_seen_count",
    "candidate_first_seen_at",
    "candidate_last_seen_at",
    "stable_price_cents",
    "stable_seen_count",
    "stable_first_seen_at",
    "stable_last_confirmed_at",
    "lowest_seen_cents",
    "first_seen_at",
    "last_seen_at",
    "last_status",
)


async def persist_exact_queue_outcomes_bulk(
    conn: Any,
    outcomes: Iterable[ExactQueuePersistenceOutcome],
    *,
    min_discount: int,
) -> ExactQueuePersistenceResult:
    """Persist one exact-verification batch with bounded Turso statements.

    Exact proof is still computed per candidate, but queue state and global
    offer memory are written in bulk. A 24-item batch uses one queue UPDATE,
    one offer-memory SELECT, and one offer-memory UPSERT instead of dozens of
    serialized remote calls.
    """

    normalized = list(outcomes)
    if not normalized:
        return ExactQueuePersistenceResult()

    queue_rows: list[tuple[Any, ...]] = []
    offer_inputs: list[_OfferInput] = []
    for outcome in normalized:
        observed_at = _as_utc(outcome.observed_at)
        if outcome.candidate is None:
            queue_rows.append(
                _failure_queue_row(
                    outcome.claim,
                    status=outcome.status,
                    error=outcome.error,
                    observed_at=observed_at,
                )
            )
            continue

        identity = exact_offer_identity(outcome.candidate)
        if identity is None:
            raise RuntimeError(
                "Exact Walmart candidate lost seller/offer identity before persistence: "
                f"{getattr(outcome.claim, 'item_id', '')}"
            )
        queue_rows.append(
            _verified_queue_row(
                outcome.claim,
                candidate=outcome.candidate,
                identity=identity,
                observed_at=observed_at,
                min_discount=min_discount,
            )
        )
        offer_inputs.append(
            _OfferInput(
                candidate=outcome.candidate,
                identity=identity,
                observed_at=observed_at,
                min_discount=min_discount,
            )
        )

    sql_statements = 0
    if queue_rows:
        await _bulk_update_queue(conn, queue_rows)
        sql_statements += 1

    offer_rows = 0
    if offer_inputs:
        offer_rows, offer_statements = await _bulk_observe_exact_offers(
            conn,
            offer_inputs,
        )
        sql_statements += offer_statements

    return ExactQueuePersistenceResult(
        queue_rows=len(queue_rows),
        offer_rows=offer_rows,
        sql_statements=sql_statements,
    )


async def _bulk_update_queue(
    conn: Any,
    rows: list[tuple[Any, ...]],
) -> None:
    placeholders = ",".join(
        "(" + ",".join("?" for _ in _QUEUE_UPDATE_COLUMNS) + ")"
        for _ in rows
    )
    params = tuple(value for row in rows for value in row)
    await conn.execute(
        f"""
        WITH updates ({", ".join(_QUEUE_UPDATE_COLUMNS)}) AS (
            VALUES {placeholders}
        )
        UPDATE {QUEUE_TABLE} AS target
        SET status = updates.status,
            verified_at = CASE
                WHEN updates.write_exact = 1 THEN updates.verified_at
                ELSE target.verified_at
            END,
            exact_current_cents = CASE
                WHEN updates.write_exact = 1 THEN updates.exact_current_cents
                ELSE target.exact_current_cents
            END,
            exact_reference_cents = CASE
                WHEN updates.write_exact = 1 THEN updates.exact_reference_cents
                ELSE target.exact_reference_cents
            END,
            exact_discount_bps = CASE
                WHEN updates.write_exact = 1 THEN updates.exact_discount_bps
                ELSE target.exact_discount_bps
            END,
            exact_reference_source = CASE
                WHEN updates.write_exact = 1 THEN updates.exact_reference_source
                ELSE target.exact_reference_source
            END,
            exact_offer_id = CASE
                WHEN updates.write_exact = 1 THEN updates.exact_offer_id
                ELSE target.exact_offer_id
            END,
            exact_seller_key = CASE
                WHEN updates.write_exact = 1 THEN updates.exact_seller_key
                ELSE target.exact_seller_key
            END,
            exact_variant_key = CASE
                WHEN updates.write_exact = 1 THEN updates.exact_variant_key
                ELSE target.exact_variant_key
            END,
            exact_condition_key = CASE
                WHEN updates.write_exact = 1 THEN updates.exact_condition_key
                ELSE target.exact_condition_key
            END,
            exact_fulfillment_key = CASE
                WHEN updates.write_exact = 1 THEN updates.exact_fulfillment_key
                ELSE target.exact_fulfillment_key
            END,
            snapshot_json = CASE
                WHEN updates.write_exact = 1 THEN updates.snapshot_json
                ELSE target.snapshot_json
            END,
            last_attempt_at = updates.last_attempt_at,
            attempt_count = target.attempt_count + 1,
            next_attempt_at = updates.next_attempt_at,
            last_error = updates.last_error,
            lease_token = '',
            lease_until = NULL
        FROM updates
        WHERE target.item_id = updates.item_id
          AND target.lease_token = updates.expected_lease_token
        """,
        params,
    )


def _verified_queue_row(
    claim: Any,
    *,
    candidate: SourceCandidate,
    identity: ExactOfferIdentity,
    observed_at: datetime,
    min_discount: int,
) -> tuple[Any, ...]:
    current = _positive_number(
        getattr(candidate, "api_current_price", None)
        or getattr(candidate, "current_price", None)
    )
    reference = _trusted_reference(candidate)
    discount = _percent_off(current, reference) or 0.0
    status = _classify_exact_candidate(candidate, min_discount=min_discount)
    next_attempt = (
        observed_at + tiered_recheck_delay(status, discount)
    ).isoformat()
    return (
        str(getattr(claim, "item_id", "") or ""),
        str(getattr(claim, "lease_token", "") or ""),
        status,
        1,
        observed_at.isoformat(),
        _price_to_cents(current),
        _price_to_cents(reference),
        int(round(discount * 100)),
        _compact_text(getattr(candidate, "api_reference_path", None), 300),
        identity.offer_id,
        identity.seller_key,
        identity.variant_key,
        identity.condition_key,
        identity.fulfillment_key,
        _candidate_snapshot(candidate),
        observed_at.isoformat(),
        next_attempt,
        "",
    )


def _failure_queue_row(
    claim: Any,
    *,
    status: str,
    error: str,
    observed_at: datetime,
) -> tuple[Any, ...]:
    attempts = max(1, int(getattr(claim, "attempt_count", 0) or 0) + 1)
    if status in {"identity_mismatch", "incomplete_identity"}:
        delay_seconds = 12 * 60 * 60
        stored_status = status
    elif status == "provider_unavailable":
        delay_seconds = 30 * 60
        stored_status = "retry"
    else:
        delay_seconds = min(
            QUEUE_RETRY_MAX_SECONDS,
            QUEUE_RETRY_BASE_SECONDS * (2 ** min(6, attempts - 1)),
        )
        stored_status = "retry"
    return (
        str(getattr(claim, "item_id", "") or ""),
        str(getattr(claim, "lease_token", "") or ""),
        stored_status,
        0,
        None,
        None,
        None,
        None,
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        observed_at.isoformat(),
        (observed_at + timedelta(seconds=delay_seconds)).isoformat(),
        _compact_text(error, 500),
    )


async def _bulk_observe_exact_offers(
    conn: Any,
    inputs: list[_OfferInput],
) -> tuple[int, int]:
    identities = [value.identity.identity_key for value in inputs]
    placeholders = ",".join("?" for _ in identities)
    cursor = await conn.execute(
        f"""
        SELECT *
        FROM {GLOBAL_OFFER_MEMORY_TABLE}
        WHERE identity_key IN ({placeholders})
        """,
        tuple(identities),
    )
    existing = {
        str(_row_get(row, "identity_key") or ""): row
        for row in await cursor.fetchall()
    }

    rows: list[tuple[Any, ...]] = []
    for value in inputs:
        row = existing.get(value.identity.identity_key)
        if row is not None and not _row_identity_matches(row, value.identity):
            continue
        prepared = _prepare_offer_row(value, row)
        if prepared is not None:
            rows.append(prepared)

    if not rows:
        return 0, 1

    value_sql = ",".join(
        "(" + ",".join("?" for _ in _OFFER_COLUMNS) + ")"
        for _ in rows
    )
    params = tuple(value for row in rows for value in row)
    await conn.execute(
        f"""
        INSERT INTO {GLOBAL_OFFER_MEMORY_TABLE} (
            {", ".join(_OFFER_COLUMNS)}
        ) VALUES {value_sql}
        ON CONFLICT(identity_key) DO UPDATE SET
            current_price_cents = excluded.current_price_cents,
            candidate_price_cents = excluded.candidate_price_cents,
            candidate_seen_count = excluded.candidate_seen_count,
            candidate_first_seen_at = excluded.candidate_first_seen_at,
            candidate_last_seen_at = excluded.candidate_last_seen_at,
            stable_price_cents = excluded.stable_price_cents,
            stable_seen_count = excluded.stable_seen_count,
            stable_first_seen_at = excluded.stable_first_seen_at,
            stable_last_confirmed_at = excluded.stable_last_confirmed_at,
            lowest_seen_cents = excluded.lowest_seen_cents,
            last_seen_at = excluded.last_seen_at,
            last_status = excluded.last_status
        WHERE {GLOBAL_OFFER_MEMORY_TABLE}.identity_version = excluded.identity_version
          AND {GLOBAL_OFFER_MEMORY_TABLE}.item_id = excluded.item_id
          AND {GLOBAL_OFFER_MEMORY_TABLE}.offer_id = excluded.offer_id
          AND {GLOBAL_OFFER_MEMORY_TABLE}.seller_key = excluded.seller_key
          AND {GLOBAL_OFFER_MEMORY_TABLE}.variant_key = excluded.variant_key
          AND {GLOBAL_OFFER_MEMORY_TABLE}.condition_key = excluded.condition_key
          AND {GLOBAL_OFFER_MEMORY_TABLE}.fulfillment_key = excluded.fulfillment_key
        """,
        params,
    )
    return len(rows), 2


def _prepare_offer_row(
    value: _OfferInput,
    row: Any | None,
) -> tuple[Any, ...] | None:
    observed_at = _as_utc(value.observed_at)
    now_iso = observed_at.isoformat()
    current_price = _positive_price(
        getattr(value.candidate, "api_current_price", None)
        or getattr(value.candidate, "current_price", None)
    )
    if current_price is None:
        return None
    current_cents = int(round(current_price * 100))

    if row is None:
        return (
            value.identity.identity_key,
            IDENTITY_VERSION,
            value.identity.item_id,
            value.identity.offer_id,
            value.identity.seller_key,
            value.identity.variant_key,
            value.identity.condition_key,
            value.identity.fulfillment_key,
            current_cents,
            current_cents,
            1,
            now_iso,
            now_iso,
            None,
            0,
            None,
            None,
            current_cents,
            now_iso,
            now_iso,
            "learning",
        )

    stable_price = _valid_stable_reference(row, observed_at)
    stable_seen_count = int(_row_get(row, "stable_seen_count") or 0)
    lowest_seen_cents = _int_or_none(_row_get(row, "lowest_seen_cents"))

    status = "same_or_higher"
    if stable_price is None:
        status = "learning"
    elif current_price < stable_price:
        drop_dollars = round(stable_price - current_price, 2)
        drop_percent = round(drop_dollars / stable_price * 100.0, 2)
        if (
            drop_percent >= max(1, int(value.min_discount))
            and drop_dollars >= 5.0
        ):
            lowest_seen = (
                round(lowest_seen_cents / 100.0, 2)
                if lowest_seen_cents is not None
                else None
            )
            status = (
                "new_low"
                if lowest_seen is not None and current_price < lowest_seen
                else "lower_price"
            )

    candidate_price_cents = int(
        _row_get(row, "candidate_price_cents") or current_cents
    )
    candidate_seen_count = int(
        _row_get(row, "candidate_seen_count") or 1
    )
    candidate_first_seen_at = str(
        _row_get(row, "candidate_first_seen_at") or now_iso
    )
    candidate_last_seen_at = (
        _parse_datetime(_row_get(row, "candidate_last_seen_at"))
        or observed_at
    )

    confirmation_advanced = False
    if candidate_price_cents != current_cents:
        candidate_price_cents = current_cents
        candidate_seen_count = 1
        candidate_first_seen_at = now_iso
        candidate_last_seen_at = observed_at
    elif (
        observed_at - candidate_last_seen_at
    ).total_seconds() >= MIN_CONFIRMATION_GAP_SECONDS:
        candidate_seen_count += 1
        candidate_last_seen_at = observed_at
        confirmation_advanced = True

    next_stable_cents = _int_or_none(
        _row_get(row, "stable_price_cents")
    )
    next_stable_seen_count = stable_seen_count
    next_stable_first_seen_at = _row_get(
        row,
        "stable_first_seen_at",
    )
    next_stable_last_confirmed_at = _row_get(
        row,
        "stable_last_confirmed_at",
    )
    if candidate_seen_count >= MIN_STABLE_CONFIRMATIONS and (
        next_stable_cents != current_cents or confirmation_advanced
    ):
        if next_stable_cents != current_cents:
            next_stable_cents = current_cents
            next_stable_seen_count = candidate_seen_count
            next_stable_first_seen_at = candidate_first_seen_at
        else:
            next_stable_seen_count = max(
                next_stable_seen_count,
                candidate_seen_count,
            )
        next_stable_last_confirmed_at = now_iso

    lowest_cents = min(
        value_
        for value_ in (lowest_seen_cents, current_cents)
        if value_ is not None
    )

    return (
        value.identity.identity_key,
        IDENTITY_VERSION,
        value.identity.item_id,
        value.identity.offer_id,
        value.identity.seller_key,
        value.identity.variant_key,
        value.identity.condition_key,
        value.identity.fulfillment_key,
        current_cents,
        candidate_price_cents,
        candidate_seen_count,
        candidate_first_seen_at,
        candidate_last_seen_at.isoformat(),
        next_stable_cents,
        next_stable_seen_count,
        next_stable_first_seen_at,
        next_stable_last_confirmed_at,
        lowest_cents,
        str(_row_get(row, "first_seen_at") or now_iso),
        now_iso,
        status,
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
