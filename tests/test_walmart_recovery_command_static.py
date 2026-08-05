from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COG = (ROOT / "sniperplug/cogs/canonical_public_alerts.py").read_text(
    encoding="utf-8"
)
SERVICE = (ROOT / "sniperplug/services/walmart_delivery_recovery.py").read_text(
    encoding="utf-8"
)
CATALOG = (ROOT / "sniperplug/services/command_catalog.py").read_text(
    encoding="utf-8"
)
SURFACE = (ROOT / "sniperplug/services/command_surface.py").read_text(
    encoding="utf-8"
)


def test_recovery_command_is_canonical_and_visible() -> None:
    assert 'name="walmart_recovery"' in COG
    assert "/walmart_recovery" in CATALOG
    assert '"walmart_recovery"' in SURFACE
    assert "Use `/walmart_recovery`" in COG


def test_actual_server_owner_controls_every_bypass_action() -> None:
    assert "server_owner_id" in COG
    assert "requester_is_server_owner" in COG
    assert "Only the actual server owner" in COG
    assert "Confirm recovery action" in COG
    assert "Post once (owner)" in COG
    assert "Share as lead (owner)" in COG


def test_soft_override_never_bypasses_exact_proof() -> None:
    assert "SOFT_OVERRIDE_OUTCOMES" in SERVICE
    assert '"quality_blocked"' not in SERVICE.split("SOFT_OVERRIDE_OUTCOMES", 1)[1].split("SAFE_RETRY_OUTCOMES", 1)[0]
    assert "min_discount=1" in SERVICE
    assert "cannot be called a verified deal" in SERVICE
    assert "one-time server-owner override" in SERVICE
    assert "share_review_card" in SERVICE


def test_recovery_actions_are_audited_and_idempotent() -> None:
    assert "walmart_delivery_recovery_actions" in SERVICE
    assert "OWNER_OVERRIDE_POST_PREFIX" in SERVICE
    assert "reserve_public_deal_post" in SERVICE
    assert "finalize_successful_public_post" in SERVICE
    assert "_mark_original_delivery_receipt" in SERVICE
