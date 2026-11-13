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

import os

PRICE_SNIPER_SORT = "price"
PRICE_SNIPER_ORDER = "ascending"

# Hot list: the queries that actually produce $1 price errors (phones,
# consoles, GPUs). Checked by a hard-capped priority slot that runs every few
# minutes regardless of the broad cursor — see priority_sniper_query_for_batch.
# "graphics card" is deliberately NOT in PRICE_SNIPER_QUERIES: the GPU lane
# exists only here, so the general rotation is not diluted by a query the
# priority slot already covers. Every other hot query is also in the general
# rotation, so priority is an extra check at a different phase, not its only
# coverage.
PRIORITY_SNIPER_QUERIES: tuple[str, ...] = (
    "iphone",
    "apple iphone",
    "playstation 5 console",
    "xbox series x console",
    "nintendo switch console",
    "graphics card",
)

# Priority cadence defaults: one priority search every 5 batches at the fixed
# 60s/batch rate is 12/hour. Both knobs are env-overridable; a bad value
# fails safe to the default instead of disabling the cap or the slot.
_DEFAULT_PRIORITY_EVERY_N_BATCHES = 5
_DEFAULT_PRIORITY_MAX_PER_HOUR = 12


def _priority_env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return value if value >= 1 else default


def priority_sniper_interval_batches(
    *,
    every_n_batches: int | None = None,
    max_per_hour: int | None = None,
) -> int:
    """Batches between priority searches, honouring the per-hour cap.

    The cap binds from below: a caller asking for every batch with a 12/hour
    cap still waits 5 batches, because 60 minutes / 12 = 5. A tighter cap
    stretches the interval further. The interval is structural — at most one
    priority search per batch, and it only ever replaces a sniper slot, never
    a broad route — so broad coverage cannot be starved by priority.
    """

    every = _DEFAULT_PRIORITY_EVERY_N_BATCHES if every_n_batches is None else int(every_n_batches)
    cap = _DEFAULT_PRIORITY_MAX_PER_HOUR if max_per_hour is None else int(max_per_hour)
    if every < 1:
        every = _DEFAULT_PRIORITY_EVERY_N_BATCHES
    if cap < 1:
        cap = _DEFAULT_PRIORITY_MAX_PER_HOUR
    min_interval = -(-60 // cap)  # ceil(60 / max_per_hour) at 1 batch/minute
    return max(every, min_interval)


def priority_sniper_query_for_batch(
    batch_number: int,
    *,
    every_n_batches: int | None = None,
    max_per_hour: int | None = None,
    avoid: str | None = None,
) -> str | None:
    """Hot-list query for this batch, or None when no priority slot is due.

    Derived from the batch number only, like the general rotation, so a
    retried or replayed claim repeats exactly the same request and no new
    persistent state is needed. Batch 0 is a priority batch: the hot list
    starts getting checked immediately, not after the first interval.

    ``avoid`` is the query the batch's rotation slot already covers. Five of
    the six hot queries are also rotation queries, so without it a batch
    could spend two of its four searches on the same query. When the selected
    hot query collides, the next hot query is used instead — still
    deterministic, still one priority search, and the skipped hot query is
    picked up on the next priority firing.
    """

    interval = priority_sniper_interval_batches(
        every_n_batches=every_n_batches,
        max_per_hour=max_per_hour,
    )
    batch = max(0, int(batch_number))
    if batch % interval != 0:
        return None
    index = (batch // interval) % len(PRIORITY_SNIPER_QUERIES)
    query = PRIORITY_SNIPER_QUERIES[index]
    if avoid is not None and query == avoid:
        query = PRIORITY_SNIPER_QUERIES[(index + 1) % len(PRIORITY_SNIPER_QUERIES)]
    return query


def priority_snipers_before_batch(
    batch_number: int,
    *,
    every_n_batches: int | None = None,
    max_per_hour: int | None = None,
) -> int:
    """Priority slots consumed by batches [0, batch_number).

    The request planner uses this to advance the general rotation only by the
    rotation slots actually spent, so a displaced rotation query is deferred,
    never skipped.
    """

    interval = priority_sniper_interval_batches(
        every_n_batches=every_n_batches,
        max_per_hour=max_per_hour,
    )
    batch = max(0, int(batch_number))
    return (batch + interval - 1) // interval


def priority_sniper_env() -> tuple[int, int]:
    """Effective (every_n_batches, max_per_hour) from the environment."""

    return (
        _priority_env_int(
            "SNIPERPLUG_PRIORITY_SNIPER_EVERY_N_BATCHES",
            _DEFAULT_PRIORITY_EVERY_N_BATCHES,
        ),
        _priority_env_int(
            "SNIPERPLUG_PRIORITY_SNIPER_MAX_PER_HOUR",
            _DEFAULT_PRIORITY_MAX_PER_HOUR,
        ),
    )

PRICE_SNIPER_QUERIES: tuple[str, ...] = (
    "65 inch 4k smart tv",
    "75 inch 4k smart tv",
    "oled tv",
    "gaming laptop",
    "gaming desktop pc",
    "prebuilt gaming pc",
    "mini pc",
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
