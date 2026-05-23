from __future__ import annotations

from dataclasses import dataclass

from sniperplug.models.local_inventory import (
    InventoryProofLevel,
    LocalInventoryProof,
    LocalInventoryRequest,
    clearance_signal_from_price,
)
from sniperplug.providers.base import DealProvider, ProviderCapability, ProviderHealth, ProviderScanRequest, ProviderScanResult, ProviderStatus


@dataclass(frozen=True)
class HomeDepotConfig:
    enabled: bool = True


class HomeDepotProvider(DealProvider):
    """Home Depot clearance/local-proof skeleton.

    This provider intentionally does not scrape Home Depot. Penny/clearance
    pricing is store-specific and often needs in-store scan proof, so this
    adapter starts as a safe proof/routing layer that accepts SKU/store/ZIP plus
    optional observed price and labels the confidence level clearly.
    """

    provider_key = "home_depot"
    display_name = "Home Depot"
    capabilities = frozenset({ProviderCapability.PRODUCT_LOOKUP, ProviderCapability.OFFER_CHECK})

    def __init__(self, config: HomeDepotConfig | None = None) -> None:
        self.config = config or HomeDepotConfig()

    async def healthcheck(self) -> ProviderHealth:
        if not self.config.enabled:
            return ProviderHealth(
                provider_key=self.provider_key,
                ok=False,
                status=ProviderStatus.DISABLED,
                message="Home Depot provider is disabled.",
            )
        return ProviderHealth(
            provider_key=self.provider_key,
            ok=True,
            status=ProviderStatus.STAGED,
            message="Staged: local clearance proof model is loaded, but no official live inventory API is configured.",
        )

    async def scan(self, request: ProviderScanRequest) -> ProviderScanResult:
        return ProviderScanResult(
            provider_key=self.provider_key,
            candidates=(),
            warnings=(
                "Home Depot scan is staged only. Use /sniperplug local_check with SKU/store/ZIP and observed price for proof routing.",
            ),
            page=request.page,
            page_size=0,
        )

    async def check_local_inventory(self, request: LocalInventoryRequest) -> LocalInventoryProof:
        signal = clearance_signal_from_price(request.observed_price)
        warnings = [
            "Home Depot penny/clearance prices are store-specific and must be verified locally.",
            "Do not treat online data alone as a confirmed penny deal.",
        ]

        proof_level = InventoryProofLevel.PRODUCT_SEEN
        availability_text = "Manual SKU/local proof record created. Live store inventory not confirmed."
        if request.store_id or request.zip_code:
            proof_level = InventoryProofLevel.LOCAL_PAGE_SEEN
            availability_text = "Store/ZIP was supplied, but live shelf quantity is not confirmed by an official API."
        if request.observed_price is not None:
            proof_level = InventoryProofLevel.LOCAL_PRICE_SEEN
            availability_text = "Observed local price supplied by staff/user. Needs in-store scan proof before public alert."

        return LocalInventoryProof(
            retailer=self.display_name,
            product_id=request.product_id,
            sku=request.sku or request.product_id,
            upc=request.upc,
            store_id=request.store_id,
            zip_code=request.zip_code,
            local_price=request.observed_price,
            availability_text=availability_text,
            proof_level=proof_level,
            clearance_signal=signal,
            warnings=warnings,
            source="manual_home_depot_clearance_check",
        )
