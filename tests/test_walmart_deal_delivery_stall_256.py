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


ROOT = Path(__file__).resolve().parents[1]


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


def test_marketplace_alias_without_shipping_stays_fail_closed() -> None:
    provider = provider_with_metadata()
    candidate = candidate_from(
        provider,
        {
            "itemId": 910005,
            "name": "Marketplace shipping unknown",
            "selectedOffer": {
                "offerId": "offer-910005",
                "currentPrice": 20.00,
                "wasPrice": 80.00,
                "merchantInfo": {
                    "merchantName": "Exact Marketplace LLC",
                    "merchantId": "seller-910005",
                    "isMarketplace": True,
                },
            },
            "availableOnline": True,
        },
    )

    assert candidate is not None
    assert candidate.seller_name == "Exact Marketplace LLC"
    assert candidate.variant_attributes["selectedOfferMarketplace"] == "yes"
    assert candidate.item_price == 20.00
    assert candidate.shipping_status == "unknown"
    assert candidate.current_price is None
    assert candidate.api_current_price is None
    assert build_fresh_discovery_offer_snapshot(candidate) == ""


def test_contradictory_marketplace_flag_wins_fail_closed_over_walmart_name() -> None:
    provider = provider_with_metadata()
    candidate = candidate_from(
        provider,
        {
            "itemId": 910006,
            "name": "Contradictory marketplace identity",
            "selectedOffer": {
                "offerId": "offer-910006",
                "currentPrice": 20.00,
                "wasPrice": 80.00,
                "seller": "Walmart.com",
                "isMarketplace": True,
            },
            "availableOnline": True,
        },
    )

    assert candidate is not None
    assert candidate.seller_name == "Walmart.com"
    assert candidate.variant_attributes["selectedOfferMarketplace"] == "yes"
    assert candidate.current_price is None
    assert candidate.api_current_price is None
    assert build_fresh_discovery_offer_snapshot(candidate) == ""


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

        monkeypatch.setattr(bulk_runtime, "ensure_exact_runtime_schema_once", noop_schema)
        monkeypatch.setattr(bulk_runtime, "maintain_terminal_identity_rows_bounded", fake_maintenance)
        monkeypatch.setattr(bulk_runtime, "load_walmart_exact_queue_pressure", fake_pressure)
        monkeypatch.setattr(bulk_runtime, "claim_due_rows_batched", fake_claim)
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
        assert captured["limit"] == 8
        assert result.pending_total == 12

    asyncio.run(run())


def test_all_walmart_retention_is_after_fanout_and_bounded() -> None:
    bulk_runtime_source = (
        ROOT / "sniperplug/services/walmart_exact_queue_bulk_runtime.py"
    ).read_text(encoding="utf-8")
    enqueue_source = (
        ROOT / "sniperplug/services/walmart_exact_verification_queue_bulk.py"
    ).read_text(encoding="utf-8")
    runner_source = (
        ROOT / "sniperplug/cogs/global_auto_scan_runner.py"
    ).read_text(encoding="utf-8")
    fanout_source = (
        ROOT / "sniperplug/services/walmart_global_deal_fanout_bulk.py"
    ).read_text(encoding="utf-8")
    legacy_fanout_source = (
        ROOT / "sniperplug/services/walmart_global_deal_fanout.py"
    ).read_text(encoding="utf-8")

    assert "maybe_prune_walmart_exact_queue_bounded" not in bulk_runtime_source
    assert "maybe_prune_walmart_exact_verification_queue" not in enqueue_source

    legacy_schema = legacy_fanout_source.split(
        "async def ensure_global_deal_event_tables", 1
    )[1].split("async def prune_global_deal_events_bounded", 1)[0]
    bulk_schema = fanout_source.split(
        "async def ensure_global_deal_event_tables_once", 1
    )[1].split("async def maybe_prune_global_deal_event_storage_bounded", 1)[0]
    assert "DELETE FROM" not in legacy_schema
    assert "DELETE FROM" not in bulk_schema
    assert "LIMIT ?" in legacy_fanout_source.split(
        "async def prune_global_deal_events_bounded", 1
    )[1].split("async def maybe_prune_global_deal_events_after_fanout", 1)[0]
    assert "FANOUT_CLEANUP_BATCH_SIZE = 100" in fanout_source

    exact_worker = runner_source.split(
        "async def _walmart_exact_verification_worker", 1
    )[1].split("async def _event_loop_watchdog", 1)[0]
    fanout_index = exact_worker.index("await self._fanout_external_verified_events()")
    cleanup_index = exact_worker.index("await self._run_walmart_post_fanout_maintenance()")
    assert fanout_index < cleanup_index
    assert "maybe_prune_global_deal_event_storage_bounded" in runner_source
    assert "maybe_prune_walmart_exact_queue_bounded" in runner_source
    assert "if self._catalog_active or self._reconciliation_active" in runner_source
