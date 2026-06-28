from pathlib import Path


AUTO = Path("sniperplug/cogs/auto_scan_runner.py").read_text(encoding="utf-8")
DB = Path("sniperplug/storage/db.py").read_text(encoding="utf-8")
MEMORY = Path("sniperplug/services/walmart_price_memory.py").read_text(encoding="utf-8")
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


def test_price_memory_drop_attaches_structured_public_proof():
    assert "def attach_price_memory_public_proof" in MEMORY
    assert '"price_memory_drop"' in MEMORY
    assert '"referencePriceTrusted": "yes"' in MEMORY
    assert "api_reference_price" in MEMORY
    assert "api_discount_percent" in MEMORY
    assert "should_alert" in MEMORY


def test_public_quality_allows_observed_price_memory_drop_with_real_reference():
    start = QUALITY.index("if lane == LANE_PRICE_MEMORY_DROP")
    body = QUALITY[start:start + 700]

    assert "priceMemoryIdentity" in body
    assert "reference > current" in body
    assert "discount >= max" in body
    assert "referencePriceTrusted" not in body
