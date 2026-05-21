from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WatchlistSeed:
    """Known high-value watch target used to sharpen monitor plans.

    Seeds are not proof of a deal. They only make future provider scans more
    exact by carrying sought-after model names, identifiers when known, and
    value floors for anomaly scoring.
    """

    category_key: str
    label: str
    watch_terms: tuple[str, ...]
    model_terms: tuple[str, ...] = ()
    skus: tuple[str, ...] = ()
    upcs: tuple[str, ...] = ()
    min_normal_value: float | None = None
    near_zero_trigger_price: float = 1.00
    notes: tuple[str, ...] = ()


WATCHLIST_SEEDS: tuple[WatchlistSeed, ...] = (
    WatchlistSeed(
        category_key="gpus",
        label="RTX 50-series graphics cards",
        watch_terms=("rtx 5090", "rtx 5080", "rtx 5070 ti", "rtx 5070"),
        model_terms=(
            "geforce rtx 5090",
            "geforce rtx 5080",
            "geforce rtx 5070 ti",
            "geforce rtx 5070",
            "inspire 3x oc",
            "gaming trio",
            "suprim",
            "strix",
            "tuf gaming",
            "aorus master",
            "windforce",
        ),
        min_normal_value=549.00,
        notes=("High resale/value category", "Near-zero pricing should route to staff review first"),
    ),
    WatchlistSeed(
        category_key="cpus",
        label="High-demand desktop CPUs",
        watch_terms=("ryzen 9", "ryzen 7 x3d", "intel i9", "intel i7", "threadripper"),
        model_terms=("9950x3d", "9900x3d", "9800x3d", "7950x3d", "14900k", "14700k"),
        min_normal_value=249.00,
        notes=("CPU glitches often look like bundle or checkout mistakes",),
    ),
    WatchlistSeed(
        category_key="ram",
        label="DDR5 memory kits",
        watch_terms=("ddr5 64gb", "ddr5 96gb", "ddr5 128gb", "corsair dominator", "g.skill trident"),
        model_terms=("trident z5", "dominator titanium", "vengeance ddr5", "flare x5"),
        min_normal_value=129.00,
        notes=("Memory kit pricing can break on bundles and multipacks",),
    ),
    WatchlistSeed(
        category_key="ssds",
        label="Large NVMe SSDs",
        watch_terms=("4tb nvme", "8tb nvme", "990 pro", "sn850x", "crucial t705"),
        model_terms=("samsung 990 pro", "wd black sn850x", "crucial t705", "solidigm p44"),
        min_normal_value=199.00,
        notes=("High-value storage is good for bulk/business account anomalies",),
    ),
    WatchlistSeed(
        category_key="apple",
        label="Apple flagship products",
        watch_terms=("iphone 16 pro", "iphone 16 pro max", "macbook pro m4", "ipad pro m4", "apple watch ultra"),
        model_terms=("pro max", "m4 pro", "m4 max", "ultra 2", "cellular unlocked"),
        min_normal_value=399.00,
        notes=("Check seller, carrier locks, renewed/refurb condition, and account-specific pricing",),
    ),
    WatchlistSeed(
        category_key="brand_direct_electronics",
        label="Brand-direct electronics",
        watch_terms=("samsung oled", "lg oled", "sony bravia", "pixel pro", "galaxy ultra"),
        model_terms=("s95d", "s90d", "g4 oled", "c4 oled", "bravia 9", "pixel 9 pro", "s24 ultra", "s25 ultra"),
        min_normal_value=499.00,
        notes=("Brand-direct checkout and promo stacking can create brief anomalies",),
    ),
    WatchlistSeed(
        category_key="sneakers",
        label="Hyped sneaker/streetwear drops",
        watch_terms=("air jordan", "jordan retro", "nike dunk", "yeezy", "ultraboost", "puma suede"),
        model_terms=("retro high", "retro low", "sb dunk", "foam runner", "samba", "campus 00s"),
        min_normal_value=80.00,
        notes=("Member-only, size-specific, and regional pricing must be flagged",),
    ),
    WatchlistSeed(
        category_key="gold_jewelry",
        label="Gold jewelry",
        watch_terms=("14k gold chain", "10k gold chain", "gold bracelet", "solid gold", "diamond pendant"),
        model_terms=("cuban link", "rope chain", "figaro", "franco", "miami cuban", "tennis bracelet"),
        min_normal_value=199.00,
        notes=("Proof needs metal purity, seller, weight if available, and return policy",),
    ),
    WatchlistSeed(
        category_key="motor_oil",
        label="Motor oil and auto fluids",
        watch_terms=("mobil 1", "full synthetic oil", "motor oil case", "5w-30", "0w-20", "rotella"),
        model_terms=("extended performance", "advanced full synthetic", "case pack", "12 quart", "5 quart"),
        min_normal_value=24.00,
        notes=("Bulk/case-pack quantity mistakes matter more than single-bottle discounts",),
    ),
    WatchlistSeed(
        category_key="tools",
        label="Power tools and batteries",
        watch_terms=("dewalt battery", "milwaukee m18", "makita kit", "ryobi combo", "ego battery"),
        model_terms=("m18 fuel", "flexvolt", "xr", "brushless kit", "battery 2 pack", "combo kit"),
        min_normal_value=99.00,
        notes=("Kit and multipack glitches are high value",),
    ),
    WatchlistSeed(
        category_key="business_bulk",
        label="Business and bulk supplies",
        watch_terms=("case pack", "bulk pack", "business price", "multi pack", "pallet"),
        model_terms=("case of", "pack of 12", "pack of 24", "commercial", "office bundle"),
        min_normal_value=75.00,
        notes=("Business-account pricing can be account-specific and must be labeled",),
    ),
)


def seeds_for_category(category_key: str) -> tuple[WatchlistSeed, ...]:
    normalized = category_key.strip().lower()
    return tuple(seed for seed in WATCHLIST_SEEDS if seed.category_key == normalized)


def best_seed_for_category(category_key: str) -> WatchlistSeed | None:
    matches = seeds_for_category(category_key)
    return matches[0] if matches else None


def seeded_terms(category_key: str, existing_terms: tuple[str, ...]) -> tuple[str, ...]:
    terms: list[str] = list(existing_terms)
    for seed in seeds_for_category(category_key):
        terms.extend(seed.watch_terms)
        terms.extend(seed.model_terms)
    return tuple(dict.fromkeys(term.strip().lower() for term in terms if term.strip()))
