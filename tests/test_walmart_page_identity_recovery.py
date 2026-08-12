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

    async def _official_exact_search_payload(self, item_id: str) -> dict:
        raise WalmartProviderError(
            f"fixture exact search unavailable for {item_id}",
            retry_kind="api_fixture_unavailable",
        )

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


def test_recent_terminal_rearm_falls_back_to_sql_changes_when_rowcount_missing(monkeypatch) -> None:
    class Cursor:
        def __init__(self, row=None, *, rowcount=-1):
            self.row = row
            self.rowcount = rowcount

        async def fetchone(self):
            return self.row

    class Connection:
        def __init__(self):
            self.marker = False
            self.changes = 0

        async def execute(self, sql, params=()):
            text = str(sql)
            if f"SELECT migration_key FROM {PAGE_RECOVERY_REARM_TABLE}" in text:
                return Cursor(None, rowcount=-1)
            if "UPDATE walmart_exact_detail_queue" in text:
                self.changes = 5
                return Cursor(rowcount=-1)
            if text.strip() == "SELECT changes()":
                return Cursor((self.changes,), rowcount=-1)
            if f"INSERT INTO {PAGE_RECOVERY_REARM_TABLE}" in text:
                self.marker = True
                return Cursor(rowcount=1)
            return Cursor(rowcount=0)

        async def commit(self):
            return None

    conn = Connection()
    db = SimpleNamespace(require_conn=lambda: conn)

    async def no_op(_db):
        return None

    monkeypatch.setattr(exact_queue, "ensure_walmart_exact_verification_queue", no_op)

    assert asyncio.run(rearm_recent_identity_rows_for_page_recovery(db)) == 5


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


def test_nested_offer_seller_cannot_be_borrowed_for_another_offer_price() -> None:
    script = json.dumps(
        {
            "@type": "Product",
            "sku": "1008",
            "currentPrice": "10.00",
            "offers": [
                {"price": "10.00"},
                {"price": "20.00", "seller": {"name": "Seller B"}},
            ],
        }
    )

    result = recover_page_identity_from_scripts(
        [script],
        item_id="1008",
        api_item_price=10.0,
    )

    assert result.proof is None
    assert result.status in {"seller_identity_missing", "price_mismatch", "no_price_aligned_seller"}


def test_redirect_host_must_be_walmart_domain(monkeypatch) -> None:
    from sniperplug.providers import walmart_page_recovery as page_recovery

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def geturl(self):
            return "https://walmart.com.evil.example/ip/1009"

        def read(self, _size):
            return b""

    monkeypatch.setattr(page_recovery.urllib.request, "urlopen", lambda *_args, **_kwargs: FakeResponse())

    try:
        page_recovery._request_product_page_scripts("1009", 3.0)
    except page_recovery.WalmartPageRecoveryTransientError as exc:
        assert "redirected away" in str(exc)
    else:
        raise AssertionError("lookalike redirect host must not be trusted")


def test_live_redswing_shape_recovers_deep_seller_info_and_free_shipping() -> None:
    provider = FixturePageProvider(
        {
            "itemId": 17549068871,
            "name": "RedSwing Camping Tent Stove",
            "selectedOffer": {
                "offerId": "offer-redswing-live",
                "currentPrice": 56.99,
                "wasPrice": 99.99,
                "isMarketPlaceItem": True,
            },
            "availableOnline": True,
        },
        [
            json.dumps(
                {
                    "props": {
                        "pageProps": {
                            "initialData": {
                                "data": {
                                    "product": {
                                        "usItemId": "17549068871",
                                        "buyBox": {
                                            "priceInfo": {"currentPrice": {"amount": 56.99}},
                                            "sellerInfo": {
                                                "sellerDisplayName": "Merry Li",
                                                "sellerId": "seller-merry-li",
                                            },
                                            "fulfillment": {
                                                "shippingOption": {"price": {"amount": 0.0}}
                                            },
                                        },
                                    }
                                }
                            }
                        }
                    }
                }
            )
        ],
    )

    payload = asyncio.run(provider.fetch_product_detail_payload("17549068871"))
    candidate = provider._candidate_from_item(
        payload,
        ProviderScanRequest(source_key="test", query="17549068871", max_results=1),
    )

    assert candidate is not None
    assert candidate.selected_offer_id == "offer-redswing-live"
    assert candidate.seller_name == "Merry Li"
    assert candidate.shipping_status == "free"
    assert candidate.shipping_cost == 0.0
    assert candidate.current_price == 56.99
    assert candidate.api_current_price == 56.99
    assert candidate.typical_price == 99.99
    assert candidate.variant_attributes["pageIdentityRecoveryShippingStatus"] == "free"


def test_live_grill_shape_recovers_paid_shipping_and_uses_delivered_price() -> None:
    provider = FixturePageProvider(
        {
            "itemId": 2368146027,
            "name": "Upgraded Gas Grill 4 Burner",
            "selectedOffer": {
                "offerId": "offer-seizeen-live",
                "currentPrice": 149.99,
                "wasPrice": 289.99,
                "isMarketPlaceItem": True,
            },
            "availableOnline": True,
        },
        [
            json.dumps(
                {
                    "product": {
                        "item_id": "2368146027",
                        "buyBoxWinner": {
                            "currentPrice": 149.99,
                            "merchantInfo": {
                                "merchantName": "Seizeen",
                                "merchantId": "seller-seizeen",
                            },
                            "shippingFee": 49.99,
                        },
                    }
                }
            )
        ],
    )

    payload = asyncio.run(provider.fetch_product_detail_payload("2368146027"))
    candidate = provider._candidate_from_item(
        payload,
        ProviderScanRequest(source_key="test", query="2368146027", max_results=1),
    )

    assert candidate is not None
    assert candidate.seller_name == "Seizeen"
    assert candidate.shipping_status == "paid"
    assert candidate.shipping_cost == 49.99
    assert candidate.item_price == 149.99
    assert candidate.delivered_price == 199.98
    assert candidate.current_price == 199.98
    assert candidate.api_discount_percent is not None
    assert 31.0 <= candidate.api_discount_percent <= 31.1
    assert candidate.variant_attributes["pageIdentityRecoveryShippingStatus"] == "paid"


def test_existing_marketplace_seller_with_unknown_shipping_still_triggers_page_recovery() -> None:
    provider = FixturePageProvider(
        {
            "itemId": 2001,
            "name": "Marketplace seller already known",
            "selectedOffer": {
                "offerId": "offer-2001",
                "sellerName": "Known Seller",
                "currentPrice": 20.0,
                "wasPrice": 50.0,
                "isMarketPlaceItem": True,
            },
        },
        [
            json.dumps(
                {
                    "product": {
                        "usItemId": "2001",
                        "buyBox": {
                            "currentPrice": 20.0,
                            "sellerInfo": {"sellerName": "Known Seller"},
                            "freeShipping": True,
                        },
                    }
                }
            )
        ],
    )

    payload = asyncio.run(provider.fetch_product_detail_payload("2001"))
    candidate = provider._candidate_from_item(
        payload,
        ProviderScanRequest(source_key="test", query="2001", max_results=1),
    )

    assert provider.page_calls == 1
    assert candidate is not None
    assert candidate.shipping_status == "free"
    assert candidate.current_price == 20.0


def test_large_next_data_script_is_not_dropped_before_live_buybox_data() -> None:
    from sniperplug.providers import walmart_page_recovery as page_recovery

    parser = page_recovery._StructuredScriptParser()
    payload = json.dumps(
        {
            "padding": "x" * (2 * 1024 * 1024 + 64 * 1024),
            "product": {
                "usItemId": "3001",
                "buyBox": {
                    "currentPrice": 10.0,
                    "sellerInfo": {"sellerName": "Large State Seller"},
                },
            },
        }
    )
    parser.feed('<script id="__NEXT_DATA__" type="application/json">')
    parser.feed(payload)
    parser.feed("</script>")
    parser.close()

    assert len(parser.scripts) == 1
    result = page_recovery.recover_page_identity_from_scripts(
        parser.scripts,
        item_id="3001",
        api_item_price=10.0,
    )
    assert result.proof is not None
    assert result.proof.seller_name == "Large State Seller"


def test_page_recovery_failure_reason_is_preserved_in_terminal_identity_error() -> None:
    from sniperplug.models.candidate import SourceCandidate
    from sniperplug.services.walmart_exact_queue_runtime import _exact_identity_failure_reason

    candidate = SourceCandidate(
        source_key="walmart_exact_detail_queue",
        retailer="Walmart",
        title="Blocked item",
        product_url="https://www.walmart.com/ip/4001",
        direct_product_url="https://www.walmart.com/ip/4001",
        product_id="4001",
        product_id_type="sku",
        sku="4001",
        selected_offer_id="offer-4001",
        variant_attributes={
            "exactDetailPriceProof": "yes",
            "exactDetailItemId": "4001",
            "pageIdentityRecoveryStatus": "no_price_aligned_seller",
            "pageIdentityRecoveryDetail": "matching product existed but seller-bearing buy box was not price aligned",
        },
    )

    reason = _exact_identity_failure_reason(candidate)
    assert reason.startswith("missing seller identity")
    assert "page recovery=no_price_aligned_seller" in reason
    assert "seller-bearing buy box" in reason


class ExactAffiliateRecoveryProvider(FixturePageProvider):
    def __init__(self, detail_payload: dict, exact_search_payload: dict, scripts: list[str] | None = None):
        super().__init__(detail_payload, scripts or [])
        self.exact_search_payload = exact_search_payload
        self.exact_search_calls = 0

    async def _official_exact_search_payload(self, item_id: str) -> dict:
        self.exact_search_calls += 1
        assert str(self.exact_search_payload.get("itemId")) == str(item_id)
        payload = copy.deepcopy(self.exact_search_payload)
        payload["sniperplugOfficialExactSource"] = "affiliate_search_exact_item"
        return payload


def test_missing_detail_seller_uses_exact_affiliate_search_before_product_page() -> None:
    provider = ExactAffiliateRecoveryProvider(
        {
            "itemId": 22395155,
            "name": "Bounty Everyday Disposable Paper Napkins",
            "salePrice": 5.74,
            "wasPrice": 14.49,
            "availableOnline": True,
        },
        {
            "itemId": 22395155,
            "name": "Bounty Everyday Disposable Paper Napkins",
            "salePrice": 5.74,
            "wasPrice": 14.49,
            "sellerName": "Walmart",
            "isMarketPlaceItem": False,
            "availableOnline": True,
        },
    )

    payload = asyncio.run(provider.fetch_product_detail_payload("22395155"))
    candidate = provider._candidate_from_item(
        payload,
        ProviderScanRequest(source_key="test", query="22395155", max_results=1),
    )

    assert provider.exact_search_calls == 1
    assert provider.page_calls == 0
    assert payload["sellerName"] == "Walmart"
    assert candidate is not None
    assert candidate.seller_name == "Walmart"
    assert candidate.variant_attributes["officialExactSource"] == "affiliate_search_exact_item"
    assert any("official Affiliate search" in signal for signal in candidate.signals)


def test_exact_affiliate_search_cannot_accept_relevance_neighbor(monkeypatch) -> None:
    from sniperplug.providers.walmart import WalmartProvider

    provider = WalmartProvider(
        WalmartAffiliateConfig(
            enabled=True,
            consumer_id="test",
            private_key_b64="unused",
        )
    )

    def fake_search(*, query, request, page_size):
        assert query == "2814230491"
        return {
            "items": [
                {
                    "itemId": 999999,
                    "name": "Different DEWALT kit",
                    "salePrice": 180.0,
                    "sellerName": "Wrong Seller",
                }
            ]
        }

    monkeypatch.setattr(provider, "_search", fake_search)

    try:
        provider._exact_search_item_payload("2814230491")
    except WalmartProviderError as exc:
        assert exc.retry_kind == "api_exact_search_no_match"
    else:
        raise AssertionError("exact Affiliate fallback must reject a relevance neighbor")


def test_detail_fallback_exact_search_is_not_repeated_before_page() -> None:
    class AlreadyExactSearchProvider(FixturePageProvider):
        def __init__(self):
            super().__init__(
                {
                    "itemId": 777001,
                    "name": "Exact search still lacks seller",
                    "salePrice": 12.00,
                    "sniperplugOfficialExactSource": "affiliate_search_exact_item",
                },
                [jsonld_product(item_id="777001", price=12.00, seller="Walmart.com")],
            )
            self.exact_search_calls = 0

        async def _official_exact_search_payload(self, item_id: str) -> dict:
            self.exact_search_calls += 1
            raise AssertionError("exact Affiliate fallback must not run twice")

    provider = AlreadyExactSearchProvider()
    payload = asyncio.run(provider.fetch_product_detail_payload("777001"))

    assert provider.exact_search_calls == 0
    assert provider.page_calls == 1
    assert payload["sellerName"] == "Walmart.com"
