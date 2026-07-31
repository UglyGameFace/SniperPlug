from pathlib import Path


SOURCE = Path("sniperplug/cogs/active_deals.py").read_text()


def test_active_deals_keeps_controls_on_single_page():
    assert "def build_active_deals_view" in SOURCE
    assert "if not page_data.rows and page_data.total_pages <= 1" in SOURCE
    assert "view=build_active_deals_view(page_data)" in SOURCE


def test_walmart_rows_have_exact_selector_and_page_batch_button():
    assert "class WalmartPageDealSelect" in SOURCE
    assert "Recheck one exact Walmart item on this page" in SOURCE
    assert 'label="Recheck Walmart on Page"' in SOURCE
    assert "page_data.walmart_rows[:BATCH_RECHECK_MAX_ITEMS]" in SOURCE
    assert "BATCH_RECHECK_CONCURRENCY" in SOURCE
    assert "BATCH_RECHECK_TIMEOUT_SECONDS" in SOURCE


def test_component_controls_recheck_manage_server_permission():
    assert "async def interaction_check" in SOURCE
    assert 'getattr(interaction.permissions, "manage_guild", False)' in SOURCE
    assert "You need **Manage Server**" in SOURCE


def test_component_reloads_only_active_exact_walmart_rows():
    assert "async def load_active_walmart_rows_by_keys" in SOURCE
    assert "retailer = 'walmart' AND status = 'active'" in SOURCE
    assert "active_key IN" in SOURCE
    assert "That cached Walmart row is no longer active" in SOURCE
    assert "Refresh `/active_deals` before rechecking it" in SOURCE


def test_active_page_query_includes_cache_identity():
    assert "SELECT active_key, retailer, title, url" in SOURCE
    assert "NON_PERSISTED_RECHECK_STATUSES" in SOURCE
    assert '"timeout"' in SOURCE
