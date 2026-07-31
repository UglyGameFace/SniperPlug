from __future__ import annotations

from pathlib import Path

SEARCH = Path("sniperplug/cogs/home_depot_search.py")
LOCAL = Path("sniperplug/cogs/home_depot_local.py")
TEST = Path("tests/test_home_depot_owner_proof.py")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        if new in text:
            return text
        raise SystemExit(f"{label} not found")
    return text.replace(old, new, 1)


def patch_search() -> None:
    text = SEARCH.read_text()
    text = replace_once(
        text,
        '            quota_after = serpapi_quota_guard.record(interaction.user.id, cost=1)\n',
        '            quota_cost = 0 if result.metadata.get("cache_hit") else 1\n'
        '            quota_after = serpapi_quota_guard.record(interaction.user.id, cost=quota_cost)\n',
        "Home Depot search cache-aware quota",
    )
    text = replace_once(
        text,
        '                    f"SerpApi used: **{quota_after.monthly_used}/{quota_after.monthly_limit} monthly safe budget** • "\n',
        '                    f"SerpApi charged: **{quota_cost} credit(s)** • "\n'
        '                    f"Usage: **{quota_after.monthly_used}/{quota_after.monthly_limit} monthly safe budget** • "\n',
        "Home Depot search quota summary",
    )
    text = replace_once(
        text,
        '            summary.description += "\\nThese are **verification candidates**, not confirmed in-store penny deals."\n',
        '            summary.description += (\n'
        '                "\\nThese are **private shopper verification leads**, not confirmed in-store penny deals. "\n'
        '                "Only your own store/register check can confirm a penny price."\n'
        '            )\n',
        "Home Depot search proof boundary",
    )
    text = text.replace('            "Route: **Staff Review**\\n"', '            "Route: **Private Owner Review**\\n"')
    text = replace_once(
        text,
        '    embed.add_field(name="🟢 Liveness", value=home_depot_liveness_block(candidate.current_price, penny_score, raw_fallback=raw_fallback), inline=False)\n',
        '    embed.add_field(name="🟢 Liveness", value=home_depot_liveness_block(candidate.current_price, penny_score, raw_fallback=raw_fallback), inline=False)\n'
        '    embed.add_field(\n'
        '        name="🏪 What this proves",\n'
        '        value=(\n'
        '            "This card proves only the online/search evidence shown above. "\n'
        '            "It does **not** prove shelf stock, the exact local store price, or a register penny. "\n'
        '            "Confirm those yourself in the selected Home Depot store before buying or sharing publicly."\n'
        '        ),\n'
        '        inline=False,\n'
        '    )\n',
        "Home Depot card evidence boundary",
    )
    text = text.replace(
        '    footer_bits.append("Verify in store before posting")',
        '    footer_bits.append("Private lead • Verify in store before posting • confirm personally")',
    )
    SEARCH.write_text(text)


def patch_local() -> None:
    text = LOCAL.read_text()
    text = text.replace(
        '"Choose the actual store below so SniperPlug can run a store-specific stock check.\\n\\n"',
        '"Choose the actual store below so SniperPlug can run a store-specific private stock check.\\n\\n"',
    )
    text = text.replace(
        '"**Important:** ZIP-only Home Depot results are blocked because they can return the wrong store."',
        '"**Important:** ZIP-only stock claims are blocked because they can resolve to the wrong store. The picker keeps the evidence tied to the store you actually choose."',
    )
    text = text.replace(
        'embed.set_footer(text="Pick a store first. SniperPlug will not use ZIP-only local stock proof.")',
        'embed.set_footer(text="Private owner review • pick the exact store before trusting local stock evidence.")',
    )
    text = text.replace(
        '"**The ZIP-only scan was blocked on purpose** so it does not show wrong-location stock like Bangor again."',
        '"**The ZIP-only stock claim was blocked on purpose** so SniperPlug does not attach another store’s inventory to your location."',
    )
    text = text.replace(
        'embed.set_footer(text="No ZIP-only stock card was posted. Store-specific proof is required.")',
        'embed.set_footer(text="No ZIP-only stock claim was shown • choose the exact store for private verification.")',
    )
    LOCAL.write_text(text)


def write_tests() -> None:
    TEST.write_text('''from pathlib import Path\n\n\nSEARCH = Path("sniperplug/cogs/home_depot_search.py").read_text()\nLOCAL = Path("sniperplug/cogs/home_depot_local.py").read_text()\n\n\ndef test_home_depot_search_uses_private_owner_language():\n    assert "Route: **Private Owner Review**" in SEARCH\n    assert "Route: **Staff Review**" not in SEARCH\n    assert "private shopper verification leads" in SEARCH\n    assert "Only your own store/register check can confirm a penny price" in SEARCH\n    assert "It does **not** prove shelf stock" in SEARCH\n    assert "Verify in store before posting" in SEARCH\n\n\ndef test_home_depot_search_does_not_charge_cache_hits():\n    assert 'quota_cost = 0 if result.metadata.get("cache_hit") else 1' in SEARCH\n    assert 'serpapi_quota_guard.record(interaction.user.id, cost=quota_cost)' in SEARCH\n    assert 'SerpApi charged: **{quota_cost} credit(s)**' in SEARCH\n\n\ndef test_home_depot_local_copy_requires_exact_store_without_employee_language():\n    assert "private stock check" in LOCAL\n    assert "ZIP-only stock claims are blocked" in LOCAL\n    assert "choose the exact store for private verification" in LOCAL\n    assert "employee" not in LOCAL.lower()\n''')


def main() -> None:
    patch_search()
    patch_local()
    write_tests()


if __name__ == "__main__":
    main()
