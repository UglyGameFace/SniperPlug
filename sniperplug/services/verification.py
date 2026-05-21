from __future__ import annotations

from sniperplug.models.deal import NormalizedDeal


VERIFIED = "verified"
CANDIDATE = "candidate"
STAFF_REVIEW = "staff_review"
EXPIRED = "expired"
DEMO = "demo"


def verification_badges(deal: NormalizedDeal) -> list[str]:
    """
    Build honest user-facing proof badges.

    These labels must not overclaim. A badge only says something was checked when
    the deal object explicitly says a provider or checker verified it.
    """
    badges: list[str] = []

    status = deal.verification_status.lower().strip()
    if status == VERIFIED:
        badges.append("✅ Verified")
    elif status == STAFF_REVIEW:
        badges.append("🛠️ Staff Review")
    elif status == EXPIRED:
        badges.append("❌ Expired")
    elif status == DEMO or deal.source == "manual_test":
        badges.append("🧪 Demo Data")
    else:
        badges.append("🔎 Candidate")

    if deal.is_price_verified:
        badges.append("💵 Price Checked")
    else:
        badges.append("💵 Price Unchecked")

    if deal.is_link_verified:
        badges.append("🔗 Link Checked")
    else:
        badges.append("🔗 Link Unchecked")

    if deal.image_url and deal.is_image_verified:
        badges.append("🖼️ Image Verified")
    elif deal.image_url:
        badges.append("🖼️ Image Supplied")
    else:
        badges.append("⚠️ Image Not Verified")

    if deal.requires_business_account:
        badges.append("🏢 Business Account")

    if deal.is_ymmv:
        badges.append("🧊 YMMV")

    return unique_keep_order(badges)


def compact_verification_summary(deal: NormalizedDeal) -> str:
    badges = verification_badges(deal)
    notes = [note for note in deal.verification_notes if note.strip()]

    lines = [" • ".join(badges[:4])]
    if len(badges) > 4:
        lines.append(" • ".join(badges[4:8]))
    if notes:
        lines.extend(f"• {note}" for note in notes[:3])

    return "\n".join(lines)


def unique_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for item in items:
        if item not in seen:
            output.append(item)
            seen.add(item)
    return output
