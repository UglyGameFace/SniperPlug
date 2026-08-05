import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUTOSCAN_HEALTH = (
    ROOT / "sniperplug/cogs/canonical_public_alerts.py"
).read_text(encoding="utf-8")
STORAGE_HEALTH = (
    ROOT / "sniperplug/cogs/storage_admin.py"
).read_text(encoding="utf-8")
DELIVERY_HEALTH = (
    ROOT / "sniperplug/services/walmart_delivery_health.py"
).read_text(encoding="utf-8")


def test_unfinished_hp_cannot_make_walmart_report_not_ready() -> None:
    assert "walmart_delivery_ready =" in AUTOSCAN_HEALTH
    assert "hp_delivery_ready =" in AUTOSCAN_HEALTH
    assert "target_delivery_ready =" in AUTOSCAN_HEALTH
    assert "One unhealthy retailer does not silently disable another retailer" in AUTOSCAN_HEALTH
    assert "This does not block Walmart" in AUTOSCAN_HEALTH
    assert re.search(r"^\s+delivery_ready\s*=", AUTOSCAN_HEALTH, re.MULTILINE) is None


def test_global_walmart_health_no_longer_depends_on_legacy_per_guild_report() -> None:
    assert "load_walmart_delivery_health" in AUTOSCAN_HEALTH
    assert "latest_autoscan_report" not in AUTOSCAN_HEALTH
    assert "format_latest_report_line" not in AUTOSCAN_HEALTH
    assert "Walmart delivery audit — current rules" in AUTOSCAN_HEALTH


def test_storage_health_exposes_live_global_pipeline() -> None:
    assert "Live Walmart pipeline" in STORAGE_HEALTH
    assert "Zero legacy rows do not prove that discovery stopped" in STORAGE_HEALTH
    assert "Global event rows" in DELIVERY_HEALTH
    assert "eligible_without_post" in DELIVERY_HEALTH
    assert "read-only audit" in DELIVERY_HEALTH
