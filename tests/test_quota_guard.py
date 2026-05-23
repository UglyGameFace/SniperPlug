from datetime import datetime, timezone

from sniperplug.services.quota_guard import QuotaGuard


def test_quota_guard_blocks_after_daily_limit():
    guard = QuotaGuard(provider="test", monthly_limit=250, safe_monthly_limit=200, daily_limit=2, hourly_user_limit=10)
    now = datetime(2026, 5, 23, 12, tzinfo=timezone.utc)

    assert guard.record(user_id=1, now=now).allowed is True
    assert guard.record(user_id=1, now=now).allowed is True
    blocked = guard.check(user_id=1, now=now)

    assert blocked.allowed is False
    assert "Daily" in (blocked.reason or "")


def test_quota_guard_blocks_hourly_user_limit():
    guard = QuotaGuard(provider="test", monthly_limit=250, safe_monthly_limit=200, daily_limit=10, hourly_user_limit=1)
    now = datetime(2026, 5, 23, 12, tzinfo=timezone.utc)

    assert guard.record(user_id=1, now=now).allowed is True
    blocked = guard.check(user_id=1, now=now)

    assert blocked.allowed is False
    assert "Hourly" in (blocked.reason or "")


def test_quota_guard_uses_safe_monthly_limit():
    guard = QuotaGuard(provider="test", monthly_limit=250, safe_monthly_limit=2, daily_limit=10, hourly_user_limit=10)
    now = datetime(2026, 5, 23, 12, tzinfo=timezone.utc)

    assert guard.record(user_id=1, now=now).allowed is True
    assert guard.record(user_id=1, now=now).allowed is True
    blocked = guard.check(user_id=1, now=now)

    assert blocked.allowed is False
    assert "Monthly" in (blocked.reason or "")
