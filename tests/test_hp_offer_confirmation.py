from __future__ import annotations

import asyncio
import json

import pytest

from sniperplug.hp_watcher.client import HTTPDocument
from sniperplug.hp_watcher.parser import HPPriceOffer
from sniperplug.hp_watcher.service import (
    confirm_exact_hp_offer,
    exact_hp_offers_match,
    offer_requires_exact_confirmation,
)
from sniperplug.hp_watcher.storage import CatalogProduct


PRODUCT = CatalogProduct(
    product_key="hp-product:test",
    product_url="https://www.hp.com/us-en/shop/pdp/hp-travel-backpack",
    sku="6B8U4AA",
    catalog_entry_id="3074457345619999999",
    title="HP Travel Backpack",
    image_url="",
)
DISCOVERED = HPPriceOffer(
    product_id=PRODUCT.catalog_entry_id,
    part_number="6B8U4AA#ABA",
    sku=PRODUCT.sku,
    current_price=12.99,
    msrp_price=54.99,
    in_stock=True,
    can_add_to_cart=True,
)


class FakeClient:
    def __init__(self, offer: HPPriceOffer):
        self.offer = offer
        self.calls: list[tuple[list[str], bool]] = []

    async def fetch_price_batch(self, product_ids, *, cache_bust=False):
        self.calls.append((list(product_ids), bool(cache_bust)))
        payload = {
            "priceData": [
                {
                    "productId": self.offer.product_id,
                    "partNumber": self.offer.part_number,
                    "price": self.offer.current_price,
                    "lPrice": self.offer.msrp_price,
                    "inStock": self.offer.in_stock,
                    "canAddToCart": self.offer.can_add_to_cart,
                }
            ]
        }
        return HTTPDocument(url="https://www.hp.com/confirm", status=200, text=json.dumps(payload))


def test_first_seen_msrp_markdown_requires_independent_confirmation() -> None:
    assert offer_requires_exact_confirmation(PRODUCT, DISCOVERED, min_discount=50) is True


def test_unchanged_known_markdown_does_not_repeat_confirmation_every_poll() -> None:
    known = CatalogProduct(
        **{
            **PRODUCT.__dict__,
            "previous_current_price": 12.99,
            "previous_reference_price": 54.99,
            "previous_in_stock": True,
        }
    )
    assert offer_requires_exact_confirmation(known, DISCOVERED, min_discount=50) is False


def test_price_drop_and_back_in_stock_require_confirmation() -> None:
    price_drop_product = CatalogProduct(
        **{
            **PRODUCT.__dict__,
            "previous_current_price": 20.0,
            "previous_reference_price": 54.99,
            "previous_in_stock": True,
        }
    )
    assert offer_requires_exact_confirmation(price_drop_product, DISCOVERED, min_discount=50) is True

    restock_product = CatalogProduct(
        **{
            **PRODUCT.__dict__,
            "previous_current_price": 12.99,
            "previous_reference_price": 54.99,
            "previous_in_stock": False,
        }
    )
    assert offer_requires_exact_confirmation(restock_product, DISCOVERED, min_discount=50) is True


def test_matching_cache_busted_confirmation_is_accepted(monkeypatch) -> None:
    async def run() -> None:
        async def no_sleep(_delay):
            return None

        monkeypatch.setattr("sniperplug.hp_watcher.service.asyncio.sleep", no_sleep)
        client = FakeClient(DISCOVERED)
        confirmed = await confirm_exact_hp_offer(client, PRODUCT, DISCOVERED)
        assert exact_hp_offers_match(DISCOVERED, confirmed) is True
        assert client.calls == [([PRODUCT.catalog_entry_id], True)]

    asyncio.run(run())


def test_price_disagreement_fails_closed(monkeypatch) -> None:
    async def run() -> None:
        async def no_sleep(_delay):
            return None

        monkeypatch.setattr("sniperplug.hp_watcher.service.asyncio.sleep", no_sleep)
        changed = HPPriceOffer(
            product_id=DISCOVERED.product_id,
            part_number=DISCOVERED.part_number,
            sku=DISCOVERED.sku,
            current_price=54.99,
            msrp_price=54.99,
            in_stock=True,
            can_add_to_cart=True,
        )
        with pytest.raises(ValueError, match="disagreed"):
            await confirm_exact_hp_offer(FakeClient(changed), PRODUCT, DISCOVERED)

    asyncio.run(run())
