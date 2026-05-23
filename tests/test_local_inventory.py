import asyncio

from sniperplug.models.local_inventory import (
    ClearanceStage,
    InventoryProofLevel,
    LocalInventoryRequest,
    clearance_signal_from_price,
    make_unsupported_inventory_proof,
)
from sniperplug.providers.home_depot import HomeDepotProvider


def test_clearance_signal_detects_home_depot_style_endings():
    penny = clearance_signal_from_price(3.01)
    final_markdown = clearance_signal_from_price(12.03)
    early_clearance = clearance_signal_from_price(24.06)
    normal = clearance_signal_from_price(19.99)

    assert penny is not None
    assert penny.stage == ClearanceStage.PENNY_CANDIDATE
    assert penny.price_ending == "01"

    assert final_markdown is not None
    assert final_markdown.stage == ClearanceStage.FINAL_MARKDOWN_CANDIDATE
    assert final_markdown.price_ending == "03"

    assert early_clearance is not None
    assert early_clearance.stage == ClearanceStage.CLEARANCE_CANDIDATE
    assert early_clearance.price_ending == "06"

    assert normal is None


def test_home_depot_local_check_routes_observed_price_to_staff_review():
    async def run_check():
        provider = HomeDepotProvider()
        proof = await provider.check_local_inventory(
            LocalInventoryRequest(
                retailer="home_depot",
                sku="123456",
                product_id="123456",
                store_id="6201",
                zip_code="06606",
                observed_price=5.03,
            )
        )
        assert proof.retailer == "Home Depot"
        assert proof.sku == "123456"
        assert proof.store_id == "6201"
        assert proof.proof_level == InventoryProofLevel.LOCAL_PRICE_SEEN
        assert proof.should_staff_review is True
        assert proof.should_public_alert is False
        assert proof.clearance_signal is not None
        assert proof.clearance_signal.stage == ClearanceStage.FINAL_MARKDOWN_CANDIDATE

    asyncio.run(run_check())


def test_unsupported_provider_inventory_proof_is_explicitly_unsupported():
    proof = make_unsupported_inventory_proof(
        "example_store",
        LocalInventoryRequest(retailer="example_store", sku="sku-1", zip_code="06606"),
    )

    assert proof.proof_level == InventoryProofLevel.NONE
    assert proof.should_staff_review is False
    assert proof.should_public_alert is False
    assert "does not support local inventory" in proof.warnings[0]
