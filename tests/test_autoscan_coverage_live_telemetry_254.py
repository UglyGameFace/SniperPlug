from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CATALOG_SOURCE = (
    ROOT / "sniperplug/services/walmart_catalog_discovery_only.py"
).read_text(encoding="utf-8")
RUNNER_SOURCE = (
    ROOT / "sniperplug/cogs/global_auto_scan_runner.py"
).read_text(encoding="utf-8")


def test_logged_catalog_discovery_note_exposes_live_page_window() -> None:
    assert '"Walmart catalog discovery-only pass: "' in CATALOG_SOURCE
    assert 'f"page window **{page_label}**/**{CATALOG_MAX_PAGE}** • "' in CATALOG_SOURCE
    assert 'f"completed full passes before this batch **{completed_passes}** • "' in CATALOG_SOURCE

    # The production logger intentionally forwards this class of catalog note.
    assert '"catalog discovery-only pass"' in RUNNER_SOURCE
