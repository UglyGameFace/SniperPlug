from __future__ import annotations

import asyncio
from pathlib import Path

from sniperplug.services import walmart_exact_runtime_schema as runtime_schema
from sniperplug.services.walmart_exact_queue_runtime import TerminalIdentityMaintenance


ROOT = Path(__file__).resolve().parents[1]
BULK_RUNTIME = (
    ROOT / "sniperplug/services/walmart_exact_queue_bulk_runtime.py"
).read_text(encoding="utf-8")


class FakeConnection:
    pass


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

        first = FakeDatabase(FakeConnection())
        second = FakeDatabase(FakeConnection())
        await runtime_schema.ensure_exact_runtime_schema_once(first)
        await runtime_schema.ensure_exact_runtime_schema_once(first)
        await runtime_schema.ensure_exact_runtime_schema_once(second)

        assert calls == {"queue": 2, "offer": 2}

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


def test_bulk_runtime_uses_cached_schema_and_bounded_maintenance() -> None:
    assert "ensure_exact_runtime_schema_once(db)" in BULK_RUNTIME
    assert "maintain_terminal_identity_rows_bounded(db)" in BULK_RUNTIME
    assert "ensure_walmart_exact_verification_queue(db)" not in BULK_RUNTIME
    assert "ensure_global_offer_memory_table(db)" not in BULK_RUNTIME
    assert "maintain_terminal_identity_rows(db)" not in BULK_RUNTIME
