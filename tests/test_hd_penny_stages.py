"""Stage decoder tests, pinned to the sourced research table.

Sources: ~/workspace/hd-penny-research-20261120.md — .03 final (consensus),
.02 final (faster ladder, lower confidence), .06 mid (consensus), .04 mid
(faster ladder), .00 first markdown only with clearance context, $0.01
exactly is the penny, .53/.52/.97/.98 carry no stage meaning anywhere.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sniperplug.models.local_inventory import ClearanceStage, clearance_signal_from_price
from sniperplug.services.hd_penny_stages import (
    CONFIDENCE_CONSENSUS,
    CONFIDENCE_LOWER,
    PennyStage,
    decode_price_stage,
    estimate_next_drop,
    price_to_cents,
)


def test_penny_is_exactly_one_cent() -> None:
    reading = decode_price_stage(1, clearance_context=False)
    assert reading.stage == PennyStage.PENNY
    assert reading.confidence == CONFIDENCE_CONSENSUS
    # A $5.01 price merely ends in .01 — it is not a penny.
    assert decode_price_stage(501, clearance_context=True).stage == PennyStage.UNKNOWN
    assert decode_price_stage(501, clearance_context=False).stage == PennyStage.NOT_CLEARANCE


def test_final_stage_endings() -> None:
    final_03 = decode_price_stage(1203, clearance_context=False)
    assert final_03.stage == PennyStage.FINAL
    assert final_03.confidence == CONFIDENCE_CONSENSUS
    final_02 = decode_price_stage(1202, clearance_context=False)
    assert final_02.stage == PennyStage.FINAL
    assert final_02.confidence == CONFIDENCE_LOWER


def test_mid_stage_endings() -> None:
    mid_06 = decode_price_stage(2406, clearance_context=False)
    assert mid_06.stage == PennyStage.MID
    assert mid_06.confidence == CONFIDENCE_CONSENSUS
    mid_04 = decode_price_stage(2404, clearance_context=False)
    assert mid_04.stage == PennyStage.MID
    assert mid_04.confidence == CONFIDENCE_LOWER


def test_zero_ending_needs_clearance_context() -> None:
    assert decode_price_stage(2900, clearance_context=True).stage == PennyStage.FIRST
    assert decode_price_stage(2900, clearance_context=False).stage == PennyStage.NOT_CLEARANCE


def test_unsourced_endings_never_decode_as_stages() -> None:
    for cents in (1253, 1252, 1097, 1098, 1999, 833, 1010):
        with_context = decode_price_stage(cents, clearance_context=True)
        without_context = decode_price_stage(cents, clearance_context=False)
        assert with_context.stage == PennyStage.UNKNOWN, cents
        assert without_context.stage == PennyStage.NOT_CLEARANCE, cents


def test_missing_price_is_unknown() -> None:
    assert decode_price_stage(None, clearance_context=True).stage == PennyStage.UNKNOWN


def test_price_to_cents_is_exact_for_the_penny() -> None:
    assert price_to_cents(0.01) == 1
    assert price_to_cents(12.03) == 1203
    assert price_to_cents(0.1 + 0.02) == 12
    assert price_to_cents(None) is None


def test_drop_estimate_uses_seed_then_learned_dwell() -> None:
    entered = datetime(2026, 11, 1, tzinfo=timezone.utc)
    seeded = estimate_next_drop(PennyStage.FINAL, entered)
    assert seeded is not None
    assert seeded.learned is False
    assert seeded.dwell_days == 21.0
    assert seeded.earliest == entered + timedelta(days=10.5)
    assert seeded.latest == entered + timedelta(days=31.5)

    learned = estimate_next_drop(PennyStage.FINAL, entered, learned_dwell_days=4.0)
    assert learned is not None
    assert learned.learned is True
    assert learned.earliest == entered + timedelta(days=2.0)
    assert learned.latest == entered + timedelta(days=6.0)

    mid = estimate_next_drop(PennyStage.MID, entered)
    assert mid is not None and mid.dwell_days == 42.0


def test_drop_estimate_only_for_ladder_stages() -> None:
    entered = datetime(2026, 11, 1, tzinfo=timezone.utc)
    assert estimate_next_drop(PennyStage.PENNY, entered) is None
    assert estimate_next_drop(PennyStage.NOT_CLEARANCE, entered) is None
    assert estimate_next_drop(PennyStage.UNKNOWN, entered) is None


def test_manual_clearance_signal_knows_the_faster_ladder() -> None:
    final_02 = clearance_signal_from_price(8.02)
    assert final_02 is not None
    assert final_02.stage == ClearanceStage.FINAL_MARKDOWN_CANDIDATE
    mid_04 = clearance_signal_from_price(8.04)
    assert mid_04 is not None
    assert mid_04.stage == ClearanceStage.CLEARANCE_CANDIDATE
    # Unsourced endings stay silent in the manual lane too.
    assert clearance_signal_from_price(8.53) is None
    assert clearance_signal_from_price(8.98) is None
