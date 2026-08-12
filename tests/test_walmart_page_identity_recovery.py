from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import discord

from sniperplug.cogs.canonical_public_alerts import WalmartRecoveryView
from sniperplug.providers.base import ProviderScanRequest
from sniperplug.providers.walmart import WalmartAffiliateConfig, WalmartProviderError
from sniperplug.providers.walmart_page_recovery import (
    PAGE_RECOVERY_REARM_KEY,
    PAGE_RECOVERY_REARM_LIMIT,
    PAGE_RECOVERY_REARM_TABLE,
    WalmartPageRecoveryProvider,
    rearm_recent_identity_rows_for_page_recovery,
    recover_page_identity_from_scripts,
)
from sniperplug.services import walmart_exact_verification_queue as exact_queue
from sniperplug.services.walmart_delivery_recovery import WalmartRecoveryItem


class FixturePageProvider(WalmartPageRecoveryProvider):
    def __init__(self, payload: dict, scripts: list[str]):
        super().__init__(
            WalmartAffiliateConfig(
                enabled=True,
                consumer_id="test",
                private_key_b64="unused",
            )
        )
        self.payload = payload
        self.scripts = scripts
        self.page_calls = 0

    async def _official_detail_payload(self, item_id: str) -> dict:
        assert str(self.payload.get("itemId")) == str(item_id)
        return copy.deepcopy(self.payload)

    async def _load_product_page_scripts(self, item_id: str) -> list[str]:
        self.page_calls += 1
        return list(self.scripts)


def jsonld_product(
    *,
    item_id: str,
    price: float,
    seller: str,
    seller_id: str | None = None,
    offer_id: str | None = None,
) -> str:
    seller_payload: dict[str, str] = {"@type": "Organization", "name": seller}
    if seller_id:
        seller_payload["id"] = seller_id
    offer: dict[str, object] = {
        "@type": "Offer",
        "price": f"{price:.2f}",
        "seller": seller_payload,
    }
    if offer_id:
        offer["id"] = offer_id
    return json.dumps(
        {
            "@context": "https://schema.org",
            "@type": "Product",
            "sku": item_id,
            "url": f"https://www.walmart.com/ip/{item_id}",
            "offers": offer,
        }
    )


def test_complete_api_seller_skips_product_page() -> None:
    provider = FixturePageProvider(
        {
            "itemId": 1001,
            "name": "Already complete",
            "salePrice": 20.0,
            "sellerName": "Walmart",
            "isMarketPlaceItem": False,
        },
        [jsonld_product(item_id="1001", price=20.0, seller="Walmart.com")],
    )

    payload = asyncio.run(provider.fetch_product_detail_payload("1001"))

    assert payload["sellerName"] == "Walmart"
    assert provider.page_calls == 0


def test_missing_api_seller_is_recovered_only_from_exact_price_aligned_product_page() -> None:
    provider = FixturePageProvider(
        {
            "itemId": 1002,
            "name": "Recovered Walmart offer",
            "salePrice": 19.99,
            "wasPrice": 79.99,
            "availableOnline": True,
        },
        [
            jsonld_product(
                item_id="1002",
                price=19.99,
                seller="Walmart.com",
                offer_id="page-offer-1002",
            )
        ],
    )

    payload = asyncio.run(provider.fetch_product_detail_payload("1002"))
    candidate = provider._candidate_from_item(
        payload,
        ProviderScanRequest(source_key="test", query="1002", max_results=1),
    )

    assert provider.page_calls == 1
    assert payload["sellerName"] == "Walmart.com"
    assert payload["offerId"] == "page-offer-1002"
    assert payload["marketplace"] is False
    assert payload["isMarketPlaceItem"] is False
    assert payload["sniperplugPageRecoveryStatus"] == "verified_identity"
    assert candidate is not None
    assert candidate.seller_name == "Walmart.com"
    assert candidate.variant_attributes["pageIdentityRecoveryStatus"] == "verified_identity"
    assert any("product-page structured data" in signal for signal in candidate.signals)


def test_third_party_seller_can_be_recovered_without_weakening_shipping_guard() -> None:
    provider = FixturePageProvider(
        {
            "itemId": 1003,
            "name": "Marketplace item",
            "salePrice": 29.99,
            "wasPrice": 89.99,
            "availableOnline": True,
        },
        [
            jsonld_product(
                item_id="1003",
                price=29.99,
                seller="Exact Marketplace LLC",
                seller_id="seller-1003",
                offer_id="offer-1003",
            )
        ],
    )

    payload = asyncio.run(provider.fetch_product_detail_payload("1003"))
    candidate = provider._candidate_from_item(
        payload,
        ProviderScanRequest(source_key="test", query="1003", max_results=1),
    )

    assert payload["sellerName"] == "Exact Marketplace LLC"
    assert payload["sellerId"] == "seller-1003"
    assert payload["marketplace"] is True
    assert candidate is not None
    assert candidate.seller_name == "Exact Marketplace LLC"
    assert candidate.shipping_status == "unknown"
    assert candidate.current_price is None
    assert candidate.api_current_price is None
    assert candidate.variant_attributes["selectedOfferPublicPriceStatus"] == "blocked_shipping_unknown"


def test_api_page_marketplace_conflict_stays_fail_closed() -> None:
    provider = FixturePageProvider(
        {
            "itemId": 1004,
            "name": "Conflicting seller",
            "salePrice": 15.0,
            "isMarketPlaceItem": True,
        },
        [jsonld_product(item_id="1004", price=15.0, seller="Walmart.com")],
    )

    payload = asyncio.run(provider.fetch_product_detail_payload("1004"))

    assert payload.get("sellerName") in {None, ""}
    assert payload["isMarketPlaceItem"] is True
    assert payload["sniperplugPageRecoveryStatus"] == "marketplace_conflict"


def test_api_page_price_change_is_retryable_not_bound_to_wrong_seller() -> None:
    provider = FixturePageProvider(
        {
            "itemId": 1005,
            "name": "Price changed between exact reads",
            "salePrice": 20.0,
        },
        [jsonld_product(item_id="1005", price=25.0, seller="Walmart.com")],
    )

    try:
        asyncio.run(provider.fetch_product_detail_payload("1005"))
    except WalmartProviderError as exc:
        assert "price mismatch" in str(exc).lower()
    else:
        raise AssertionError("API/page price mismatch must stay retryable and fail closed")


def test_structured_data_for_another_item_cannot_supply_identity() -> None:
    provider = FixturePageProvider(
        {
            "itemId": 1006,
            "name": "Exact requested item",
            "salePrice": 18.0,
        },
        [jsonld_product(item_id="9999", price=18.0, seller="Walmart.com")],
    )

    payload = asyncio.run(provider.fetch_product_detail_payload("1006"))

    assert payload.get("sellerName") in {None, ""}
    assert payload["sniperplugPageRecoveryStatus"] == "no_exact_item_match"


def test_equal_strength_conflicting_offers_are_ambiguous() -> None:
    script = json.dumps(
        {
            "@type": "Product",
            "sku": "1007",
            "offers": [
                {"price": "10.00", "seller": {"name": "Seller A"}},
                {"price": "10.00", "seller": {"name": "Seller B"}},
            ],
        }
    )

    result = recover_page_identity_from_scripts(
        [script],
        item_id="1007",
        api_item_price=10.0,
    )

    assert result.proof is None
    assert result.status == "ambiguous_selected_offer"


def test_recent_terminal_rearm_is_bounded_and_one_time(monkeypatch) -> None:
    class Cursor:
        def __init__(self, row=None, *, rowcount=0):
            self.row = row
            self.rowcount = rowcount

        async def fetchone(self):
            return self.row

    class Connection:
        def __init__(self):
            self.marker = False
            self.sql: list[str] = []
            self.params: list[tuple] = []
            self.commits = 0

        async def execute(self, sql, params=()):
            text = str(sql)
            self.sql.append(text)
            self.params.append(tuple(params))
            if f"SELECT migration_key FROM {PAGE_RECOVERY_REARM_TABLE}" in text:
                return Cursor({"migration_key": PAGE_RECOVERY_REARM_KEY} if self.marker else None)
            if "UPDATE walmart_exact_detail_queue" in text:
                return Cursor(rowcount=7)
            if f"INSERT INTO {PAGE_RECOVERY_REARM_TABLE}" in text:
                self.marker = True
                return Cursor(rowcount=1)
            return Cursor()

        async def commit(self):
            self.commits += 1

    conn = Connection()
    db = SimpleNamespace(require_conn=lambda: conn)

    async def no_op(_db):
        return None

    monkeypatch.setattr(exact_queue, "ensure_walmart_exact_verification_queue", no_op)

    first = asyncio.run(rearm_recent_identity_rows_for_page_recovery(db))
    second = asyncio.run(rearm_recent_identity_rows_for_page_recovery(db))

    assert first == 7
    assert second == 0
    update_sql = next(sql for sql in conn.sql if "UPDATE walmart_exact_detail_queue" in sql)
    assert "status = 'incomplete_identity'" in update_sql
    assert "lower(last_error) LIKE '%seller%'" in update_sql
    assert "ORDER BY priority_score DESC, last_seen_at DESC" in update_sql
    assert "LIMIT ?" in update_sql
    update_params = conn.params[conn.sql.index(update_sql)]
    assert update_params[-1] == PAGE_RECOVERY_REARM_LIMIT
    assert conn.commits == 1


def test_identity_blocked_recovery_item_always_has_open_walmart_link() -> None:
    url = "https://www.walmart.com/ip/123456789"
    item = WalmartRecoveryItem(
        deal_key="queue:123456789",
        public_key="",
        label="Blocked exact identity item",
        event_at="2026-08-12T00:00:00+00:00",
        outcome="exact_identity_blocked",
        detail="missing seller identity",
        discount=None,
        threshold=50,
        item_id="123456789",
        product_url=url,
        last_error="missing seller identity",
        post_status="",
        candidate=None,
        card=None,
    )
    view = WalmartRecoveryView(
        bot=SimpleNamespace(db=None),
        guild_id=1,
        requester_id=2,
        server_owner_id=2,
        threshold=50,
        category_preferences={},
        items=[item],
    )

    links = [
        child
        for child in view.children
        if isinstance(child, discord.ui.Button) and child.label == "Open Walmart"
    ]
    assert len(links) == 1
    assert links[0].url == url
    assert item.can_owner_override is False
    assert item.can_recheck_exact is True


def test_canonical_runtime_registers_page_recovery_provider_and_bounded_rearm() -> None:
    source = (Path(__file__).resolve().parents[1] / "sniperplug/bot.py").read_text(
        encoding="utf-8"
    )
    assert "WalmartPageRecoveryProvider" in source
    assert "rearm_recent_identity_rows_for_page_recovery" in source
    assert "WalmartProvider(walmart_affiliate_config" not in source
