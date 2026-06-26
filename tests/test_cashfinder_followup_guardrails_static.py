from pathlib import Path


def test_followup_guardrails_document_scope():
    text = Path("docs/cash_finder_followup_guardrails.md").read_text(encoding="utf-8")
    assert "does not change Walmart authentication" in text
    assert "public markdown posting" in text
    assert "open-box public posting" in text
    assert "no-op wrappers" in text
