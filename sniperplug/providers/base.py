from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Sequence

from sniperplug.models.candidate import SourceCandidate
from sniperplug.models.local_inventory import LocalInventoryProof, LocalInventoryRequest, make_unsupported_inventory_proof


class ProviderCapability(str, Enum):
    PRODUCT_LOOKUP = "product_lookup"
    CATEGORY_SCAN = "category_scan"
    PRICE_HISTORY = "price_history"
    OFFER_CHECK = "offer_check"
    CART_CHECK = "cart_check"
    IMAGE_LOOKUP = "image_lookup"
    BUSINESS_PRICING = "business_pricing"
    MEMBER_PRICING = "member_pricing"
    LOCAL_INVENTORY = "local_inventory"
    LOCAL_PRICE = "local_price"
    CLEARANCE_SIGNAL = "clearance_signal"


class ProviderStatus(str, Enum):
    DISABLED = "disabled"
    STAGED = "staged"
    READY = "ready"
    ERROR = "error"


@dataclass(frozen=True)
class ProviderHealth:
    provider_key: str
    ok: bool
    message: str
    status: ProviderStatus = ProviderStatus.ERROR


@dataclass(frozen=True)
class ProviderScanRequest:
    """Input for provider scans.

    Providers should only use fields they truly support. Unsupported fields must
    be ignored safely, not guessed into fake behavior.
    """
    source_key: str
    category: str | None = None
    query: str | None = None
    product_ids: tuple[str, ...] = ()
    max_results: int = 25
    page: int = 1
    sort: str | None = None
    order: str | None = None
    # Metadata may include service objects like db for internal cache wiring.
    # Providers must ignore unknown keys safely.
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderScanResult:
    provider_key: str
    candidates: tuple[SourceCandidate, ...]
    warnings: tuple[str, ...] = ()
    total_results: int | None = None
    page: int = 1
    page_size: int | None = None
    start_index: int | None = None
    has_next_page: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


class DealProvider(ABC):
    """Base class for all source-first providers.

    Provider rules:
    - Return SourceCandidate objects only.
    - Do not post Discord alerts directly.
    - Do not use placeholder images.
    - Do not guess product IDs.
    - Do not create candidates from social chatter alone.
    - If a field is unknown, leave it None and add a warning/signal.
    """

    provider_key: str
    display_name: str
    capabilities: frozenset[ProviderCapability]

    @abstractmethod
    async def healthcheck(self) -> ProviderHealth:
        """Return whether this provider is configured and reachable."""

    @abstractmethod
    async def scan(self, request: ProviderScanRequest) -> ProviderScanResult:
        """Scan a source/category/query and return source-found candidates."""

    async def check_local_inventory(self, request: LocalInventoryRequest) -> LocalInventoryProof:
        """Return local inventory/price proof when a provider supports it.

        The default is intentionally unsupported. Individual retailer adapters
        must opt in so SniperPlug never pretends a store has universal inventory
        proof when it does not.
        """
        return make_unsupported_inventory_proof(self.provider_key, request)

    def supports(self, capability: ProviderCapability) -> bool:
        return capability in self.capabilities


def provider_supports_all(provider: DealProvider, capabilities: Sequence[ProviderCapability]) -> bool:
    return all(provider.supports(capability) for capability in capabilities)
