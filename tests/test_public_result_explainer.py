from sniperplug.services.public_deal_posts import PublicPostResult
from sniperplug.services.public_result_explainer import explain_public_post_result


def test_explain_public_post_disabled_tells_owner_to_setup():
    result = PublicPostResult(attempted=3, skipped_disabled=3, cached_active=3)

    rendered = explain_public_post_result(result)

    assert "Posted: **0**" in rendered
    assert "Run `/setup_sniperplug`" in rendered


def test_explain_public_post_not_alertable_is_clear():
    result = PublicPostResult(attempted=2, skipped_not_alertable=2, cached_active=2)

    rendered = explain_public_post_result(result)

    assert "proof was too weak" in rendered
    assert "staff-review only" in rendered
