from __future__ import annotations

import pytest

from sniperplug.storage.libsql_process import (
    _FatalWorkerError,
    _commit_once,
    _execute_transaction_safe,
)


class FakeConnection:
    def __init__(self, *, in_transaction: bool, error: Exception | None = None):
        self.in_transaction = in_transaction
        self.error = error
        self.executed: list[tuple[str, object]] = []
        self.rollback_calls = 0
        self.commit_calls = 0

    def execute(self, sql, params=None):
        self.executed.append((str(sql), params))
        if self.error is not None:
            raise self.error
        return object()

    def rollback(self):
        self.rollback_calls += 1

    def commit(self):
        self.commit_calls += 1
        if self.error is not None:
            raise self.error


def stream_error() -> RuntimeError:
    return RuntimeError("Hrana stream not found: closed stream")


def test_read_only_stream_failure_reconnects_once_without_transaction() -> None:
    first = FakeConnection(in_transaction=False, error=stream_error())
    second = FakeConnection(in_transaction=False)
    reconnect_calls = 0

    def reconnect():
        nonlocal reconnect_calls
        reconnect_calls += 1
        return second

    result = _execute_transaction_safe(first, "SELECT 1", (), reconnect)

    assert result is not None
    assert reconnect_calls == 1
    assert len(first.executed) == 1
    assert len(second.executed) == 1


def test_write_stream_failure_is_never_replayed() -> None:
    first = FakeConnection(in_transaction=False, error=stream_error())
    reconnect_calls = 0

    def reconnect():
        nonlocal reconnect_calls
        reconnect_calls += 1
        return FakeConnection(in_transaction=False)

    with pytest.raises(_FatalWorkerError, match="discarded instead of replaying"):
        _execute_transaction_safe(
            first,
            "UPDATE queue SET status = 'verified' WHERE id = 1",
            (),
            reconnect,
        )

    assert reconnect_calls == 0


def test_any_failure_inside_active_transaction_discards_worker() -> None:
    connection = FakeConnection(
        in_transaction=True,
        error=RuntimeError("constraint failed"),
    )

    with pytest.raises(_FatalWorkerError, match="inside or near a transaction"):
        _execute_transaction_safe(connection, "SELECT 1", (), lambda: None)

    assert connection.rollback_calls == 1


def test_failed_commit_is_not_retried_on_new_connection() -> None:
    connection = FakeConnection(
        in_transaction=True,
        error=stream_error(),
    )

    with pytest.raises(_FatalWorkerError, match="commit outcome is unknown"):
        _commit_once(connection)

    assert connection.commit_calls == 1
