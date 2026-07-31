from __future__ import annotations

import importlib
import sys
from dataclasses import dataclass
from pathlib import Path


# Running ``python scripts/smoke_imports.py`` sets sys.path[0] to the scripts
# directory, not the repository root. Add the root explicitly so CI exercises
# the package exactly as checked out instead of failing before the first import.
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))


CRITICAL_MODULES = (
    "sniperplug.bot",
    "sniperplug.cogs.active_deals",
    "sniperplug.cogs.auto_discovery",
    "sniperplug.cogs.auto_scan_runner",
    "sniperplug.cogs.native_auto_scan_runner",
    "sniperplug.cogs.clearance_bank",
    "sniperplug.cogs.deal_feedback_admin",
    "sniperplug.cogs.home_depot_local",
    "sniperplug.cogs.home_depot_search",
    "sniperplug.cogs.local_inventory",
    "sniperplug.cogs.movie_tickets",
    "sniperplug.cogs.public_alerts",
    "sniperplug.cogs.settings_dashboard",
    "sniperplug.cogs.sniperplug",
    "sniperplug.cogs.storage_admin",
    "sniperplug.cogs.verified_deal_scanner",
    "sniperplug.cogs.workflow",
    "sniperplug.services.autoscan_history",
    "sniperplug.services.deal_confidence",
    "sniperplug.services.deal_feedback",
    "sniperplug.services.deal_search_modes",
    "sniperplug.services.fresh_deal_filter",
    "sniperplug.services.manual_review_share",
    "sniperplug.services.movie_ticket_drops",
    "sniperplug.services.public_alert_config",
    "sniperplug.services.public_deal_posts",
    "sniperplug.services.public_posting",
    "sniperplug.services.storage_maintenance",
    "sniperplug.services.verified_discount_hunt",
    "sniperplug.storage.db",
)


CRITICAL_SYMBOLS = {
    "sniperplug.services.public_deal_posts": (
        "PublicPostResult",
        "maybe_post_public_deal_cards",
        "card_product_key",
        "ensure_public_post_tables",
    ),
    "sniperplug.services.deal_feedback": (
        "DealFeedbackView",
        "build_deal_feedback_view",
        "apply_feedback_learning_to_cards",
        "register_persistent_feedback_views",
    ),
    "sniperplug.cogs.public_alerts": (
        "auto_scan_allowed",
        "record_auto_scan_run",
        "list_retailer_auto_scan_settings",
    ),
    "sniperplug.services.movie_ticket_drops": (
        "AtomPromotionsClient",
        "MovieTicketStore",
        "parse_atom_promotions_html",
    ),
    "sniperplug.services.storage_maintenance": (
        "run_storage_maintenance",
    ),
}


@dataclass(frozen=True)
class SmokeFailure:
    module: str
    reason: str


def main() -> int:
    failures: list[SmokeFailure] = []
    loaded: dict[str, object] = {}

    for module_name in CRITICAL_MODULES:
        try:
            loaded[module_name] = importlib.import_module(module_name)
        except Exception as exc:  # noqa: BLE001 - smoke script should report every import failure.
            failures.append(SmokeFailure(module_name, f"import failed: {type(exc).__name__}: {exc}"))

    for module_name, symbols in CRITICAL_SYMBOLS.items():
        module = loaded.get(module_name)
        if module is None:
            continue
        for symbol in symbols:
            if not hasattr(module, symbol):
                failures.append(SmokeFailure(module_name, f"missing symbol: {symbol}"))

    if failures:
        print("❌ SniperPlug import smoke check failed:")
        for failure in failures:
            print(f" - {failure.module}: {failure.reason}")
        return 1

    print(
        f"✅ SniperPlug import smoke check passed: {len(CRITICAL_MODULES)} modules, "
        f"{sum(len(v) for v in CRITICAL_SYMBOLS.values())} symbols"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
