from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def replace_once(relative: str, old: str, new: str) -> None:
    path = ROOT / relative
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{relative}: expected one replacement, found {count}: {old[:120]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def append_once(relative: str, marker: str, addition: str) -> None:
    path = ROOT / relative
    text = path.read_text(encoding="utf-8")
    if addition.strip() in text:
        return
    count = text.count(marker)
    if count != 1:
        raise RuntimeError(f"{relative}: expected one append marker, found {count}")
    path.write_text(text.replace(marker, marker + addition, 1), encoding="utf-8")


# --- Walmart provider retry classification ---------------------------------
replace_once(
    "sniperplug/providers/walmart.py",
    '''        raise WalmartProviderError(f"Walmart detail promo endpoint unavailable for {clean_id}: {last_error}")\n''',
    '''        retry_kind = str(getattr(last_error, "retry_kind", "") or "").strip()\n        raise WalmartProviderError(\n            f"Walmart detail promo endpoint unavailable for {clean_id}: {last_error}",\n            retry_kind=retry_kind or "api_detail",\n        )\n''',
)
replace_once(
    "sniperplug/providers/walmart.py",
    '''        except urllib.error.HTTPError as exc:\n            body = exc.read().decode("utf-8", errors="replace")[:400]\n            raise WalmartProviderError(f"Walmart API HTTP {exc.code}: {body}") from exc\n        except urllib.error.URLError as exc:\n            raise WalmartProviderError(f"Walmart API network error: {exc.reason}") from exc\n        try:\n            decoded = json.loads(raw)\n        except json.JSONDecodeError as exc:\n            raise WalmartProviderError("Walmart API returned non-JSON response.") from exc\n        if not isinstance(decoded, dict):\n            raise WalmartProviderError("Walmart API returned unexpected payload shape.")\n''',
    '''        except urllib.error.HTTPError as exc:\n            body = exc.read().decode("utf-8", errors="replace")[:400]\n            raise WalmartProviderError(\n                f"Walmart API HTTP {exc.code}: {body}",\n                retry_kind=f"api_http_{int(exc.code)}",\n            ) from exc\n        except TimeoutError as exc:\n            raise WalmartProviderError(\n                "Walmart API request timed out.",\n                retry_kind="api_timeout",\n            ) from exc\n        except urllib.error.URLError as exc:\n            reason = getattr(exc, "reason", None)\n            retry_kind = "api_timeout" if isinstance(reason, TimeoutError) else "api_network"\n            raise WalmartProviderError(\n                f"Walmart API network error: {reason}",\n                retry_kind=retry_kind,\n            ) from exc\n        try:\n            decoded = json.loads(raw)\n        except json.JSONDecodeError as exc:\n            raise WalmartProviderError(\n                "Walmart API returned non-JSON response.",\n                retry_kind="api_non_json",\n            ) from exc\n        if not isinstance(decoded, dict):\n            raise WalmartProviderError(\n                "Walmart API returned unexpected payload shape.",\n                retry_kind="api_payload_shape",\n            )\n''',
)
replace_once(
    "sniperplug/providers/walmart.py",
    '''class WalmartProviderError(RuntimeError):\n    pass\n''',
    '''class WalmartProviderError(RuntimeError):\n    def __init__(self, message: str, *, retry_kind: str | None = None) -> None:\n        super().__init__(message)\n        self.retry_kind = str(retry_kind or "").strip()\n''',
)

# --- Product-page retry classification + rearm rowcount --------------------
replace_once(
    "sniperplug/providers/walmart_page_recovery.py",
    '''class WalmartPageRecoveryTransientError(RuntimeError):\n    """A product-page proof attempt failed in a way that should be retried."""\n''',
    '''class WalmartPageRecoveryTransientError(RuntimeError):\n    """A product-page proof attempt failed in a way that should be retried."""\n\n    def __init__(self, message: str, *, reason_code: str = "page_transient") -> None:\n        super().__init__(message)\n        self.reason_code = str(reason_code or "page_transient").strip() or "page_transient"\n''',
)
replace_once(
    "sniperplug/providers/walmart_page_recovery.py",
    '''            except WalmartPageRecoveryTransientError as exc:\n                raise WalmartProviderError(\n                    "Walmart product-page identity recovery temporarily unavailable "\n                    f"for {clean_item_id}: {exc}"\n                ) from exc\n''',
    '''            except WalmartPageRecoveryTransientError as exc:\n                raise WalmartProviderError(\n                    "Walmart product-page identity recovery temporarily unavailable "\n                    f"for {clean_item_id}: {exc}",\n                    retry_kind=exc.reason_code,\n                ) from exc\n''',
)
replace_once(
    "sniperplug/providers/walmart_page_recovery.py",
    '''                raise WalmartProviderError(\n                    "Walmart product-page identity recovery saw a price mismatch for "\n                    f"{clean_item_id}: {recovery.detail or 'selected offer changed'}; "\n                    "retrying instead of binding a seller"\n                )\n''',
    '''                raise WalmartProviderError(\n                    "Walmart product-page identity recovery saw a price mismatch for "\n                    f"{clean_item_id}: {recovery.detail or 'selected offer changed'}; "\n                    "retrying instead of binding a seller",\n                    retry_kind="page_price_mismatch",\n                )\n''',
)
replace_once(
    "sniperplug/providers/walmart_page_recovery.py",
    '''                raise WalmartProviderError(\n                    "Walmart product-page identity recovery saw an API/page selected-offer "\n                    f"price mismatch for {clean_item_id}: api=${api_price:.2f} "\n                    f"page=${page_price:.2f}; retrying instead of binding a seller"\n                )\n''',
    '''                raise WalmartProviderError(\n                    "Walmart product-page identity recovery saw an API/page selected-offer "\n                    f"price mismatch for {clean_item_id}: api=${api_price:.2f} "\n                    f"page=${page_price:.2f}; retrying instead of binding a seller",\n                    retry_kind="page_price_mismatch",\n                )\n''',
)
replace_once(
    "sniperplug/providers/walmart_page_recovery.py",
    '''    rearmed = _safe_rowcount(update_cursor)\n''',
    '''    rearmed = await _affected_rows(conn, update_cursor)\n''',
)
replace_once(
    "sniperplug/providers/walmart_page_recovery.py",
    '''                raise WalmartPageRecoveryTransientError(\n                    "product page redirected away from walmart.com"\n                )\n''',
    '''                raise WalmartPageRecoveryTransientError(\n                    "product page redirected away from walmart.com",\n                    reason_code="page_redirect",\n                )\n''',
)
replace_once(
    "sniperplug/providers/walmart_page_recovery.py",
    '''                    raise WalmartPageRecoveryTransientError(\n                        "product page exceeded bounded recovery response size"\n                    )\n''',
    '''                    raise WalmartPageRecoveryTransientError(\n                        "product page exceeded bounded recovery response size",\n                        reason_code="page_response_too_large",\n                    )\n''',
)
replace_once(
    "sniperplug/providers/walmart_page_recovery.py",
    '''        if exc.code in _TRANSIENT_HTTP_CODES:\n            raise WalmartPageRecoveryTransientError(f"HTTP {exc.code}") from exc\n        if exc.code == 404:\n            return []\n        raise WalmartPageRecoveryTransientError(f"HTTP {exc.code}") from exc\n    except urllib.error.URLError as exc:\n        raise WalmartPageRecoveryTransientError(f"network error: {exc.reason}") from exc\n    except TimeoutError as exc:\n        raise WalmartPageRecoveryTransientError("request timed out") from exc\n\n    sample = " ".join(sample_parts).lower()\n    if not parser.scripts and any(marker in sample for marker in _CHALLENGE_MARKERS):\n        raise WalmartPageRecoveryTransientError("Walmart returned a challenge page without structured product data")\n''',
    '''        if exc.code in _TRANSIENT_HTTP_CODES:\n            raise WalmartPageRecoveryTransientError(\n                f"HTTP {exc.code}",\n                reason_code=f"page_http_{int(exc.code)}",\n            ) from exc\n        if exc.code == 404:\n            return []\n        raise WalmartPageRecoveryTransientError(\n            f"HTTP {exc.code}",\n            reason_code=f"page_http_{int(exc.code)}",\n        ) from exc\n    except urllib.error.URLError as exc:\n        reason = getattr(exc, "reason", None)\n        retry_kind = "page_timeout" if isinstance(reason, TimeoutError) else "page_network"\n        raise WalmartPageRecoveryTransientError(\n            f"network error: {reason}",\n            reason_code=retry_kind,\n        ) from exc\n    except TimeoutError as exc:\n        raise WalmartPageRecoveryTransientError(\n            "request timed out",\n            reason_code="page_timeout",\n        ) from exc\n\n    sample = " ".join(sample_parts).lower()\n    if not parser.scripts and any(marker in sample for marker in _CHALLENGE_MARKERS):\n        raise WalmartPageRecoveryTransientError(\n            "Walmart returned a challenge page without structured product data",\n            reason_code="page_challenge",\n        )\n''',
)
replace_once(
    "sniperplug/providers/walmart_page_recovery.py",
    '''def _safe_rowcount(cursor: Any) -> int:\n    try:\n        value = int(getattr(cursor, "rowcount", 0) or 0)\n    except (TypeError, ValueError, OverflowError):\n        return 0\n    return max(0, value)\n''',
    '''async def _affected_rows(conn: Any, cursor: Any) -> int:\n    try:\n        value = int(getattr(cursor, "rowcount", -1))\n    except (TypeError, ValueError, OverflowError):\n        value = -1\n    if value >= 0:\n        return value\n\n    changes_cursor = await conn.execute("SELECT changes()")\n    row = await changes_cursor.fetchone()\n    if row is None:\n        return 0\n    try:\n        return max(0, int(row[0] or 0))\n    except (TypeError, ValueError, KeyError, IndexError):\n        try:\n            return max(0, int(row["changes()"] or 0))\n        except Exception:\n            return 0\n''',
)

# --- Queue status / retry policy -------------------------------------------
replace_once(
    "sniperplug/services/walmart_exact_verification_queue.py",
    '''QUEUE_LEASE_SECONDS = 120\nQUEUE_CLEANUP_INTERVAL_SECONDS = 6 * 60 * 60\nQUEUE_RETRY_BASE_SECONDS = 5 * 60\nQUEUE_RETRY_MAX_SECONDS = 6 * 60 * 60\n''',
    '''QUEUE_LEASE_SECONDS = 240\nQUEUE_CLEANUP_INTERVAL_SECONDS = 6 * 60 * 60\nQUEUE_RETRY_BASE_SECONDS = 5 * 60\nQUEUE_RETRY_MAX_SECONDS = 6 * 60 * 60\nPAGE_RETRY_STATUS = "page_retry"\nPAGE_RETRY_BASE_SECONDS = 30 * 60\nPAGE_RETRY_RATE_LIMIT_SECONDS = 60 * 60\nPAGE_RETRY_PRICE_MISMATCH_SECONDS = 10 * 60\n''',
)
replace_once(
    "sniperplug/services/walmart_exact_verification_queue.py",
    '''async def _store_failure(\n    conn: Any,\n    *,\n    claim: _QueueClaim,\n    status: str,\n    error: str,\n    now: datetime,\n) -> None:\n    attempts = max(1, claim.attempt_count + 1)\n    if status in {"identity_mismatch", "incomplete_identity"}:\n        delay_seconds = 12 * 60 * 60\n        stored_status = status\n    elif status == "provider_unavailable":\n        delay_seconds = 30 * 60\n        stored_status = "retry"\n    else:\n        delay_seconds = min(\n            QUEUE_RETRY_MAX_SECONDS,\n            QUEUE_RETRY_BASE_SECONDS * (2 ** min(6, attempts - 1)),\n        )\n        stored_status = "retry"\n    await conn.execute(\n''',
    '''async def _store_failure(\n    conn: Any,\n    *,\n    claim: _QueueClaim,\n    status: str,\n    error: str,\n    now: datetime,\n) -> None:\n    attempts = max(1, claim.attempt_count + 1)\n    stored_status, delay_seconds = _failure_storage_policy(\n        status=status,\n        error=error,\n        attempts=attempts,\n    )\n    await conn.execute(\n''',
)
append_once(
    "sniperplug/services/walmart_exact_verification_queue.py",
    '''async def _store_failure(\n''',
    '''''',
)
# Insert the shared policy immediately before _store_failure.
replace_once(
    "sniperplug/services/walmart_exact_verification_queue.py",
    '''async def _store_failure(\n''',
    '''def _failure_storage_policy(\n    *,\n    status: str,\n    error: str,\n    attempts: int,\n) -> tuple[str, int]:\n    if status in {"identity_mismatch", "incomplete_identity"}:\n        return status, 12 * 60 * 60\n    if status == PAGE_RETRY_STATUS:\n        lowered = str(error or "").lower()\n        if "page_price_mismatch" in lowered:\n            return PAGE_RETRY_STATUS, PAGE_RETRY_PRICE_MISMATCH_SECONDS\n        if any(\n            marker in lowered\n            for marker in (\n                "page_http_403",\n                "page_http_429",\n                "page_challenge",\n            )\n        ):\n            return PAGE_RETRY_STATUS, PAGE_RETRY_RATE_LIMIT_SECONDS\n        return PAGE_RETRY_STATUS, PAGE_RETRY_BASE_SECONDS\n    if status == "provider_unavailable":\n        return "retry", 30 * 60\n    delay_seconds = min(\n        QUEUE_RETRY_MAX_SECONDS,\n        QUEUE_RETRY_BASE_SECONDS * (2 ** min(6, max(1, int(attempts)) - 1)),\n    )\n    return "retry", delay_seconds\n\n\nasync def _store_failure(\n''',
)

# --- Bulk persistence uses the same failure policy -------------------------
replace_once(
    "sniperplug/services/walmart_exact_queue_bulk_persistence.py",
    '''    QUEUE_RETRY_BASE_SECONDS,\n    QUEUE_RETRY_MAX_SECONDS,\n    QUEUE_TABLE,\n''',
    '''    QUEUE_TABLE,\n    _failure_storage_policy,\n''',
)
replace_once(
    "sniperplug/services/walmart_exact_queue_bulk_persistence.py",
    '''    attempts = max(1, int(getattr(claim, "attempt_count", 0) or 0) + 1)\n    if status in {"identity_mismatch", "incomplete_identity"}:\n        delay_seconds = 12 * 60 * 60\n        stored_status = status\n    elif status == "provider_unavailable":\n        delay_seconds = 30 * 60\n        stored_status = "retry"\n    else:\n        delay_seconds = min(\n            QUEUE_RETRY_MAX_SECONDS,\n            QUEUE_RETRY_BASE_SECONDS * (2 ** min(6, attempts - 1)),\n        )\n        stored_status = "retry"\n''',
    '''    attempts = max(1, int(getattr(claim, "attempt_count", 0) or 0) + 1)\n    stored_status, delay_seconds = _failure_storage_policy(\n        status=status,\n        error=error,\n        attempts=attempts,\n    )\n''',
)

# --- Claim order and runtime index include page-recovery lane --------------
replace_once(
    "sniperplug/services/walmart_exact_queue_drain.py",
    '''    QUEUE_LEASE_SECONDS,\n    QUEUE_RETENTION_DAYS,\n    QUEUE_TABLE,\n''',
    '''    PAGE_RETRY_STATUS,\n    QUEUE_LEASE_SECONDS,\n    QUEUE_RETENTION_DAYS,\n    QUEUE_TABLE,\n''',
)
replace_once(
    "sniperplug/services/walmart_exact_queue_drain.py",
    '''                WHEN 'pending' THEN 0\n                WHEN 'retry' THEN 1\n                WHEN 'failed' THEN 2\n                WHEN 'verifying' THEN 3\n                WHEN 'verified_markdown' THEN 4\n                WHEN 'verified_under_threshold' THEN 5\n                WHEN 'verified_no_reference' THEN 6\n                WHEN 'verified_reference' THEN 7\n                WHEN 'not_buyable' THEN 8\n                ELSE 9\n''',
    '''                WHEN 'pending' THEN 0\n                WHEN 'retry' THEN 1\n                WHEN 'failed' THEN 2\n                WHEN 'page_retry' THEN 3\n                WHEN 'verifying' THEN 4\n                WHEN 'verified_markdown' THEN 5\n                WHEN 'verified_under_threshold' THEN 6\n                WHEN 'verified_no_reference' THEN 7\n                WHEN 'verified_reference' THEN 8\n                WHEN 'not_buyable' THEN 9\n                ELSE 10\n''',
)
# Keep the imported constant live and make typo drift fail tests/static checks.
replace_once(
    "sniperplug/services/walmart_exact_queue_drain.py",
    '''    bounded_limit = max(1, int(limit))\n''',
    '''    bounded_limit = max(1, int(limit))\n    page_retry_status = PAGE_RETRY_STATUS\n''',
)
replace_once(
    "sniperplug/services/walmart_exact_queue_drain.py",
    '''                WHEN 'page_retry' THEN 3\n''',
    '''                WHEN '{page_retry_status}' THEN 3\n''',
)
# The SQL is an f-string, so the local constant substitution above is intentional.

replace_once(
    "sniperplug/services/walmart_exact_runtime_schema.py",
    '''CLAIM_ORDER_INDEX = f"idx_{QUEUE_TABLE}_claim_order"\nSTATUS_DUE_INDEX = f"idx_{QUEUE_TABLE}_status_due"\n''',
    '''LEGACY_CLAIM_ORDER_INDEX = f"idx_{QUEUE_TABLE}_claim_order"\nCLAIM_ORDER_INDEX = f"idx_{QUEUE_TABLE}_claim_order_v2"\nSTATUS_DUE_INDEX = f"idx_{QUEUE_TABLE}_status_due"\n''',
)
replace_once(
    "sniperplug/services/walmart_exact_runtime_schema.py",
    '''        await conn.execute(\n            f"""\n            CREATE INDEX IF NOT EXISTS {CLAIM_ORDER_INDEX}\n''',
    '''        await conn.execute(f"DROP INDEX IF EXISTS {LEGACY_CLAIM_ORDER_INDEX}")\n        await conn.execute(\n            f"""\n            CREATE INDEX IF NOT EXISTS {CLAIM_ORDER_INDEX}\n''',
)
replace_once(
    "sniperplug/services/walmart_exact_runtime_schema.py",
    '''                    WHEN 'pending' THEN 0\n                    WHEN 'retry' THEN 1\n                    WHEN 'failed' THEN 2\n                    WHEN 'verifying' THEN 3\n                    WHEN 'verified_markdown' THEN 4\n                    WHEN 'verified_under_threshold' THEN 5\n                    WHEN 'verified_no_reference' THEN 6\n                    WHEN 'verified_reference' THEN 7\n                    WHEN 'not_buyable' THEN 8\n                    ELSE 9\n''',
    '''                    WHEN 'pending' THEN 0\n                    WHEN 'retry' THEN 1\n                    WHEN 'failed' THEN 2\n                    WHEN 'page_retry' THEN 3\n                    WHEN 'verifying' THEN 4\n                    WHEN 'verified_markdown' THEN 5\n                    WHEN 'verified_under_threshold' THEN 6\n                    WHEN 'verified_no_reference' THEN 7\n                    WHEN 'verified_reference' THEN 8\n                    WHEN 'not_buyable' THEN 9\n                    ELSE 10\n''',
)

# --- Health and bounded pressure split page-recovery retries ---------------
replace_once(
    "sniperplug/services/walmart_exact_queue_health.py",
    '''INITIAL_WORK_STATUSES = ("pending", "retry", "failed", "verifying")\nRECHECK_STATUSES = (\n''',
    '''INITIAL_WORK_STATUSES = ("pending", "retry", "failed", "verifying")\nRECOVERY_RETRY_STATUSES = ("page_retry",)\nRECHECK_STATUSES = (\n''',
)
replace_once(
    "sniperplug/services/walmart_exact_queue_health.py",
    '''    initial_due_now: int = 0\n    recheck_due_now: int = 0\n    delayed_retries: int = 0\n''',
    '''    initial_due_now: int = 0\n    recovery_due_now: int = 0\n    recheck_due_now: int = 0\n    delayed_retries: int = 0\n    delayed_recovery_retries: int = 0\n''',
)
replace_once(
    "sniperplug/services/walmart_exact_queue_health.py",
    '''            f"(new/retry **{self.initial_due_now}** • scheduled rechecks **{self.recheck_due_now}**) • "\n            f"delayed transient retries **{self.delayed_retries}** • "\n''',
    '''            f"(new/retry **{self.initial_due_now}** • page recovery **{self.recovery_due_now}** • "\n            f"scheduled rechecks **{self.recheck_due_now}**) • "\n            f"delayed transient retries **{self.delayed_retries}** "\n            f"(page recovery **{self.delayed_recovery_retries}**) • "\n''',
)
replace_once(
    "sniperplug/services/walmart_exact_queue_health.py",
    '''                SUM(CASE\n                    WHEN is_recent = 1\n                     AND status IN (\n                         'verified_markdown',\n''',
    '''                SUM(CASE\n                    WHEN is_recent = 1\n                     AND status = 'page_retry'\n                     AND next_attempt_at <= ?\n                     AND (lease_until IS NULL OR lease_until < ?)\n                    THEN 1 ELSE 0 END) AS recovery_due_now,\n                SUM(CASE\n                    WHEN is_recent = 1\n                     AND status IN (\n                         'verified_markdown',\n''',
)
replace_once(
    "sniperplug/services/walmart_exact_queue_health.py",
    '''                SUM(CASE\n                    WHEN is_recent = 1\n                     AND status IN ('retry', 'failed')\n                     AND next_attempt_at > ?\n                    THEN 1 ELSE 0 END) AS delayed_retries,\n                SUM(CASE\n                    WHEN is_recent = 1\n                     AND status IN ('incomplete_identity', 'identity_mismatch')\n''',
    '''                SUM(CASE\n                    WHEN is_recent = 1\n                     AND status IN ('retry', 'failed', 'page_retry')\n                     AND next_attempt_at > ?\n                    THEN 1 ELSE 0 END) AS delayed_retries,\n                SUM(CASE\n                    WHEN is_recent = 1\n                     AND status = 'page_retry'\n                     AND next_attempt_at > ?\n                    THEN 1 ELSE 0 END) AS delayed_recovery_retries,\n                SUM(CASE\n                    WHEN is_recent = 1\n                     AND status IN ('incomplete_identity', 'identity_mismatch')\n''',
)
replace_once(
    "sniperplug/services/walmart_exact_queue_health.py",
    '''                now_iso,\n                now_iso,\n                now_iso,\n                now_iso,\n                now_iso,\n                now_iso,\n                now_iso,\n                now_iso,\n            ),\n''',
    '''                now_iso,\n                now_iso,\n                now_iso,\n                now_iso,\n                now_iso,\n                now_iso,\n                now_iso,\n                now_iso,\n                now_iso,\n                now_iso,\n                now_iso,\n            ),\n''',
)
replace_once(
    "sniperplug/services/walmart_exact_queue_health.py",
    '''        initial_due_now=_as_int(_row_get(row, "initial_due_now", 2)),\n        recheck_due_now=_as_int(_row_get(row, "recheck_due_now", 3)),\n        delayed_retries=_as_int(_row_get(row, "delayed_retries", 4)),\n        identity_blocked=_as_int(_row_get(row, "identity_blocked", 5)),\n        verified=_as_int(_row_get(row, "verified", 6)),\n        verifying=_as_int(_row_get(row, "verifying", 7)),\n        pending=_as_int(_row_get(row, "pending", 8)),\n        unavailable=_as_int(_row_get(row, "unavailable", 9)),\n        stale=_as_int(_row_get(row, "stale", 10)),\n''',
    '''        initial_due_now=_as_int(_row_get(row, "initial_due_now", 2)),\n        recovery_due_now=_as_int(_row_get(row, "recovery_due_now", 3)),\n        recheck_due_now=_as_int(_row_get(row, "recheck_due_now", 4)),\n        delayed_retries=_as_int(_row_get(row, "delayed_retries", 5)),\n        delayed_recovery_retries=_as_int(_row_get(row, "delayed_recovery_retries", 6)),\n        identity_blocked=_as_int(_row_get(row, "identity_blocked", 7)),\n        verified=_as_int(_row_get(row, "verified", 8)),\n        verifying=_as_int(_row_get(row, "verifying", 9)),\n        pending=_as_int(_row_get(row, "pending", 10)),\n        unavailable=_as_int(_row_get(row, "unavailable", 11)),\n        stale=_as_int(_row_get(row, "stale", 12)),\n''',
)

replace_once(
    "sniperplug/services/walmart_exact_queue_pressure.py",
    '''FRESH_PRESSURE_CAP = 64\nVERIFYING_PRESSURE_CAP = 64\n''',
    '''FRESH_PRESSURE_CAP = 64\nRECOVERY_PRESSURE_CAP = 64\nVERIFYING_PRESSURE_CAP = 64\n''',
)
replace_once(
    "sniperplug/services/walmart_exact_queue_pressure.py",
    '''    recheck_cap = bounded_cap\n    verifying_cap = min(bounded_cap, VERIFYING_PRESSURE_CAP)\n''',
    '''    recovery_cap = min(bounded_cap, RECOVERY_PRESSURE_CAP)\n    recheck_cap = bounded_cap\n    verifying_cap = min(bounded_cap, VERIFYING_PRESSURE_CAP)\n''',
)
replace_once(
    "sniperplug/services/walmart_exact_queue_pressure.py",
    '''        recheck_due AS (\n            SELECT item_id\n            FROM {QUEUE_TABLE}\n            WHERE status IN (\n''',
    '''        recovery_due AS (\n            SELECT item_id\n            FROM {QUEUE_TABLE}\n            WHERE status = 'page_retry'\n              AND next_attempt_at <= ?\n              AND last_seen_at >= ?\n              AND (lease_until IS NULL OR lease_until < ?)\n            LIMIT ?\n        ),\n        recheck_due AS (\n            SELECT item_id\n            FROM {QUEUE_TABLE}\n            WHERE status IN (\n''',
)
replace_once(
    "sniperplug/services/walmart_exact_queue_pressure.py",
    '''            (SELECT COUNT(*) FROM fresh_due) AS initial_due_now,\n            (SELECT COUNT(*) FROM recheck_due) AS recheck_due_now,\n            (SELECT COUNT(*) FROM active_verifying) AS verifying\n''',
    '''            (SELECT COUNT(*) FROM fresh_due) AS initial_due_now,\n            (SELECT COUNT(*) FROM recovery_due) AS recovery_due_now,\n            (SELECT COUNT(*) FROM recheck_due) AS recheck_due_now,\n            (SELECT COUNT(*) FROM active_verifying) AS verifying\n''',
)
replace_once(
    "sniperplug/services/walmart_exact_queue_pressure.py",
    '''            fresh_cap,\n            now_iso,\n            discovery_cutoff,\n            now_iso,\n            recheck_cap,\n            now_iso,\n            verifying_cap,\n''',
    '''            fresh_cap,\n            now_iso,\n            discovery_cutoff,\n            now_iso,\n            recovery_cap,\n            now_iso,\n            discovery_cutoff,\n            now_iso,\n            recheck_cap,\n            now_iso,\n            verifying_cap,\n''',
)
replace_once(
    "sniperplug/services/walmart_exact_queue_pressure.py",
    '''    fresh = _as_int(_row_get(row, "initial_due_now", 0))\n    rechecks = _as_int(_row_get(row, "recheck_due_now", 1))\n    verifying = _as_int(_row_get(row, "verifying", 2))\n    due = fresh + rechecks\n''',
    '''    fresh = _as_int(_row_get(row, "initial_due_now", 0))\n    recovery = _as_int(_row_get(row, "recovery_due_now", 1))\n    rechecks = _as_int(_row_get(row, "recheck_due_now", 2))\n    verifying = _as_int(_row_get(row, "verifying", 3))\n    due = fresh + recovery + rechecks\n''',
)
replace_once(
    "sniperplug/services/walmart_exact_queue_pressure.py",
    '''        initial_due_now=fresh,\n        recheck_due_now=rechecks,\n        verifying=verifying,\n''',
    '''        initial_due_now=fresh,\n        recovery_due_now=recovery,\n        recheck_due_now=rechecks,\n        verifying=verifying,\n''',
)
replace_once(
    "sniperplug/services/walmart_exact_queue_pressure.py",
    '''        f"(new/retry **{max(0, int(health.initial_due_now or 0))}** • "\n        f"scheduled rechecks **{max(0, int(health.recheck_due_now or 0))}**) • "\n''',
    '''        f"(new/retry **{max(0, int(health.initial_due_now or 0))}** • "\n        f"page recovery **{max(0, int(health.recovery_due_now or 0))}** • "\n        f"scheduled rechecks **{max(0, int(health.recheck_due_now or 0))}**) • "\n''',
)

# --- Fresh-work policy ignores page retry lane except emergency total -------
replace_once(
    "sniperplug/services/walmart_fresh_work_policy.py",
    '''        int(getattr(health, "initial_due_now", 0) or 0)\n        or int(getattr(health, "recheck_due_now", 0) or 0)\n        or int(getattr(health, "due_now", 0) or 0) == 0\n''',
    '''        int(getattr(health, "initial_due_now", 0) or 0)\n        or int(getattr(health, "recovery_due_now", 0) or 0)\n        or int(getattr(health, "recheck_due_now", 0) or 0)\n        or int(getattr(health, "due_now", 0) or 0) == 0\n''',
)
replace_once(
    "sniperplug/services/walmart_fresh_work_policy.py",
    '''    recheck_due = max(0, int(getattr(health, "recheck_due_now", 0) or 0))\n    fresh_limit_value = max(1, int(fresh_limit))\n''',
    '''    recovery_due = max(0, int(getattr(health, "recovery_due_now", 0) or 0))\n    recheck_due = max(0, int(getattr(health, "recheck_due_now", 0) or 0))\n    fresh_limit_value = max(1, int(fresh_limit))\n''',
)
replace_once(
    "sniperplug/services/walmart_fresh_work_policy.py",
    '''            f"new/retry due **{fresh_due}** • scheduled rechecks **{recheck_due}** • "\n''',
    '''            f"new/retry due **{fresh_due}** • page recovery **{recovery_due}** • "\n            f"scheduled rechecks **{recheck_due}** • "\n''',
)
replace_once(
    "sniperplug/services/walmart_fresh_work_policy.py",
    '''            f"new/retry due **{fresh_due}** • scheduled rechecks **{recheck_due}** • "\n            f"verifying **{verifying}** • terminal identity blocks excluded "\n            f"**{identity_blocked}**"\n        )\n\n    return None\n''',
    '''            f"new/retry due **{fresh_due}** • page recovery **{recovery_due}** • "\n            f"scheduled rechecks **{recheck_due}** • "\n            f"verifying **{verifying}** • terminal identity blocks excluded "\n            f"**{identity_blocked}**"\n        )\n\n    return None\n''',
)
replace_once(
    "sniperplug/services/walmart_fresh_work_policy.py",
    '''        f"new/retry={max(0, int(getattr(health, 'initial_due_now', 0) or 0))} "\n        f"rechecks={max(0, int(getattr(health, 'recheck_due_now', 0) or 0))} "\n''',
    '''        f"new/retry={max(0, int(getattr(health, 'initial_due_now', 0) or 0))} "\n        f"page_recovery={max(0, int(getattr(health, 'recovery_due_now', 0) or 0))} "\n        f"rechecks={max(0, int(getattr(health, 'recheck_due_now', 0) or 0))} "\n''',
)

# --- Exact worker timeout budget + transient reason buckets ----------------
replace_once(
    "sniperplug/services/walmart_exact_queue_runtime.py",
    '''    QUEUE_TABLE,\n    _classify_exact_candidate,\n''',
    '''    PAGE_RETRY_STATUS,\n    QUEUE_TABLE,\n    _classify_exact_candidate,\n''',
)
replace_once(
    "sniperplug/services/walmart_exact_queue_runtime.py",
    '''DRAIN_CONCURRENCY = 4\n\n\n@dataclass''',
    '''DRAIN_CONCURRENCY = 4\nEXACT_FETCH_HARD_TIMEOUT_SECONDS = 50.0\n\n\n@dataclass''',
)
replace_once(
    "sniperplug/services/walmart_exact_queue_runtime.py",
    '''    failed: int = 0\n    pending_total: int = 0\n''',
    '''    failed: int = 0\n    retry_page: int = 0\n    retry_api: int = 0\n    retry_timeout: int = 0\n    retry_price_change: int = 0\n    retry_candidate: int = 0\n    retry_other: int = 0\n    pending_total: int = 0\n''',
)
replace_once(
    "sniperplug/services/walmart_exact_queue_runtime.py",
    '''        timing = (\n            f"claim **{self.claim_seconds:.2f}s** • "\n            f"fetch **{self.fetch_seconds:.2f}s** • "\n            f"store **{self.store_seconds:.2f}s**"\n        )\n''',
    '''        retry_detail = (\n            f"page **{self.retry_page}** • api **{self.retry_api}** • "\n            f"timeout **{self.retry_timeout}** • price-change **{self.retry_price_change}** • "\n            f"candidate **{self.retry_candidate}** • other **{self.retry_other}**"\n        )\n        timing = (\n            f"claim **{self.claim_seconds:.2f}s** • "\n            f"fetch **{self.fetch_seconds:.2f}s** • "\n            f"store **{self.store_seconds:.2f}s**"\n        )\n''',
)
replace_once(
    "sniperplug/services/walmart_exact_queue_runtime.py",
    '''            f"({identity_detail}) • transient failures **{self.failed}** • "\n''',
    '''            f"({identity_detail}) • transient failures **{self.failed}** ({retry_detail}) • "\n''',
)
replace_once(
    "sniperplug/services/walmart_exact_queue_runtime.py",
    '''    timeout_seconds: float = 8.0,\n''',
    '''    timeout_seconds: float = EXACT_FETCH_HARD_TIMEOUT_SECONDS,\n''',
)
replace_once(
    "sniperplug/services/walmart_exact_queue_runtime.py",
    '''        "failed": 0,\n    }\n''',
    '''        "failed": 0,\n        "retry_page": 0,\n        "retry_api": 0,\n        "retry_timeout": 0,\n        "retry_price_change": 0,\n        "retry_candidate": 0,\n        "retry_other": 0,\n    }\n''',
)
replace_once(
    "sniperplug/services/walmart_exact_queue_runtime.py",
    '''            else:\n                counts["failed"] += 1\n            continue\n''',
    '''            else:\n                counts["failed"] += 1\n                counts[_retry_error_bucket(status=status, error=error)] += 1\n            continue\n''',
)
replace_once(
    "sniperplug/services/walmart_exact_queue_runtime.py",
    '''    try:\n        payload = await asyncio.wait_for(\n            detail_fetcher(claim.item_id),\n            timeout=max(0.1, float(timeout_seconds)),\n        )\n''',
    '''    try:\n        payload = await asyncio.wait_for(\n            detail_fetcher(claim.item_id),\n            timeout=max(1.0, float(timeout_seconds)),\n        )\n''',
)
replace_once(
    "sniperplug/services/walmart_exact_queue_runtime.py",
    '''    except asyncio.CancelledError:\n        raise\n    except Exception as error:  # noqa: BLE001 - provider failures remain retryable.\n        return None, "retry", _compact_text(error, 500)\n\n    if exact is None:\n        return None, "retry", "exact Walmart detail response did not build a candidate"\n''',
    '''    except asyncio.CancelledError:\n        raise\n    except TimeoutError:\n        return (\n            None,\n            "retry",\n            f"retry_kind=outer_timeout exact Walmart detail exceeded {float(timeout_seconds):.1f}s hard safety budget",\n        )\n    except Exception as error:  # noqa: BLE001 - provider failures remain retryable.\n        retry_kind = str(getattr(error, "retry_kind", "") or "").strip().lower()\n        status = PAGE_RETRY_STATUS if retry_kind.startswith("page_") else "retry"\n        kind = retry_kind or "provider_exception"\n        return None, status, f"retry_kind={kind} {_compact_text(error, 440)}"\n\n    if exact is None:\n        return (\n            None,\n            "retry",\n            "retry_kind=candidate_build exact Walmart detail response did not build a candidate",\n        )\n''',
)
replace_once(
    "sniperplug/services/walmart_exact_queue_runtime.py",
    '''        return None, "retry", "exact detail did not provide a numeric current price"\n''',
    '''        return None, "retry", "retry_kind=api_price_missing exact detail did not provide a numeric current price"\n''',
)
replace_once(
    "sniperplug/services/walmart_exact_queue_runtime.py",
    '''def _identity_error_bucket(*, status: str, error: str) -> str:\n''',
    '''def _retry_error_bucket(*, status: str, error: str) -> str:\n    lowered = str(error or "").lower()\n    if "page_price_mismatch" in lowered:\n        return "retry_price_change"\n    if status == PAGE_RETRY_STATUS or "retry_kind=page_" in lowered:\n        return "retry_page"\n    if "timeout" in lowered:\n        return "retry_timeout"\n    if "retry_kind=api_" in lowered:\n        return "retry_api"\n    if "candidate_build" in lowered:\n        return "retry_candidate"\n    return "retry_other"\n\n\ndef _identity_error_bucket(*, status: str, error: str) -> str:\n''',
)

# Bulk runtime mirrors the new timeout and counters.
replace_once(
    "sniperplug/services/walmart_exact_queue_bulk_runtime.py",
    '''    DRAIN_BATCH_SIZE,\n    DRAIN_CONCURRENCY,\n    TERMINAL_IDENTITY_STATUSES,\n''',
    '''    DRAIN_BATCH_SIZE,\n    DRAIN_CONCURRENCY,\n    EXACT_FETCH_HARD_TIMEOUT_SECONDS,\n    TERMINAL_IDENTITY_STATUSES,\n''',
)
replace_once(
    "sniperplug/services/walmart_exact_queue_bulk_runtime.py",
    '''    _fetch_exact_candidate_off_loop,\n    _identity_error_bucket,\n''',
    '''    _fetch_exact_candidate_off_loop,\n    _identity_error_bucket,\n    _retry_error_bucket,\n''',
)
replace_once(
    "sniperplug/services/walmart_exact_queue_bulk_runtime.py",
    '''    timeout_seconds: float = 8.0,\n''',
    '''    timeout_seconds: float = EXACT_FETCH_HARD_TIMEOUT_SECONDS,\n''',
)
replace_once(
    "sniperplug/services/walmart_exact_queue_bulk_runtime.py",
    '''        "failed": 0,\n    }\n''',
    '''        "failed": 0,\n        "retry_page": 0,\n        "retry_api": 0,\n        "retry_timeout": 0,\n        "retry_price_change": 0,\n        "retry_candidate": 0,\n        "retry_other": 0,\n    }\n''',
)
replace_once(
    "sniperplug/services/walmart_exact_queue_bulk_runtime.py",
    '''            else:\n                counts["failed"] += 1\n            continue\n''',
    '''            else:\n                counts["failed"] += 1\n                counts[_retry_error_bucket(status=status, error=error)] += 1\n            continue\n''',
)

# --- Public quality gate exposes exact rejection reason --------------------
replace_once(
    "sniperplug/services/public_deal_quality.py",
    '''def has_verified_api_threshold_discount(card: Any, *, source_label: str = "", min_discount: int = 50) -> bool:\n    if is_review_or_watchlist(card, source_label=source_label):\n        return False\n    if has_low_trust_reference(card, source_label=source_label):\n        return False\n    if display_reference_without_proof(card, source_label=source_label):\n        return False\n    if not has_real_price(card):\n        return False\n    if not direct_product_url(card):\n        return False\n    lane = normalized_lane(card)\n    if lane in PRIVATE_PROMO_LANES:\n        return False\n    discount = structured_discount(card) or 0.0\n    if discount < max(1, int(min_discount)):\n        return False\n    if lane == LANE_PRICE_MEMORY_DROP:\n        return has_global_exact_offer_memory_proof(card)\n    if lane in {LANE_OPEN_BOX_LIKE_NEW, LANE_RESTORED_REFURBISHED}:\n        condition = normalized_condition(attr_value(card, "api_condition", "condition"))\n        if not condition or not has_structured_reference_proof(card):\n            return False\n        if lane == LANE_OPEN_BOX_LIKE_NEW and not is_open_box_condition(condition):\n            return False\n        if lane == LANE_RESTORED_REFURBISHED and not is_restored_condition(condition):\n            return False\n    return lane in PUBLIC_PRICE_LANES or lane in {LANE_CLEARANCE, LANE_ROLLBACK}\n''',
    '''def public_deal_rejection_reason(\n    card: Any,\n    *,\n    source_label: str = "",\n    min_discount: int = 50,\n) -> str | None:\n    if is_review_or_watchlist(card, source_label=source_label):\n        return "review_or_watchlist"\n    if has_low_trust_reference(card, source_label=source_label):\n        return "low_trust_reference"\n    if display_reference_without_proof(card, source_label=source_label):\n        return "display_reference_without_structured_proof"\n    if not has_real_price(card):\n        return "missing_current_price"\n    if not direct_product_url(card):\n        return "missing_direct_walmart_url"\n    lane = normalized_lane(card)\n    if lane in PRIVATE_PROMO_LANES:\n        return f"private_promo_lane:{lane}"\n    discount = structured_discount(card) or 0.0\n    if discount < max(1, int(min_discount)):\n        return f"below_threshold:{discount:.2f}<{max(1, int(min_discount))}"\n    if lane == LANE_PRICE_MEMORY_DROP:\n        if not has_global_exact_offer_memory_proof(card):\n            return "price_memory_proof_incomplete"\n        return None\n    if lane in {LANE_OPEN_BOX_LIKE_NEW, LANE_RESTORED_REFURBISHED}:\n        condition = normalized_condition(attr_value(card, "api_condition", "condition"))\n        if not condition:\n            return "condition_missing"\n        if not has_structured_reference_proof(card):\n            return "condition_reference_proof_missing"\n        if lane == LANE_OPEN_BOX_LIKE_NEW and not is_open_box_condition(condition):\n            return "open_box_condition_mismatch"\n        if lane == LANE_RESTORED_REFURBISHED and not is_restored_condition(condition):\n            return "restored_condition_mismatch"\n    if lane not in PUBLIC_PRICE_LANES and lane not in {LANE_CLEARANCE, LANE_ROLLBACK}:\n        return f"unsupported_public_lane:{lane or 'unknown'}"\n    return None\n\n\ndef has_verified_api_threshold_discount(card: Any, *, source_label: str = "", min_discount: int = 50) -> bool:\n    return public_deal_rejection_reason(\n        card,\n        source_label=source_label,\n        min_discount=min_discount,\n    ) is None\n''',
)

replace_once(
    "sniperplug/services/public_deal_posts.py",
    '''from sniperplug.services.public_deal_quality import is_public_deal_candidate, prepare_public_deal_candidate, prepare_public_scout_candidate\n''',
    '''from sniperplug.services.public_deal_quality import (\n    is_public_deal_candidate,\n    prepare_public_deal_candidate,\n    prepare_public_scout_candidate,\n    public_deal_rejection_reason,\n)\n''',
)
replace_once(
    "sniperplug/services/public_deal_posts.py",
    '''    skipped_not_alertable: int = 0\n    skipped_disabled: int = 0\n''',
    '''    skipped_not_alertable: int = 0\n    not_alertable_reasons: tuple[str, ...] = ()\n    skipped_disabled: int = 0\n''',
)
replace_once(
    "sniperplug/services/public_deal_posts.py",
    '''    skipped_wrong_retailer = 0\n    notes: list[str] = []\n''',
    '''    skipped_wrong_retailer = 0\n    not_alertable_reasons: list[str] = []\n    notes: list[str] = []\n''',
)
replace_once(
    "sniperplug/services/public_deal_posts.py",
    '''        except Exception as exc:\n            notes.append(f"public category decision failed; card blocked: {clean_error_text(exc)}")\n            skipped_not_alertable += 1\n            continue\n        if category_decision.action == "suppress":\n            skipped_not_alertable += 1\n            continue\n''',
    '''        except Exception as exc:\n            cleaned = clean_error_text(exc)\n            notes.append(f"public category decision failed; card blocked: {cleaned}")\n            not_alertable_reasons.append(f"category_error:{cleaned}")\n            skipped_not_alertable += 1\n            continue\n        if category_decision.action == "suppress":\n            not_alertable_reasons.append(\n                f"category_suppress:{category_decision.category_key}:{category_decision.reason}"\n            )\n            skipped_not_alertable += 1\n            continue\n''',
)
replace_once(
    "sniperplug/services/public_deal_posts.py",
    '''        if allow_review_scout:\n            public_ready = prepare_public_scout_candidate(card, source_label=source_label)\n        else:\n            public_ready = prepare_public_deal_candidate(\n                card,\n                source_label=source_label,\n                min_discount=min_public_discount,\n            )\n        if not public_ready:\n            skipped_not_alertable += 1\n            continue\n''',
    '''        if allow_review_scout:\n            public_ready = prepare_public_scout_candidate(card, source_label=source_label)\n            if not public_ready:\n                not_alertable_reasons.append("scout_quality_rejected")\n        else:\n            quality_reason = public_deal_rejection_reason(\n                card,\n                source_label=source_label,\n                min_discount=min_public_discount,\n            )\n            if quality_reason is not None:\n                not_alertable_reasons.append(f"quality:{quality_reason}")\n                skipped_not_alertable += 1\n                continue\n            public_ready = prepare_public_deal_candidate(\n                card,\n                source_label=source_label,\n                min_discount=min_public_discount,\n            )\n            if not public_ready:\n                not_alertable_reasons.append("quality:prepare_failed_after_pass")\n        if not public_ready:\n            skipped_not_alertable += 1\n            continue\n''',
)
replace_once(
    "sniperplug/services/public_deal_posts.py",
    '''        if not bool(should_alert):\n            skipped_not_alertable += 1\n            continue\n''',
    '''        if not bool(should_alert):\n            not_alertable_reasons.append("should_alert=false")\n            skipped_not_alertable += 1\n            continue\n''',
)
replace_once(
    "sniperplug/services/public_deal_posts.py",
    '''        skipped_not_alertable=skipped_not_alertable,\n        skipped_disabled=0,\n''',
    '''        skipped_not_alertable=skipped_not_alertable,\n        not_alertable_reasons=tuple(dict.fromkeys(not_alertable_reasons))[:8],\n        skipped_disabled=0,\n''',
)
replace_once(
    "sniperplug/services/walmart_global_deal_fanout_bulk.py",
    '''        "not_alertable=%s disabled=%s wrong_retailer=%s label=%s",\n''',
    '''        "not_alertable=%s not_alertable_reasons=%s disabled=%s wrong_retailer=%s label=%s",\n''',
)
replace_once(
    "sniperplug/services/walmart_global_deal_fanout_bulk.py",
    '''        int(getattr(result, "skipped_not_alertable", 0) or 0),\n        int(getattr(result, "skipped_disabled", 0) or 0),\n''',
    '''        int(getattr(result, "skipped_not_alertable", 0) or 0),\n        list(getattr(result, "not_alertable_reasons", ()) or ()),\n        int(getattr(result, "skipped_disabled", 0) or 0),\n''',
)

# --- Tests -----------------------------------------------------------------
append_once(
    "tests/test_walmart_page_identity_recovery.py",
    '''def test_recent_terminal_rearm_is_bounded_and_one_time(monkeypatch) -> None:\n''',
    '''''',
)
# Add a separate rowcount fallback regression before the Open Walmart test.
replace_once(
    "tests/test_walmart_page_identity_recovery.py",
    '''def test_identity_blocked_recovery_item_always_has_open_walmart_link() -> None:\n''',
    '''def test_recent_terminal_rearm_falls_back_to_sql_changes_when_rowcount_missing(monkeypatch) -> None:\n    class Cursor:\n        def __init__(self, row=None, *, rowcount=-1):\n            self.row = row\n            self.rowcount = rowcount\n\n        async def fetchone(self):\n            return self.row\n\n    class Connection:\n        def __init__(self):\n            self.marker = False\n            self.changes = 0\n\n        async def execute(self, sql, params=()):\n            text = str(sql)\n            if f"SELECT migration_key FROM {PAGE_RECOVERY_REARM_TABLE}" in text:\n                return Cursor(None, rowcount=-1)\n            if "UPDATE walmart_exact_detail_queue" in text:\n                self.changes = 5\n                return Cursor(rowcount=-1)\n            if text.strip() == "SELECT changes()":\n                return Cursor((self.changes,), rowcount=-1)\n            if f"INSERT INTO {PAGE_RECOVERY_REARM_TABLE}" in text:\n                self.marker = True\n                return Cursor(rowcount=1)\n            return Cursor(rowcount=0)\n\n        async def commit(self):\n            return None\n\n    conn = Connection()\n    db = SimpleNamespace(require_conn=lambda: conn)\n\n    async def no_op(_db):\n        return None\n\n    monkeypatch.setattr(exact_queue, "ensure_walmart_exact_verification_queue", no_op)\n\n    assert asyncio.run(rearm_recent_identity_rows_for_page_recovery(db)) == 5\n\n\ndef test_identity_blocked_recovery_item_always_has_open_walmart_link() -> None:\n''',
)

# Runtime page retry classification/persistence test.
replace_once(
    "tests/test_walmart_exact_queue_runtime.py",
    '''from sniperplug.providers.walmart import WalmartAffiliateConfig, WalmartProvider\n''',
    '''from sniperplug.providers.walmart import WalmartAffiliateConfig, WalmartProvider, WalmartProviderError\n''',
)
replace_once(
    "tests/test_walmart_exact_queue_runtime.py",
    '''from sniperplug.services.walmart_exact_verification_queue import (\n    QUEUE_TABLE,\n''',
    '''from sniperplug.services.walmart_exact_verification_queue import (\n    PAGE_RETRY_STATUS,\n    QUEUE_TABLE,\n''',
)
append_once(
    "tests/test_walmart_exact_queue_runtime.py",
    '''    asyncio.run(run())\n''',
    '''\n\ndef test_page_recovery_transient_uses_nonfresh_retry_lane() -> None:\n    class PageRetryProvider(FakeDetailProvider):\n        async def fetch_product_detail_payload(self, item_id: str) -> dict:\n            self.calls.append(item_id)\n            raise WalmartProviderError(\n                "Walmart product-page identity recovery temporarily unavailable: HTTP 403",\n                retry_kind="page_http_403",\n            )\n\n    async def run() -> None:\n        conn = await aiosqlite.connect(":memory:")\n        db = FakeDatabase(conn)\n        await enqueue_walmart_exact_verification_candidates(\n            db,\n            [search_candidate("444444")],\n            min_discount=50,\n            source_label="global:test",\n        )\n        provider = PageRetryProvider({})\n\n        result = await process_actionable_walmart_exact_queue_batch(\n            db,\n            provider=provider,\n            limit=1,\n            concurrency=1,\n            min_discount=50,\n        )\n\n        assert result.claimed == 1\n        assert result.failed == 1\n        assert result.retry_page == 1\n        cursor = await conn.execute(\n            f"SELECT status, last_error, next_attempt_at FROM {QUEUE_TABLE} WHERE item_id = ?",\n            ("444444",),\n        )\n        status, last_error, next_attempt_at = await cursor.fetchone()\n        assert status == PAGE_RETRY_STATUS\n        assert "retry_kind=page_http_403" in last_error\n        assert datetime.fromisoformat(next_attempt_at) >= datetime.now(timezone.utc) + timedelta(minutes=55)\n\n        health = await load_walmart_exact_queue_health(db)\n        assert health.initial_due_now == 0\n        assert health.delayed_recovery_retries == 1\n        await conn.close()\n\n    asyncio.run(run())\n''',
)

# Pressure tests now contain four bounded lanes.
replace_once(
    "tests/test_walmart_exact_queue_pressure.py",
    '''            "initial_due_now": 11,\n            "recheck_due_now": 319,\n            "verifying": 2,\n''',
    '''            "initial_due_now": 11,\n            "recovery_due_now": 23,\n            "recheck_due_now": 319,\n            "verifying": 2,\n''',
)
replace_once(
    "tests/test_walmart_exact_queue_pressure.py",
    '''    assert health.recheck_due_now == 319\n    assert health.due_now == 330\n    assert health.verifying == 2\n    assert health.total == 332\n    assert conn.sql.count("LIMIT ?") == 3\n    assert conn.params[3] == 64\n    assert conn.params[7] == 600\n    assert conn.params[9] == 64\n''',
    '''    assert health.recovery_due_now == 23\n    assert health.recheck_due_now == 319\n    assert health.due_now == 353\n    assert health.verifying == 2\n    assert health.total == 355\n    assert conn.sql.count("LIMIT ?") == 4\n    assert conn.params[3] == 64\n    assert conn.params[7] == 64\n    assert conn.params[11] == 600\n    assert conn.params[13] == 64\n''',
)
replace_once(
    "tests/test_walmart_exact_queue_pressure.py",
    '''    assert conn.params[3] == 64\n    assert conn.params[7] == 64\n    assert conn.params[9] == 64\n''',
    '''    assert conn.params[3] == 64\n    assert conn.params[7] == 64\n    assert conn.params[11] == 64\n    assert conn.params[13] == 64\n''',
)
replace_once(
    "tests/test_walmart_exact_queue_pressure.py",
    '''    assert "actionable due **330**" in summary\n    assert "new/retry **11**" in summary\n    assert "scheduled rechecks **319**" in summary\n''',
    '''    assert "actionable due **353**" in summary\n    assert "new/retry **11**" in summary\n    assert "page recovery **23**" in summary\n    assert "scheduled rechecks **319**" in summary\n''',
)
replace_once(
    "tests/test_walmart_exact_queue_pressure.py",
    '''    assert PRESSURE.count("LIMIT ?") == 3\n    assert "DEFAULT_PRESSURE_CAP = 600" in PRESSURE\n    assert "FRESH_PRESSURE_CAP = 64" in PRESSURE\n    assert "VERIFYING_PRESSURE_CAP = 64" in PRESSURE\n''',
    '''    assert PRESSURE.count("LIMIT ?") == 4\n    assert "DEFAULT_PRESSURE_CAP = 600" in PRESSURE\n    assert "FRESH_PRESSURE_CAP = 64" in PRESSURE\n    assert "RECOVERY_PRESSURE_CAP = 64" in PRESSURE\n    assert "VERIFYING_PRESSURE_CAP = 64" in PRESSURE\n''',
)

# Fresh-work policy regression: recovery lane alone must not hit 12-row pause.
replace_once(
    "tests/test_walmart_fresh_work_policy.py",
    '''def test_fresh_work_pauses_catalog_before_aggressive_drain() -> None:\n''',
    '''def test_page_recovery_retry_backlog_does_not_hit_fresh_pause_threshold() -> None:\n    health = WalmartExactQueueHealth(\n        due_now=200,\n        initial_due_now=0,\n        recovery_due_now=200,\n        recheck_due_now=0,\n        verifying=0,\n    )\n\n    assert catalog_backpressure_reason(health) is None\n    assert should_use_drain_mode(health) is False\n\n\ndef test_fresh_work_pauses_catalog_before_aggressive_drain() -> None:\n''',
)

# Public quality/fanout diagnostics regression.
replace_once(
    "tests/test_walmart_global_deal_fanout_bulk.py",
    '''    assert "Global Walmart public destination decision" in BULK_SOURCE\n''',
    '''    assert "Global Walmart public destination decision" in BULK_SOURCE\n    assert "not_alertable_reasons=%s" in BULK_SOURCE\n''',
)

# Add focused public reason tests to the public quality test module if present.
quality_test = ROOT / "tests/test_public_deal_quality.py"
if quality_test.exists():
    text = quality_test.read_text(encoding="utf-8")
    if "public_deal_rejection_reason" not in text:
        text = text.replace(
            "from sniperplug.services.public_deal_quality import (",
            "from sniperplug.services.public_deal_quality import (\n    public_deal_rejection_reason,",
            1,
        )
        text += '''\n\ndef test_public_rejection_reason_reports_threshold_without_weakening_gate() -> None:\n    card = _card(current=40.0, reference=100.0)\n    reason = public_deal_rejection_reason(card, min_discount=70)\n    assert reason is not None\n    assert reason.startswith("below_threshold:")\n\n'''
        quality_test.write_text(text, encoding="utf-8")

# Update task record with implementation-in-progress findings.
path = ROOT / "ACTIVE_TASK.md"
text = path.read_text(encoding="utf-8")
text = text.replace(
    "## Validation\n- Pending implementation.",
    "## Validation\n- Implementation patch applied on `fix/walmart-page-retry-fanout`; targeted and full validation pending canonical CI.",
)
text = text.replace(
    "## Cleanup / conflict inspection\n- Pending implementation.",
    "## Cleanup / conflict inspection\n- Runtime retry persistence is being centralized through one failure-storage policy so bulk and compatibility paths cannot drift. Final diff/conflict inspection pending.",
)
path.write_text(text, encoding="utf-8")

# Self-clean temporary applicator artifacts before the implementation commit.
for relative in (
    ".github/scripts/apply_walmart_retry_fanout.py",
    ".github/workflows/apply-walmart-retry-fanout.yml",
):
    target = ROOT / relative
    if target.exists():
        target.unlink()

print("Walmart retry/fanout patch applied")
