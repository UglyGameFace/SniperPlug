from __future__ import annotations

import asyncio
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiosqlite
import pytest

from sniperplug.cogs import global_auto_scan_runner as runner
from sniperplug.services.walmart_exact_queue_pressure import (
    STATUS_DUE_INDEX,
    STATUS_VERIFIED_DUE_INDEX,
    load_walmart_exact_queue_pressure,
)
from sniperplug.services.walmart_exact_runtime_schema import (
    ensure_exact_runtime_schema_once,
)
from sniperplug.services.walmart_exact_verification_queue import (
    ensure_walmart_exact_verification_queue,
)


@pytest.mark.asyncio
async def test_catalog_pressure_requires_runtime_indexes_and_schema_init_fixes_it() -> None:
    conn = await aiosqlite.connect(":memory:")
    db = SimpleNamespace(require_conn=lambda: conn)
    try:
        # The base exact-queue table alone does not create the two specialized
        # indexes required by the catalog pressure query's INDEXED BY clauses.
        await ensure_walmart_exact_verification_queue(db)
        with pytest.raises(sqlite3.OperationalError, match="no such index"):
            await load_walmart_exact_queue_pressure(db)

        await ensure_exact_runtime_schema_once(db)
        index_cursor = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND name IN (?, ?)",
            (STATUS_DUE_INDEX, STATUS_VERIFIED_DUE_INDEX),
        )
        names = {row[0] for row in await index_cursor.fetchall()}
        assert names == {STATUS_DUE_INDEX, STATUS_VERIFIED_DUE_INDEX}

        pressure = await load_walmart_exact_queue_pressure(db)
        assert pressure.due_now == 0
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_catalog_worker_retries_pressure_error_instead_of_dying(monkeypatch) -> None:
    bot = SimpleNamespace(db=object(), wait_until_ready=AsyncMock())
    cog = runner.AutoScanRunnerCog.__new__(runner.AutoScanRunnerCog)
    cog.bot = bot
    cog._catalog_active = False
    cog._runtime_phase = "idle"
    cog._any_foreground_walmart_scan_busy = lambda: False

    calls = 0
    schema = AsyncMock()
    delays: list[float] = []

    async def pressure(_db):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise sqlite3.OperationalError("no such index: queue_status_due")
        # Simulate cog unload after the worker successfully survives the
        # preceding error and begins the next cycle.
        raise asyncio.CancelledError

    async def sleep_cycle(_started, seconds):
        delays.append(seconds)

    monkeypatch.setattr(runner, "GLOBAL_DISCOVERY_INITIAL_DELAY_SECONDS", 0)
    monkeypatch.setattr(runner, "ensure_exact_runtime_schema_once", schema)
    monkeypatch.setattr(runner, "load_walmart_exact_queue_pressure", pressure)
    monkeypatch.setattr(
        runner.legacy, "provider_health_error_message",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(runner, "_sleep_fixed_rate", sleep_cycle)

    await cog._walmart_global_catalog_worker()

    assert calls == 2
    assert schema.await_count == 2
    assert delays == [runner.GLOBAL_DISCOVERY_INTERVAL_SECONDS]
