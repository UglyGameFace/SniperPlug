from pathlib import Path


SEARCH = Path("sniperplug/cogs/home_depot_search.py").read_text()
LOCAL = Path("sniperplug/cogs/home_depot_local.py").read_text()


def test_home_depot_search_uses_private_owner_language():
    assert "Route: **Private Owner Review**" in SEARCH
    assert "Route: **Staff Review**" not in SEARCH
    assert "private shopper verification leads" in SEARCH
    assert "Only your own store/register check can confirm a penny price" in SEARCH
    assert "It does **not** prove shelf stock" in SEARCH
    assert "Verify in store before posting" in SEARCH


def test_home_depot_search_does_not_charge_cache_hits():
    assert 'quota_cost = 0 if result.metadata.get("cache_hit") else 1' in SEARCH
    assert 'serpapi_quota_guard.record(interaction.user.id, cost=quota_cost)' in SEARCH
    assert 'SerpApi charged: **{quota_cost} credit(s)**' in SEARCH


def test_home_depot_local_copy_requires_exact_store_without_employee_language():
    assert "private stock check" in LOCAL
    assert "ZIP-only stock claims are blocked" in LOCAL
    assert "choose the exact store for private verification" in LOCAL
    assert "employee" not in LOCAL.lower()
