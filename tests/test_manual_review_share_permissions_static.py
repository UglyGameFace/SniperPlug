from pathlib import Path


SOURCE = Path("sniperplug/services/manual_review_share.py").read_text(encoding="utf-8")


def test_all_manual_post_controls_use_shared_manage_guild_guard() -> None:
    assert "def can_manually_post_review" in SOURCE
    assert SOURCE.count("if not can_manually_post_review(interaction):") >= 2
    assert 'getattr(permissions, "manage_guild", False)' in SOURCE


def test_manual_post_permission_check_fails_closed_without_guild_context() -> None:
    assert "permissions is not None and" in SOURCE
    assert "MANUAL_REVIEW_PERMISSION_ERROR" in SOURCE
