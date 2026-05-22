from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum

from sniperplug.models.local_stock import LocalStockRequest, LocalStockResult


class LocalStockProviderStatus(str, Enum):
    DISABLED = "disabled"
    READY = "ready"
    ERROR = "error"


@dataclass(frozen=True)
class LocalStockProviderHealth:
    retailer_key: str
    ok: bool
    message: str
    status: LocalStockProviderStatus = LocalStockProviderStatus.ERROR


class LocalStockProvider(ABC):
    """Base class for SKU + ZIP local inventory/price providers.

    Provider rules:
    - Return LocalStockOffer objects only.
    - Do not post Discord alerts directly.
    - Do not invent stock counts, aisle, bay, or local prices.
    - If a retailer blocks/does not provide local inventory, return warnings.
    """

    retailer_key: str
    display_name: str

    @abstractmethod
    async def healthcheck(self) -> LocalStockProviderHealth:
        """Return whether this local stock provider is configured."""

    @abstractmethod
    async def check_stock(self, request: LocalStockRequest) -> LocalStockResult:
        """Check SKU/product stock around a ZIP code."""


class LocalStockProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, LocalStockProvider] = {}

    def register(self, provider: LocalStockProvider) -> None:
        self._providers[provider.retailer_key] = provider

    def get(self, retailer_key: str) -> LocalStockProvider | None:
        return self._providers.get(retailer_key)

    def list_keys(self) -> list[str]:
        return sorted(self._providers.keys())

    async def healthchecks(self) -> list[LocalStockProviderHealth]:
        return [await provider.healthcheck() for provider in self._providers.values()]


local_stock_registry = LocalStockProviderRegistry()
