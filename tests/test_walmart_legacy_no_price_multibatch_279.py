from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiosqlite

from sniperplug.services import walmart_exact_runtime_schema as runtime_schema
from sniperplug.services.walmart_exact_verification_queue import (
    QUEUE_TABLE,
    ensure_walmart_exact_verification_queue,
)


CONFIRMED_NO_PRICE_ERROR = (
    "retry_kind=api_price_missing exact detail did not provide a numeric current price; "
    "recovery_kind=api_price_missing"
)


class FakeConnection:
    def __init__(self) -> None:
        self.execute_count = 0
        self.commit_count = 0

    async def execute(self, _sql, _params=()):
        self.execute_count += 1
        return None

    async def commit(self) -> None:
        self.commit_count += 1


class FakeDatabase:
    def __init__(self, conn) -> None:
        self.conn = conn

    def require_conn(self):
        return self.conn


def _advance_monotonic(monkeypatch) -> None:
    ticks = iter(float(value) for value in range(0, 10_000, 20))
    monkeypatch.setattr(runtime_schema, "_monotonic", lambda: next(ticks))


def test_runtime_schema_repeats_one_bounded_legacy_batch_until_zero(monkeypatch) -> None:
    async def run() -> None:
        calls = {"queue": 0, "offer": 0, "legacy": 0}
        results = iter((200, 17, 0))

        async def ensure_queue(_db) -> None:
            calls["queue"] += 1

        async def ensure_offer(_db) -> None:
            calls["offer"] += 1

        async def normalize(_db, *, limit) -> int:
            assert limit == runtime_schema.LEGACY_COOLDOWN_BATCH_LIMIT
            calls["legacy"] += 1
            return next(results)

        monkeypatch.setattr(
            runtime_schema,
            "ensure_walmart_exact_verification_queue",
            ensure_queue,
        )
        monkeypatch.setattr(
            runtime_schema,
            "ensure_global_offer_memory_table",
            ensure_offer,
        )
        monkeypatch.setattr(
            runtime_schema,
            "normalize_legacy_confirmed_no_price_cooldowns",
            normalize,
        )
        _advance_monotonic(monkeypatch)

        conn = FakeConnection()
        db = FakeDatabase(conn)

        await runtime_schema.ensure_exact_runtime_schema_once(db)
        await runtime_schema.ensure_exact_runtime_schema_once(db)
        await runtime_schema.ensure_exact_runtime_schema_once(db)
        await runtime_schema.ensure_exact_runtime_schema_once(db)

        assert calls == {"queue": 1, "offer": 1, "legacy": 3}
        assert conn.execute_count == 6
        assert conn.commit_count == 2

        state = runtime_schema._state_for(conn)
        assert state.schema_ready is True
        assert state.legacy_cooldown_complete is True
        assert state.legacy_cooldown_batches == 3

    asyncio.run(run())


def test_runtime_schema_drains_more_than_one_legacy_batch_without_unbounding_work(monkeypatch) -> None:
    async def run() -> None:
        _advance_monotonic(monkeypatch)
        conn = await aiosqlite.connect(":memory:")
        db = FakeDatabase(conn)
        try:
            await ensure_walmart_exact_verification_queue(db)
            now = datetime.now(timezone.utc)
            last_attempt = now - timedelta(minutes=30)
            last_seen = last_attempt - timedelta(minutes=1)
            old_next = last_attempt + timedelta(minutes=5)
            target = last_attempt + timedelta(hours=6)

            await conn.executemany(
                f"""
                INSERT INTO {QUEUE_TABLE} (
                    item_id,
                    first_seen_at,
                    last_seen_at,
                    status,
                    attempt_count,
                    last_attempt_at,
                    next_attempt_at,
                    last_error
                ) VALUES (?, ?, ?, 'retry', 1, ?, ?, ?)
                """,
                [
                    (
                        str(930000 + index),
                        (last_attempt - timedelta(hours=2)).isoformat(),
                        last_seen.isoformat(),
                        last_attempt.isoformat(),
                        old_next.isoformat(),
                        CONFIRMED_NO_PRICE_ERROR,
                    )
                    for index in range(runtime_schema.LEGACY_COOLDOWN_BATCH_LIMIT + 5)
                ],
            )
            await conn.commit()

            await runtime_schema.ensure_exact_runtime_schema_once(db)
            cursor = await conn.execute(
                f"SELECT COUNT(*) FROM {QUEUE_TABLE} WHERE next_attempt_at = ?",
                (target.isoformat(),),
            )
            assert (await cursor.fetchone())[0] == runtime_schema.LEGACY_COOLDOWN_BATCH_LIMIT

            await runtime_schema.ensure_exact_runtime_schema_once(db)
            cursor = await conn.execute(
                f"SELECT COUNT(*) FROM {QUEUE_TABLE} WHERE next_attempt_at = ?",
                (target.isoformat(),),
            )
            assert (await cursor.fetchone())[0] == runtime_schema.LEGACY_COOLDOWN_BATCH_LIMIT + 5

            # One later zero-change pass marks catch-up complete. Further worker
            # calls must not keep querying the legacy population in steady state.
            await runtime_schema.ensure_exact_runtime_schema_once(db)
            state = runtime_schema._state_for(conn)
            assert state.legacy_cooldown_complete is True
            assert state.legacy_cooldown_batches == 3

            await runtime_schema.ensure_exact_runtime_schema_once(db)
            assert state.legacy_cooldown_batches == 3
        finally:
            await conn.close()

    asyncio.run(run())


def test_immediate_nested_schema_check_cannot_run_second_catchup_batch(monkeypatch) -> None:
    async def run() -> None:
        calls = {"legacy": 0}

        async def ensure_queue(_db) -> None:
            return None

        async def ensure_offer(_db) -> None:
            return None

        async def normalize(_db, *, limit) -> int:
            assert limit == runtime_schema.LEGACY_COOLDOWN_BATCH_LIMIT
            calls["legacy"] += 1
            return 200

        monkeypatch.setattr(runtime_schema, "ensure_walmart_exact_verification_queue", ensure_queue)
        monkeypatch.setattr(runtime_schema, "ensure_global_offer_memory_table", ensure_offer)
        monkeypatch.setattr(runtime_schema, "normalize_legacy_confirmed_no_price_cooldowns", normalize)

        values = iter((100.0, 100.0, 100.0, 101.0))
        monkeypatch.setattr(runtime_schema, "_monotonic", lambda: next(values))

        db = FakeDatabase(FakeConnection())
        await runtime_schema.ensure_exact_runtime_schema_once(db)
        await runtime_schema.ensure_exact_runtime_schema_once(db)

        assert calls["legacy"] == 1
        state = runtime_schema._state_for(db.conn)
        assert state.legacy_cooldown_complete is False
        assert state.legacy_cooldown_batches == 1

    asyncio.run(run())


def test_v279_catchup_marker_is_kept_in_runtime_schema() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (
        root / "sniperplug/services/walmart_exact_runtime_schema.py"
    ).read_text(encoding="utf-8")

    assert "legacy_no_price_floor_v278" in source
    assert "legacy_no_price_multibatch_v279" in source
    assert "legacy_cooldown_complete" in source
    assert "LEGACY_COOLDOWN_PASS_INTERVAL_SECONDS" in source
    assert "def _monotonic()" in source
