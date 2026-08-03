from __future__ import annotations

from sniperplug.cogs.canonical_workflow import merge_canonical_retailers


def test_canonical_setup_does_not_add_target_without_saved_location() -> None:
    assert merge_canonical_retailers(("amazon", "bestbuy")) == (
        "amazon",
        "bestbuy",
        "walmart",
        "hp",
    )


def test_canonical_setup_adds_target_only_after_location_is_ready() -> None:
    assert merge_canonical_retailers(
        ("amazon", "bestbuy"),
        include_target=True,
    ) == (
        "amazon",
        "bestbuy",
        "walmart",
        "hp",
        "target",
    )


def test_canonical_setup_removes_stale_target_without_location() -> None:
    assert merge_canonical_retailers(
        ("HP Store", "Wal-Mart", "Target Store", "hp", "target.com")
    ) == (
        "hp",
        "walmart",
    )


def test_canonical_setup_normalizes_target_when_location_exists() -> None:
    assert merge_canonical_retailers(
        ("HP Store", "Wal-Mart", "Target Store", "hp", "target.com"),
        include_target=True,
    ) == (
        "hp",
        "walmart",
        "target",
    )
