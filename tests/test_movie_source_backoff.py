from __future__ import annotations

import asyncio
from pathlib import Path

import aiohttp

from sniperplug.cogs import registered_multi_source_movies as runtime


ROOT = Path(__file__).resolve().parents[1]
REGISTERED = (ROOT / "sniperplug/cogs/registered_multi_source_movies.py").read_text(encoding="utf-8")


def make_cog():
    cog = object.__new__(runtime.MovieTicketsCog)
    cog._source_failure_counts = {}
    cog._source_retry_after = {}
    cog._source_access_blocked = set()
    return cog


def test_source_backoff_escalates_and_clears(monkeypatch) -> None:
    cog = make_cog()
    now = 1000.0
    monkeypatch.setattr(runtime.time, "monotonic", lambda: now)

    error = RuntimeError("temporary source failure")
    assert cog._register_source_failure("gofobo", error) == 120
    assert cog._source_backoff_remaining("gofobo") == 120

    assert cog._register_source_failure("gofobo", error) == 300
    assert cog._source_backoff_remaining("gofobo") == 300

    cog._clear_source_failure("gofobo")
    assert cog._source_backoff_remaining("gofobo") == 0
    assert "gofobo" not in cog._source_failure_counts
    assert "gofobo" not in cog._source_access_blocked


def test_atom_403_uses_long_access_circuit_breaker(monkeypatch) -> None:
    cog = make_cog()
    now = 1000.0
    monkeypatch.setattr(runtime.time, "monotonic", lambda: now)

    error = RuntimeError("Official Atom promotions page returned HTTP 403.")
    assert runtime._is_source_access_block_error(error) is True
    assert runtime._is_transient_source_error(error) is True
    assert cog._register_source_failure("atom", error) == 6 * 60 * 60
    assert cog._source_backoff_remaining("atom") == 6 * 60 * 60
    assert "atom" in cog._source_access_blocked


def test_transient_network_failures_are_classified_without_traceback_spam() -> None:
    assert runtime._is_transient_source_error(asyncio.TimeoutError()) is True
    assert runtime._is_transient_source_error(aiohttp.ClientConnectionError("down")) is True
    assert runtime._is_transient_source_error(OSError("connection reset")) is True
    assert runtime._is_transient_source_error(RuntimeError("HTML structure changed")) is False


def test_registered_runtime_overrides_scan_with_cached_backoff_lane() -> None:
    assert "async def _scan_official_source" in REGISTERED
    assert "SOURCE_BACKOFF_SECONDS = (120, 300, 900, 1800)" in REGISTERED
    assert "SOURCE_ACCESS_BLOCK_BACKOFF_SECONDS = 6 * 60 * 60" in REGISTERED
    assert "preserved verified cache and continued other sources" in REGISTERED
    assert "if attempted == 0" in REGISTERED
    assert "manual_refresh = target_guild_id is not None" in REGISTERED
    assert "hard_blocked = source_key in self._source_access_blocked" in REGISTERED
