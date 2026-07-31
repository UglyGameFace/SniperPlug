from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCOUT = (ROOT / "sniperplug/services/scout_lane_polish.py").read_text(encoding="utf-8")
AUTO = (ROOT / "sniperplug/cogs/auto_scan_runner.py").read_text(encoding="utf-8")


def test_public_scout_lane_stays_disabled() -> None:
    assert "return False" in SCOUT.split("def is_high_confidence_public_scout", 1)[1].split("def _compact_description", 1)[0]
    assert "return []" in SCOUT.split("def select_best_public_scout_cards", 1)[1]


def test_formatter_uses_actual_rank_not_caller_floor() -> None:
    formatter = SCOUT.split("def polish_public_scout_card", 1)[1]
    assert "actual_rank = scout_rank" in formatter
    assert "setattr(card, \"score\", actual_rank)" in formatter
    assert "setattr(card, \"should_alert\", False)" in formatter
    assert "max(actual_rank, 95)" not in formatter
    assert "max(rank, 95)" not in formatter


def test_review_card_is_compact_and_truthful() -> None:
    formatter = SCOUT.split("def polish_public_scout_card", 1)[1]
    assert "embed.clear_fields()" in formatter
    assert "Not a verified deal" in formatter
    assert "Private review score" in formatter
    assert "Walmart price" in formatter
    assert "Quick check" in SCOUT
    assert "High-confidence Scout" not in formatter
    assert "API fields" not in formatter
    assert "Marketplace comp / flip context" not in formatter


def test_weak_reference_is_a_hard_score_penalty() -> None:
    ranker = SCOUT.split("def scout_rank", 1)[1].split("def is_high_confidence_public_scout", 1)[0]
    assert "score -= 60" in ranker
    assert "score -= 25" in ranker
    assert "has_weak_reference_warning(card)" in ranker


def test_legacy_forced_floor_cannot_control_displayed_rank() -> None:
    # The older caller still passes a compatibility rank value. The formatter
    # must never display or persist that supplied floor.
    assert "rank = max(scout_rank(card, min_discount=result.min_discount), 95)" in AUTO
    formatter = SCOUT.split("def polish_public_scout_card", 1)[1]
    assert "caller-provided rank is deliberately ignored" in formatter
    assert "rank:{actual_rank}" in formatter
