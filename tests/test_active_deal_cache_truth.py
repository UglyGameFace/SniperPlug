from datetime import datetime, timezone
from pathlib import Path

from sniperplug.cogs.active_deals import observation_age


SOURCE = Path("sniperplug/cogs/active_deals.py").read_text()


def test_every_active_cache_read_prunes_stale_rows_first():
    assert "async def list_active_deals" in SOURCE
    assert "await mark_stale_deals(db, guild_id, stale_after_hours=DEFAULT_STALE_AFTER_HOURS)" in SOURCE
    assert "async def active_deal_counts" in SOURCE
    counts_body = SOURCE.split("async def active_deal_counts", 1)[1].split("async def mark_stale_deals", 1)[0]
    assert "await mark_stale_deals" in counts_body


def test_active_cache_copy_does_not_claim_live_retailer_truth():
    assert "Recently Observed Deals • Public Deal Cache" in SOURCE
    assert "not a live retailer guarantee" in SOURCE
    assert "Recently observed does not mean currently in stock or unchanged" in SOURCE


def test_cleanup_copy_explains_stale_semantics():
    assert "Stale means the observation aged out" in SOURCE
    assert "does not prove the retailer listing is dead" in SOURCE


def test_observation_age_is_human_readable():
    now = datetime(2026, 7, 31, 0, 0, tzinfo=timezone.utc)
    assert observation_age("2026-07-30T23:59:30+00:00", now=now) == "just now"
    assert observation_age("2026-07-30T23:30:00+00:00", now=now) == "30m ago"
    assert observation_age("2026-07-30T18:00:00+00:00", now=now) == "6h ago"
    assert observation_age("2026-07-28T00:00:00+00:00", now=now) == "3d ago"
    assert observation_age("bad-value", now=now) == "unknown time ago"
