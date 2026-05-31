from __future__ import annotations

import ast
import inspect
from pathlib import Path

from sniperplug.services.public_deal_posts import maybe_post_public_deal_cards


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REMOVED_MONKEY_PATCH_MODULES = {
    "manual_posting_explainer.py",
    "raw_price_review_patch.py",
    "public_alert_text_id_patch.py",
    "walmart_flip_research_patch.py",
    "walmart_discovery_expansion.py",
    "command_error_bridge.py",
    "walmart_renderer_install.py",
    "walmart_marketplace_comp_guard.py",
}
REMOVED_STARTUP_HOOKS = {
    "install_manual_posting_explainer_patch",
    "install_raw_price_review_patch",
    "install_public_alert_text_id_patch",
    "install_walmart_flip_research_patch",
    "install_walmart_discovery_expansion",
    "install_local_command_error_bridges",
    "install_walmart_renderer",
    "install_walmart_marketplace_comp_guard",
    "install_strict_walmart_cash_guard",
}
REMOVED_PUBLIC_POST_CONFIG_NAMES = {
    "get_public_post_config",
    "update_public_alert_channel_id",
}
REMOVED_WALMART_CASH_PATCH_NAMES = {
    "_direct_walmart_cash_amount",
    "walmart_provider._walmart_promotion_proof",
}


def test_all_sniperplug_python_files_parse() -> None:
    for path in (PROJECT_ROOT / "sniperplug").rglob("*.py"):
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_public_post_call_kwargs_match_signature() -> None:
    """Catch the exact class of bug where a caller passes a removed keyword."""
    allowed_kwargs = set(inspect.signature(maybe_post_public_deal_cards).parameters)
    for path in (PROJECT_ROOT / "sniperplug").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            called_name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
            if called_name != "maybe_post_public_deal_cards":
                continue
            for keyword in node.keywords:
                if keyword.arg is None:
                    continue
                assert keyword.arg in allowed_kwargs, f"{path}:{node.lineno} passes unsupported maybe_post_public_deal_cards kwarg {keyword.arg!r}"


def test_removed_public_post_config_names_stay_removed() -> None:
    """Public alert config now lives in public_alert_config.py, not public_deal_posts.py."""
    for path in (PROJECT_ROOT / "sniperplug").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        for name in REMOVED_PUBLIC_POST_CONFIG_NAMES:
            assert name not in source, f"{path} still references removed public_deal_posts config helper {name!r}"


def test_removed_walmart_cash_patch_names_stay_removed() -> None:
    """Walmart Cash proof now lives in walmart_cash.py and the provider calls it directly."""
    for path in (PROJECT_ROOT / "sniperplug").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        for name in REMOVED_WALMART_CASH_PATCH_NAMES:
            assert name not in source, f"{path} still references removed Walmart Cash patch name {name!r}"


def test_removed_monkey_patch_modules_stay_removed() -> None:
    service_dir = PROJECT_ROOT / "sniperplug" / "services"
    existing = {path.name for path in service_dir.glob("*.py")}
    assert not (existing & REMOVED_MONKEY_PATCH_MODULES)


def test_removed_startup_hooks_stay_removed() -> None:
    bot_source = (PROJECT_ROOT / "sniperplug" / "bot.py").read_text(encoding="utf-8")
    for hook in REMOVED_STARTUP_HOOKS:
        assert hook not in bot_source


def test_bot_startup_imports() -> None:
    """Catch Discloud-style offline crashes from `from sniperplug.bot import run`."""
    from sniperplug.bot import run  # noqa: F401


def test_recent_runtime_modules_import() -> None:
    import sniperplug.cogs.auto_scan_runner  # noqa: F401
    import sniperplug.services.public_alert_config  # noqa: F401
    import sniperplug.services.public_deal_posts  # noqa: F401
    import sniperplug.services.error_logging  # noqa: F401
    import sniperplug.services.embed_delivery_patch  # noqa: F401
