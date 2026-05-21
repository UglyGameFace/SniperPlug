from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderReadiness:
    name: str
    status: str
    detail: str
    next_step: str


READY = "Ready"
PARKED = "Parked"
PLANNED = "Planned"
TEST_ONLY = "Test Only"


def provider_readiness_list() -> tuple[ProviderReadiness, ...]:
    """Human-readable provider roadmap for staff.

    This does not read secrets, call APIs, or scan websites. It is a visibility
    layer so staff can understand what is live, test-only, parked, or planned.
    """
    return (
        ProviderReadiness(
            name="Manual/Test Pipeline",
            status=TEST_ONLY,
            detail="scan_test uses demo SourceCandidate objects to test scoring, routing, and alert UX.",
            next_step="Use this to validate alert quality before live providers.",
        ),
        ProviderReadiness(
            name="Best Buy",
            status=PARKED,
            detail="Useful for product catalog, pricing, availability, and images, but access is blocked until a company-domain developer account is approved.",
            next_step="Revisit after a SniperPlug domain email is available.",
        ),
        ProviderReadiness(
            name="Amazon / Keepa",
            status=PLANNED,
            detail="Best first serious price-history lane for Amazon products, offers, and price anomaly discovery.",
            next_step="Add after deciding on Keepa/API access and rate limits.",
        ),
        ProviderReadiness(
            name="Brand Direct Stores",
            status=PLANNED,
            detail="Samsung, LG, Sony, Nike, Adidas, Puma, jewelry stores, auto stores, and warehouse clubs need source-specific adapters.",
            next_step="Add one adapter at a time with strict anti-placeholder and anti-guess rules.",
        ),
        ProviderReadiness(
            name="Social Chatter",
            status=PLANNED,
            detail="Only an urgency booster after a real source-first price anomaly exists; never a standalone alert source.",
            next_step="Use later for confirmation/urgency, not deal creation.",
        ),
    )
