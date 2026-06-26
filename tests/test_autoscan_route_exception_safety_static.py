from pathlib import Path

AUTO = Path("sniperplug/services/verified_discount_hunt.py").read_text(encoding="utf-8")


def test_autoscan_gather_handles_baseexception_results():
    assert "return_exceptions=True" in AUTO
    assert "isinstance(item, BaseException)" in AUTO
    assert "not isinstance(item, tuple)" in AUTO
    assert "bad Walmart route result" in AUTO


def test_autoscan_does_not_unpack_panics_blindly():
    safety = AUTO.index("isinstance(item, BaseException)")
    unpack = AUTO.index("query, result = item")
    assert safety < unpack
    assert "bad Walmart provider result" in AUTO
