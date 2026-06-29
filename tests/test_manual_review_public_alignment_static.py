from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_manual_review_public_posts_use_public_pipeline_helpers() -> None:
    source = read("sniperplug/services/manual_review_share.py")
    assert "resolve_public_alert_channel" in source
    assert "reserve_public_deal_post" in source
    assert "mark_public_deal_posted" in source
    assert "release_public_deal_reservation" in source
    assert "safe_find_recent_alert" in source
    assert "should_suppress_recent_alert" in source
    assert "record_alert_dedupe" in source
    assert "build_deal_feedback_view" in source


def test_manual_review_public_posts_are_labeled_staff_shared() -> None:
    source = read("sniperplug/services/manual_review_share.py")
    assert "MANUAL_REVIEW_SOURCE_LABEL" in source
    assert "staff_shared_review_scout" in source
    assert "Staff-shared review lead" in source
    assert "not an automatic verified public markdown post" in source
    assert "PUBLIC_SCOUT_ALERT_KEY" in source


def test_manual_review_public_posts_keep_button_pagination() -> None:
    source = read("sniperplug/services/manual_review_share.py")
    assert "DEFAULT_REVIEW_PAGE_SIZE = 3" in source
    assert "DEFAULT_REVIEW_MAX_CARDS = 12" in source
    assert "ManualShareButton" in source
    assert "ManualReviewPageButton" in source
