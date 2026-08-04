from __future__ import annotations

import os
from pathlib import Path

import pytest

from sniperplug.storage.libsql_process import LibsqlProcessConnection
from sniperplug.storage.process_database import (
    SnowflakeSafeLibsqlProcessConnection,
    libsql_safe_parameter,
)


ROOT = Path(__file__).resolve().parents[1]
MAIN = (ROOT / "main.py").read_text(encoding="utf-8")
PROCESS_RUNTIME = (ROOT / "sniperplug/process_runtime.py").read_text(encoding="utf-8")
PROCESS_DATABASE = (
    ROOT / "sniperplug/storage/process_database.py"
).read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_process_connection_round_trips_rows_and_large_ids() -> None:
    conn = await SnowflakeSafeLibsqlProcessConnection.open(database=":memory:")
    try:
        assert conn.identity is not None
        assert conn.identity.pid > 0
        assert conn.identity.pid != os.getpid()
        assert conn.identity.parent_pid == os.getpid()

        await conn.executescript(
            """
            CREATE TABLE sample (
                id INTEGER PRIMARY KEY,
                label TEXT NOT NULL
            );
            """
        )
        snowflake = 9_223_372_036_854_775_000
        await conn.execute(
            "INSERT INTO sample (id, label) VALUES (?, ?)",
            (snowflake, "isolated"),
        )
        await conn.commit()

        cursor = await conn.execute(
            "SELECT id, label FROM sample WHERE id = ?",
            (snowflake,),
        )
        row = await cursor.fetchone()

        assert row == {"id": snowflake, "label": "isolated"}
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_process_connection_rejects_requests_after_close() -> None:
    conn = await LibsqlProcessConnection.open(database=":memory:")
    await conn.close()

    with pytest.raises(RuntimeError, match="closed"):
        await conn.execute("SELECT 1")


def test_large_integer_parameters_are_decimal_text_not_floats() -> None:
    snowflake = 1_514_374_173_517_152_418

    assert libsql_safe_parameter(snowflake) == str(snowflake)
    assert libsql_safe_parameter(-snowflake) == str(-snowflake)
    assert libsql_safe_parameter(42) == 42
    assert libsql_safe_parameter(True) is True


def test_canonical_entrypoint_uses_process_isolated_runtime() -> None:
    assert "from sniperplug.process_runtime import run" in MAIN
    assert "asyncio.run" not in MAIN
    assert "ProcessIsolatedDatabase" in PROCESS_RUNTIME
    assert "self.db = ProcessIsolatedDatabase" in PROCESS_RUNTIME
    assert 'self.backend = "turso-process-isolated"' in PROCESS_DATABASE
    assert "native_libsql_in_gateway_process=false" in PROCESS_DATABASE
    assert "large_integer_text_transport=true" in PROCESS_DATABASE
