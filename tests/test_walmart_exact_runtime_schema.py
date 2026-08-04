from __future__ import annotations

import asyncio
from pathlib import Path

from sniperplug.services import walmart_exact_runtime_schema as runtime_schema
from sniperplug.services.walmart_exact_queue_runtime import TerminalIdentityMaintenance


ROOT = Path(__file__).resolve().parents[1]
BULK_RUNTIME = (
    ROOT / "sniperplug/services/walmart_exact_queue_bulk_runtime.py"
).read_text(encoding="utf-8")
DRAIN = (
    ROOT / "sniperplug/services/walmart_exact_queue_drain.py"
).read_text(encoding="utf-8")


class FakeConnection:
    def __init__(self) -> None:
        self.execute_count = 0
        self.commit_count = 0
        self.sql: list[str] = []

    async def execute(self, sql, params=()):
        self.execute_count += 1
        self.sql.append(str(sql))
        return None

    async def commit(self) -> None:
        self.commit_count += 1


class FakeDatabase:
    def __init__(self, conn: FakeConnection):
        self.conn = conn

    def require_conn(self):
        return self.conn


def test_exact_runtime_schema_initializes_once_per_connection(monkeypatch) -> None:
    async def run() -> None:
        calls = {"queue": 0, "offer": 0}

        async def ensure_queue(_db) -> None:
            calls["queue"] += 1

        async def ensure_offer(_db) -> None:
            calls["offer"] += 1

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

        first_conn = FakeConnection()
        second_conn = FakeConnection()
        first = FakeDatabase(first_conn)
        second = FakeDatabase(second_conn)
        await runtime_schema.ensure_exact_runtime_schema_once(first)
        await runtime_schema.ensure_exact_runtime_schema_once(first)
        await runtime_schema.ensure_exact_runtime_schema_once(second)

        assert calls == {"queue": 2, "offer": 2}
        assert first_conn.execute_count == 2
        assert first_conn.commit_count == 1
        assert second_conn.execute_count == 2
        assert second_conn.commit_count == 1
        claim_sql, pressure_sql = first_conn.sql
        assert runtime_schema.CLAIM_ORDER_INDEX in claim_sql
        assert "CASE status" in claim_sql
        assert "priority_score DESC" in claim_sql
        assert "last_seen_at DESC" in claim_sql
        assert "WHERE status NOT IN" in claim_sql
        assert runtime_schema.STATUS_DUE_INDEX in pressure_sql
        assert "status," in pressure_sql
        assert "next_attempt_at" in pressure_sql
        assert "lease_until" in pressure_sql

    asyncio.run(run())


def test_terminal_identity_maintenance_is_bounded(monkeypatch) -> None:
    async def run() -> None:
        calls = {"queue": 0, "offer": 0, "maintenance": 0}

        async def ensure_queue(_db) -> None:
            calls["queue"] += 1

        async def ensure_offer(_db) -> None:
            calls["offer"] += 1

        async def maintain(_db, *, now=None) -> TerminalIdentityMaintenance:
            calls["maintenance"] += 1
            return TerminalIdentityMaintenance(quarantined=2, rearmed=1)

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
            "maintain_terminal_identity_rows",
            maintain,
        )

        db = FakeDatabase(FakeConnection())
        first = await runtime_schema.maintain_terminal_identity_rows_bounded(db)
        second = await runtime_schema.maintain_terminal_identity_rows_bounded(db)

        assert first == TerminalIdentityMaintenance(quarantined=2, rearmed=1)
        assert second == TerminalIdentityMaintenance()
        assert calls == {"queue": 1, "offer": 1, "maintenance": 1}

    asyncio.run(run())


def test_claim_query_order_matches_claim_order_index() -> None:
    for fragment in (
        "CASE status",
        "WHEN 'pending' THEN 0",
        "WHEN 'verified_markdown' THEN 4",
        "priority_score DESC",
        "last_seen_at DESC",
    ):
        assert fragment in DRAIN
        assert fragment in (
            ROOT / "sniperplug/services/walmart_exact_runtime_schema.py"
        ).read_text(encoding="utf-8")


def test_bulk_runtime_uses_cached_schema_and_bounded_maintenance() -> None:
    assert "ensure_exact_runtime_schema_once(db)" in BULK_RUNTIME
    assert "maintain_terminal_identity_rows_bounded(db)" in BULK_RUNTIME
    assert "ensure_walmart_exact_verification_queue(db)" not in BULK_RUNTIME
    assert "ensure_global_offer_memory_table(db)" not in BULK_RUNTIME
    assert "maintain_terminal_identity_rows(db)" not in BULK_RUNTIME
    assert "maybe_prune_walmart_exact_queue_bounded" in BULK_RUNTIME
    assert "load_walmart_exact_queue_pressure" in BULK_RUNTIME
