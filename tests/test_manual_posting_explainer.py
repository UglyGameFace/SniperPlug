from pathlib import Path

from sniperplug.services.public_deal_posts import PublicPostResult


def test_public_post_result_tracks_skip_counts_without_compat_module():
    result = PublicPostResult(attempted=5, skipped_not_alertable=5, cached_active=5)

    assert result.any_activity is True
    assert result.posted == 0
    assert result.skipped_not_alertable == 5
    assert result.cached_active == 5


def test_manual_posting_compat_module_stays_removed():
    assert not Path("sniperplug/services/manual_posting_explainer.py").exists()
