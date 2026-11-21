"""Home Depot clearance cents decoder for the penny tracker.

The stage table encoded here comes from the sourced research in
``~/workspace/hd-penny-research-20261120.md`` (11 deal-press / community /
scan-data sources; Home Depot publishes no official key):

- ``$0.01`` exactly is the penny: an internal "pull this item" system price.
  It is the one ending every source — including the skeptical scan-data
  operators — agrees is real. A penny is only *proven* when a price of
  exactly $0.01 is observed.
- ``.03`` is the final markdown before the penny (consensus: countdown and
  discount-depth readings agree, ~75% off). These are the ones to look for.
- ``.02`` is the final step on the Krazy Coupon Lady "faster ladder"
  (~90% off). Lower confidence: countdown sources never mention it.
- ``.06`` is an early/mid markdown; another drop follows (consensus stage,
  ~25% off, ~6 weeks to the next drop on the countdown reading).
- ``.04`` is the faster ladder's mid step (~50% off). Lower confidence.
- ``.00`` on a clearance (yellow-tag) item is the first markdown. A bare
  .00 price with no clearance context is just a round price, so the caller
  must supply ``clearance_context``.
- ``.53`` / ``.52`` / ``.97`` / ``.98`` and every other ending carry no
  sourced stage meaning and are never decoded as clearance stages.

Timing between stages is genuinely disputed (guides say weeks; scan data
says days at the deep stages), so drop projections are estimates: seeded
from the countdown reading (.06 stage ~6 weeks, .03 stage ~3 weeks) and
then replaced per item by stage durations the tracker actually observes.

This module is pure: no I/O, no clock reads except through parameters.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum


class PennyStage(str, Enum):
    NOT_CLEARANCE = "not_clearance"
    FIRST = "first"
    MID = "mid"
    FINAL = "final"
    PENNY = "penny"
    UNKNOWN = "unknown"


# Progression order toward the penny. UNKNOWN and NOT_CLEARANCE both sit
# off the ladder (0): an item can enter the ladder from either.
STAGE_ORDER: dict[PennyStage, int] = {
    PennyStage.NOT_CLEARANCE: 0,
    PennyStage.UNKNOWN: 0,
    PennyStage.FIRST: 1,
    PennyStage.MID: 2,
    PennyStage.FINAL: 3,
    PennyStage.PENNY: 4,
}

STAGE_LABELS: dict[PennyStage, str] = {
    PennyStage.NOT_CLEARANCE: "Not on clearance",
    PennyStage.FIRST: "First markdown",
    PennyStage.MID: "Mid clearance",
    PennyStage.FINAL: "Final markdown — one to look for",
    PennyStage.PENNY: "Penny price",
    PennyStage.UNKNOWN: "Clearance watch (stage unclear)",
}

# Countdown-reading dwell seeds, in days. Estimates only; the tracker
# replaces them with observed stage durations as it collects history.
SEED_STAGE_DWELL_DAYS: dict[PennyStage, float] = {
    PennyStage.FIRST: 42.0,
    PennyStage.MID: 42.0,
    PennyStage.FINAL: 21.0,
}

CONFIDENCE_CONSENSUS = "consensus"
CONFIDENCE_LOWER = "lower"
CONFIDENCE_NONE = "none"


@dataclass(frozen=True)
class StageReading:
    stage: PennyStage
    ending: str | None
    confidence: str
    basis: str


@dataclass(frozen=True)
class DropEstimate:
    earliest: datetime
    latest: datetime
    dwell_days: float
    learned: bool


def price_to_cents(price: float | None) -> int | None:
    if price is None:
        return None
    try:
        cents = int(round(float(price) * 100))
    except (TypeError, ValueError):
        return None
    return cents if cents >= 0 else None


def cents_ending(price_cents: int) -> str:
    return f"{price_cents % 100:02d}"


def decode_price_stage(
    price_cents: int | None,
    *,
    clearance_context: bool,
) -> StageReading:
    """Decode one observed price into its clearance stage.

    ``clearance_context`` is True when the item is already known to be a
    clearance item (yellow tag, tracked clearance lead, or an observed
    markdown against a higher was-price). It only affects the ``.00``
    ending, which is a plain round price without that context.
    """

    if price_cents is None or price_cents < 0:
        return StageReading(PennyStage.UNKNOWN, None, CONFIDENCE_NONE, "no price observed")
    if price_cents == 1:
        return StageReading(
            PennyStage.PENNY,
            "01",
            CONFIDENCE_CONSENSUS,
            "price is exactly $0.01 — Home Depot's pull-from-inventory penny price",
        )
    ending = price_cents % 100
    if ending == 3:
        return StageReading(
            PennyStage.FINAL,
            "03",
            CONFIDENCE_CONSENSUS,
            ".03 is the final markdown before the penny",
        )
    if ending == 2:
        return StageReading(
            PennyStage.FINAL,
            "02",
            CONFIDENCE_LOWER,
            ".02 is the last step on the faster markdown ladder (single-source lineage)",
        )
    if ending == 6:
        return StageReading(
            PennyStage.MID,
            "06",
            CONFIDENCE_CONSENSUS,
            ".06 is an early/mid markdown; another drop follows",
        )
    if ending == 4:
        return StageReading(
            PennyStage.MID,
            "04",
            CONFIDENCE_LOWER,
            ".04 is the mid step on the faster markdown ladder (single-source lineage)",
        )
    if ending == 0:
        if clearance_context:
            return StageReading(
                PennyStage.FIRST,
                "00",
                CONFIDENCE_CONSENSUS,
                ".00 on a clearance item is the first markdown",
            )
        return StageReading(
            PennyStage.NOT_CLEARANCE,
            "00",
            CONFIDENCE_NONE,
            "round .00 price with no clearance context is just a regular price",
        )
    if clearance_context:
        return StageReading(
            PennyStage.UNKNOWN,
            f"{ending:02d}",
            CONFIDENCE_NONE,
            "clearance item whose ending carries no sourced stage meaning",
        )
    return StageReading(
        PennyStage.NOT_CLEARANCE,
        f"{ending:02d}",
        CONFIDENCE_NONE,
        "ending carries no sourced clearance-stage meaning",
    )


def estimate_next_drop(
    stage: PennyStage,
    stage_entered_at: datetime,
    *,
    learned_dwell_days: float | None = None,
) -> DropEstimate | None:
    """Estimated window for the next drop out of ``stage``.

    The window is the stage-entry time plus the dwell, widened to
    [0.5x, 1.5x] because per-stage timing is disputed across sources.
    ``learned_dwell_days`` (observed by the tracker) wins over the seed.
    Always an estimate — callers must label it as one.
    """

    seed = SEED_STAGE_DWELL_DAYS.get(stage)
    if seed is None:
        return None
    learned = learned_dwell_days is not None and learned_dwell_days > 0
    dwell = float(learned_dwell_days) if learned else seed
    entered = _utc(stage_entered_at)
    return DropEstimate(
        earliest=entered + timedelta(days=dwell * 0.5),
        latest=entered + timedelta(days=dwell * 1.5),
        dwell_days=dwell,
        learned=learned,
    )


def stage_sort_priority(stage: PennyStage) -> int:
    """Poll priority: final-stage items are checked first, pennies never."""

    return {
        PennyStage.FINAL: 0,
        PennyStage.MID: 1,
        PennyStage.FIRST: 2,
        PennyStage.UNKNOWN: 3,
        PennyStage.NOT_CLEARANCE: 4,
        PennyStage.PENNY: 5,
    }[stage]


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
