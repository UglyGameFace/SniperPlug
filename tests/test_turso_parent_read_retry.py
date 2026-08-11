from __future__ import annotations

import logging

import pytest

from sniperplug.storage.libsql_process import LibsqlWorkerError
from sniperplug.storage.process_database import (
    TRANSIENT_READ_RETRY_ATTEMPTS,
    _SnowflakeSafeParameters,
    _await_turso_startup_readiness,
)


class _SequencedBaseConnection:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls: list[tuple[str, object]] = []
        self.discard_calls = 0

    async def execute(self, sql, params=None):
        self.calls.append((str(sql), params))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def _discard_worker_sync(self) -> None:
        self.discard_calls += 1


class _RetryConnection(_SnowflakeSafeParameters, _SequencedBaseConnection):
    pass


class _ReadinessConnection:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls: list[str] = []

    async def execute(self, sql, params=None):
        self.calls.append(str(sql))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _stream_error() -> LibsqlWorkerError:
    return LibsqlWorkerError(
        'ValueError: Hrana: api error: status=404 Not Found, '
        'body={"error":"stream not found: test"}'
    )


def _gateway_error() -> LibsqlWorkerError:
    return LibsqlWorkerError(
        'ValueError: Hrana: api error: status=502 Bad Gateway, '
        'body={"error":"upstream forward failed"}'
    )


def _transport_eof_error() -> LibsqlWorkerError:
    return LibsqlWorkerError(
        "ValueError: Hrana: cursor error: error reading a body from connection: "
        "unexpected EOF during chunk size line"
    )


@pytest.mark.asyncio
async def test_read_survives_stream_not_found_followed_by_gateway_failure(monkeypatch) -> None:
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr("sniperplug.storage.process_database.asyncio.sleep", fake_sleep)
    connection = _RetryConnection([_stream_error(), _gateway_error(), "ok"])

    result = await connection.execute("SELECT 1 FROM sqlite_master LIMIT 1")

    assert result == "ok"
    assert len(connection.calls) == 3
    assert connection.discard_calls == 2
    assert sleeps == [0.35, 0.7]


@pytest.mark.asyncio
async def test_read_survives_hrana_unexpected_eof_from_production(monkeypatch) -> None:
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr("sniperplug.storage.process_database.asyncio.sleep", fake_sleep)
    connection = _RetryConnection([_transport_eof_error(), "ok"])

    result = await connection.execute(
        "SELECT 1 FROM target_location_migrations WHERE migration_key = ? LIMIT 1",
        ("remove-unsafe-target-enrollment",),
    )

    assert result == "ok"
    assert len(connection.calls) == 2
    assert connection.discard_calls == 1
    assert sleeps == [0.35]


@pytest.mark.asyncio
async def test_write_is_never_replayed_after_transient_worker_error(monkeypatch) -> None:
    async def forbidden_sleep(_delay: float) -> None:
        raise AssertionError("write retry must not sleep or replay")

    monkeypatch.setattr("sniperplug.storage.process_database.asyncio.sleep", forbidden_sleep)
    connection = _RetryConnection([_gateway_error(), "must-not-run"])

    with pytest.raises(LibsqlWorkerError, match="502 Bad Gateway"):
        await connection.execute("UPDATE queue SET status = 'done' WHERE id = 1")

    assert len(connection.calls) == 1
    assert connection.discard_calls == 0


@pytest.mark.asyncio
async def test_eof_write_is_never_replayed(monkeypatch) -> None:
    async def forbidden_sleep(_delay: float) -> None:
        raise AssertionError("EOF write retry must not sleep or replay")

    monkeypatch.setattr("sniperplug.storage.process_database.asyncio.sleep", forbidden_sleep)
    connection = _RetryConnection([_transport_eof_error(), "must-not-run"])

    with pytest.raises(LibsqlWorkerError, match="unexpected EOF"):
        await connection.execute("UPDATE queue SET status = 'done' WHERE id = 1")

    assert len(connection.calls) == 1
    assert connection.discard_calls == 0


@pytest.mark.asyncio
async def test_transaction_adjacent_fatal_read_is_never_replayed(monkeypatch) -> None:
    async def forbidden_sleep(_delay: float) -> None:
        raise AssertionError("transaction-adjacent read must not retry")

    monkeypatch.setattr("sniperplug.storage.process_database.asyncio.sleep", forbidden_sleep)
    fatal = LibsqlWorkerError(
        "_FatalWorkerError: libSQL operation failed inside or near a transaction; "
        "Hrana: status=502 Bad Gateway"
    )
    connection = _RetryConnection([fatal, "must-not-run"])

    with pytest.raises(LibsqlWorkerError, match="inside or near a transaction"):
        await connection.execute("SELECT 1")

    assert len(connection.calls) == 1
    assert connection.discard_calls == 0


@pytest.mark.asyncio
async def test_transient_read_retry_is_bounded(monkeypatch) -> None:
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr("sniperplug.storage.process_database.asyncio.sleep", fake_sleep)
    connection = _RetryConnection([_gateway_error() for _ in range(TRANSIENT_READ_RETRY_ATTEMPTS)])

    with pytest.raises(LibsqlWorkerError, match="502 Bad Gateway"):
        await connection.execute("SELECT 1")

    assert len(connection.calls) == TRANSIENT_READ_RETRY_ATTEMPTS
    assert connection.discard_calls == TRANSIENT_READ_RETRY_ATTEMPTS - 1
    assert sleeps == [0.35, 0.7, 1.4, 2.0]


@pytest.mark.asyncio
async def test_read_survives_parent_worker_timeout(monkeypatch) -> None:
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr("sniperplug.storage.process_database.asyncio.sleep", fake_sleep)
    timeout = TimeoutError(
        "Turso/libSQL worker pid=77 exceeded 90.0s for execute."
    )
    connection = _RetryConnection([timeout, "ok"])

    result = await connection.execute("SELECT 1")

    assert result == "ok"
    assert len(connection.calls) == 2
    assert connection.discard_calls == 1
    assert sleeps == [0.35]


@pytest.mark.asyncio
async def test_parent_worker_timeout_write_is_never_replayed(monkeypatch) -> None:
    async def forbidden_sleep(_delay: float) -> None:
        raise AssertionError("timed-out write must not sleep or replay")

    monkeypatch.setattr("sniperplug.storage.process_database.asyncio.sleep", forbidden_sleep)
    timeout = TimeoutError(
        "Turso/libSQL worker pid=77 exceeded 90.0s for execute."
    )
    connection = _RetryConnection([timeout, "must-not-run"])

    with pytest.raises(TimeoutError, match="exceeded 90.0s"):
        await connection.execute("UPDATE queue SET status = 'done' WHERE id = 1")

    assert len(connection.calls) == 1
    assert connection.discard_calls == 0


@pytest.mark.asyncio
async def test_startup_readiness_waits_for_stable_consecutive_reads(monkeypatch) -> None:
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr("sniperplug.storage.process_database.asyncio.sleep", fake_sleep)
    connection = _ReadinessConnection([_gateway_error(), "ok", "ok", "ok"])

    await _await_turso_startup_readiness(
        connection,
        timeout_seconds=60.0,
        required_successes=3,
    )

    assert len(connection.calls) == 4
    assert sleeps == [1.0, 0.5, 0.5]
    assert set(connection.calls) == {"SELECT 1 AS sniperplug_turso_ready"}


@pytest.mark.asyncio
async def test_startup_readiness_resets_success_streak_after_transient_failure(monkeypatch) -> None:
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr("sniperplug.storage.process_database.asyncio.sleep", fake_sleep)
    connection = _ReadinessConnection(["ok", _transport_eof_error(), "ok", "ok", "ok"])

    await _await_turso_startup_readiness(
        connection,
        timeout_seconds=60.0,
        required_successes=3,
    )

    assert len(connection.calls) == 5
    assert sleeps == [0.5, 1.0, 0.5, 0.5]


@pytest.mark.asyncio
async def test_startup_readiness_outlives_reporting_window_and_recovers(
    monkeypatch,
    caplog,
) -> None:
    sleeps: list[float] = []

    class FakeLoop:
        now = 0.0

        def time(self) -> float:
            return self.now

    loop = FakeLoop()

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)
        loop.now += delay

    monkeypatch.setattr("sniperplug.storage.process_database.asyncio.sleep", fake_sleep)
    monkeypatch.setattr(
        "sniperplug.storage.process_database.asyncio.get_running_loop",
        lambda: loop,
    )
    caplog.set_level(logging.ERROR, logger="sniperplug")
    connection = _ReadinessConnection(
        [_gateway_error(), _gateway_error(), _gateway_error(), _gateway_error(), "ok", "ok", "ok"]
    )

    await _await_turso_startup_readiness(
        connection,
        timeout_seconds=5.0,
        required_successes=3,
    )

    assert len(connection.calls) == 7
    assert sleeps == [1.0, 2.0, 4.0, 8.0, 0.5, 0.5]
    assert "startup will continue waiting instead of crashing" in caplog.text
    assert "retryable_outage_fatal=false" in caplog.text


@pytest.mark.asyncio
async def test_startup_readiness_does_not_hide_non_transient_database_errors(monkeypatch) -> None:
    async def forbidden_sleep(_delay: float) -> None:
        raise AssertionError("non-transient startup errors must not be retried")

    monkeypatch.setattr("sniperplug.storage.process_database.asyncio.sleep", forbidden_sleep)
    connection = _ReadinessConnection(
        [LibsqlWorkerError("ValueError: no such table: broken_schema")]
    )

    with pytest.raises(LibsqlWorkerError, match="no such table"):
        await _await_turso_startup_readiness(
            connection,
            timeout_seconds=60.0,
            required_successes=3,
        )

    assert len(connection.calls) == 1
