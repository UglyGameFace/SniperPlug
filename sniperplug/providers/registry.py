from __future__ import annotations

from dataclasses import dataclass

from sniperplug.providers.base import DealProvider, ProviderHealth, ProviderStatus


@dataclass
class ProviderRegistry:
    providers: dict[str, DealProvider]

    def __init__(self) -> None:
        self.providers = {}

    def register(self, provider: DealProvider) -> None:
        if provider.provider_key in self.providers:
            raise ValueError(f"Provider already registered: {provider.provider_key}")
        self.providers[provider.provider_key] = provider

    def get(self, provider_key: str) -> DealProvider | None:
        return self.providers.get(provider_key)

    def list_keys(self) -> list[str]:
        return sorted(self.providers)

    async def healthchecks(self) -> list[ProviderHealth]:
        results: list[ProviderHealth] = []
        for provider in self.providers.values():
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
