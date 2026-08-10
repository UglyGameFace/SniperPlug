from __future__ import annotations

import pytest

from sniperplug.storage.libsql_process import LibsqlWorkerError
from sniperplug.storage.process_database import (
    TRANSIENT_READ_RETRY_ATTEMPTS,
    _SnowflakeSafeParameters,
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
