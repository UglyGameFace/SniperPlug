from __future__ import annotations

from pathlib import Path


TARGETS = [
    "sniperplug/services/walmart_cash_offers.py",
    "sniperplug/services/walmart_cash_pipeline.py",
    "sniperplug/services/walmart_promo_classifier.py",
    "sniperplug/cogs/deal_scanner.py",
]


def test_python_targets_parse_cleanly():
    for target in TARGETS:
        text = Path(target).read_text(encoding="utf-8")
        compile(text, target, "exec")
