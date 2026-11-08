from __future__ import annotations

"""Price-sorted Walmart searches for high-value resale items.

Relevance-sorted department searches ("walmart electronics") surface
bestsellers, so a glitched $1 TV never reaches page one. Sorting a specific
product search by price, lowest first, puts the cheapest matching listing at
the top, which is exactly where a price error lands.

Results are discovery only: every item still goes through the exact-detail
queue (item, seller, offer, stock and price proof) before any alert.

Known limit: cheap accessories (cases, mounts) also sort to the top, so this
reliably surfaces near-$1 errors; a partial error (e.g. $40 AirPods Pro) can
be pushed below the first 25 results by accessories cheaper than it.
"""

PRICE_SNIPER_SORT = "price"
PRICE_SNIPER_ORDER = "ascending"

PRICE_SNIPER_QUERIES: tuple[str, ...] = (
    "65 inch 4k smart tv",
    "75 inch 4k smart tv",
    "oled tv",
    "gaming laptop",
    "apple macbook",
    "apple ipad",
    "samsung galaxy tab",
    "unlocked smartphone",
    "iphone",
    "apple iphone",
    "playstation 5 console",
    "xbox series x console",
    "nintendo switch console",
    "meta quest vr headset",
    "apple airpods",
    "apple watch",
    "dyson vacuum",
    "robot vacuum",
    "kitchenaid stand mixer",
    "espresso machine",
    "dewalt power tool kit",
    "milwaukee power tool kit",
    "mens cologne eau de parfum",
    "womens perfume eau de parfum",
    "lego set",
    "pokemon trading card booster box",
    "gaming monitor",
    "portable power station",
    "electric scooter",
)


def price_sniper_query_for_batch(batch_number: int) -> str:
    """Deterministic rotation so a replayed claim repeats the same request."""
    return PRICE_SNIPER_QUERIES[max(0, int(batch_number)) % len(PRICE_SNIPER_QUERIES)]
