from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_review_summary_reports_ignored_reference_sources() -> None:
    source = read("sniperplug/services/walmart_review_candidates.py")
    assert "ignored_reference_sources" in source
    assert "no_value_reasons" in source
    assert "ignored refs" in source
    assert "no-value reasons" in source
    assert "blocked_reference_label" in source


def test_exact_match_metric_is_not_called_rescue() -> None:
    source = read("sniperplug/services/walmart_review_candidates.py")
    assert "search-route exact matches" in source
    assert "exact matches rescued" not in source


def test_blocked_reference_labels_include_source_categories() -> None:
    source = read("sniperplug/services/walmart_review_candidates.py")
    assert "low-trust field" in source
    assert "marketplace comp" in source
    assert "suspicious ratio" in source
    assert "size-sensitive ratio" in source
