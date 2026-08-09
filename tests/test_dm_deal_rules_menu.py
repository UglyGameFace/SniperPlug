from __future__ import annotations

import inspect

import pytest

from sniperplug.cogs.dm_deal_preferences_view import (
    DmDealPreferencesView,
    parse_dm_rule_values,
)


def test_dm_rule_parser_accepts_clear_personal_thresholds() -> None:
    rules = parse_dm_rule_values(
        threshold_text="35",
        savings_text="$20.50",
        score_text="78",
        max_price_text="499.99",
        daily_cap_text="25",
    )

    assert rules.min_discount == 35
    assert rules.min_savings_cents == 2050
    assert rules.min_score == 78
    assert rules.max_price_cents == 49_999
    assert rules.daily_cap == 25


def test_dm_rule_parser_allows_no_personal_max_price() -> None:
    rules = parse_dm_rule_values(
        threshold_text="30",
        savings_text="0",
        score_text="60",
        max_price_text="",
        daily_cap_text="50",
    )

    assert rules.max_price_cents is None


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("threshold_text", "96", "DM Deal Threshold"),
        ("savings_text", "-1", "Minimum savings"),
        ("score_text", "251", "Minimum Sniper score"),
        ("max_price_text", "0", "Maximum item price"),
        ("daily_cap_text", "101", "Daily DM limit"),
    ],
)
def test_dm_rule_parser_rejects_out_of_range_values(
    field: str,
    value: str,
    message: str,
) -> None:
    kwargs = {
        "threshold_text": "35",
        "savings_text": "10",
        "score_text": "78",
        "max_price_text": "100",
        "daily_cap_text": "25",
    }
    kwargs[field] = value

    with pytest.raises(ValueError, match=message):
        parse_dm_rule_values(**kwargs)


def test_personal_menu_labels_public_and_flip_thresholds_separately() -> None:
    source = inspect.getsource(DmDealPreferencesView)

    assert "DM Threshold:" in source
    assert "DM Deal Threshold:" in source
    assert "server's public deal channel only" in source
    assert "Flip Profit:" in source
    assert "profit minimum applies **only to Flip Override**" in source


def test_personal_menu_save_persists_normal_dm_rules() -> None:
    source = inspect.getsource(DmDealPreferencesView._save)

    assert "mode=self.dm_mode" in source
    assert "min_discount=self.dm_min_discount" in source
    assert "min_savings_cents=self.dm_min_savings_cents" in source
    assert "min_score=self.dm_min_score" in source
    assert "max_price_cents=self.dm_max_price_cents" in source
    assert "max_alerts_per_day=self.dm_daily_cap" in source
