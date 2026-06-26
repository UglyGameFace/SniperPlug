from __future__ import annotations


def build_autoscan_no_post_report(
    *,
    products_checked: int,
    verified_cards_found: int,
    public_ready_cards: int,
    weak_reference_ignored: int,
    missing_trusted_reference: int,
    review_scout_leads: int,
    exact_match_rescues: int,
    strongest_review_candidates_kept: int,
) -> str:
    """Build diagnostic text for an autoscan run that has zero public output."""

    return (
        "No verified deal was sent to the public alert channel.\n"
        f"• products checked: **{int(products_checked or 0)}**\n"
        f"• verified cards found: **{int(verified_cards_found or 0)}**\n"
        f"• public-ready cards found: **{int(public_ready_cards or 0)}**\n"
        f"• weak reference ignored: **{int(weak_reference_ignored or 0)}**\n"
        f"• missing trusted reference: **{int(missing_trusted_reference or 0)}**\n"
        f"• review/scout leads: **{int(review_scout_leads or 0)}**\n"
        f"• exact-match rescues: **{int(exact_match_rescues or 0)}**\n"
        f"• strongest review candidates kept: **{int(strongest_review_candidates_kept or 0)}**\n"
        "Review/scout leads remain diagnostics unless they pass verified API proof."
    )
