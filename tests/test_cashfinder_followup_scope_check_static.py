from pathlib import Path


def test_followup_scope_check_is_private_only():
    text = Path("docs/cash_finder_followup_scope_check.md").read_text(encoding="utf-8")
    assert "Cash Finder copy and private diagnostics only" in text
    assert "no public alert threshold changes" in text
    assert "no Walmart auth/signature changes" in text
