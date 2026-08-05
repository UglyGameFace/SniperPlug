from __future__ import annotations

from types import SimpleNamespace

from sniperplug.services.dm_personal_categories import category_key_for_card


def _card(title: str, attrs: dict[str, str]):
    return SimpleNamespace(
        label=title,
        variant_attributes=attrs,
    )


def test_baby_yoda_taxonomy_and_breadcrumb_do_not_mean_baby_kids() -> None:
    for attrs in (
        {"taxonomy": "Star Wars > Baby Yoda > Collectibles"},
        {"breadcrumb": "Toys > Star Wars > Baby Yoda"},
        {"breadcrumbs": "Collectibles | Baby Yoda | Figures"},
        {
            "department": "Collectibles",
            "productCategory": "Star Wars Baby Yoda Figures",
        },
    ):
        category = category_key_for_card(
            _card("Star Wars Baby Yoda Collectible Figure", attrs)
        )
        assert category != "baby_kids"


def test_trusted_structured_baby_departments_still_classify_correctly() -> None:
    for attrs in (
        {"department": "Baby"},
        {"productCategory": "Baby Clothing"},
        {"productType": "Infant Apparel"},
        {"shelfName": "Baby > Travel > Strollers"},
    ):
        assert category_key_for_card(
            _card("Organic Cotton Product", attrs)
        ) == "baby_kids"
