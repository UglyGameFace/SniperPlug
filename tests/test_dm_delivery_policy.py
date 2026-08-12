from __future__ import annotations

import inspect

import aiosqlite
import pytest

import sniperplug.services.dm_deal_alerts as alerts
import sniperplug.services.dm_delivery_policy as policy
from sniperplug.services.dm_deal_alerts import (
    DmDealAlertPreference,
    DmDealMatchDecision,
    UNLIMITED_MAX_ALERTS_PER_DAY,
)
from sniperplug.services import verified_retailer_event_fanout
from sniperplug.services import walmart_global_deal_fanout
from sniperplug.services import walmart_global_deal_fanout_bulk


class _FakeDatabase:
    def __init__(self, conn):
        self.conn = conn

    def require_conn(self):
        return self.conn


def _preference(
    *,
    mode: str = "smart",
    cap: int = 25,
    categories: tuple[str, ...] = (),
) -> DmDealAlertPreference:
    return DmDealAlertPreference(
        user_id=42,
        enabled=True,
        mode=mode,
        max_alerts_per_day=cap,
        categories=categories,
    )


def _decision(
    category: str,
    *,
    priority_override: bool = False,
) -> DmDealMatchDecision:
    return DmDealMatchDecision(
        True,
        "test match",
        category_key=category,
        priority_override=priority_override,
    )


def test_apparel_shoes_and_sneakers_share_one_fashion_family() -> None:
    assert policy.category_family_for_key("premium_apparel") == "fashion"
    assert policy.category_family_for_key("shoes_apparel") == "fashion"
    assert policy.category_family_for_key("sneakers") == "fashion"


def test_default_finite_cap_reserves_most_slots_from_fashion() -> None:
    assert policy.family_limit_for_daily_cap(25, "fashion") == 7
    assert policy.family_limit_for_daily_cap(25, "tech") == 12
    assert policy.family_limit_for_daily_cap(1, "fashion") == 1


def test_unlimited_has_no_family_limit() -> None:
    assert (
        policy.family_limit_for_daily_cap(
            UNLIMITED_MAX_ALERTS_PER_DAY,
            "fashion",
        )
        is None
    )


def test_diversity_only_applies_to_broad_smart_nonpriority_alerts() -> None:
    fashion = _decision("premium_apparel")
    assert policy.diversity_applies(_preference(), fashion) is True

    favorite = _preference(categories=("favorite:premium_apparel",))
    assert policy.diversity_applies(favorite, fashion) is False

    allowlist = _preference(categories=("premium_apparel", "apple"))
    assert policy.diversity_applies(allowlist, fashion) is False

    assert policy.diversity_applies(
        _preference(mode="all"),
        fashion,
    ) is False
    assert policy.diversity_applies(
        _preference(cap=UNLIMITED_MAX_ALERTS_PER_DAY),
        fashion,
    ) is False
    assert policy.diversity_applies(
        _preference(),
        _decision("premium_apparel", priority_override=True),
    ) is False


@pytest.mark.asyncio
async def test_unlimited_gate_never_checks_or_rejects_daily_counts(monkeypatch) -> None:
    async def fail_total(*args, **kwargs):
        raise AssertionError("Unlimited must not query the total daily cap")

    async def fail_family(*args, **kwargs):
        raise AssertionError("Unlimited must not query a hidden family cap")

    monkeypatch.setattr(policy, "dm_alerts_sent_today", fail_total)
    monkeypatch.setattr(policy, "dm_family_alerts_sent_today", fail_family)

    gate = await policy.evaluate_dm_delivery_gate(
        object(),
        preference=_preference(cap=UNLIMITED_MAX_ALERTS_PER_DAY),
        decision=_decision("premium_apparel"),
    )

    assert gate.allowed is True
    assert gate.reason == "unlimited"
    assert gate.category_family == "fashion"
    assert gate.family_limit is None


@pytest.mark.asyncio
async def test_finite_total_cap_still_wins_before_diversity(monkeypatch) -> None:
    async def total(*args, **kwargs):
        return 25

    async def fail_family(*args, **kwargs):
        raise AssertionError("family count should not run after the total cap is full")

    monkeypatch.setattr(policy, "dm_alerts_sent_today", total)
    monkeypatch.setattr(policy, "dm_family_alerts_sent_today", fail_family)

    gate = await policy.evaluate_dm_delivery_gate(
        object(),
        preference=_preference(cap=25),
        decision=_decision("premium_apparel"),
    )

    assert gate.allowed is False
    assert gate.reason == "daily_cap"
    assert gate.sent_today == 25


@pytest.mark.asyncio
async def test_fashion_wave_is_stopped_after_reserved_share(monkeypatch) -> None:
    async def total(*args, **kwargs):
        return 7

    async def family(*args, **kwargs):
        assert kwargs["category_family"] == "fashion"
        return 7

    monkeypatch.setattr(policy, "dm_alerts_sent_today", total)
    monkeypatch.setattr(policy, "dm_family_alerts_sent_today", family)

    gate = await policy.evaluate_dm_delivery_gate(
        object(),
        preference=_preference(cap=25),
        decision=_decision("sneakers"),
    )

    assert gate.allowed is False
    assert gate.reason == "diversity_cap"
    assert gate.category_family == "fashion"
    assert gate.family_sent_today == 7
    assert gate.family_limit == 7


@pytest.mark.asyncio
async def test_favorite_fashion_category_bypasses_automatic_diversity(monkeypatch) -> None:
    async def total(*args, **kwargs):
        return 7

    async def fail_family(*args, **kwargs):
        raise AssertionError("favorite category should bypass automatic diversity")

    monkeypatch.setattr(policy, "dm_alerts_sent_today", total)
    monkeypatch.setattr(policy, "dm_family_alerts_sent_today", fail_family)

    gate = await policy.evaluate_dm_delivery_gate(
        object(),
        preference=_preference(categories=("favorite:sneakers",)),
        decision=_decision("sneakers"),
    )

    assert gate.allowed is True
    assert gate.reason == "allowed"


@pytest.mark.asyncio
async def test_family_receipt_schema_and_history_survive_policy_reinitialization() -> None:
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    db = _FakeDatabase(conn)
    alerts._SCHEMA_READY = False
    policy._SCHEMA_READY = False

    try:
        await policy.record_dm_delivery_receipt(
            db,
            user_id=42,
            deal_key="walmart:test-fashion-1",
            category_family="fashion",
        )

        assert await alerts.dm_receipt_exists(
            db,
            user_id=42,
            deal_key="walmart:test-fashion-1",
        ) is True
        assert await policy.dm_family_alerts_sent_today(
            db,
            user_id=42,
            category_family="fashion",
        ) == 1

        # Simulate a process/schema cache reset. The durable row must remain
        # countable after the policy re-runs its idempotent schema setup.
        policy._SCHEMA_READY = False
        assert await policy.dm_family_alerts_sent_today(
            db,
            user_id=42,
            category_family="fashion",
        ) == 1

        await policy.delete_dm_delivery_history(db, user_id=42)
        assert await policy.dm_family_alerts_sent_today(
            db,
            user_id=42,
            category_family="fashion",
        ) == 0
    finally:
        alerts._SCHEMA_READY = False
        policy._SCHEMA_READY = False
        await conn.close()


def test_every_retained_dm_fanout_uses_the_shared_delivery_gate() -> None:
    for module in (
        walmart_global_deal_fanout_bulk,
        verified_retailer_event_fanout,
        walmart_global_deal_fanout,
    ):
        source = inspect.getsource(module)
        assert "evaluate_dm_delivery_gate" in source
        assert "record_dm_delivery_receipt" in source
        assert "dm_delivery_lock" in source
        assert "sent_today >= preference.max_alerts_per_day" not in source
