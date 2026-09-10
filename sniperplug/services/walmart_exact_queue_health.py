from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sniperplug.services.walmart_exact_queue_drain import (
    PAGE_RECOVERY_CLAIM_CAP,
    VERIFIED_LOW_SIGNAL_RECHECK_STATUSES,
    VERIFIED_MARKDOWN_RECHECK_STATUSES,
    VERIFIED_REFERENCE_RECHECK_STATUSES,
    verified_recheck_cutoffs,
)
from sniperplug.services.walmart_exact_verification_queue import (
    CONFIRMED_API_PRICE_MISSING_RETRY_SECONDS,
    QUEUE_RETENTION_DAYS,
)


QUEUE_TABLE = "walmart_exact_detail_queue"
TERMINAL_IDENTITY_STATUSES = ("incomplete_identity", "identity_mismatch")
INITIAL_WORK_STATUSES = ("pending", "retry", "failed", "verifying")
RECOVERY_RETRY_STATUSES = ("page_retry",)
RECHECK_STATUSES = (
    "verified_markdown",
    "verified_under_threshold",
    "verified_no_reference",
    "verified_reference",
    "not_buyable",
)
CONFIRMED_NO_PRICE_FLOOR_SQL = (
    f"julianday(next_attempt_at) >= "
    f"julianday(last_attempt_at, '+{CONFIRMED_API_PRICE_MISSING_RETRY_SECONDS} seconds')"
)


def _sql_statuses(statuses: tuple[str, ...]) -> str:
    return ", ".join(f"'{status}'" for status in statuses)


def _sql_literal(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


@dataclass(frozen=True)
class WalmartExactQueueHealth:
    total: int = 0
    due_now: int = 0
    initial_due_now: int = 0
    confirmed_no_price_due_now: int = 0
    recovery_due_now: int = 0
    recheck_due_now: int = 0
    delayed_retries: int = 0
    delayed_recovery_retries: int = 0
    identity_blocked: int = 0
    verified: int = 0
    verifying: int = 0
    pending: int = 0
    unavailable: int = 0
    stale: int = 0
    page_recovery_mode: str = "unknown"
    page_recovery_batch_cap: int = PAGE_RECOVERY_CLAIM_CAP
    page_circuit_remaining_seconds: int = 0

    @property
    def page_recovery_claimable_this_batch(self) -> int:
        return min(
            max(0, int(self.recovery_due_now)),
            max(0, int(self.page_recovery_batch_cap)),
        )

    def summary_line(self) -> str:
        if self.page_recovery_mode == "api_only":
            recovery_mode = (
                "API-only while Walmart HTML challenge circuit is open"
            )
            if self.page_circuit_remaining_seconds > 0:
                recovery_mode += f" ({self.page_circuit_remaining_seconds}s HTML cooldown left)"
        elif self.page_recovery_mode == "normal":
            recovery_mode = "official API + bounded product-page recovery"
        else:
            recovery_mode = "runtime mode unavailable"

        return (
            "queue health: "
            f"total **{self.total}** • due backlog now **{self.due_now}** "
            f"(new/retry **{self.initial_due_now}** • "
            f"confirmed no-price retry **{self.confirmed_no_price_due_now}** • "
            f"page recovery **{self.recovery_due_now}** • "
            f"scheduled rechecks **{self.recheck_due_now}**) • "
            f"page recovery worker **{recovery_mode}** • "
            f"claimable this batch **{self.page_recovery_claimable_this_batch}**/"
            f"**{self.recovery_due_now}** (cap **{self.page_recovery_batch_cap}**) • "
            f"delayed transient retries **{self.delayed_retries}** "
            f"(page recovery **{self.delayed_recovery_retries}**) • "
            f"identity unavailable / safely blocked **{self.identity_blocked}** (terminal) • "
            f"verified **{self.verified}** • actively verifying **{self.verifying}** • "
            f"pending **{self.pending}** • unavailable **{self.unavailable}** • "
            f"stale/unclaimable **{self.stale}**"
        )


async def load_walmart_exact_queue_health(db: Any) -> WalmartExactQueueHealth:
    """Return full owner-facing queue state plus current recovery availability.

    ``due_now`` is a backlog count, not a promise that every row can be leased in
    one worker cycle. A confirmed dual-no-price retry is reported in its own lane
    only while ``next_attempt_at`` still represents at least the full #277
    cooldown from ``last_attempt_at``. #277 preserves that schedule across weak or
    cosmetic rediscovery but shortens it when fresh proof or a material apparent
    price/reference change wakes the row, so a shortened schedule remains genuine
    fresh pressure without relying on ambiguous catalog timestamps.

    Page recovery is intentionally capped to a tiny lane. When Walmart's product-
    page challenge circuit is open, those bounded claims use official API/exact-
    search recovery only; HTML remains suppressed.

    Legacy early verified timestamps are ignored until each status reaches its
    shared fallback age: one day for verified markdowns, three days for
    reference/under-threshold states, and seven days for no-reference/unavailable
    states. Material catalog changes are ``pending`` and remain immediately
    actionable. Terminal identity failures stay fail-closed until strict fresh
    proof or the bounded terminal reprobe path safely rearms them.
    """

    if db is None:
        return WalmartExactQueueHealth()
    conn = db.require_conn()
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    discovery_cutoff = (now - timedelta(days=QUEUE_RETENTION_DAYS)).isoformat()
    (
        markdown_recheck_cutoff,
        reference_recheck_cutoff,
        low_signal_recheck_cutoff,
    ) = verified_recheck_cutoffs(now)
    markdown_statuses = _sql_statuses(VERIFIED_MARKDOWN_RECHECK_STATUSES)
    reference_statuses = _sql_statuses(VERIFIED_REFERENCE_RECHECK_STATUSES)
    low_signal_statuses = _sql_statuses(VERIFIED_LOW_SIGNAL_RECHECK_STATUSES)
    reference_cutoff_sql = _sql_literal(reference_recheck_cutoff)
    low_signal_cutoff_sql = _sql_literal(low_signal_recheck_cutoff)
    verified_guard = f"""
        (
            verified_at IS NULL
            OR (status IN ({markdown_statuses}) AND verified_at <= ?)
            OR (status IN ({reference_statuses}) AND verified_at <= {reference_cutoff_sql})
            OR (status IN ({low_signal_statuses}) AND verified_at <= {low_signal_cutoff_sql})
        )
    """
    confirmed_no_price_sql = f"""
        status = 'retry'
        AND instr(lower(last_error), 'retry_kind=api_price_missing') > 0
        AND instr(lower(last_error), 'recovery_kind=api_price_missing') > 0
        AND last_attempt_at IS NOT NULL
        AND {CONFIRMED_NO_PRICE_FLOOR_SQL}
    """
    try:
        cursor = await conn.execute(
            f"""
            WITH queue_state AS (
                SELECT *,
                    CASE WHEN last_seen_at >= ? THEN 1 ELSE 0 END AS is_recent
                FROM {QUEUE_TABLE}
            )
            SELECT
                COUNT(*) AS total,
                SUM(CASE
                    WHEN is_recent = 1
                     AND status NOT IN ('incomplete_identity', 'identity_mismatch')
                     AND next_attempt_at <= ?
                     AND (lease_until IS NULL OR lease_until < ?)
                     AND (
                         status NOT IN (
                             'verified_markdown',
                             'verified_under_threshold',
                             'verified_no_reference',
                             'verified_reference',
                             'not_buyable'
                         )
                         OR {verified_guard}
                     )
                    THEN 1 ELSE 0 END) AS due_now,
                SUM(CASE
                    WHEN is_recent = 1
                     AND status IN ('pending', 'retry', 'failed', 'verifying')
                     AND NOT ({confirmed_no_price_sql})
                     AND next_attempt_at <= ?
                     AND (lease_until IS NULL OR lease_until < ?)
                    THEN 1 ELSE 0 END) AS initial_due_now,
                SUM(CASE
                    WHEN is_recent = 1
                     AND status = 'page_retry'
                     AND next_attempt_at <= ?
                     AND (lease_until IS NULL OR lease_until < ?)
                    THEN 1 ELSE 0 END) AS recovery_due_now,
                SUM(CASE
                    WHEN is_recent = 1
                     AND status IN (
                         'verified_markdown',
                         'verified_under_threshold',
                         'verified_no_reference',
                         'verified_reference',
                         'not_buyable'
                     )
                     AND next_attempt_at <= ?
                     AND (lease_until IS NULL OR lease_until < ?)
                     AND {verified_guard}
                    THEN 1 ELSE 0 END) AS recheck_due_now,
                SUM(CASE
                    WHEN is_recent = 1
                     AND status IN ('retry', 'failed', 'page_retry')
                     AND next_attempt_at > ?
                    THEN 1 ELSE 0 END) AS delayed_retries,
                SUM(CASE
                    WHEN is_recent = 1
                     AND status = 'page_retry'
                     AND next_attempt_at > ?
                    THEN 1 ELSE 0 END) AS delayed_recovery_retries,
                SUM(CASE
                    WHEN is_recent = 1
                     AND status IN ('incomplete_identity', 'identity_mismatch')
                    THEN 1 ELSE 0 END) AS identity_blocked,
                SUM(CASE
                    WHEN is_recent = 1
                     AND status LIKE 'verified_%'
                    THEN 1 ELSE 0 END) AS verified,
                SUM(CASE
                    WHEN is_recent = 1
                     AND status = 'verifying'
                     AND lease_until IS NOT NULL
                     AND lease_until >= ?
                    THEN 1 ELSE 0 END) AS verifying,
                SUM(CASE
                    WHEN is_recent = 1 AND status = 'pending'
                    THEN 1 ELSE 0 END) AS pending,
                SUM(CASE
                    WHEN is_recent = 1 AND status = 'not_buyable'
                    THEN 1 ELSE 0 END) AS unavailable,
                SUM(CASE WHEN is_recent = 0 THEN 1 ELSE 0 END) AS stale,
                SUM(CASE
                    WHEN is_recent = 1
                     AND ({confirmed_no_price_sql})
                     AND next_attempt_at <= ?
                     AND (lease_until IS NULL OR lease_until < ?)
                    THEN 1 ELSE 0 END) AS confirmed_no_price_due_now
            FROM queue_state
            """,
            (
                discovery_cutoff,
                now_iso,
                now_iso,
                markdown_recheck_cutoff,
                now_iso,
                now_iso,
                now_iso,
                now_iso,
                now_iso,
                now_iso,
                markdown_recheck_cutoff,
                now_iso,
                now_iso,
                now_iso,
                now_iso,
                now_iso,
            ),
        )
        row = await cursor.fetchone()
    except Exception:
        return WalmartExactQueueHealth()

    recovery_mode, recovery_cap, circuit_remaining = _page_recovery_runtime_state()
    return WalmartExactQueueHealth(
        total=_as_int(_row_get(row, "total", 0)),
        due_now=_as_int(_row_get(row, "due_now", 1)),
        initial_due_now=_as_int(_row_get(row, "initial_due_now", 2)),
        recovery_due_now=_as_int(_row_get(row, "recovery_due_now", 3)),
        recheck_due_now=_as_int(_row_get(row, "recheck_due_now", 4)),
        delayed_retries=_as_int(_row_get(row, "delayed_retries", 5)),
        delayed_recovery_retries=_as_int(_row_get(row, "delayed_recovery_retries", 6)),
        identity_blocked=_as_int(_row_get(row, "identity_blocked", 7)),
        verified=_as_int(_row_get(row, "verified", 8)),
        verifying=_as_int(_row_get(row, "verifying", 9)),
        pending=_as_int(_row_get(row, "pending", 10)),
        unavailable=_as_int(_row_get(row, "unavailable", 11)),
        stale=_as_int(_row_get(row, "stale", 12)),
        confirmed_no_price_due_now=_as_int(
            _row_get(row, "confirmed_no_price_due_now", 13)
        ),
        page_recovery_mode=recovery_mode,
        page_recovery_batch_cap=recovery_cap,
        page_circuit_remaining_seconds=circuit_remaining,
    )


def _page_recovery_runtime_state() -> tuple[str, int, int]:
    """Read the live provider circuit without coupling queue SQL to the provider."""

    try:
        from sniperplug.providers.registry import provider_registry
        from sniperplug.services.walmart_exact_queue_runtime import (
            _page_challenge_circuit_open,
            _page_challenge_circuit_remaining_seconds,
        )

        provider = provider_registry.get("walmart")
        if provider is None:
            return "unknown", PAGE_RECOVERY_CLAIM_CAP, 0
        if _page_challenge_circuit_open(provider):
            return (
                "api_only",
                PAGE_RECOVERY_CLAIM_CAP,
                _page_challenge_circuit_remaining_seconds(provider),
            )
        return "normal", PAGE_RECOVERY_CLAIM_CAP, 0
    except Exception:
        return "unknown", PAGE_RECOVERY_CLAIM_CAP, 0


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


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
