from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sniperplug.models.candidate import SourceCandidate
from sniperplug.services.walmart_exact_price_enrichment import (
    _percent_off,
    _positive_number,
    _trusted_reference,
)
from sniperplug.services.walmart_exact_verification_queue import (
    QUEUE_LEASE_SECONDS,
    QUEUE_RETENTION_DAYS,
    QUEUE_TABLE,
    _QueueClaim,
    _candidate_snapshot,
    _classify_exact_candidate,
    _compact_text,
    _int_or_none,
    _price_to_cents,
    _row_get,
)
from sniperplug.services.walmart_fresh_discovery_proof import (
    DISCOVERY_PROOF_MAX_AGE_SECONDS,
)
from sniperplug.services.walmart_global_offer_memory import exact_offer_identity


TERMINAL_IDENTITY_STATUSES = ("incomplete_identity", "identity_mismatch")
VERIFIED_MARKDOWN_RECHECK_HOURS = 24
VERIFIED_REFERENCE_RECHECK_HOURS = 72
VERIFIED_LOW_SIGNAL_RECHECK_HOURS = 7 * 24
VERIFIED_MARKDOWN_RECHECK_STATUSES = ("verified_markdown",)
VERIFIED_REFERENCE_RECHECK_STATUSES = (
    "verified_under_threshold",
    "verified_reference",
)
VERIFIED_LOW_SIGNAL_RECHECK_STATUSES = (
    "verified_no_reference",
    "not_buyable",
)
VERIFIED_RECHECK_STATUSES = (
    *VERIFIED_MARKDOWN_RECHECK_STATUSES,
    *VERIFIED_REFERENCE_RECHECK_STATUSES,
    *VERIFIED_LOW_SIGNAL_RECHECK_STATUSES,
)
# Product-page recovery itself is already limited to two concurrent requests.
# Do not let a backlog of distinct challenged pages consume an entire 8/24-item
# exact batch. Fresh catalog snapshots are exempt because they are attempted
# without page I/O first and remain subject to the normal strict promotion gate.
PAGE_RECOVERY_CLAIM_CAP = 2
_DISCOVERY_SNAPSHOT_COLUMNS = (
    "discovery_snapshot_json, discovery_snapshot_at"
)
_LEGACY_DISCOVERY_SNAPSHOT_PROJECTION = (
    "'' AS discovery_snapshot_json, NULL AS discovery_snapshot_at"
)
STATUS_DUE_INDEX = f"idx_{QUEUE_TABLE}_status_due"
FRESH_PROOF_DUE_INDEX = f"idx_{QUEUE_TABLE}_fresh_proof_due"
STATUS_VERIFIED_DUE_INDEX = f"idx_{QUEUE_TABLE}_status_verified_due"


def verified_recheck_cutoffs(now: datetime) -> tuple[str, str, str]:
    """Return claim cutoffs for high-, medium-, and low-signal verified states."""

    now_utc = now.astimezone(timezone.utc)
    return (
        (now_utc - timedelta(hours=VERIFIED_MARKDOWN_RECHECK_HOURS)).isoformat(),
        (now_utc - timedelta(hours=VERIFIED_REFERENCE_RECHECK_HOURS)).isoformat(),
        (now_utc - timedelta(hours=VERIFIED_LOW_SIGNAL_RECHECK_HOURS)).isoformat(),
    )


def verified_recheck_delay(status: str) -> timedelta:
    """Return the fallback exact cadence for a verified queue status.

    Catalog-visible price/reference/selected-offer changes rearm verified rows to
    ``pending`` immediately, so these delays are safety sweeps rather than the
    primary change detector. High-value proven markdowns retain a daily fallback;
    weaker reference states use three days; no-reference/unavailable states use a
    weekly fallback to avoid spending Turso writes re-proving unchanged low-signal
    outcomes every day.
    """

    if status in VERIFIED_MARKDOWN_RECHECK_STATUSES:
        return timedelta(hours=VERIFIED_MARKDOWN_RECHECK_HOURS)
    if status in VERIFIED_REFERENCE_RECHECK_STATUSES:
        return timedelta(hours=VERIFIED_REFERENCE_RECHECK_HOURS)
    if status in VERIFIED_LOW_SIGNAL_RECHECK_STATUSES:
        return timedelta(hours=VERIFIED_LOW_SIGNAL_RECHECK_HOURS)
    return timedelta(hours=12)


async def claim_due_rows_batched(
    conn: Any,
    *,
    now: datetime,
    limit: int,
    page_recovery_claim_cap: int = PAGE_RECOVERY_CLAIM_CAP,
) -> list[_QueueClaim]:
    """Select an ordered slice, then lease only those rows with one small update.

    Turso bills rows scanned rather than only rows returned. The old single
    all-status SELECT could therefore inspect most of a mature queue every 20
    seconds even though ``LIMIT 8`` returned almost nothing. That became
    especially expensive after status-aware recheck floors intentionally stopped
    rewriting legacy early ``next_attempt_at`` timestamps.

    Production now builds one bounded candidate pool from index-seekable lanes:
    fresh proof, each initial/retry status, capped page recovery, each verified
    age band, missing verified timestamps, and expired verifying leases. Each
    lane can contribute only the batch-sized amount the worker can actually use,
    then the original global freshness/status/priority order chooses the final
    slice. This keeps the same durable lease, status priority, page cap, and
    verified-age semantics without a broad queue scan.

    A shared lease token still protects all final writes. If another process
    races between the SELECT and UPDATE, the UPDATE guards and returned IDs
    prevent this worker from processing rows it did not lease.

    Direct compatibility callers that have the current columns but have not run
    runtime index initialization fall back to the historical bounded selector.
    Older pre-snapshot/pre-verified-at callers retain the original legacy
    selector after that current-schema fallback reports its missing optional
    columns. Production always initializes the indexed runtime schema before
    claiming.
    """

    now_utc = now.astimezone(timezone.utc)
    now_iso = now_utc.isoformat()
    discovery_cutoff = (
        now_utc - timedelta(days=QUEUE_RETENTION_DAYS)
    ).isoformat()
    snapshot_cutoff = (
        now_utc - timedelta(seconds=DISCOVERY_PROOF_MAX_AGE_SECONDS)
    ).isoformat()
    (
        markdown_recheck_cutoff,
        reference_recheck_cutoff,
        low_signal_recheck_cutoff,
    ) = verified_recheck_cutoffs(now_utc)
    bounded_limit = max(1, int(limit))
    bounded_page_recovery_claim_cap = max(0, int(page_recovery_claim_cap))
    batch_token = uuid.uuid4().hex
    lease_until = (
        now_utc + timedelta(seconds=QUEUE_LEASE_SECONDS)
    ).isoformat()

    selected_rows = await _select_due_rows_with_optional_discovery_snapshot(
        conn,
        now_iso=now_iso,
        discovery_cutoff=discovery_cutoff,
        snapshot_cutoff=snapshot_cutoff,
        markdown_recheck_cutoff=markdown_recheck_cutoff,
        reference_recheck_cutoff=reference_recheck_cutoff,
        low_signal_recheck_cutoff=low_signal_recheck_cutoff,
        bounded_limit=bounded_limit,
        page_recovery_claim_cap=bounded_page_recovery_claim_cap,
    )
    if not selected_rows:
        return []

    selected_by_id: dict[str, Any] = {}
    selected_order: list[str] = []
    for row in selected_rows:
        item_id = str(_row_get(row, "item_id", index=0) or "").strip()
        if not item_id or item_id in selected_by_id:
            continue
        selected_by_id[item_id] = row
        selected_order.append(item_id)

    if not selected_order:
        return []

    placeholders = ", ".join("?" for _ in selected_order)
    update_cursor = await conn.execute(
        f"""
        UPDATE {QUEUE_TABLE}
        SET lease_token = ?, lease_until = ?, status = 'verifying'
        WHERE item_id IN ({placeholders})
          AND next_attempt_at <= ?
          AND last_seen_at >= ?
          AND status NOT IN ('incomplete_identity', 'identity_mismatch')
          AND (lease_until IS NULL OR lease_until < ?)
        RETURNING item_id
        """,
        (
            batch_token,
            lease_until,
            *selected_order,
            now_iso,
            discovery_cutoff,
            now_iso,
        ),
    )
    leased_rows = await update_cursor.fetchall()
    await conn.commit()

    leased_ids = {
        str(_row_get(row, "item_id", index=0) or "").strip()
        for row in leased_rows
    }
    claims: list[_QueueClaim] = []
    for item_id in selected_order:
        if item_id not in leased_ids:
            continue
        row = selected_by_id[item_id]
        claims.append(
            _QueueClaim(
                item_id=item_id,
                title=str(_row_get(row, "title", index=1) or ""),
                product_url=str(_row_get(row, "product_url", index=2) or ""),
                image_url=str(_row_get(row, "image_url", index=3) or ""),
                apparent_current_cents=_int_or_none(
                    _row_get(row, "apparent_current_cents", index=4)
                ),
                apparent_reference_cents=_int_or_none(
                    _row_get(row, "apparent_reference_cents", index=5)
                ),
                route_hint=str(_row_get(row, "route_hint", index=6) or ""),
                lease_token=batch_token,
                attempt_count=int(_row_get(row, "attempt_count", index=7) or 0),
                discovery_snapshot_json=str(
                    _row_get(row, "discovery_snapshot_json", index=8) or ""
                ),
                discovery_snapshot_at=(
                    str(_row_get(row, "discovery_snapshot_at", index=9) or "")
                    or None
                ),
            )
        )
    return claims


async def _select_due_rows_with_optional_discovery_snapshot(
    conn: Any,
    *,
    now_iso: str,
    discovery_cutoff: str,
    snapshot_cutoff: str,
    markdown_recheck_cutoff: str,
    reference_recheck_cutoff: str,
    low_signal_recheck_cutoff: str,
    bounded_limit: int,
    page_recovery_claim_cap: int,
) -> list[Any]:
    current_params = (
        now_iso,
        discovery_cutoff,
        now_iso,
        markdown_recheck_cutoff,
        reference_recheck_cutoff,
        low_signal_recheck_cutoff,
        snapshot_cutoff,
        now_iso,
        discovery_cutoff,
        now_iso,
        snapshot_cutoff,
        page_recovery_claim_cap,
        snapshot_cutoff,
        bounded_limit,
    )
    legacy_params = (
        now_iso,
        discovery_cutoff,
        now_iso,
        now_iso,
        discovery_cutoff,
        now_iso,
        page_recovery_claim_cap,
        bounded_limit,
    )

    try:
        cursor = await conn.execute(
            _indexed_due_rows_select_sql(),
            _indexed_due_rows_select_params(
                now_iso=now_iso,
                discovery_cutoff=discovery_cutoff,
                snapshot_cutoff=snapshot_cutoff,
                markdown_recheck_cutoff=markdown_recheck_cutoff,
                reference_recheck_cutoff=reference_recheck_cutoff,
                low_signal_recheck_cutoff=low_signal_recheck_cutoff,
                bounded_limit=bounded_limit,
                page_recovery_claim_cap=page_recovery_claim_cap,
            ),
        )
    except Exception as exc:
        if _is_missing_runtime_claim_index(exc):
            try:
                cursor = await conn.execute(
                    _due_rows_select_sql(
                        _DISCOVERY_SNAPSHOT_COLUMNS,
                        has_snapshot_columns=True,
                        has_verified_at=True,
                    ),
                    current_params,
                )
            except Exception as fallback_exc:
                if not _is_missing_optional_claim_column(fallback_exc):
                    raise
                cursor = await conn.execute(
                    _due_rows_select_sql(
                        _LEGACY_DISCOVERY_SNAPSHOT_PROJECTION,
                        has_snapshot_columns=False,
                        has_verified_at=False,
                    ),
                    legacy_params,
                )
        elif _is_missing_optional_claim_column(exc):
            cursor = await conn.execute(
                _due_rows_select_sql(
                    _LEGACY_DISCOVERY_SNAPSHOT_PROJECTION,
                    has_snapshot_columns=False,
                    has_verified_at=False,
                ),
                legacy_params,
            )
        else:
            raise
    return list(await cursor.fetchall())


def _indexed_due_rows_select_sql() -> str:
    projection = """
            item_id, title, product_url, image_url,
            apparent_current_cents, apparent_reference_cents,
            route_hint, attempt_count,
            discovery_snapshot_json, discovery_snapshot_at
    """.strip()
    nonfresh = """
            NOT (
                discovery_snapshot_json <> ''
                AND discovery_snapshot_at IS NOT NULL
                AND discovery_snapshot_at >= ?
            )
    """.strip()

    def ordinary_cte(name: str, status: str, rank: int) -> str:
        return f"""
        {name} AS (
            SELECT
                {projection},
                1 AS freshness_rank,
                {rank} AS status_rank,
                priority_score,
                last_seen_at
            FROM {QUEUE_TABLE} INDEXED BY {STATUS_DUE_INDEX}
            WHERE status = '{status}'
              AND next_attempt_at <= ?
              AND last_seen_at >= ?
              AND (lease_until IS NULL OR lease_until < ?)
              AND {nonfresh}
            ORDER BY priority_score DESC, last_seen_at DESC
            LIMIT ?
        )
        """

    def verified_cte(name: str, status: str, rank: int) -> str:
        return f"""
        {name} AS (
            SELECT
                {projection},
                1 AS freshness_rank,
                {rank} AS status_rank,
                priority_score,
                last_seen_at
            FROM {QUEUE_TABLE} INDEXED BY {STATUS_VERIFIED_DUE_INDEX}
            WHERE status = '{status}'
              AND verified_at <= ?
              AND next_attempt_at <= ?
              AND last_seen_at >= ?
              AND (lease_until IS NULL OR lease_until < ?)
              AND {nonfresh}
            ORDER BY priority_score DESC, last_seen_at DESC
            LIMIT ?
        )
        """

    verified_statuses = ", ".join(
        f"'{status}'" for status in VERIFIED_RECHECK_STATUSES
    )
    ctes = [
        f"""
        fresh_candidates AS (
            SELECT
                {projection},
                0 AS freshness_rank,
                CASE status
                    WHEN 'pending' THEN 0
                    WHEN 'retry' THEN 1
                    WHEN 'failed' THEN 2
                    WHEN 'page_retry' THEN 3
                    WHEN 'verified_markdown' THEN 4
                    WHEN 'verified_under_threshold' THEN 5
                    WHEN 'verified_no_reference' THEN 6
                    WHEN 'verified_reference' THEN 7
                    WHEN 'not_buyable' THEN 8
                    WHEN 'verifying' THEN 9
                    ELSE 10
                END AS status_rank,
                priority_score,
                last_seen_at
            FROM {QUEUE_TABLE} INDEXED BY {FRESH_PROOF_DUE_INDEX}
            WHERE discovery_snapshot_json <> ''
              AND discovery_snapshot_at IS NOT NULL
              AND discovery_snapshot_at >= ?
              AND next_attempt_at <= ?
              AND last_seen_at >= ?
              AND status NOT IN ('incomplete_identity', 'identity_mismatch')
              AND (lease_until IS NULL OR lease_until < ?)
              AND (
                  status NOT IN ({verified_statuses})
                  OR verified_at IS NULL
                  OR (status = 'verified_markdown' AND verified_at <= ?)
                  OR (
                      status IN ('verified_under_threshold', 'verified_reference')
                      AND verified_at <= ?
                  )
                  OR (
                      status IN ('verified_no_reference', 'not_buyable')
                      AND verified_at <= ?
                  )
              )
            ORDER BY status_rank, priority_score DESC, last_seen_at DESC
            LIMIT ?
        )
        """,
        ordinary_cte("pending_candidates", "pending", 0),
        ordinary_cte("retry_candidates", "retry", 1),
        ordinary_cte("failed_candidates", "failed", 2),
        ordinary_cte("page_candidates", "page_retry", 3),
        verified_cte("verified_markdown_candidates", "verified_markdown", 4),
        verified_cte(
            "verified_under_threshold_candidates",
            "verified_under_threshold",
            5,
        ),
        verified_cte(
            "verified_no_reference_candidates",
            "verified_no_reference",
            6,
        ),
        verified_cte("verified_reference_candidates", "verified_reference", 7),
        verified_cte("not_buyable_candidates", "not_buyable", 8),
        f"""
        missing_verified_candidates AS (
            SELECT
                {projection},
                1 AS freshness_rank,
                CASE status
                    WHEN 'verified_markdown' THEN 4
                    WHEN 'verified_under_threshold' THEN 5
                    WHEN 'verified_no_reference' THEN 6
                    WHEN 'verified_reference' THEN 7
                    WHEN 'not_buyable' THEN 8
                    ELSE 10
                END AS status_rank,
                priority_score,
                last_seen_at
            FROM {QUEUE_TABLE} INDEXED BY {STATUS_VERIFIED_DUE_INDEX}
            WHERE status IN ({verified_statuses})
              AND verified_at IS NULL
              AND next_attempt_at <= ?
              AND last_seen_at >= ?
              AND (lease_until IS NULL OR lease_until < ?)
              AND {nonfresh}
            ORDER BY status_rank, priority_score DESC, last_seen_at DESC
            LIMIT ?
        )
        """,
        ordinary_cte("verifying_candidates", "verifying", 9),
    ]
    names = (
        "fresh_candidates",
        "pending_candidates",
        "retry_candidates",
        "failed_candidates",
        "page_candidates",
        "verified_markdown_candidates",
        "verified_under_threshold_candidates",
        "verified_no_reference_candidates",
        "verified_reference_candidates",
        "not_buyable_candidates",
        "missing_verified_candidates",
        "verifying_candidates",
    )
    pool = "\n                UNION ALL\n".join(
        f"SELECT * FROM {name}" for name in names
    )
    return f"""
        WITH
        {", ".join(ctes)},
        candidate_pool AS (
            {pool}
        )
        SELECT
            {projection}
        FROM candidate_pool
        ORDER BY
            freshness_rank,
            status_rank,
            priority_score DESC,
            last_seen_at DESC
        LIMIT ?
    """


def _indexed_due_rows_select_params(
    *,
    now_iso: str,
    discovery_cutoff: str,
    snapshot_cutoff: str,
    markdown_recheck_cutoff: str,
    reference_recheck_cutoff: str,
    low_signal_recheck_cutoff: str,
    bounded_limit: int,
    page_recovery_claim_cap: int,
) -> tuple[Any, ...]:
    params: list[Any] = [
        snapshot_cutoff,
        now_iso,
        discovery_cutoff,
        now_iso,
        markdown_recheck_cutoff,
        reference_recheck_cutoff,
        low_signal_recheck_cutoff,
        bounded_limit,
    ]
    for _ in range(3):
        params.extend(
            (now_iso, discovery_cutoff, now_iso, snapshot_cutoff, bounded_limit)
        )
    params.extend(
        (
            now_iso,
            discovery_cutoff,
            now_iso,
            snapshot_cutoff,
            page_recovery_claim_cap,
        )
    )
    for cutoff in (
        markdown_recheck_cutoff,
        reference_recheck_cutoff,
        low_signal_recheck_cutoff,
        reference_recheck_cutoff,
        low_signal_recheck_cutoff,
    ):
        params.extend(
            (
                cutoff,
                now_iso,
                discovery_cutoff,
                now_iso,
                snapshot_cutoff,
                bounded_limit,
            )
        )
    params.extend(
        (now_iso, discovery_cutoff, now_iso, snapshot_cutoff, bounded_limit)
    )
    params.extend(
        (now_iso, discovery_cutoff, now_iso, snapshot_cutoff, bounded_limit)
    )
    params.append(bounded_limit)
    return tuple(params)


def _is_missing_runtime_claim_index(exc: BaseException) -> bool:
    message = str(exc or "").lower()
    return "no such index" in message and (
        STATUS_DUE_INDEX.lower() in message
        or FRESH_PROOF_DUE_INDEX.lower() in message
        or STATUS_VERIFIED_DUE_INDEX.lower() in message
    )


def _due_rows_select_sql(
    snapshot_projection: str,
    *,
    has_snapshot_columns: bool,
    has_verified_at: bool,
) -> str:
    if has_snapshot_columns:
        page_guard = f"""
          AND (
              status <> 'page_retry'
              OR (
                  discovery_snapshot_json <> ''
                  AND discovery_snapshot_at IS NOT NULL
                  AND discovery_snapshot_at >= ?
              )
              OR item_id IN (
                  SELECT item_id
                  FROM {QUEUE_TABLE}
                  WHERE status = 'page_retry'
                    AND next_attempt_at <= ?
                    AND last_seen_at >= ?
                    AND (lease_until IS NULL OR lease_until < ?)
                    AND NOT (
                        discovery_snapshot_json <> ''
                        AND discovery_snapshot_at IS NOT NULL
                        AND discovery_snapshot_at >= ?
                    )
                  ORDER BY priority_score DESC, last_seen_at DESC
                  LIMIT ?
              )
          )
        """
        freshness_order = """
            CASE
                WHEN discovery_snapshot_json <> ''
                     AND discovery_snapshot_at IS NOT NULL
                     AND discovery_snapshot_at >= ?
                THEN 0 ELSE 1
            END,
        """
    else:
        page_guard = f"""
          AND (
              status <> 'page_retry'
              OR item_id IN (
                  SELECT item_id
                  FROM {QUEUE_TABLE}
                  WHERE status = 'page_retry'
                    AND next_attempt_at <= ?
                    AND last_seen_at >= ?
                    AND (lease_until IS NULL OR lease_until < ?)
                  ORDER BY priority_score DESC, last_seen_at DESC
                  LIMIT ?
              )
          )
        """
        freshness_order = ""

    if has_verified_at:
        verified_statuses = ", ".join(
            f"'{status}'" for status in VERIFIED_RECHECK_STATUSES
        )
        reference_statuses = ", ".join(
            f"'{status}'" for status in VERIFIED_REFERENCE_RECHECK_STATUSES
        )
        low_signal_statuses = ", ".join(
            f"'{status}'" for status in VERIFIED_LOW_SIGNAL_RECHECK_STATUSES
        )
        verified_guard = f"""
          AND (
              status NOT IN ({verified_statuses})
              OR verified_at IS NULL
              OR (
                  status = 'verified_markdown'
                  AND verified_at <= ?
              )
              OR (
                  status IN ({reference_statuses})
                  AND verified_at <= ?
              )
              OR (
                  status IN ({low_signal_statuses})
                  AND verified_at <= ?
              )
          )
        """
    else:
        verified_guard = ""

    return f"""
        SELECT
            item_id, title, product_url, image_url,
            apparent_current_cents, apparent_reference_cents,
            route_hint, attempt_count,
            {snapshot_projection}
        FROM {QUEUE_TABLE}
        WHERE next_attempt_at <= ?
          AND last_seen_at >= ?
          AND status NOT IN ('incomplete_identity', 'identity_mismatch')
          AND (lease_until IS NULL OR lease_until < ?)
          {verified_guard}
          {page_guard}
        ORDER BY
            {freshness_order}
            CASE status
                WHEN 'pending' THEN 0
                WHEN 'retry' THEN 1
                WHEN 'failed' THEN 2
                WHEN 'page_retry' THEN 3
                WHEN 'verified_markdown' THEN 4
                WHEN 'verified_under_threshold' THEN 5
                WHEN 'verified_no_reference' THEN 6
                WHEN 'verified_reference' THEN 7
                WHEN 'not_buyable' THEN 8
                WHEN 'verifying' THEN 9
                ELSE 10
            END,
            priority_score DESC,
            last_seen_at DESC
        LIMIT ?
    """


def _is_missing_optional_claim_column(exc: BaseException) -> bool:
    message = str(exc or "").lower()
    return (
        "no such column" in message
        and (
            "discovery_snapshot_json" in message
            or "discovery_snapshot_at" in message
            or "verified_at" in message
        )
    )


async def store_exact_candidate_with_tiered_recheck(
    conn: Any,
    *,
    item_id: str,
    candidate: SourceCandidate,
    min_discount: int,
    now: datetime,
    lease_token: str,
) -> None:
    identity = exact_offer_identity(candidate)
    if identity is None:
        return

    current = _positive_number(
        getattr(candidate, "api_current_price", None)
        or getattr(candidate, "current_price", None)
    )
    reference = _trusted_reference(candidate)
    discount = _percent_off(current, reference) or 0.0
    status = _classify_exact_candidate(candidate, min_discount=min_discount)
    next_attempt = (now + tiered_recheck_delay(status, discount)).isoformat()
    snapshot = _candidate_snapshot(candidate)

    params: list[Any] = [
        status,
        now.isoformat(),
        _price_to_cents(current),
        _price_to_cents(reference),
        int(round(discount * 100)),
        _compact_text(getattr(candidate, "api_reference_path", None), 300),
        identity.offer_id,
        identity.seller_key,
        identity.variant_key,
        identity.condition_key,
        identity.fulfillment_key,
        snapshot,
        now.isoformat(),
        next_attempt,
        item_id,
    ]
    if lease_token:
        where_guard = "lease_token = ?"
        params.append(lease_token)
    else:
        where_guard = "(lease_until IS NULL OR lease_until < ?)"
        params.append(now.isoformat())

    await conn.execute(
        f"""
        UPDATE {QUEUE_TABLE}
        SET status = ?,
            verified_at = ?,
            exact_current_cents = ?,
            exact_reference_cents = ?,
            exact_discount_bps = ?,
            exact_reference_source = ?,
            exact_offer_id = ?,
            exact_seller_key = ?,
            exact_variant_key = ?,
            exact_condition_key = ?,
            exact_fulfillment_key = ?,
            snapshot_json = ?,
            last_attempt_at = ?,
            attempt_count = attempt_count + 1,
            next_attempt_at = ?,
            last_error = '',
            lease_token = '',
            lease_until = NULL
        WHERE item_id = ? AND {where_guard}
        """,
        tuple(params),
    )


def tiered_recheck_delay(status: str, discount_percent: float) -> timedelta:
    """Use catalog wakeups plus status-aware low-write exact safety sweeps."""

    _ = discount_percent
    return verified_recheck_delay(status)
