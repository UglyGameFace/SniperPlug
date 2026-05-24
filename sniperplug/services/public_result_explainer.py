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
    not_alertable = int(getattr(result, "skipped_not_alertable", 0) or 0)
    errors = tuple(getattr(result, "errors", ()) or ())

    lines.append(f"Tried: **{attempted}** • Posted: **{posted}** • Cached active: **{cached}**")
    if disabled:
        lines.append(f"• **{disabled}** skipped because public alerts are off or no alert channel is set. Run `/setup_sniperplug`.")
    if wrong_retailer:
        lines.append(f"• **{wrong_retailer}** skipped because that retailer is not enabled for public posting.")
    if duplicate:
        lines.append(f"• **{duplicate}** blocked as duplicate same-product/same-price public posts.")
    if not_alertable:
        lines.append(f"• **{not_alertable}** stayed private because proof was too weak, not a true markdown, or staff-review only.")
    if errors:
        lines.append("• Errors: " + "; ".join(str(error)[:160] for error in errors[:3]))
    if posted == 0 and cached and not (disabled or wrong_retailer or duplicate or not_alertable or errors):
        lines.append("• Cached but not posted. Check `/sniperplug_dashboard` and `/active_deals` for current config/proof state.")
    return "\n".join(lines[:8])
