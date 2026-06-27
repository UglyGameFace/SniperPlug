from __future__ import annotations

from typing import Any


def build_autoscan_no_post_intelligence(report: Any) -> str:
    """Explain a zero-post autoscan using hard counters already on the report.

    This does not loosen public posting. It only turns the existing report state
    into a clear private/staff diagnostic so a zero-post run is actionable.
    """

    result = getattr(report, "public_result", None)
    lines: list[str] = []

    products_checked = int(getattr(report, "products_checked", 0) or 0)
    searches_checked = int(getattr(report, "searches_checked", 0) or 0)
    verified_before_memory = int(getattr(report, "verified_before_memory", 0) or 0)
    verified_after_memory = int(getattr(report, "total_cards", 0) or 0)
    fresh_cards = int(getattr(report, "fresh_cards", 0) or 0)
    public_attempt = int(getattr(report, "cards_attempted_for_public", 0) or 0)
    posted = int(getattr(result, "posted", 0) or 0) if result is not None else 0

    lines.append(
        "Scan volume: "
        f"**{products_checked}** products across **{searches_checked}** searches."
    )
    lines.append(
        "Verified/public funnel: "
        f"verified before memory **{verified_before_memory}** → after memory/ranking **{verified_after_memory}** → "
        f"fresh/lower-price **{fresh_cards}** → public guard **{public_attempt}** → posted **{posted}**."
    )

    memory_summary = compact(getattr(report, "price_memory_summary", ""), 520)
    if memory_summary:
        if memory_summary == "not used":
            lines.append("Observed price memory: **not active on this run**. That means this run could only post from Walmart's own trusted was/reference fields.")
        else:
            lines.append(f"Observed price memory: {memory_summary}")

    verification = compact(getattr(report, "verification_failure_summary", ""), 520)
    if verification:
        lines.append(f"Proof blockers: {verification}")

    review = compact(getattr(report, "review_candidate_summary", ""), 520)
    if review:
        lines.append(f"Private review/scout leads: {review}")

    repeat = compact(getattr(report, "repeat_summary", ""), 420)
    if repeat:
        lines.append(f"Fresh/repeat gate: {repeat}")

    if result is not None and posted <= 0:
        duplicate = int(getattr(result, "skipped_duplicate", 0) or 0)
        recent_duplicate = int(getattr(result, "skipped_recent_alert_duplicate", 0) or 0)
        reserved_duplicate = int(getattr(result, "skipped_reserved_duplicate", 0) or 0)
        not_alertable = int(getattr(result, "skipped_not_alertable", 0) or 0)
        disabled = int(getattr(result, "skipped_disabled", 0) or 0)
        wrong_retailer = int(getattr(result, "skipped_wrong_retailer", 0) or 0)
        gate_bits: list[str] = []
        if duplicate:
            gate_bits.append(f"duplicates **{duplicate}**")
        if recent_duplicate:
            gate_bits.append(f"same/higher recent posts **{recent_duplicate}**")
        if reserved_duplicate:
            gate_bits.append(f"active reservations **{reserved_duplicate}**")
        if not_alertable:
            gate_bits.append(f"public-quality blocked **{not_alertable}**")
        if disabled:
            gate_bits.append(f"disabled/missing channel **{disabled}**")
        if wrong_retailer:
            gate_bits.append(f"wrong retailer **{wrong_retailer}**")
        if gate_bits:
            lines.append("Final public guard blocks: " + " • ".join(gate_bits) + ".")

        errors = tuple(getattr(result, "errors", ()) or ())
        if errors:
            lines.append("Posting errors: " + "; ".join(compact(error, 120) for error in errors[:3]))

    routes = compact(getattr(report, "route_summary", ""), 520)
    if routes:
        lines.append(f"Top routes checked: {routes}")

    warnings = tuple(getattr(report, "warnings", ()) or ())
    if warnings:
        lines.append("Important warnings: " + " | ".join(compact(warning, 140) for warning in warnings[:3]))

    if verified_after_memory <= 0:
        lines.append(
            "Bottom line: no public card was created. Keep building observed price memory or lower the public threshold only if you intentionally want more risk."
        )
    elif fresh_cards <= 0:
        lines.append(
            "Bottom line: public-quality cards existed, but the fresh/lower-price gate kept them out to avoid repeat spam."
        )
    elif public_attempt <= 0:
        lines.append(
            "Bottom line: fresh cards existed but none reached public preflight after confidence/category/ranking gates."
        )
    elif posted <= 0:
        lines.append(
            "Bottom line: cards reached public preflight, but final posting guards blocked them."
        )

    return "\n".join(f"• {line}" for line in dedupe_keep_order(lines))[:4000] or "• No no-post details available."


def dedupe_keep_order(lines: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for line in lines:
        clean = " ".join(str(line or "").split())
        if not clean or clean in seen:
            continue
        seen.add(clean)
        output.append(clean)
    return output


def compact(value: Any, limit: int = 260) -> str:
    text = " | ".join(str(value or "").splitlines())
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
