from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY = (ROOT / "sniperplug/services/autoscan_route_policy.py").read_text(encoding="utf-8")
OBSERVED = (ROOT / "sniperplug/services/autoscan_observed_price_memory.py").read_text(encoding="utf-8")
PUBLIC_QUALITY = (ROOT / "sniperplug/services/public_deal_quality.py").read_text(encoding="utf-8")


def test_autoscan_route_policy_installs_observed_price_memory():
    assert "install_autoscan_observed_price_memory" in POLICY
    assert "install_autoscan_observed_price_memory()" in POLICY


def test_autoscan_collection_uses_observed_memory_from_all_candidates():
    assert "select_observed_price_drop_cards" in OBSERVED
    assert "candidates=list(deduped_candidates)" in OBSERVED
    assert "use_price_memory=True" in OBSERVED
    assert "run_autoscan_verified_category_with_observed_memory" in OBSERVED


def test_observed_memory_cards_join_autoscan_public_candidate_pool():
    assert "observed_memory.cards" in OBSERVED
    assert "legacy_memory.shown" in OBSERVED
    assert "cards = rank_verified_cards(memory_cards)" in OBSERVED


def test_observed_memory_summary_names_lane_and_examples():
    assert "legacy verified-card memory" in OBSERVED
    assert "observed_drop_examples" in OBSERVED
    assert "examples:" in OBSERVED
    assert "price memory enabled, no products checked" in OBSERVED


def test_observed_price_memory_lane_is_public_quality_but_not_scout():
    assert "LANE_PRICE_MEMORY_DROP" in PUBLIC_QUALITY
    assert "Public Scout Lane is intentionally disabled" in PUBLIC_QUALITY
    assert "priceMemoryIdentity" in PUBLIC_QUALITY
    assert "referencePriceTrusted" in PUBLIC_QUALITY
