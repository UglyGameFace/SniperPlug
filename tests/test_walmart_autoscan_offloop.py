from __future__ import annotations

import asyncio
import threading
from pathlib import Path

from sniperplug.providers.base import ProviderScanResult
from sniperplug.services import walmart_autoscan_offloop as offloop
from sniperplug.services.walmart_exact_price_enrichment import (
    ExactPriceEnrichmentResult,
)


ROOT = Path(__file__).resolve().parents[1]
AUTOSCAN = (
    ROOT / "sniperplug/services/autoscan_observed_price_memory.py"
).read_text(encoding="utf-8")


class _FakeProvider:
    def __init__(self) -> None:
        self.thread_id: int | None = None
        self.request = None

    async def scan(self, request):
        self.thread_id = threading.get_ident()
        self.request = request
        return ProviderScanResult(
            provider_key="walmart",
            candidates=(),
            warnings=(),
            page=request.page,
            page_size=request.max_results,
        )


def test_autoscan_provider_and_candidate_parser_run_on_worker_thread(monkeypatch) -> None:
    provider = _FakeProvider()
    main_thread_id = threading.get_ident()
    monkeypatch.setattr(offloop.provider_registry, "get", lambda key: provider)

    result = asyncio.run(
        offloop.run_walmart_autoscan_scan_off_event_loop(
            "clearance",
            2,
            25,
            "price",
            "ascending",
            "global_catalog_autoscan",
        )
    )

    assert result.provider_key == "walmart"
    assert provider.thread_id is not None
    assert provider.thread_id != main_thread_id
    assert provider.request.query == "clearance"
    assert provider.request.page == 2
    assert provider.request.metadata["autoscan_lightweight"] == "yes"
    assert provider.request.metadata["off_event_loop"] == "yes"


def test_foreground_exact_enrichment_runs_on_worker_thread(monkeypatch) -> None:
    main_thread_id = threading.get_ident()
    observed: dict[str, object] = {}

    async def fake_enrichment(candidates, **kwargs):
        observed["thread_id"] = threading.get_ident()
        observed["candidates"] = list(candidates)
        observed["kwargs"] = kwargs
        return ExactPriceEnrichmentResult(candidates=list(candidates))

    monkeypatch.setattr(offloop, "enrich_walmart_exact_prices", fake_enrichment)

    result = asyncio.run(
        offloop.enrich_walmart_exact_prices_off_event_loop(
            [],
            provider=object(),
            limit=24,
            concurrency=4,
            timeout_seconds=8.0,
            min_discount=50,
        )
    )

    assert result.candidates == []
    assert observed["thread_id"] != main_thread_id
    assert observed["kwargs"] == {
        "provider": observed["kwargs"]["provider"],
        "limit": 24,
        "concurrency": 4,
        "timeout_seconds": 8.0,
        "min_discount": 50,
    }


def test_global_autoscan_uses_offloop_search_and_enrichment_paths() -> None:
    assert "run_walmart_autoscan_scan_off_event_loop(" in AUTOSCAN
    assert "enrich_walmart_exact_prices_off_event_loop(" in AUTOSCAN
    assert "deal_scanner.run_walmart_scan(" not in AUTOSCAN
    assert "exact_prices = await enrich_walmart_exact_prices(" not in AUTOSCAN
