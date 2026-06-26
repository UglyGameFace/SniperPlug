from pathlib import Path


def test_cashfinder_followup_notes_explain_dependency_and_no_monkey_patch():
    notes = Path("docs/cash_finder_followup_notes.md").read_text(encoding="utf-8")
    assert "This is **not** a proven no-offer result" in notes
    assert "native no-op compatibility hooks" in notes
    assert "pip install -r requirements.txt" in notes
