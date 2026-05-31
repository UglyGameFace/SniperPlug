from __future__ import annotations

import ast
import inspect
from pathlib import Path

from sniperplug.services.public_deal_posts import maybe_post_public_deal_cards


PROJECT_ROOT = Path(__file__).resolve().parents[1]


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


def test_recent_runtime_modules_import() -> None:
    import sniperplug.cogs.auto_scan_runner  # noqa: F401
    import sniperplug.services.public_deal_posts  # noqa: F401
    import sniperplug.services.error_logging  # noqa: F401
    import sniperplug.services.embed_delivery_patch  # noqa: F401
