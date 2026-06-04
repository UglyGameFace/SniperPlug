from __future__ import annotations

from typing import Any


def explain_public_post_result(result: Any) -> str:
    """Make posting outcomes understandable for owners.

    This is intentionally text-only so it can be used in summaries, dashboards,
    logs, and future command responses without importing Discord.
    """
    if result is None or not getattr(result, "any_activity", False):
        return "Public posting did not run. Usually this means there were no cards to try."

    lines: list[str] = []
    attempted = int(getattr(result, "attempted", 0) or 0)
    posted = int(getattr(result, "posted", 0) or 0)
    cached = int(getattr(result, "cached_active", 0) or 0)
    disabled = int(getattr(result, "skipped_disabled", 0) or 0)
    wrong_retailer = int(getattr(result, "skipped_wrong_retailer", 0) or 0)
    duplicate = int(getattr(result, "skipped_duplicate", 0) or 0)
    recent_duplicate = int(getattr(result, "skipped_recent_alert_duplicate", 0) or 0)
    reserved_duplicate = int(getattr(result, "skipped_reserved_duplicate", 0) or 0)
    not_alertable = int(getattr(result, "skipped_not_alertable", 0) or 0)
    errors = tuple(getattr(result, "errors", ()) or ())

    lines.append(f"Tried: **{attempted}** • Posted: **{posted}** • Cached active: **{cached}**")
    if disabled:
        lines.append(f"• **{disabled}** skipped because public alerts are off or no alert channel is set. Run `/autoscan_setup channel:#walmart-deals`.")
    if wrong_retailer:
        lines.append(f"• **{wrong_retailer}** skipped because that retailer is not enabled for public posting.")
    if duplicate:
        parts: list[str] = []
        if recent_duplicate:
            parts.append(f"**{recent_duplicate}** already posted at the same/higher price")
        if reserved_duplicate:
            parts.append(f"**{reserved_duplicate}** blocked by active post reservation")
        if not parts:
            parts.append(f"**{duplicate}** blocked as duplicate public posts")
        lines.append("• Duplicate guard: " + " • ".join(parts) + ". Lower-price repeats can post again.")
    if not_alertable:
        lines.append(f"• **{not_alertable}** stayed private because proof was too weak, not a true markdown, or staff-review only.")
    if errors:
        lines.append("• Errors: " + "; ".join(clean_text(str(error), 160) for error in errors[:3]))
    if posted == 0 and cached and not (disabled or wrong_retailer or duplicate or not_alertable or errors):
        lines.append("• Cached but not posted. Check `/autoscan_health` and `/active_deals` for config/proof state.")
    return "\n".join(lines[:8])


def clean_text(value: str, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
