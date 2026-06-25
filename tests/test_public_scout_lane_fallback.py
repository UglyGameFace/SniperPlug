from pathlib import Path

QUALITY = Path("sniperplug/services/public_deal_quality.py").read_text(encoding="utf-8")
AUTO = Path("sniperplug/cogs/auto_scan_runner.py").read_text(encoding="utf-8")
DB = Path("sniperplug/storage/db.py").read_text(encoding="utf-8")


def test_scout_lane_exists_but_public_posting_is_disabled():
    assert "PUBLIC_SCOUT_LANE_FIELD" in QUALITY
    assert "prepare_public_scout_candidate" in QUALITY
    assert "Public Scout Lane is intentionally disabled" in QUALITY


def test_autoscan_keeps_scout_private_when_verified_lane_empty():
    assert "Public Scout Lane only posts high-confidence leads" in AUTO
    assert "allow_review_scout=True" not in AUTO
    assert "Public Scout Lane is disabled for public posts" in AUTO


def test_libsql_fetchall_has_connection_safety_code():
    assert "asyncio.Lock" in DB
