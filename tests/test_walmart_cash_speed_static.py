from pathlib import Path

DEALS = Path("sniperplug/cogs/deal_scanner.py").read_text(encoding="utf-8")


def test_walmart_cash_routes_are_bounded_and_concurrent():
    assert "asyncio.wait_for" in DEALS
    assert "asyncio.Semaphore(2)" in DEALS
    assert "asyncio.Semaphore(4)" not in DEALS


def test_walmart_cash_command_uses_fast_direct_pass():
    assert "queries[:3]" in DEALS
    assert "queries[:6]" not in DEALS

    assert "for page in (1,)" in DEALS or "for page in [1]" in DEALS
    assert "for page in (1, 2)" not in DEALS
    assert "for page in [1, 2]" not in DEALS


def test_walmart_cash_has_user_facing_timeout_message():
    assert "timed out" in DEALS.lower() or "timeout" in DEALS.lower()
