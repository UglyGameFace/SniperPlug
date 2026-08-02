from __future__ import annotations

from sniperplug.cogs.canonical_workflow import merge_canonical_retailers


def test_canonical_setup_adds_hp_without_removing_existing_store_choices() -> None:
    assert merge_canonical_retailers(("amazon", "bestbuy")) == (
        "amazon",
        "bestbuy",
        "walmart",
        "hp",
    )


def test_canonical_setup_normalizes_and_deduplicates_retailers() -> None:
    assert merge_canonical_retailers(("HP Store", "Wal-Mart", "hp")) == (
        "hp",
        "walmart",
    )
