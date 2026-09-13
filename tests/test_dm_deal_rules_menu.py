from __future__ import annotations

import inspect

import pytest

from sniperplug.cogs.dm_deal_preferences_view import (
    DmDealPreferencesView,
    _DmDealRulesModal,
    parse_dm_rule_values,
)
from sniperplug.services.dm_deal_alerts import UNLIMITED_MAX_ALERTS_PER_DAY


def test_dm_rule_parser_accepts_full_personal_rule_update() -> None:
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
    assert rules.clear_max_price is False
    assert rules.daily_cap == 25
    assert rules.has_updates is True


def test_dm_rule_parser_accepts_explicit_unlimited_daily_limit() -> None:
    for value in ("unlimited", "0", "off", "no limit"):
        rules = parse_dm_rule_values(
            threshold_text="",
            savings_text="",
            score_text="",
            max_price_text="",
            daily_cap_text=value,
        )
        assert rules.daily_cap == UNLIMITED_MAX_ALERTS_PER_DAY
        assert rules.has_updates is True


def test_dm_rule_parser_blank_fields_mean_keep_everything() -> None:
    rules = parse_dm_rule_values(
        threshold_text="",
        savings_text="",
        score_text="",
        max_price_text="",
        daily_cap_text="",
    )
    assert rules.min_discount is None
    assert rules.min_savings_cents is None
    assert rules.min_score is None
    assert rules.max_price_cents is None
    assert rules.clear_max_price is False
    assert rules.daily_cap is None
    assert rules.has_updates is False


def test_dm_rule_parser_can_change_only_threshold() -> None:
    rules = parse_dm_rule_values(
        threshold_text="50",
        savings_text="",
        score_text="",
        max_price_text="",
        daily_cap_text="",
    )
    assert rules.min_discount == 50
    assert rules.min_savings_cents is None
    assert rules.min_score is None
    assert rules.max_price_cents is None
    assert rules.clear_max_price is False
    assert rules.daily_cap is None


def test_dm_rule_parser_uses_explicit_off_to_remove_max_price() -> None:
    rules = parse_dm_rule_values(
        threshold_text="",
        savings_text="",
        score_text="",
        max_price_text="off",
        daily_cap_text="",
    )
    assert rules.max_price_cents is None
    assert rules.clear_max_price is True
    assert rules.has_updates is True


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
        "threshold_text": "",
        "savings_text": "",
        "score_text": "",
        "max_price_text": "",
        "daily_cap_text": "",
    }
    kwargs[field] = value
    with pytest.raises(ValueError, match=message):
        parse_dm_rule_values(**kwargs)


def test_dm_rules_modal_does_not_force_every_field() -> None:
    source = inspect.getsource(_DmDealRulesModal)
    assert source.count("required=False") == 5
    assert ".default =" not in source
    assert "blank keeps" in source.lower()
    assert "type off to remove" in source.lower()
    assert "unlimited" in source.lower()


def test_personal_menu_keeps_rules_and_flip_separate_without_wall_of_help_text() -> None:
    source = inspect.getsource(DmDealPreferencesView)
    assert "Deal rules:" in source
    assert "DM Deal Threshold" not in source
    assert "Flip Override:" in source
    assert "Flip Profit:" in source
    assert "Products & brands" in source
    assert "Categories" in source
    assert "Safety boundary" not in source


def test_personal_menu_save_persists_normal_dm_rules() -> None:
    source = inspect.getsource(DmDealPreferencesView._save)
    assert "mode=self.dm_mode" in source
    assert "min_discount=self.dm_min_discount" in source
    assert "min_savings_cents=self.dm_min_savings_cents" in source
    assert "min_score=self.dm_min_score" in source
    assert "max_price_cents=self.dm_max_price_cents" in source
    assert "max_alerts_per_day=self.dm_daily_cap" in source
