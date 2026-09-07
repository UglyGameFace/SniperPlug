from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from sniperplug.providers.base import ProviderScanRequest
from sniperplug.providers.walmart import WalmartAffiliateConfig, WalmartProvider
from sniperplug.services import walmart_exact_queue_bulk_runtime as bulk_runtime
from sniperplug.services.walmart_exact_queue_health import WalmartExactQueueHealth
from sniperplug.services.walmart_fresh_discovery_proof import (
    build_fresh_discovery_offer_snapshot,
)
from sniperplug.services.walmart_metadata_install import install_walmart_product_metadata


class FakeDatabase:
    def __init__(self, conn: object) -> None:
        self.conn = conn

    def require_conn(self):
        return self.conn


def provider_with_metadata() -> WalmartProvider:
    provider = WalmartProvider(
        WalmartAffiliateConfig(
            enabled=True,
            consumer_id="test",
            private_key_b64="unused",
        )
    )
    install_walmart_product_metadata(provider)
    return provider


def candidate_from(provider: WalmartProvider, payload: dict):
    return provider._candidate_from_item(
        payload,
        request=ProviderScanRequest(source_key="fixture", query="fixture", max_results=1),
    )


def test_merchant_info_alias_restores_safe_first_party_fresh_proof() -> None:
    provider = provider_with_metadata()
    candidate = candidate_from(
        provider,
        {
            "itemId": 910001,
            "name": "Modern merchant identity",
            "selectedOffer": {
                "offerId": "offer-910001",
                "currentPrice": 19.99,
                "wasPrice": 79.99,
                "merchantInfo": {
                    "merchantName": "Walmart.com",
                    "merchantId": "F55CDC31AB754BB68FE0B39041159D63",
                },
            },
            "availableOnline": True,
        },
    )

    assert candidate is not None
    assert candidate.seller_name == "Walmart.com"
    assert candidate.selected_offer_id == "offer-910001"
    assert candidate.variant_attributes["selectedOfferSeller"] == "Walmart.com"
    assert candidate.variant_attributes["selectedOfferMarketplace"] == "no"
    assert candidate.current_price == 19.99
    assert build_fresh_discovery_offer_snapshot(candidate)


def test_scalar_marketplace_seller_reapplies_delivered_price_guard() -> None:
    provider = provider_with_metadata()
    candidate = candidate_from(
        provider,
        {
            "itemId": 910002,
            "name": "Marketplace scalar seller",
            "selectedOffer": {
                "offerId": "offer-910002",
                "currentPrice": 20.00,
                "wasPrice": 80.00,
                "seller": "Exact Marketplace LLC",
                "isMarketplace": True,
                "shippingCost": 5.00,
            },
            "availableOnline": True,
        },
    )

    assert candidate is not None
    assert candidate.seller_name == "Exact Marketplace LLC"
    assert candidate.variant_attributes["selectedOfferMarketplace"] == "yes"
    assert candidate.item_price == 20.00
    assert candidate.shipping_cost == 5.00
    assert candidate.shipping_status == "paid"
    assert candidate.delivered_price == 25.00
    assert candidate.current_price == 25.00
    assert candidate.api_current_price == 25.00
    assert build_fresh_discovery_offer_snapshot(candidate)


def test_sibling_offer_cannot_donate_missing_selected_seller() -> None:
    provider = provider_with_metadata()
    candidate = candidate_from(
        provider,
        {
            "itemId": 910003,
            "name": "Selected offer has no seller",
            "selectedOffer": {
                "offerId": "offer-910003",
                "currentPrice": 15.00,
                "wasPrice": 60.00,
            },
            "offers": [
                {
                    "offerId": "wrong-sibling",
                    "currentPrice": 15.00,
                    "sellerName": "Walmart.com",
                }
            ],
            "availableOnline": True,
        },
    )

    assert candidate is not None
    assert candidate.seller_name in {None, ""}
    assert not candidate.variant_attributes.get("selectedOfferSeller")
    assert build_fresh_discovery_offer_snapshot(candidate) == ""


def test_explicit_first_party_flag_can_supply_missing_walmart_name() -> None:
    provider = provider_with_metadata()
    candidate = candidate_from(
        provider,
        {
            "itemId": 910004,
            "name": "Explicit first party",
            "selectedOffer": {
                "offerId": "offer-910004",
                "currentPrice": 25.00,
                "wasPrice": 100.00,
                "isWalmartSeller": True,
            },
            "availableOnline": True,
        },
    )

    assert candidate is not None
    assert candidate.seller_name == "Walmart"
    assert candidate.variant_attributes["selectedOfferMarketplace"] == "no"
    assert build_fresh_discovery_offer_snapshot(candidate)


def test_paced_probe_does_not_reclaim_known_page_retry_lane(monkeypatch) -> None:
    async def run() -> None:
        captured: dict[str, int] = {}
        cleanup_calls = 0

        async def noop_schema(_db) -> None:
            return None

        async def fake_maintenance(_db):
            return SimpleNamespace(quarantined=0, rearmed=0)

        async def fake_pressure(_db, *, cap):
            assert cap == bulk_runtime.EXACT_WORKER_PRESSURE_CAP
            return WalmartExactQueueHealth(due_now=12, recovery_due_now=12)

        async def fake_claim(_conn, *, now, limit, page_recovery_claim_cap):
            captured["cap"] = page_recovery_claim_cap
            captured["limit"] = limit
            return []

        async def fake_cleanup(_conn, *, now):
            nonlocal cleanup_calls
            cleanup_calls += 1
            return SimpleNamespace(deleted=0, skipped_noop_write=True)

        monkeypatch.setattr(bulk_runtime, "ensure_exact_runtime_schema_once", noop_schema)
        monkeypatch.setattr(bulk_runtime, "maintain_terminal_identity_rows_bounded", fake_maintenance)
        monkeypatch.setattr(bulk_runtime, "load_walmart_exact_queue_pressure", fake_pressure)
        monkeypatch.setattr(bulk_runtime, "claim_due_rows_batched", fake_claim)
        monkeypatch.setattr(bulk_runtime, "maybe_prune_walmart_exact_queue_bounded", fake_cleanup)
        monkeypatch.setattr(bulk_runtime, "_page_challenge_circuit_remaining_seconds", lambda _provider: 0)
        monkeypatch.setattr(bulk_runtime, "_page_challenge_circuit_open", lambda _provider: False)
        monkeypatch.setattr(bulk_runtime, "page_html_probe_remaining_seconds", lambda _provider: 41)

        result = await bulk_runtime.process_actionable_walmart_exact_queue_batch(
            FakeDatabase(object()),
            provider=object(),
            limit=8,
            concurrency=2,
        )

        assert captured["cap"] == 0
        assert cleanup_calls == 1
        assert result.pending_total == 12

    asyncio.run(run())


def test_queue_cleanup_is_idle_only_in_exact_worker_source() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "sniperplug/services/walmart_exact_queue_bulk_runtime.py"
    ).read_text(encoding="utf-8")
    idle_block = source.split("if not claims:", 1)[1].split("semaphore =", 1)[0]
    busy_block = source.split("semaphore =", 1)[1]

    assert "maybe_prune_walmart_exact_queue_bounded" in idle_block
    assert "maybe_prune_walmart_exact_queue_bounded" not in busy_block
