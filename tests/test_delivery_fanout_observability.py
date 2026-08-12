from __future__ import annotations

import inspect

from sniperplug.services.verified_retailer_event_fanout import (
    VerifiedRetailerFanoutResult,
    fanout_verified_retailer_events,
)
from sniperplug.services.walmart_global_deal_fanout_bulk import GlobalDealFanoutResult


def test_walmart_fanout_summary_exposes_dm_filter_duplicate_cap_and_diversity_skips() -> None:
    result = GlobalDealFanoutResult(
        dm_preferences_checked=5,
        dm_matches=3,
        dm_skipped_duplicate=1,
        dm_skipped_daily_cap=1,
        dm_skipped_diversity=2,
        dm_sent=1,
    )

    summary = result.summary_line()

    assert "DM checked/matched/sent **5/3/1**" in summary
    assert "DM skips filter/duplicate/cap/diversity **2/1/1/2**" in summary


def test_verified_retailer_summary_exposes_checked_matched_and_diversity_dm_counts() -> None:
    result = VerifiedRetailerFanoutResult(
        dm_preferences_checked=4,
        dm_matches=2,
        dm_skipped_duplicate=1,
        dm_skipped_daily_cap=1,
        dm_skipped_diversity=2,
        dm_sent=1,
    )

    summary = result.summary_line()

    assert "DM checked/matched/sent **4/2/1**" in summary
    assert "DM skips duplicate/cap/diversity **1/1/2**" in summary


def test_verified_retailer_fanout_explicitly_loads_all_public_destinations() -> None:
    source = inspect.getsource(fanout_verified_retailer_events)

    assert "required_retailer=None" in source
