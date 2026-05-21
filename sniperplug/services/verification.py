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

    Public alerts should not look like scary debug output. We only show positive
    checks when something was actually verified, and we clearly label demo data.
    Unknown checks are omitted instead of shown as alarming "unchecked" badges.
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
        badges.append("🔎 Source Candidate")

    if deal.is_price_verified:
        badges.append("💵 Price Checked")

    if deal.is_link_verified:
        badges.append("🔗 Link Checked")

    if deal.image_url and deal.is_image_verified:
        badges.append("🖼️ Image Verified")
    elif deal.image_url:
        badges.append("🖼️ Image Supplied")
    else:
        badges.append("⚠️ No Product Image")

    if deal.requires_business_account:
        badges.append("🏢 Business Account")

    if deal.is_ymmv:
        badges.append("🧊 YMMV")

    return unique_keep_order(badges)


def compact_verification_summary(deal: NormalizedDeal) -> str:
    badges = verification_badges(deal)
    notes = [clean_note(note) for note in deal.verification_notes if note.strip()]
    notes = [note for note in notes if note]

    lines = [" • ".join(badges[:4])]
    if len(badges) > 4:
        lines.append(" • ".join(badges[4:8]))
    if notes:
        lines.extend(f"• {note}" for note in notes[:2])

    return "\n".join(lines)


def clean_note(note: str) -> str:
    # Keep public notes readable. Technical scoring/debug details are rendered in
    # dedicated staff/test fields instead of mixed into Proof.
    lowered = note.lower()
    if "anomaly score:" in lowered:
        return ""
    return note.strip()


def unique_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for item in items:
        if item not in seen:
            output.append(item)
            seen.add(item)
    return output
