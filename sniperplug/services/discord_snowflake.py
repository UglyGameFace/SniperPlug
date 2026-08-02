from __future__ import annotations

from typing import Any


MAX_DISCORD_SNOWFLAKE = (1 << 64) - 1


def snowflake_text(value: Any) -> str:
    """Return a Discord snowflake as exact decimal text for DB transport.

    Discord IDs are unsigned 64-bit integers and exceed JavaScript's exact
    numeric range. Sending them to a remote SQL transport as decimal text keeps
    every digit intact. SQLite INTEGER affinity may still store the value as an
    exact signed 64-bit integer when the schema uses INTEGER.
    """

    if isinstance(value, bool):
        raise ValueError("boolean is not a Discord snowflake")
    text = str(value or "").strip()
    if not text or not text.isdigit():
        raise ValueError(f"invalid Discord snowflake: {value!r}")
    parsed = int(text)
    if parsed <= 0 or parsed > MAX_DISCORD_SNOWFLAKE:
        raise ValueError(f"Discord snowflake out of range: {value!r}")
    return text


def optional_snowflake_text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return snowflake_text(value)
