from __future__ import annotations

from typing import Any

from sniperplug.providers.base import (
    DealProvider,
    ProviderHealth,
    ProviderScanRequest,
    ProviderScanResult,
)
from sniperplug.services.walmart_request_coordinator import (
    EXACT_PRIORITY,
    SEARCH_PRIORITY,
    walmart_request_slot,
)


class CoordinatedWalmartProvider(DealProvider):
    """Request-level concurrency and priority wrapper for every Walmart caller.

    The previous global lock covered entire catalog/manual jobs. A slow four-page
    catalog pass could therefore block exact verification for minutes. This
    wrapper limits individual HTTP calls instead: exact detail gets priority,
    while catalog and manual search remain bounded without monopolizing the
    provider for the duration of a whole job.
    """

    provider_key = "walmart"

    def __init__(self, delegate: DealProvider) -> None:
        self.delegate = delegate
        self.display_name = getattr(delegate, "display_name", "Walmart")
        self.capabilities = getattr(delegate, "capabilities", frozenset())

    @property
    def inner(self) -> Any:
        return getattr(self.delegate, "inner", self.delegate)

    @property
    def config(self) -> Any:
        return getattr(self.delegate, "config", None)

    async def healthcheck(self) -> ProviderHealth:
        return await self.delegate.healthcheck()

    async def scan(self, request: ProviderScanRequest) -> ProviderScanResult:
        async with walmart_request_slot(priority=SEARCH_PRIORITY):
            return await self.delegate.scan(request)

    async def fetch_product_detail_payload(self, item_id: str) -> dict:
        fetcher = getattr(self.delegate, "fetch_product_detail_payload", None)
        if not callable(fetcher):
            raise RuntimeError("Walmart provider does not support exact item detail")
        async with walmart_request_slot(priority=EXACT_PRIORITY):
            return await fetcher(item_id)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.delegate, name)
