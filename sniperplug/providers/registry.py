from __future__ import annotations

from dataclasses import dataclass

from sniperplug.providers.base import DealProvider, ProviderHealth, ProviderStatus
from sniperplug.providers.coordinated_walmart import CoordinatedWalmartProvider
from sniperplug.services.walmart_metadata_install import install_walmart_product_metadata


@dataclass
class ProviderRegistry:
    providers: dict[str, DealProvider]

    def __init__(self) -> None:
        self.providers = {}

    def register(self, provider: DealProvider, *, replace: bool = False) -> None:
        """Register a provider without silently keeping stale runtime objects.

        Production startup explicitly clears the registry before wiring providers.
        ``replace=True`` remains available for tests and controlled runtime reloads.
        Walmart candidates receive the retailer-wide product metadata extractor.
        The request-priority wrapper is applied only to SniperPlug's real cached
        Walmart runtime, never to arbitrary test/plugin providers that merely use
        the normalized key ``walmart``.
        """
        key = str(provider.provider_key or "").strip().lower()
        if not key:
            raise ValueError("Provider key is required")
        if key in self.providers and not replace:
            raise ValueError(f"Provider already registered: {key}")
        if key == "walmart":
            install_walmart_product_metadata(provider)
            if not isinstance(provider, CoordinatedWalmartProvider):
                # Local import avoids widening the registry's import graph while
                # precisely identifying the production provider construction.
                from sniperplug.providers.cached_walmart import CachedWalmartProvider

                if isinstance(provider, CachedWalmartProvider):
                    provider = CoordinatedWalmartProvider(provider)
        self.providers[key] = provider

    def clear(self) -> None:
        """Remove process-global provider instances before a fresh bot startup."""
        self.providers.clear()

    def get(self, provider_key: str) -> DealProvider | None:
        return self.providers.get(str(provider_key or "").strip().lower())

    def list_keys(self) -> list[str]:
        return sorted(self.providers)

    async def healthchecks(self) -> list[ProviderHealth]:
        results: list[ProviderHealth] = []
        for provider in tuple(self.providers.values()):
            try:
                results.append(await provider.healthcheck())
            except Exception as exc:  # defensive guard for bad provider integrations
                results.append(
                    ProviderHealth(
                        provider_key=provider.provider_key,
                        ok=False,
                        status=ProviderStatus.ERROR,
                        message=f"Healthcheck failed: {exc}",
                    )
                )
        return results


provider_registry = ProviderRegistry()
