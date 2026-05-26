from __future__ import annotations

from sniperplug.cogs import deal_scanner
from sniperplug.cogs.deal_scanner import HuntPreset
from sniperplug.models.candidate import SourceCandidate
from sniperplug.providers.base import ProviderScanResult


PAGES_PER_QUERY = 3
RESULTS_PER_PAGE = 25


EXPANDED_PRESETS: dict[str, HuntPreset] = {
    "glitch": HuntPreset("glitch", "Glitch Hunt", "🚨", "Broad Walmart markdown hunting across high-resale categories.", ("clearance", "rollback", "price drop", "gaming monitor", "4k tv", "oled tv", "laptop clearance", "ssd", "lego clearance", "air fryer clearance", "vacuum clearance", "patio clearance", "tool clearance", "open box", "restored", "like new", "refurbished"), 70),
    "tech": HuntPreset("tech", "Tech & Gaming", "🎮", "Monitors, TVs, PC parts, storage, gaming gear, and restored electronics.", ("gaming monitor", "oled tv", "4k tv", "wireless earbuds", "ssd", "gaming headset", "gaming keyboard", "gaming mouse", "laptop clearance", "tablet clearance", "router", "external hard drive", "restored monitor", "open box electronics", "like new electronics"), 45),
    "essentials": HuntPreset("essentials", "Daily Essentials", "🧼", "Detergent, cleaning, paper goods, toiletries, and household restocks.", ("laundry detergent", "dish detergent", "paper towels", "toilet paper", "trash bags", "cleaning supplies", "disinfecting wipes", "razor", "shampoo", "body wash", "toothpaste", "diapers", "coupon", "walmart cash household"), 20),
    "home": HuntPreset("home", "Home & Kitchen", "🏠", "Small appliances, kitchen, furniture, patio, storage, and seasonal home markdowns.", ("air fryer", "coffee maker", "vacuum", "robot vacuum", "patio furniture", "storage cabinet", "mattress", "office chair", "kitchen appliance clearance", "cookware clearance", "bedding clearance", "seasonal clearance"), 35),
    "toys": HuntPreset("toys", "Toys & Gifts", "🧸", "LEGO, games, collectibles, outdoor toys, and giftable clearance.", ("lego", "lego clearance", "toys clearance", "board game clearance", "pokemon", "collectible", "barbie", "nerf", "outdoor toy", "scooter", "ride on toy", "video game clearance"), 35),
    "auto_tools": HuntPreset("auto_tools", "Auto & Tools", "🛠️", "Car care, oil, tools, garage, DIY, and resale-friendly hardware finds.", ("motor oil", "tire inflator", "socket set", "tool set", "drill", "impact driver", "battery charger", "car wash", "turtle wax", "meguiars", "armor all", "garage storage", "pressure washer clearance"), 25),
}


def install_walmart_discovery_expansion() -> None:
    """Check more official Walmart API results while keeping strict proof rules."""
    if getattr(deal_scanner, "_sniperplug_walmart_discovery_expanded", False):
        return
    deal_scanner.HUNT_PRESETS.clear()
    deal_scanner.HUNT_PRESETS.update(EXPANDED_PRESETS)
    deal_scanner.run_preset_hunt = run_expanded_preset_hunt
    deal_scanner._sniperplug_walmart_discovery_expanded = True


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
    aggregate = ProviderScanResult(provider_key="walmart", candidates=tuple(deal_scanner.dedupe_candidates(all_candidates)), warnings=tuple(warnings), page=1, page_size=len(all_candidates), start_index=1, has_next_page=True)
    fallback_chain = tuple(x for x in (preset.min_discount, *deal_scanner.PRESET_FALLBACK_DISCOUNTS, 0) if x <= preset.min_discount)
    cards, shown_discount = deal_scanner.cards_with_fallback(aggregate, preset.min_discount, alerts_only=False, fallback_discounts=fallback_chain)
    cards.sort(key=lambda card: (card.discount, card.score), reverse=True)
    return cards, pages_checked, len(all_candidates), warnings, shown_discount
