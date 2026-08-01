from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY = (ROOT / "sniperplug/services/autoscan_route_policy.py").read_text(encoding="utf-8")
OBSERVED = (ROOT / "sniperplug/services/autoscan_observed_price_memory.py").read_text(encoding="utf-8")
GLOBAL_MEMORY = (ROOT / "sniperplug/services/walmart_global_offer_memory.py").read_text(encoding="utf-8")
BOT = (ROOT / "sniperplug/bot.py").read_text(encoding="utf-8")
PUBLIC_QUALITY = (ROOT / "sniperplug/services/public_deal_quality.py").read_text(encoding="utf-8")


def test_autoscan_route_policy_is_native_not_installed():
    assert "public_autoscan_hunt_presets" in POLICY
    assert "install_public_autoscan_route_policy" not in POLICY
    assert "install_public_autoscan_route_policy" not in BOT


def test_autoscan_collection_uses_observed_memory_from_all_candidates():
    assert "select_observed_price_drop_cards" in OBSERVED
    assert "candidates=list(deduped_candidates)" in OBSERVED
    assert "use_price_memory=True" in OBSERVED
    assert "run_autoscan_verified_category_with_observed_memory" in OBSERVED


def test_only_global_exact_offer_memory_cards_join_public_candidate_pool():
    assert "observed_memory.cards" in OBSERVED
    assert "legacy_memory.shown" not in OBSERVED
    assert "select_price_intelligent_cards" not in OBSERVED
    assert "memory_cards = hunt.dedupe_cards([*verified_cards, *observed_memory.cards])" in OBSERVED
    assert "cards = rank_verified_cards(memory_cards)" in OBSERVED


def test_observed_memory_summary_names_global_exact_offer_lane_and_examples():
    assert "global exact-offer price memory" in OBSERVED
    assert "observed_drop_examples" in OBSERVED
    assert "examples:" in OBSERVED
    assert "legacy verified-card memory" not in OBSERVED


def test_global_memory_is_compact_shared_and_confirmation_gated():
    assert 'GLOBAL_OFFER_MEMORY_TABLE = "walmart_offer_price_memory"' in GLOBAL_MEMORY
    assert "MIN_STABLE_CONFIRMATIONS = 2" in GLOBAL_MEMORY
    assert "MIN_CONFIRMATION_GAP_SECONDS = 4 * 60 * 60" in GLOBAL_MEMORY
    assert "guild_id" not in GLOBAL_MEMORY
    assert "stable_price_cents" in GLOBAL_MEMORY
    assert "ON CONFLICT(identity_key) DO NOTHING" in GLOBAL_MEMORY


def test_observed_price_memory_lane_and_scout_lane_are_distinct():
    assert "LANE_PRICE_MEMORY_DROP" in PUBLIC_QUALITY
    assert "LANE_PUBLIC_SCOUT" in PUBLIC_QUALITY
    assert "has_global_exact_offer_memory_proof" in PUBLIC_QUALITY
    assert "priceMemoryIdentity" in PUBLIC_QUALITY
    assert "priceMemorySellerKey" in PUBLIC_QUALITY
    assert "priceMemoryVariantKey" in PUBLIC_QUALITY
    assert "referencePriceTrusted" in PUBLIC_QUALITY
