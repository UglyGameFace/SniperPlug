from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from sniperplug.services import walmart_global_offer_memory as memory


class FlakyPruneConnection:
    def __init__(self):
        self.calls = 0
        self.fail_next = True

    async def execute(self, _sql, _params=()):
        self.calls += 1
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("temporary cleanup failure")
        return None


def test_failed_prune_does_not_advance_cleanup_throttle(monkeypatch) -> None:
    conn = FlakyPruneConnection()
    monotonic_value = memory.CLEANUP_INTERVAL_SECONDS + 10.0
    monkeypatch.setattr(memory, "_last_cleanup_monotonic", 0.0)
    monkeypatch.setattr(memory.time, "monotonic", lambda: monotonic_value)
    now = datetime(2026, 8, 1, tzinfo=timezone.utc)

    with pytest.raises(RuntimeError, match="temporary cleanup failure"):
        asyncio.run(memory.maybe_prune_global_offer_memory(conn, now=now))

    assert memory._last_cleanup_monotonic == 0.0
    assert conn.calls == 1

    asyncio.run(memory.maybe_prune_global_offer_memory(conn, now=now))

    assert conn.calls == 3
    assert memory._last_cleanup_monotonic == monotonic_value
