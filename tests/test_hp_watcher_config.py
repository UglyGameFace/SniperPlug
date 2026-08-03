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


def test_hp_price_error_mode_defaults_to_big_ticket_69_percent(monkeypatch) -> None:
    monkeypatch.setenv("HP_WATCHER_REQUIRE_REMOTE_DB", "false")
    for name in (
        "HP_BIG_TICKET_MIN_REFERENCE_PRICE",
        "HP_PRICE_ERROR_MIN_DISCOUNT_PERCENT",
        "HP_BIG_TICKET_OFFER_INTERVAL_SECONDS",
        "HP_WATCHER_LOOP_SECONDS",
        "HP_PRODUCT_PAGE_BATCH_SIZE",
        "HP_OFFER_BATCH_SIZE",
    ):
        monkeypatch.delenv(name, raising=False)
    settings = HPWatcherSettings.from_env()
    assert settings.big_ticket_min_reference_price == 200.0
    assert settings.price_error_min_discount_percent == 69
    assert settings.big_ticket_offer_interval_seconds == 45
    assert settings.loop_seconds == 10
    assert settings.product_page_batch_size == 24
    assert settings.offer_batch_size == 80


def test_hp_price_error_policy_rejects_unsafe_extremes(monkeypatch) -> None:
    monkeypatch.setenv("HP_WATCHER_REQUIRE_REMOTE_DB", "false")
    monkeypatch.setenv("HP_BIG_TICKET_MIN_REFERENCE_PRICE", "1")
    monkeypatch.setenv("HP_PRICE_ERROR_MIN_DISCOUNT_PERCENT", "1")
    monkeypatch.setenv("HP_BIG_TICKET_OFFER_INTERVAL_SECONDS", "1")
    settings = HPWatcherSettings.from_env()
    assert settings.big_ticket_min_reference_price == 100.0
    assert settings.price_error_min_discount_percent == 50
    assert settings.big_ticket_offer_interval_seconds == 30
