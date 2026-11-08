"""Phase A3: cycle deadlines, Walmart quota guard, failure visibility.

Behavioral tests drive the production worker loops through the end-to-end
harness (``tests/walmart_pipeline_harness.py``). Only Walmart's HTTP
endpoint and Discord's gateway objects are simulated.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import aiosqlite
import pytest

from sniperplug.services.walmart_request_coordinator import (
    WalmartQuotaGuard,
    build_walmart_quota_guard_from_env,
    walmart_quota_snapshot,
)
from sniperplug.services.walmart_worker_cycle_health import (
    TABLE,
    load_worker_cycle_health,
    record_worker_cycle_outcome,
)
from walmart_pipeline_harness import Pipeline, walmart_item

ROOT = Path(__file__).resolve().parents[1]
RUNNER = (ROOT / "sniperplug/cogs/global_auto_scan_runner.py").read_text(
    encoding="utf-8"
)
ALERTS = (ROOT / "sniperplug/cogs/canonical_public_alerts.py").read_text(
    encoding="utf-8"
)


class FakeDatabase:
    def __init__(self, conn):
        self.conn = conn

    def require_conn(self):
        return self.conn


async def _pipeline(tmp_path, monkeypatch, **kw) -> Pipeline:
    return await Pipeline(tmp_path, monkeypatch, **kw).start()


async def _wait_until(predicate, *, timeout_seconds: float = 15.0) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        if await predicate():
            return True
        await asyncio.sleep(0.1)
    return False


# ---------------------------------------------------------------- quota guard

def test_walmart_quota_guard_unset_is_fail_open(monkeypatch) -> None:
    for name in (
        "WALMART_MONTHLY_LIMIT",
        "WALMART_DAILY_LIMIT",
        "WALMART_HOURLY_LIMIT",
    ):
        monkeypatch.delenv(name, raising=False)

    guard = build_walmart_quota_guard_from_env()

    assert guard.monthly_limit == 0
    assert guard.daily_limit == 0
    assert guard.hourly_limit == 0
    for _ in range(10_000):
        assert guard.record().allowed
    assert guard.check().allowed
    assert not guard.check().configured


def test_walmart_quota_guard_class_defaults_are_conservative() -> None:
    guard = WalmartQuotaGuard()

    assert guard.monthly_limit == 200_000
    assert guard.daily_limit == 10_000
    assert guard.hourly_limit == 600


def test_walmart_quota_guard_blocks_when_low_limit_set() -> None:
    guard = WalmartQuotaGuard(monthly_limit=0, daily_limit=2, hourly_limit=0)

    assert guard.record().allowed
    assert guard.record().allowed
    denied = guard.check()
    assert not denied.allowed
    assert "Daily" in (denied.reason or "")
    # A denied record does not consume quota.
    assert guard.record().allowed is False
    assert guard.check().daily_used == 2


def test_walmart_quota_guard_hourly_window_rolls_over() -> None:
    from datetime import datetime, timezone

    guard = WalmartQuotaGuard(monthly_limit=0, daily_limit=0, hourly_limit=1)
    first_hour = datetime(2026, 11, 7, 10, 0, tzinfo=timezone.utc)
    next_hour = datetime(2026, 11, 7, 11, 0, tzinfo=timezone.utc)

    assert guard.record(now=first_hour).allowed
    assert not guard.check(now=first_hour).allowed
    assert guard.check(now=next_hour).allowed


def test_coordinator_counts_every_granted_lease(monkeypatch) -> None:
    import sniperplug.services.walmart_request_coordinator as coordinator

    guard = WalmartQuotaGuard(monthly_limit=0, daily_limit=1, hourly_limit=0)
    monkeypatch.setattr(coordinator, "walmart_quota_guard", guard)
    local = coordinator.WalmartRequestCoordinator()

    local.acquire(priority=coordinator.EXACT_PRIORITY)
    # Over-cap leases are still granted (the coordinator never blocks);
    # usage stays honest because every grant is counted.
    local.acquire(priority=coordinator.EXACT_PRIORITY)
    local.release()
    local.release()

    snapshot = guard.check(cost=0)
    assert snapshot.daily_used == 2
    assert not snapshot.allowed


# ------------------------------------------------------- cycle-health service

def test_cycle_health_counter_increments_and_resets() -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        db = FakeDatabase(conn)

        health = await load_worker_cycle_health(db)
        assert health.available
        assert health.exact.total_cycles == 0
        assert not health.needs_attention()

        await record_worker_cycle_outcome(
            db, "exact", ok=False, error="boom one"
        )
        await record_worker_cycle_outcome(
            db, "exact", ok=False, error="boom two", timed_out=True
        )
        health = await load_worker_cycle_health(db)
        assert health.exact.consecutive_failures == 2
        assert health.exact.total_failures == 2
        assert health.exact.total_timeouts == 1
        assert health.exact.total_cycles == 2
        assert health.exact.last_failure_at is not None
        assert health.exact.last_error == "boom two"
        assert not health.needs_attention()

        await record_worker_cycle_outcome(
            db, "exact", ok=False, error="boom three"
        )
        health = await load_worker_cycle_health(db)
        assert health.needs_attention()
        assert "**3 consecutive failures**" in health.exact.summary_fragment()

        await record_worker_cycle_outcome(
            db,
            "exact",
            ok=True,
            api_price_missing=1,
            claimed=4,
        )
        health = await load_worker_cycle_health(db)
        assert health.exact.consecutive_failures == 0
        assert health.exact.total_cycles == 4
        assert health.exact.last_success_at is not None
        # The last failure is still visible after the reset.
        assert health.exact.last_failure_at is not None
        assert health.exact.last_api_price_missing == 1
        assert health.exact.last_batch_claimed == 4
        assert health.exact.last_batch_missing_rate == 0.25
        assert "25%" in health.exact.summary_fragment()
        assert not health.needs_attention()

        # Unknown workers and a missing db never raise.
        await record_worker_cycle_outcome(db, "nope", ok=False)
        await record_worker_cycle_outcome(None, "exact", ok=False)
        assert (await load_worker_cycle_health(None)).available is False
        await conn.close()

    asyncio.run(run())


def test_cycle_health_survives_a_missing_table() -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        db = FakeDatabase(conn)

        # No table yet and no schema call: the loader still answers.
        cursor = await conn.execute(
            f"SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (TABLE,),
        )
        assert await cursor.fetchone() is None
        health = await load_worker_cycle_health(db)
        assert health.available
        assert health.exact.total_cycles == 0
        await conn.close()

    asyncio.run(run())


# ------------------------------------------------------------- exact timeout

@pytest.mark.asyncio
async def test_exact_cycle_timeout_aborts_hung_batch_and_worker_survives(
    tmp_path, monkeypatch
):
    import sniperplug.cogs.global_auto_scan_runner as runner

    p = await _pipeline(tmp_path, monkeypatch)
    try:
        await p.enroll(p.guilds[0], threshold=40)
        p.api.add(
            walmart_item(9002, "Samsung Galaxy Tab S11", 1.00, was_price=899.99),
            queries=("samsung galaxy tab",),
        )
        await p.discover("samsung galaxy tab")
        assert await p.queue_row(9002) is not None

        real_batch = runner.process_actionable_walmart_exact_queue_batch
        p.cog._exact_cycle_timeout_seconds = 0.05

        async def hang_forever(*_args, **_kwargs):
            await asyncio.sleep(3600)

        monkeypatch.setattr(
            runner, "process_actionable_walmart_exact_queue_batch", hang_forever
        )
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(p.cog.run_exact_worker_cycle(), timeout=10)

        # The worker cog is not wedged: flags reset, the queued row is
        # untouched, and the failure was recorded durably.
        assert p.cog._exact_active is False
        row = await p.queue_row(9002)
        assert row is not None
        assert row["status"] in ("pending", "retry", "failed")
        health = await load_worker_cycle_health(p.db)
        assert health.exact.consecutive_failures == 1
        assert health.exact.total_timeouts == 1
        assert "TimeoutError" in (health.exact.last_error or "")

        # The next cycle runs normally on the same cog and clears the streak.
        monkeypatch.setattr(
            runner, "process_actionable_walmart_exact_queue_batch", real_batch
        )
        p.cog._exact_cycle_timeout_seconds = 600.0
        result = await asyncio.wait_for(p.cog.run_exact_worker_cycle(), timeout=30)
        assert result.claimed == 1
        assert (await p.queue_row(9002))["status"] == "verified_markdown"
        health = await load_worker_cycle_health(p.db)
        assert health.exact.consecutive_failures == 0
        assert health.exact.last_success_at is not None
        assert health.exact.last_failure_at is not None
    finally:
        await p.close()


@pytest.mark.asyncio
async def test_exact_worker_loop_survives_hung_cycle_and_runs_next_cycle(
    tmp_path, monkeypatch
):
    import sniperplug.cogs.global_auto_scan_runner as runner
    import sniperplug.cogs.resilient_auto_scan_runner as resilient

    p = await _pipeline(tmp_path, monkeypatch)
    try:
        await p.enroll(p.guilds[0], threshold=40)
        p.api.add(
            walmart_item(9003, "Samsung Galaxy Tab S11", 1.00, was_price=899.99),
            queries=("samsung galaxy tab",),
        )
        await p.discover("samsung galaxy tab")

        real_batch = runner.process_actionable_walmart_exact_queue_batch
        calls = {"n": 0}
        p.cog._exact_cycle_timeout_seconds = 0.05
        monkeypatch.setattr(
            resilient, "WALMART_QUEUE_INITIAL_DELAY_SECONDS", 0
        )

        async def sleep_fast(_started, _interval):
            await asyncio.sleep(0.05)

        monkeypatch.setattr(runner, "_sleep_fixed_rate", sleep_fast)

        async def hang_once_then_real(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                await asyncio.sleep(3600)
            return await real_batch(*args, **kwargs)

        monkeypatch.setattr(
            runner, "process_actionable_walmart_exact_queue_batch", hang_once_then_real
        )

        task = asyncio.create_task(p.cog._walmart_exact_verification_worker())
        try:
            succeeded = await _wait_until(
                lambda: _row_is(p, 9003, "verified_markdown"),
                timeout_seconds=15.0,
            )
            assert succeeded, "worker loop never completed a cycle after the hang"
            assert calls["n"] >= 2
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        health = await load_worker_cycle_health(p.db)
        assert health.exact.total_timeouts >= 1
        assert health.exact.total_failures >= 1
        assert health.exact.last_failure_at is not None
        assert health.exact.consecutive_failures == 0
    finally:
        await p.close()


async def _row_is(p: Pipeline, item_id: int, status: str) -> bool:
    row = await p.queue_row(item_id)
    return row is not None and row["status"] == status


# ----------------------------------------------------------- catalog timeout

@pytest.mark.asyncio
async def test_catalog_worker_timeout_releases_claim_and_next_cycle_succeeds(
    tmp_path, monkeypatch
):
    import sniperplug.cogs.global_auto_scan_runner as runner
    from sniperplug.services.walmart_exact_runtime_schema import (
        ensure_exact_runtime_schema_once,
    )
    from sniperplug.services.walmart_global_catalog_autoscan import (
        load_global_catalog_state,
    )

    p = await _pipeline(tmp_path, monkeypatch)
    try:
        # The worker reads queue pressure before its first claim, and the
        # pressure query uses INDEXED BY on the runtime indexes. Production
        # always has those once the exact worker has run its first batch.
        await ensure_exact_runtime_schema_once(p.db)
        # The harness provider fails credential validation, so its
        # healthcheck is never READY and the worker would sleep in the
        # health-error branch. The health gate is not under test here;
        # the timeout/claim behaviour is.
        async def _provider_ready():
            return None

        monkeypatch.setattr(
            "sniperplug.cogs.auto_scan_runner.provider_health_error_message",
            _provider_ready,
        )
        real_discover = runner.discover_walmart_catalog_candidates
        calls = {"n": 0}
        p.cog._catalog_discovery_timeout_seconds = 0.05
        monkeypatch.setattr(runner, "GLOBAL_DISCOVERY_INITIAL_DELAY_SECONDS", 0)

        async def sleep_fast(_started, _interval):
            await asyncio.sleep(0.05)

        monkeypatch.setattr(runner, "_sleep_fixed_rate", sleep_fast)

        async def hang_once_then_real(*args, **kwargs):
            calls["n"] += 1
            claim = kwargs.get("catalog_claim")
            if claim is not None:
                calls.setdefault("starts", []).append(int(claim.start_index))
            if calls["n"] == 1:
                await asyncio.sleep(3600)
            return await real_discover(*args, **kwargs)

        monkeypatch.setattr(
            runner, "discover_walmart_catalog_candidates", hang_once_then_real
        )

        task = asyncio.create_task(p.cog._walmart_global_catalog_worker())
        try:
            saw_failure = await _wait_until(
                lambda: _catalog_has_failures(p), timeout_seconds=15.0
            )
            assert saw_failure, "catalog timeout was never recorded"
            saw_success = await _wait_until(
                lambda: _catalog_success(p), timeout_seconds=15.0
            )
            assert saw_success, "catalog worker never completed a cycle after the hang"
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        assert calls["n"] >= 2
        # The cycle after the timeout re-claimed from route 0: the timed-out
        # cycle released its claim without advancing the durable cursor.
        assert calls["starts"][1] == 0
        state = await load_global_catalog_state(p.db)
        # Each completed batch advances the cursor by its 2 routes; the
        # loop keeps running while the test polls, so only the step size
        # and the cursor/completed agreement are asserted, not the total.
        assert state.claim_active is False
        assert state.completed_routes >= 2
        assert state.completed_routes % 2 == 0
        assert state.cursor_index == state.completed_routes
        health = await load_worker_cycle_health(p.db)
        assert health.catalog.total_timeouts >= 1
        assert health.catalog.consecutive_failures == 0
        assert health.catalog.last_failure_at is not None
        assert health.catalog.last_success_at is not None
    finally:
        await p.close()


async def _catalog_failures(p: Pipeline) -> int:
    health = await load_worker_cycle_health(p.db)
    return int(health.catalog.total_failures)


async def _catalog_has_failures(p: Pipeline) -> bool:
    return (await _catalog_failures(p)) >= 1


async def _catalog_success(p: Pipeline) -> bool:
    health = await load_worker_cycle_health(p.db)
    return health.catalog.last_success_at is not None


# ---------------------------------------------------------------- quota pause

@pytest.mark.asyncio
async def test_exact_worker_pauses_on_quota_without_dropping_rows(
    tmp_path, monkeypatch
):
    import sniperplug.services.walmart_request_coordinator as coordinator

    p = await _pipeline(tmp_path, monkeypatch)
    try:
        await p.enroll(p.guilds[0], threshold=40)
        p.api.add(
            walmart_item(9004, "Samsung Galaxy Tab S11", 1.00, was_price=899.99),
            queries=("samsung galaxy tab",),
        )
        await p.discover("samsung galaxy tab")
        assert await p.queue_row(9004) is not None

        guard = WalmartQuotaGuard(monthly_limit=0, daily_limit=1, hourly_limit=0)
        assert guard.record().allowed
        assert not guard.check().allowed
        monkeypatch.setattr(coordinator, "walmart_quota_guard", guard)

        requests_before = len(p.api.requests)
        result = await asyncio.wait_for(p.cog.run_exact_worker_cycle(), timeout=30)
        assert result.claimed == 0
        # No Walmart call was made while paused.
        assert len(p.api.requests) == requests_before
        # The row is still queued and the pause left no failure behind.
        row = await p.queue_row(9004)
        assert row is not None
        assert row["status"] in ("pending", "retry", "failed")
        health = await load_worker_cycle_health(p.db)
        assert health.exact.total_cycles == 0
        assert walmart_quota_snapshot().allowed is False

        # A fresh window resumes the same rows.
        monkeypatch.setattr(
            coordinator,
            "walmart_quota_guard",
            WalmartQuotaGuard(monthly_limit=0, daily_limit=0, hourly_limit=0),
        )
        result = await asyncio.wait_for(p.cog.run_exact_worker_cycle(), timeout=30)
        assert result.claimed == 1
        assert (await p.queue_row(9004))["status"] == "verified_markdown"
    finally:
        await p.close()


# --------------------------------------------------- api_price_missing rate

@pytest.mark.asyncio
async def test_api_price_missing_counter_tracks_per_batch_rate(
    tmp_path, monkeypatch
):
    p = await _pipeline(tmp_path, monkeypatch)
    try:
        await p.enroll(p.guilds[0], threshold=40)
        p.api.add(
            walmart_item(9005, "Samsung Galaxy Tab S11", 1.00, was_price=899.99),
            queries=("samsung galaxy tab",),
        )
        # Detail payload with no sale price at all: the search snapshot
        # claims a price, the official detail path cannot provide one.
        priceless = walmart_item(9006, "Priceless Tablet", 20.00, was_price=100.00)
        del priceless["salePrice"]
        p.api.items["9006"] = priceless
        await p.discover("samsung galaxy tab")

        from sniperplug.models.candidate import SourceCandidate
        from sniperplug.services.walmart_exact_verification_queue import (
            enqueue_walmart_exact_verification_candidates,
        )

        await enqueue_walmart_exact_verification_candidates(
            p.db,
            [
                SourceCandidate(
                    source_key="walmart",
                    retailer="Walmart",
                    title="Priceless Tablet",
                    product_url="https://www.walmart.com/ip/9006",
                    current_price=20.0,
                    typical_price=100.0,
                    api_current_price=20.0,
                    api_reference_price=100.0,
                    api_reference_path="search.wasPrice",
                    product_id="9006",
                    product_id_type="sku",
                    sku="9006",
                    selected_offer_id="9006",
                    variant_attributes={"finderSourceQuery": "samsung galaxy tab"},
                )
            ],
            min_discount=10,
            source_label="global:test",
        )

        result = await asyncio.wait_for(p.cog.run_exact_worker_cycle(), timeout=60)
        assert result.claimed == 2
        assert result.api_price_missing == 1
        assert result.verified == 1
        # The priceless row is kept for retry, not deleted.
        row = await p.queue_row(9006)
        assert row is not None
        assert row["status"] == "retry"
        assert "retry_kind=api_price_missing" in (row["last_error"] or "")

        health = await load_worker_cycle_health(p.db)
        assert health.exact.last_api_price_missing == 1
        assert health.exact.last_batch_claimed == 2
        assert health.exact.last_batch_missing_rate == 0.5
        assert "50%" in health.exact.summary_fragment()
    finally:
        await p.close()


# ------------------------------------------------------------ health surface

@pytest.mark.asyncio
async def test_health_embed_surfaces_cycle_counters_and_quota(
    tmp_path, monkeypatch
):
    import sniperplug.services.walmart_request_coordinator as coordinator
    from sniperplug.cogs.canonical_public_alerts import (
        build_global_autoscan_health_embed,
    )
    from sniperplug.services.walmart_exact_verification_queue import (
        ensure_walmart_exact_verification_queue,
    )

    p = await _pipeline(tmp_path, monkeypatch)
    try:
        await p.enroll(p.guilds[0], threshold=40)
        # Production always has the exact queue table once the worker has
        # run; the embed's extreme-price summary reads it directly.
        await ensure_walmart_exact_verification_queue(p.db)

        await record_worker_cycle_outcome(
            p.db, "exact", ok=False, error="boom: provider hung"
        )
        await record_worker_cycle_outcome(
            p.db, "exact", ok=True, api_price_missing=1, claimed=2
        )
        guard = WalmartQuotaGuard(monthly_limit=0, daily_limit=100, hourly_limit=0)
        for _ in range(3):
            guard.note_request()
        monkeypatch.setattr(coordinator, "walmart_quota_guard", guard)

        embed = await build_global_autoscan_health_embed(p.bot, p.guilds[0].id)
        fields = {field.name: field.value for field in embed.fields}
        assert "Walmart worker cycle deadlines & quota" in fields
        value = fields["Walmart worker cycle deadlines & quota"]
        assert "exact worker" in value
        assert "healthy" in value
        assert "api_price_missing" in value
        assert "50%" in value
        assert "daily **3/100**" in value
        assert "hourly 3 (no cap)" in value

        # Three consecutive failures surface as needing attention.
        await record_worker_cycle_outcome(
            p.db, "exact", ok=False, error="boom again"
        )
        await record_worker_cycle_outcome(
            p.db, "exact", ok=False, error="boom again"
        )
        await record_worker_cycle_outcome(
            p.db, "exact", ok=False, error="boom again"
        )
        embed = await build_global_autoscan_health_embed(p.bot, p.guilds[0].id)
        fields = {field.name: field.value for field in embed.fields}
        value = fields["Walmart worker cycle deadlines & quota"]
        assert "**3 consecutive failures**" in value
    finally:
        await p.close()


def test_runner_wires_deadlines_quota_and_health() -> None:
    assert "asyncio.timeout" in RUNNER
    assert "EXACT_WORKER_CYCLE_TIMEOUT_SECONDS" in RUNNER
    assert "CATALOG_DISCOVERY_TIMEOUT_SECONDS" in RUNNER
    assert "walmart_quota_snapshot" in RUNNER
    assert "record_worker_cycle_outcome" in RUNNER
    assert "load_worker_cycle_health" in ALERTS
    assert "Walmart worker cycle deadlines & quota" in ALERTS
    assert "walmart_quota_snapshot" in ALERTS
