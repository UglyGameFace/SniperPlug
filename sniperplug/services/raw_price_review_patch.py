from __future__ import annotations

from typing import Any

from sniperplug.models.candidate import SourceCandidate
from sniperplug.services.low_price_scout import score_candidate, scout_low_price_leads
from sniperplug.services.walmart_review_candidates import ReviewCandidateResult, build_review_candidate_cards


def install_raw_price_review_patch() -> None:
    """Compatibility no-op.

    Raw-price review logic now belongs in the native review/scout pipeline.
    This function intentionally does not monkey-patch anything.
    """
    return None


def raw_price_signal(candidate: SourceCandidate, deal: Any | None = None) -> bool:
    """Backward-compatible raw-price signal helper backed by native scout scoring."""
    return score_candidate(candidate) is not None


def build_review_candidate_cards_with_raw_leads(candidates, *, limit=None):
    """Backward-compatible wrapper that merges native review cards with scout leads.

    This preserves older tests/imports without monkey-patching the review builder.
    """
    safe_limit = limit or 10
    base = build_review_candidate_cards(candidates, limit=safe_limit)
    scout_cards = scout_low_price_leads(candidates, limit=safe_limit)
    merged = []
    seen: set[str] = set()
    for card in [*base.cards, *scout_cards]:
        key = getattr(card, "selected_offer_id", None) or getattr(card, "sku", None) or getattr(card, "upc", None) or getattr(card, "url", None) or getattr(card, "label", "")
        if key in seen:
            continue
        seen.add(key)
        merged.append(card)
    return ReviewCandidateResult(
        cards=merged[:safe_limit],
        under_threshold_count=base.under_threshold_count,
        missing_reference_count=base.missing_reference_count,
        weak_reference_count=base.weak_reference_count,
        missing_current_count=base.missing_current_count,
        no_value_signal_count=base.no_value_signal_count,
        rejected_bad_value_count=base.rejected_bad_value_count,
    )
