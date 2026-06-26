from pathlib import Path


def test_followup_verification_commands_are_documented():
    text = Path("docs/cash_finder_followup_verification.md").read_text(encoding="utf-8")
    assert "python -m compileall sniperplug" in text
    assert "tests/test_cashfinder_timeout_truth_static.py" in text
    assert "tests/test_walmart_cash_badge_pdp_enrichment.py" in text
    assert "pip install -r requirements.txt" in text
