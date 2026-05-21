from __future__ import annotations

from sniperplug.models.candidate import SourceCandidate
from sniperplug.providers.base import (
    DealProvider,
    ProviderCapability,
    ProviderHealth,
    ProviderScanRequest,
    ProviderScanResult,
)


class BestBuyProvider(DealProvider):
    """Best Buy Products API adapter skeleton.

    This provider is intentionally disabled unless a BESTBUY_API_KEY is supplied.
    The first implementation only adds configuration/healthcheck safety. Live
    product scanning should be added after API access is approved and tested.
    """

    provider_key = "bestbuy"
    display_name = "Best Buy"
    capabilities = frozenset(
        {
            ProviderCapability.PRODUCT_LOOKUP,
            ProviderCapability.CATEGORY_SCAN,
            ProviderCapability.IMAGE_LOOKUP,
            ProviderCapability.OFFER_CHECK,
        }
    )

    def __init__(self, api_key: str | None):
        self.api_key = api_key.strip() if api_key else None

    async def healthcheck(self) -> ProviderHealth:
        if not self.api_key:
            return ProviderHealth(
                provider_key=self.provider_key,
                ok=False,
                message="Disabled: BESTBUY_API_KEY is not configured.",
            )

        return ProviderHealth(
            provider_key=self.provider_key,
            ok=True,
            message="Configured. Live scan implementation is not enabled yet.",
        )

    async def scan(self, request: ProviderScanRequest) -> ProviderScanResult:
        if not self.api_key:
            return ProviderScanResult(
                provider_key=self.provider_key,
                candidates=(),
                warnings=("Best Buy provider disabled because BESTBUY_API_KEY is not configured.",),
            )

        return ProviderScanResult(
            provider_key=self.provider_key,
            candidates=tuple(self._demo_candidates(request)),
            warnings=(
                "Best Buy live API scanning is not implemented yet. Returned no live retailer data.",
            ),
        )

    def _demo_candidates(self, request: ProviderScanRequest) -> list[SourceCandidate]:
        # No demo candidates are returned here on purpose. Demo/test alerts live
        # in /sniperplug scan_test so provider output never looks like real data.
        return []
