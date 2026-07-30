from pathlib import Path


def test_public_post_requires_a_confirmed_durable_duplicate_guard():
    source = Path("sniperplug/services/public_deal_posts.py").read_text()
    assert "finalized, finalize_notes = await finalize_successful_public_post(" in source
    assert "for attempt in range(1, 3):" in source
    assert "return posted_state_saved or dedupe_saved, notes" in source
    assert "public post reservation could not be confirmed as posted" in source
    assert "the reservation was intentionally retained for stale-time protection" in source
