from __future__ import annotations

from sniperplug.models.candidate import SourceCandidate
from sniperplug.providers.base import (
    DealProvider,
    ProviderCapability,
    ProviderHealth,
    ProviderScanRequest,
    ProviderScanResult,
    ProviderStatus,
)


class WalmartProvider(DealProvider):
    """Walmart Marketplace/Catalog adapter skeleton.

    Walmart access is partner/marketplace oriented, not a simple public shopping
    feed. This adapter remains disabled until credentials are intentionally wired
    and a live scanner is implemented/tested.
    """

    provider_key = "walmart"
    display_name = "Walmart"
    capabilities = frozenset(
        {
            ProviderCapability.PRODUCT_LOOKUP,
            ProviderCapability.CATEGORY_SCAN,
            ProviderCapability.IMAGE_LOOKUP,
            ProviderCapability.OFFER_CHECK,
            ProviderCapability.MEMBER_PRICING,
        }
    )

    def __init__(self, configured: bool = False):
        self.configured = configured

    async def healthcheck(self) -> ProviderHealth:
        if not self.configured:
            return ProviderHealth(
                provider_key=self.provider_key,
                ok=False,
                status=ProviderStatus.DISABLED,
                message="Disabled: Walmart credentials are not configured.",
            )

        return ProviderHealth(
            provider_key=self.provider_key,
            ok=False,
            status=ProviderStatus.STAGED,
            message="Staged: Walmart credentials are configured, but live scanning is not implemented yet.",
        )

    async def scan(self, request: ProviderScanRequest) -> ProviderScanResult:
        if not self.configured:
            return ProviderScanResult(
                provider_key=self.provider_key,
                candidates=(),
                warnings=("Walmart provider disabled because credentials are not configured.",),
            )

        return ProviderScanResult(
            provider_key=self.provider_key,
            candidates=tuple(self._demo_candidates(request)),
            warnings=("Walmart provider is staged only. Live API scanning is not implemented yet.",),
        )

    def _demo_candidates(self, request: ProviderScanRequest) -> list[SourceCandidate]:
        # No demo candidates are returned here on purpose. Demo/test alerts live
        # in /sniperplug scan_test so provider output never looks like real data.
        return []
