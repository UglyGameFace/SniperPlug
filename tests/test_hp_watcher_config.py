from __future__ import annotations

import os

import pytest

from sniperplug.hp_watcher.config import HPWatcherSettings


def test_standalone_hp_watcher_does_not_require_discord_token(monkeypatch) -> None:
    monkeypatch.delenv("DISCORD_TOKEN", raising=False)
    monkeypatch.setenv("HP_WATCHER_REQUIRE_REMOTE_DB", "false")
    settings = HPWatcherSettings.from_env()
    settings.validate_runtime()
    assert settings.require_remote_database is False


def test_production_hp_watcher_requires_shared_remote_database(monkeypatch) -> None:
    monkeypatch.setenv("HP_WATCHER_REQUIRE_REMOTE_DB", "true")
    monkeypatch.delenv("TURSO_DATABASE_URL", raising=False)
    monkeypatch.delenv("LIBSQL_URL", raising=False)
    monkeypatch.delenv("TURSO_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("LIBSQL_AUTH_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="TURSO_DATABASE_URL"):
        HPWatcherSettings.from_env().validate_runtime()


def test_hp_watcher_accepts_same_turso_contract(monkeypatch) -> None:
    monkeypatch.setenv("HP_WATCHER_REQUIRE_REMOTE_DB", "true")
    monkeypatch.setenv("TURSO_DATABASE_URL", "libsql://sniperplug.example.turso.io")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "test-token")
    settings = HPWatcherSettings.from_env()
    settings.validate_runtime()
    assert settings.require_remote_database is True


def test_hp_watcher_bounds_aggressive_polling_values(monkeypatch) -> None:
    monkeypatch.setenv("HP_WATCHER_REQUIRE_REMOTE_DB", "false")
    monkeypatch.setenv("HP_WATCHER_LOOP_SECONDS", "1")
    monkeypatch.setenv("HP_REQUEST_CONCURRENCY", "100")
    monkeypatch.setenv("HP_OFFER_BATCH_SIZE", "999")
    settings = HPWatcherSettings.from_env()
    assert settings.loop_seconds == 10
    assert settings.request_concurrency == 8
    assert settings.offer_batch_size == 100
