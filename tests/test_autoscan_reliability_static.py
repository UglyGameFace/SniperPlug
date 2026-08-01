from pathlib import Path


AUTO = Path("sniperplug/cogs/auto_scan_runner.py").read_text(encoding="utf-8")
DB = Path("sniperplug/storage/db.py").read_text(encoding="utf-8")
MEMORY = Path("sniperplug/services/walmart_price_memory.py").read_text(encoding="utf-8")
GLOBAL_MEMORY = Path("sniperplug/services/walmart_global_offer_memory.py").read_text(encoding="utf-8")
QUALITY = Path("sniperplug/services/public_deal_quality.py").read_text(encoding="utf-8")


def test_autoscan_acks_safely_and_does_not_crash_unknown_interaction():
    assert "safe_defer" in AUTO
    assert "if not await safe_defer(interaction, ephemeral=True, thinking=True)" in AUTO
    assert "safe_send_interaction" in AUTO


def test_autoscan_long_followup_has_dm_fallback_for_expired_webhook():
    assert "_send_autoscan_dm_fallback" in AUTO
    assert "interaction_token_is_gone" in AUTO
    assert "invalid webhook token" in AUTO.lower()
    assert "unknown interaction" in AUTO.lower()


def test_db_retries_turso_502_and_option_unwrap_transients():
    lowered = DB.lower()
    assert "connect to upstream failed" in lowered
    assert "bad gateway" in lowered
    assert "option::unwrap" in lowered
    assert "self._is_retryable_libsql_stream_error(exc)" in DB


def test_memory_recheck_uses_hard_ids_not_random_title_queries():
    start = MEMORY.index("async def remembered_walmart_search_seeds")
    end = MEMORY.index("def decide", start)
    body = MEMORY[start:end]

    assert "walmart_item_id_from_url" in body
    assert "compact_title_seed(row" not in body
    assert "title seeds caused random junk routes" in body


def test_global_price_memory_uses_compact_exact_offer_proof():
    assert 'GLOBAL_OFFER_MEMORY_TABLE = "walmart_offer_price_memory"' in GLOBAL_MEMORY
    assert "exactDetailPriceProof" in GLOBAL_MEMORY
    assert "item_id" in GLOBAL_MEMORY
    assert "offer_id" in GLOBAL_MEMORY
    assert "seller_key" in GLOBAL_MEMORY
    assert "variant_key" in GLOBAL_MEMORY
    assert "condition_key" in GLOBAL_MEMORY
    assert "fulfillment_key" in GLOBAL_MEMORY
    assert "current_price_cents" in GLOBAL_MEMORY
    assert "stable_price_cents" in GLOBAL_MEMORY
    assert "MIN_STABLE_CONFIRMATIONS = 2" in GLOBAL_MEMORY
    assert "MIN_CONFIRMATION_GAP_SECONDS = 4 * 60 * 60" in GLOBAL_MEMORY


def test_public_quality_requires_recomputed_global_offer_fingerprint():
    start = QUALITY.index("def has_global_exact_offer_memory_proof")
    end = QUALITY.index("def walmart_item_id_from_url", start)
    body = QUALITY[start:end]

    assert "priceMemoryIdentity" in body
    assert "priceMemoryItemId" in body
    assert "priceMemoryOfferId" in body
    assert "priceMemorySellerKey" in body
    assert "priceMemoryVariantKey" in body
    assert "priceMemoryConditionKey" in body
    assert "priceMemoryFulfillmentKey" in body
    assert "priceMemoryStableConfirmations" in body
    assert "exactDetailPriceProof" in body
    assert "exactDetailItemId" in body
    assert "referencePriceTrusted" in body
    assert "trustedReferenceSource" in body
    assert "hashlib.sha256" in body
    assert "selected_offer != offer_id" in body
    assert "url_item_id != item_id" in body
