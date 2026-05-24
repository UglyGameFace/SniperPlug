from sniperplug.services.walmart_card_renderer import strict_discount_percent, truncate


def test_strict_discount_percent_requires_real_markdown():
    assert strict_discount_percent(50.0, 100.0) == 50.0
    assert strict_discount_percent(100.0, 100.0) is None
    assert strict_discount_percent(120.0, 100.0) is None
    assert strict_discount_percent(10.0, None) is None


def test_truncate_keeps_short_text():
    assert truncate("hello", 10) == "hello"


def test_truncate_shortens_long_text():
    assert truncate("abcdefghij", 5).endswith("…")
