from pathlib import Path


POSTS = Path("sniperplug/services/public_deal_posts.py").read_text(encoding="utf-8")
FRESH = Path("sniperplug/services/fresh_deal_filter.py").read_text(encoding="utf-8")
AUTO = Path("sniperplug/cogs/auto_scan_runner.py").read_text(encoding="utf-8")
HISTORY = Path("sniperplug/services/autoscan_history.py").read_text(encoding="utf-8")
FEEDBACK = Path("sniperplug/services/deal_feedback.py").read_text(encoding="utf-8")


def test_feedback_patch_already_present():
    assert "FEEDBACK_TARGET_DAYS = 3650" in FEEDBACK
    assert "super().__init__(timeout=None)" in FEEDBACK
    assert "DealFeedbackView(None, persistent=True)" in FEEDBACK
    assert "feedback_target_from_interaction_message" in FEEDBACK


def test_no_fake_cache_when_public_alerts_or_channel_are_broken():
    assert "skipped_disabled=attempted, cached_active=" not in POSTS
    assert "cache_after_posting" in POSTS


def test_fresh_filter_uses_public_quality_lane_and_threshold():
    assert "prepare_public_deal_candidate" in FRESH
    assert "min_public_discount" in FRESH
    assert "source_label" in FRESH
    assert "if not prepare_public_deal_candidate" in FRESH


def test_autoscan_passes_threshold_to_fresh_filter():
    assert "min_public_discount=result.min_discount" in AUTO
    assert "source_label=f\"{AUTO_SCAN_SOURCE_LABEL}:{preset.key}\"" in AUTO


def test_health_report_includes_real_failure_fields():
    assert "Verification blockers" in HISTORY
    assert "Review/scout audit" in HISTORY
    assert "Candidate decision trail" in HISTORY
    assert "Errors:" in HISTORY
    assert "Warnings:" in HISTORY
    assert "Routes:" in HISTORY


def test_autoscan_result_has_why_nothing_posted_field():
    assert "def autoscan_blocker_summary" in AUTO
    assert "Why nothing posted" in AUTO
    assert "Walmart API did not return trusted was/typical price proof" in AUTO
