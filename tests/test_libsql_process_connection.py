from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from sniperplug.storage.libsql_process import (
    LibsqlProcessConnection,
    _split_sql_script,
)
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
HP_RUNTIME = (ROOT / "sniperplug/hp_watcher/app.py").read_text(encoding="utf-8")
TARGET_RUNTIME = (ROOT / "sniperplug/target_watcher/app.py").read_text(encoding="utf-8")


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
    with pytest.raises(ValueError, match="Unsafe large floating-point"):
        libsql_safe_parameter(float(snowflake))


def test_sql_script_splitter_preserves_trigger_bodies_and_quoted_semicolons() -> None:
    script = """
    CREATE TABLE source (id INTEGER PRIMARY KEY, label TEXT);
    CREATE TABLE audit (text TEXT);
    CREATE TRIGGER source_audit AFTER INSERT ON source
    BEGIN
        INSERT INTO audit (text) VALUES ('created;still-one-value');
        INSERT INTO audit (text) VALUES (NEW.label);
    END;
    """

    statements = _split_sql_script(script)

    assert len(statements) == 3
    assert statements[2].count("INSERT INTO audit") == 2
    assert "created;still-one-value" in statements[2]


@pytest.mark.asyncio
async def test_process_executes_trigger_script_without_splitting_body() -> None:
    conn = await LibsqlProcessConnection.open(database=":memory:")
    try:
        await conn.executescript(
            """
            CREATE TABLE source (id INTEGER PRIMARY KEY, label TEXT);
            CREATE TABLE audit (text TEXT);
            CREATE TRIGGER source_audit AFTER INSERT ON source
            BEGIN
                INSERT INTO audit (text) VALUES ('created;safe');
                INSERT INTO audit (text) VALUES (NEW.label);
            END;
            """
        )
        await conn.execute(
            "INSERT INTO source (id, label) VALUES (?, ?)",
            (1, "row-one"),
        )
        await conn.commit()
        cursor = await conn.execute("SELECT text FROM audit ORDER BY rowid")
        rows = await cursor.fetchall()
        assert rows == [{"text": "created;safe"}, {"text": "row-one"}]
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_slow_child_operation_does_not_starve_parent_event_loop() -> None:
    conn = await LibsqlProcessConnection.open(database=":memory:")
    ticks = 0
    stop = asyncio.Event()

    async def heartbeat() -> None:
        nonlocal ticks
        while not stop.is_set():
            ticks += 1
            await asyncio.sleep(0.01)

    task = asyncio.create_task(heartbeat())
    try:
        await conn._request("test_sleep", seconds=0.4)
    finally:
        stop.set()
        await task
        await conn.close()

    assert ticks >= 20


@pytest.mark.asyncio
async def test_dead_worker_is_replaced_without_losing_committed_file_data(tmp_path) -> None:
    path = tmp_path / "worker-restart.db"
    conn = await LibsqlProcessConnection.open(database=str(path))
    try:
        await conn.execute("CREATE TABLE durable (id INTEGER PRIMARY KEY, label TEXT)")
        await conn.execute(
            "INSERT INTO durable (id, label) VALUES (?, ?)",
            (1, "persisted"),
        )
        await conn.commit()
        original_pid = conn.identity.pid
        process = conn._process
        assert process is not None
        process.terminate()
        process.join(timeout=3)

        cursor = await conn.execute("SELECT label FROM durable WHERE id = 1")
        assert await cursor.fetchone() == {"label": "persisted"}
        assert conn.identity.pid != original_pid
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_timed_out_worker_and_pipe_are_discarded_together() -> None:
    conn = await LibsqlProcessConnection.open(database=":memory:")
    try:
        original_pid = conn.identity.pid
        conn.operation_timeout_seconds = 0.1
        with pytest.raises(TimeoutError, match="exceeded"):
            await conn._request("test_sleep", seconds=2.0)
        assert conn._process is None
        assert conn._parent_pipe is None

        conn.operation_timeout_seconds = 10.0
        cursor = await conn.execute("SELECT 1 AS ok")
        assert await cursor.fetchone() == {"ok": 1}
        assert conn.identity.pid != original_pid
    finally:
        await conn.close()


def test_every_production_entrypoint_uses_shared_process_database_factory() -> None:
    assert "from sniperplug.process_runtime import run" in MAIN
    assert "asyncio.run" not in MAIN
    assert "create_runtime_database" in PROCESS_RUNTIME
    assert "self.db = create_runtime_database" in PROCESS_RUNTIME
    assert "async def close" in PROCESS_RUNTIME
    assert "await self.db.close()" in PROCESS_RUNTIME
    assert 'self.backend = "turso-process-isolated"' in PROCESS_DATABASE
    assert "native_libsql_in_gateway_process=false" in PROCESS_DATABASE
    assert "large_integer_text_transport=true" in PROCESS_DATABASE
    assert "transaction_replay=false" in PROCESS_DATABASE
    assert "create_runtime_database" in HP_RUNTIME
    assert "create_runtime_database" in TARGET_RUNTIME
    assert "from sniperplug.storage.db import Database" not in HP_RUNTIME
    assert "from sniperplug.storage.db import Database" not in TARGET_RUNTIME
