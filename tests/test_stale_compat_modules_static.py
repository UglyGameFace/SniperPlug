from pathlib import Path


def read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_legacy_runtime_patch_imports_are_native_noop_hooks():
    assert "def install_strict_walmart_cash_guard" in read("sniperplug/services/walmart_cash_guard.py")
    assert "def install_walmart_marketplace_comp_guard" in read("sniperplug/services/walmart_marketplace_comp_guard.py")
    assert "def install_walmart_flip_research_patch" in read("sniperplug/services/walmart_flip_research_patch.py")
    assert "def install_walmart_renderer" in read("sniperplug/services/walmart_renderer_install.py")


def test_manual_and_raw_review_helpers_exist():
    assert "def add_public_posting_field" in read("sniperplug/services/manual_posting_explainer.py")
    assert "def raw_price_signal" in read("sniperplug/services/raw_price_review_patch.py")
    assert "def build_review_candidate_cards_with_raw_leads" in read("sniperplug/services/raw_price_review_patch.py")
