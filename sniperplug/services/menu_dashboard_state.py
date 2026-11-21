"""Dashboard state snapshot for the /menu hub and /walmart menu.

Chunk 1 of the SniperPlug menu redesign (2026-11-17). One
``load_menu_dashboard_state(bot, guild_id, user_id)`` gathers every existing
reader into a single ``MenuDashboardState`` snapshot so both menus read
live state from one place and no cog reads tables directly.

Rules the loader follows:

* It NEVER raises. Every reader is wrapped in try/except and degrades to a
  safe default (provider UNKNOWN, threshold 40, counts 0, deals_today None).
  ``state_available`` is False only when the bot has no usable db at all;
  a single failed reader must not blank the whole snapshot.
* Delivery readiness reuses exactly the computation in
  ``build_global_autoscan_health_embed``
  (``sniperplug/cogs/canonical_public_alerts.py:620``): the same helper
  calls, the same ``public_route_ready`` / ``walmart_delivery_ready``
  formulas. It is not re-derived differently.
* Deals-today is a read-only COUNT on ``guild_public_deal_posts`` using its
  existing ``posted_at`` column. If that table exists without a
  ``posted_at`` column, ``deals_today`` is None and no migration is
  invented; if the table does not exist yet, the standard
  ``ensure_public_post_tables`` creates it (with ``posted_at``) and the
  count is 0.
* No new env vars, tables, or migrations. No writes except the ones the
  reused readers already perform (their own ``ensure_*`` table setup and
  the autoscan health path's setup repair, exactly as /walmart health does).
"""

from __future__ import annotations

import inspect
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sniperplug.cogs.active_deals import list_active_deals
from sniperplug.cogs.public_alerts import public_alert_channel_status
from sniperplug.providers.registry import provider_registry
from sniperplug.services.autoscan_live_guild_reconciliation import (
    scheduler_membership_for_guild,
)
from sniperplug.services.deal_threshold_settings import (
    DEFAULT_STARTING_DEAL_PERCENT,
    get_starting_deal_percent,
)
from sniperplug.services.dm_deal_alerts import (
    UNLIMITED_MAX_ALERTS_PER_DAY,
    get_dm_deal_alert_preference,
)
from sniperplug.services.public_alert_config import get_public_alert_config
from sniperplug.services.public_deal_posts import ensure_public_post_tables
from sniperplug.services.setup_self_heal import repair_public_alert_setup
from sniperplug.services.user_wishlist import (
    MAX_WISHLIST_ITEMS_PER_USER,
    list_user_wishlist,
)
from sniperplug.services.walmart_extreme_price_fanout import (
    extreme_alerts_enabled,
    extreme_price_health_summary,
)

# Provider health statuses, normalized to the four values the menus render.
# Anything outside READY / DISABLED / ERROR (e.g. STAGED) is UNKNOWN: the
# menus never guess a color from a status they do not understand.
_PROVIDER_STATUS_MAP = {
    "READY": "READY",
    "DISABLED": "DISABLED",
    "ERROR": "ERROR",
}

_POSTED_DEALS_TABLE = "guild_public_deal_posts"

_BOLD_NUMBER_RE = re.compile(r"\*\*(\d+)\*\*")


@dataclass
class MenuDashboardState:
    """One snapshot of everything the menu dashboards show."""

    state_available: bool = False
    provider_status: str = "UNKNOWN"
    provider_message: str = "Live state unavailable."
    threshold_percent: int = DEFAULT_STARTING_DEAL_PERCENT
    extreme_enabled: bool = False
    extreme_recent: int = 0
    delivery_ready: bool = False
    delivery_summary: str = "Delivery status unavailable."
    dm_enabled: bool = False
    dm_threshold: int | None = None
    dm_daily_cap: int | None = None
    wishlist_count: int = 0
    wishlist_cap: int = MAX_WISHLIST_ITEMS_PER_USER
    active_deals: list = field(default_factory=list)
    active_deal_count: int = 0
    deals_today: int | None = None
    hd_location_label: str = ""


def _cell(row: Any, key: str, index: int) -> Any:
    """Read one cell from a row that may be a mapping, a Row, or a tuple."""
    if row is None:
        return None
    try:
        return row[key]
    except Exception:
        pass
    try:
        return row[index]
    except Exception:
        return None


def _normalize_provider_status(raw: Any) -> str:
    value = getattr(raw, "value", raw)
    text = str(value or "").strip().upper()
    return _PROVIDER_STATUS_MAP.get(text, "UNKNOWN")


def _parse_extreme_recent(summary: str | None) -> int:
    """Recent extreme candidates, from the health summary's first bold count.

    ``extreme_price_health_summary`` renders
    ``"Last 15 min: **N** exact-verified queue rows ..."``; N is the
    bounded count of exact-price candidates seen in the window. 0 when the
    summary carries no count (e.g. the cursor is not initialized).
    """
    if not summary:
        return 0
    match = _BOLD_NUMBER_RE.search(summary)
    if not match:
        return 0
    try:
        return int(match.group(1))
    except (TypeError, ValueError):
        return 0


def _resolve_walmart_provider(bot: Any) -> Any | None:
    """Find the Walmart provider the way the runtime wires it.

    Precedence: an explicit ``bot.walmart_provider`` (test seam), a
    ``bot.provider_registry`` if the bot carries one, then the process-global
    ``provider_registry`` populated by ``SniperPlugBot.setup_hook`` (which
    registers the cached/coordinated Walmart provider under ``"walmart"``).
    Returns None when nothing is registered anywhere — the caller reports
    UNKNOWN rather than guessing from env.
    """
    provider = getattr(bot, "walmart_provider", None)
    if provider is not None:
        return provider
    registry = getattr(bot, "provider_registry", None)
    getter = getattr(registry, "get", None)
    if callable(getter):
        try:
            provider = getter("walmart")
        except Exception:
            provider = None
        if provider is not None:
            return provider
    try:
        return provider_registry.get("walmart")
    except Exception:
        return None


async def _load_provider(state: MenuDashboardState, bot: Any) -> None:
    try:
        provider = _resolve_walmart_provider(bot)
        if provider is None:
            state.provider_status = "UNKNOWN"
            state.provider_message = "Walmart provider is not registered."
            return
        health = provider.healthcheck()
        if inspect.isawaitable(health):
            health = await health
        state.provider_status = _normalize_provider_status(
            getattr(health, "status", None)
        )
        message = str(getattr(health, "message", "") or "").strip()
        if not message:
            message = {
                "READY": "Walmart provider is ready.",
                "DISABLED": "Walmart provider is disabled.",
                "ERROR": "Walmart provider reported an error.",
            }.get(state.provider_status, "Walmart provider status is unknown.")
        state.provider_message = message
    except Exception:
        state.provider_status = "UNKNOWN"
        state.provider_message = "Walmart provider healthcheck failed."


async def _load_threshold(state: MenuDashboardState, db: Any, guild_id: Any) -> None:
    try:
        state.threshold_percent = int(
            await get_starting_deal_percent(db, guild_id)
        )
    except Exception:
        state.threshold_percent = DEFAULT_STARTING_DEAL_PERCENT


async def _load_extreme(state: MenuDashboardState, db: Any, guild_id: Any) -> None:
    try:
        state.extreme_enabled = bool(await extreme_alerts_enabled(db, guild_id))
    except Exception:
        state.extreme_enabled = False
    try:
        summary = await extreme_price_health_summary(db, guild_id)
        state.extreme_recent = _parse_extreme_recent(summary)
    except Exception:
        state.extreme_recent = 0


async def _load_delivery(state: MenuDashboardState, bot: Any, db: Any, guild_id: Any) -> None:
    """Walmart delivery readiness, computed exactly as the health embed does.

    Same helpers, same order, same formulas as
    ``build_global_autoscan_health_embed`` (canonical_public_alerts.py):
    repair the public-alert setup, take its config, check scheduler
    enrollment, check the channel, then
    ``public_route_ready = enabled and channel ok and no human action`` and
    ``walmart_delivery_ready = public_route_ready and walmart selected and
    enrolled``.
    """
    try:
        repair = await repair_public_alert_setup(db, bot, guild_id)
        config = (
            repair.config
            if getattr(repair, "config", None) is not None
            else await get_public_alert_config(db, guild_id)
        )
        enrolled, _reason = await scheduler_membership_for_guild(db, bot, guild_id)
        channel_status = public_alert_channel_status(
            bot, guild_id, config.get("channel_id")
        )
        selected_retailers = set(config.get("retailers") or ()).intersection(
            {"walmart", "hp", "target"}
        )
        public_route_ready = (
            bool(config.get("enabled"))
            and channel_status.startswith("✅")
            and not repair.human_action_required
        )
        walmart_delivery_ready = (
            public_route_ready
            and "walmart" in selected_retailers
            and enrolled
        )
        state.delivery_ready = bool(walmart_delivery_ready)
        if state.delivery_ready:
            state.delivery_summary = "Walmart delivery ready."
            return
        missing: list[str] = []
        if not config.get("enabled"):
            missing.append("public alerts are off")
        if not channel_status.startswith("✅"):
            missing.append("public channel is not ready")
        if repair.human_action_required:
            missing.append("setup needs attention")
        if "walmart" not in selected_retailers:
            missing.append("Walmart is not in the selected retailers")
        if not enrolled:
            missing.append("not enrolled in the live autoscan")
        detail = "; ".join(missing) if missing else "not ready"
        state.delivery_summary = f"Walmart delivery not ready: {detail}."
    except Exception:
        state.delivery_ready = False
        state.delivery_summary = "Delivery status unavailable."


async def _load_dm_preferences(state: MenuDashboardState, db: Any, user_id: Any) -> None:
    if user_id is None:
        return
    try:
        preference = await get_dm_deal_alert_preference(db, int(user_id))
        state.dm_enabled = bool(preference.enabled)
        state.dm_threshold = int(preference.min_discount)
        cap = int(preference.max_alerts_per_day)
        # 0 is the stored "unlimited" cap, not a cap of zero.
        state.dm_daily_cap = (
            None if cap == UNLIMITED_MAX_ALERTS_PER_DAY else cap
        )
    except Exception:
        state.dm_enabled = False
        state.dm_threshold = None
        state.dm_daily_cap = None


async def _load_wishlist(state: MenuDashboardState, db: Any, user_id: Any) -> None:
    if user_id is None:
        return
    try:
        items = await list_user_wishlist(db, int(user_id))
        state.wishlist_count = len(items)
    except Exception:
        state.wishlist_count = 0


async def _load_hd_location(state: MenuDashboardState, db: Any, user_id: Any) -> None:
    if user_id is None:
        return
    try:
        from sniperplug.services.hd_user_location import get_hd_user_location

        location = await get_hd_user_location(db, user_id=int(user_id))
        state.hd_location_label = location.label if location else ""
    except Exception:
        state.hd_location_label = ""


async def _load_active_deals(state: MenuDashboardState, db: Any, guild_id: Any) -> None:
    try:
        # Same listing the public /active_deals command shows: most recent
        # first, public-quality filter on. page.total is that listing's
        # count, so the dashboard number matches what the command shows.
        page = await list_active_deals(db, guild_id, limit=3, sort="recent")
        state.active_deal_count = int(getattr(page, "total", 0) or 0)
        deals: list[dict[str, Any]] = []
        for row in getattr(page, "rows", []) or []:
            mapping = dict(row) if not isinstance(row, dict) else row
            deals.append(
                {
                    "title": str(mapping.get("title") or ""),
                    "price": mapping.get("current_price"),
                    "discount": mapping.get("discount"),
                }
            )
        state.active_deals = deals[:3]
    except Exception:
        state.active_deals = []
        state.active_deal_count = 0


async def _count_deals_today(db: Any, guild_id: Any) -> int | None:
    """COUNT of deals posted today (UTC) from ``guild_public_deal_posts``.

    Returns None when the count cannot be computed — including when the
    table exists without a ``posted_at`` column, in which case no migration
    is invented. Read-only query only.
    """
    try:
        conn = db.require_conn()
        cursor = await conn.execute(
            f"PRAGMA table_info({_POSTED_DEALS_TABLE})"
        )
        columns = {
            str(_cell(row, "name", 1)) for row in await cursor.fetchall()
        }
        if not columns:
            # Table does not exist yet: the standard ensure creates it
            # (with posted_at), and there can be no posts to count.
            await ensure_public_post_tables(db)
            columns = {"posted_at"}
        if "posted_at" not in columns:
            return None
        today = datetime.now(timezone.utc).date().isoformat()
        cursor = await conn.execute(
            f"SELECT COUNT(*) AS n FROM {_POSTED_DEALS_TABLE} "
            "WHERE guild_id = ? AND status = 'posted' "
            "AND substr(posted_at, 1, 10) = ?",
            (guild_id, today),
        )
        row = await cursor.fetchone()
        if row is None:
            return 0
        return int(_cell(row, "n", 0) or 0)
    except Exception:
        return None


async def load_menu_dashboard_state(bot: Any, guild_id: int, user_id: int) -> MenuDashboardState:
    """Gather every menu-dashboard reader into one snapshot. Never raises."""
    state = MenuDashboardState()
    db = getattr(bot, "db", None) if bot is not None else None
    if db is None:
        return state
    try:
        db.require_conn()
    except Exception:
        return state
    state.state_available = True
    try:
        await _load_provider(state, bot)
        await _load_threshold(state, db, guild_id)
        await _load_extreme(state, db, guild_id)
        await _load_delivery(state, bot, db, guild_id)
        await _load_dm_preferences(state, db, user_id)
        await _load_wishlist(state, db, user_id)
        await _load_hd_location(state, db, user_id)
        await _load_active_deals(state, db, guild_id)
        state.deals_today = await _count_deals_today(db, guild_id)
    except Exception:
        # Belt and braces: per-reader guards above already degrade
        # individually; this only guards against an unexpected failure in
        # the gathering itself. Return the partial snapshot, never raise.
        pass
    return state
