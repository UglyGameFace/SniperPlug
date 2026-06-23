from datetime import UTC, datetime
from unittest.mock import patch
from zoneinfo import ZoneInfoNotFoundError

from sniperplug.services import verizon_shine


def test_eastern_timezone_falls_back_when_tzdata_missing():
    with patch("sniperplug.services.verizon_shine.ZoneInfo", side_effect=ZoneInfoNotFoundError("missing tzdata")):
        tz = verizon_shine.eastern_tz()

    assert tz.tzname(None) == "ET"
    assert tz.utcoffset(None).total_seconds() == -5 * 3600


def test_parse_datetime_hint_survives_missing_tzdata():
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    with patch("sniperplug.services.verizon_shine.ZoneInfo", side_effect=ZoneInfoNotFoundError("missing tzdata")):
        parsed = verizon_shine.parse_datetime_hint("available today at 9pm ET", now=now)

    assert parsed is not None
    assert parsed.tzinfo is UTC
