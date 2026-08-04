from __future__ import annotations

import asyncio
from pathlib import Path

from sniperplug.services import walmart_global_deal_fanout_bulk as bulk


ROOT = Path(__file__).resolve().parents[1]
RUNNER = (ROOT / "sniperplug/cogs/global_auto_scan_runner.py").read_text(
    encoding="utf-8"
)
BULK_SOURCE = (
    ROOT / "sniperplug/services/walmart_global_deal_fanout_bulk.py"
).read_text(encoding="utf-8")


class FakeConnection:
    pass


class FakeDatabase:
    def __init__(self, conn: FakeConnection):
        self.conn = conn

    def require_conn(self):
        return self.conn


def test_fanout_schema_initializes_once_per_connection(monkeypatch) -> None:
    async def run() -> None:
        calls = 0

        async def ensure(_db) -> None:
            nonlocal calls
            calls += 1

        monkeypatch.setattr(
            bulk.legacy,
            "ensure_global_deal_event_tables",
            ensure,
        )
        db = FakeDatabase(FakeConnection())
        await bulk.ensure_global_deal_event_tables_once(db)
        await bulk.ensure_global_deal_event_tables_once(db)
        assert calls == 1

    asyncio.run(run())


def test_fanout_summary_exposes_every_public_skip_reason() -> None:
    result = bulk.GlobalDealFanoutResult(
        candidates_loaded=5,
        exact_cards=4,
        new_events=3,
        events_processed=3,
        guilds_checked=6,
        public_posts=1,
        public_skipped_recent_duplicate=2,
        public_skipped_reserved_duplicate=1,
        public_skipped_not_alertable=1,
        public_skipped_disabled=1,
        public_skipped_wrong_retailer=0,
    )
    summary = result.summary_line()
    assert "guild checks/posts **6/1**" in summary
    assert "duplicate **3**" in summary
    assert "recent **2**" in summary
    assert "reserved **1**" in summary
    assert "not alertable **1**" in summary
    assert "disabled **1**" in summary


def test_bulk_fanout_replaces_per_event_sql_loops() -> None:
    assert "ON CONFLICT(deal_key) DO NOTHING" in BULK_SOURCE
    assert "RETURNING deal_key" in BULK_SOURCE
    assert "WITH picked AS" in BULK_SOURCE
    assert "RETURNING deal_key, snapshot_json" in BULK_SOURCE
    assert "WITH finalized(deal_key, claim_token)" in BULK_SOURCE
    assert "Global Walmart public destination decision" in BULK_SOURCE


def test_production_runner_uses_bulk_fanout_runtime() -> None:
    assert "walmart_global_deal_fanout_bulk" in RUNNER
    assert "bulk_fanout=true" in RUNNER
    assert "from sniperplug.services.walmart_global_deal_fanout import" not in RUNNER
