from __future__ import annotations

from typing import Any

from sniperplug.cogs import deal_scanner
from sniperplug.cogs.deal_scanner import HuntPreset
from sniperplug.models.candidate import SourceCandidate
from sniperplug.providers.base import ProviderScanRequest, ProviderScanResult
from sniperplug.providers.cached_walmart import CachedWalmartProvider
from sniperplug.providers.registry import provider_registry


PAGES_PER_QUERY = 3
RESULTS_PER_PAGE = 25
RESALE_HUNT_KEY = "resale"


EXPANDED_PRESETS: dict[str, HuntPreset] = {
    "glitch": HuntPreset(
        "glitch",
        "Glitch Hunt",
        "🚨",
        "Broad Walmart markdown hunting across high-resale categories.",
        (
            "clearance",
            "rollback",
            "price drop",
            "gaming monitor",
            "4k tv",
            "oled tv",
            "laptop clearance",
            "ssd",
            "lego clearance",
            "air fryer clearance",
            "vacuum clearance",
            "patio clearance",
            "tool clearance",
            "open box",
            "restored",
            "like new",
            "refurbished",
        ),
        70,
    ),
    RESALE_HUNT_KEY: HuntPreset(
        RESALE_HUNT_KEY,
        "Resale Hunt",
        "♻️",
        "Open-box, restored, refurbished, and like-new leads across flip-friendly categories.",
        (
            "restored laptop",
            "restored iphone",
            "restored tv",
            "refurbished nintendo switch",
            "refurbished laptop",
            "refurbished ipad",
            "open box power tool",
            "open box electronics",
            "like new electronics",
            "restored monitor",
            "restored gaming pc",
            "restored headphones",
            "restored vacuum",
            "refurbished dyson",
        ),
        25,
    ),
    "tech": HuntPreset(
        "tech",
        "Tech & Gaming",
        "🎮",
        "Monitors, TVs, PC parts, storage, gaming gear, and restored electronics.",
        (
            "gaming monitor",
            "oled tv",
            "4k tv",
            "wireless earbuds",
            "ssd",
            "gaming headset",
            "gaming keyboard",
            "gaming mouse",
            "laptop clearance",
            "tablet clearance",
            "router",
            "external hard drive",
            "restored monitor",
            "open box electronics",
            "like new electronics",
        ),
        45,
    ),
    "essentials": HuntPreset(
        "essentials",
        "Daily Essentials",
        "🧼",
        "Detergent, cleaning, paper goods, toiletries, and household restocks.",
        (
            "laundry detergent",
            "dish detergent",
            "paper towels",
            "toilet paper",
            "trash bags",
            "cleaning supplies",
            "disinfecting wipes",
            "razor",
            "shampoo",
            "body wash",
            "toothpaste",
            "diapers",
            "coupon",
            "walmart cash household",
        ),
        20,
    ),
    "home": HuntPreset(
        "home",
        "Home & Kitchen",
        "🏠",
        "Small appliances, kitchen, furniture, patio, storage, and seasonal home markdowns.",
        (
            "air fryer",
            "coffee maker",
            "vacuum",
            "robot vacuum",
            "patio furniture",
            "storage cabinet",
            "mattress",
            "office chair",
            "kitchen appliance clearance",
            "cookware clearance",
            "bedding clearance",
            "seasonal clearance",
        ),
        35,
    ),
    "toys": HuntPreset(
        "toys",
        "Toys & Gifts",
        "🧸",
        "LEGO, games, collectibles, outdoor toys, and giftable clearance.",
        (
            "lego",
            "lego clearance",
            "toys clearance",
            "board game clearance",
            "pokemon",
            "collectible",
            "barbie",
            "nerf",
            "outdoor toy",
            "scooter",
            "ride on toy",
            "video game clearance",
        ),
        35,
    ),
    "auto_tools": HuntPreset(
        "auto_tools",
        "Auto & Tools",
        "🛠️",
        "Car care, oil, tools, garage, DIY, and resale-friendly hardware finds.",
        (
            "motor oil",
            "tire inflator",
            "socket set",
            "tool set",
            "drill",
            "impact driver",
            "battery charger",
            "car wash",
            "turtle wax",
            "meguiars",
            "armor all",
            "garage storage",
            "pressure washer clearance",
        ),
        25,
    ),
}


def install_walmart_discovery_expansion() -> None:
    """Check more official Walmart API results while keeping strict proof rules.

    This installer is already called on startup, so it also wires the DB-backed
    Walmart cache without touching bot startup registration.
    """
    if not getattr(deal_scanner, "_sniperplug_walmart_discovery_expanded", False):
        existing_resale = deal_scanner.HUNT_PRESETS.get(RESALE_HUNT_KEY)
        expanded = dict(EXPANDED_PRESETS)
        if existing_resale is not None:
            expanded[RESALE_HUNT_KEY] = existing_resale
        deal_scanner.HUNT_PRESETS.clear()
        deal_scanner.HUNT_PRESETS.update(expanded)
        deal_scanner.run_preset_hunt = run_expanded_preset_hunt
        deal_scanner._sniperplug_walmart_discovery_expanded = True

    if getattr(deal_scanner, "_sniperplug_cached_walmart_runtime_installed", False):
        return
    deal_scanner._sniperplug_original_run_walmart_scan = deal_scanner.run_walmart_scan
    deal_scanner._sniperplug_original_deal_scanner_init = deal_scanner.DealScannerCog.__init__
    deal_scanner.DealScannerCog.__init__ = _patched_deal_scanner_init
    deal_scanner.run_walmart_scan = run_cached_walmart_scan
    deal_scanner._sniperplug_cached_walmart_runtime_installed = True


def _patched_deal_scanner_init(self: Any, bot: Any) -> None:
    original_init = getattr(deal_scanner, "_sniperplug_original_deal_scanner_init")
    original_init(self, bot)
    deal_scanner._sniperplug_runtime_db = getattr(bot, "db", None)


async def run_cached_walmart_scan(query: str, page: int, max_results: int, sort_value: str | None, order_value: str | None, requested_by: str) -> ProviderScanResult:
    db = getattr(deal_scanner, "_sniperplug_runtime_db", None)
    original_scan = getattr(deal_scanner, "_sniperplug_original_run_walmart_scan", None)
    provider = provider_registry.get("walmart")
    if db is None or provider is None:
        if original_scan is not None:
            return await original_scan(query, page, max_results, sort_value, order_value, requested_by)
        return ProviderScanResult(provider_key="walmart", candidates=(), warnings=("Walmart cache runtime is not ready yet.",))
    cached_provider = provider if isinstance(provider, CachedWalmartProvider) else CachedWalmartProvider(db, provider)
    return await cached_provider.scan(
        ProviderScanRequest(
            source_key="walmart",
            query=query.strip(),
            max_results=max_results,
            page=page,
            sort=sort_value,
            order=order_value,
            metadata={"requested_by": requested_by},
        )
    )


async def run_expanded_preset_hunt(preset: HuntPreset, requested_by: str) -> tuple[list[deal_scanner.DealCard], int, int, list[str], int]:
    all_candidates: list[SourceCandidate] = []
    warnings: list[str] = []
    pages_checked = 0
    for query in preset.queries:
        for page in range(1, PAGES_PER_QUERY + 1):
            result = await deal_scanner.run_walmart_scan(query, page, RESULTS_PER_PAGE, None, None, requested_by)
            pages_checked += 1
            all_candidates.extend(result.candidates)
            warnings.extend(w for w in result.warnings if w not in warnings)
            if not result.has_next_page:
                break
    aggregate = ProviderScanResult(
        provider_key="walmart",
        candidates=tuple(deal_scanner.dedupe_candidates(all_candidates)),
        warnings=tuple(warnings),
        page=1,
        page_size=len(all_candidates),
        start_index=1,
        has_next_page=True,
    )
    fallback_chain = tuple(x for x in (preset.min_discount, *deal_scanner.PRESET_FALLBACK_DISCOUNTS, 0) if x <= preset.min_discount)
    cards, shown_discount = deal_scanner.cards_with_fallback(aggregate, preset.min_discount, alerts_only=False, fallback_discounts=fallback_chain)
    cards.sort(key=lambda card: (card.discount, card.score), reverse=True)
    return cards, pages_checked, len(all_candidates), warnings, shown_discount
