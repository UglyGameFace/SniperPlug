"""The reader's OpenCV self-repair (2026-11-19 live finding).

Discloud's Python image has no libGL, so the opencv-python wheel
rapidocr depends on cannot import there; the reader installs the
headless build over it the first time the rapidocr import fails.
These tests pin the repair contract: no repair when the import
works, exactly one repair + retry when it fails, and the failure
still surfaces when the retry fails too.
"""

from __future__ import annotations

import pytest

from sniperplug.ocr_reader.engine import _load_rapidocr_class


def test_no_repair_when_import_works() -> None:
    repairs: list[int] = []
    sentinel = object()

    result = _load_rapidocr_class(
        import_fn=lambda: sentinel,
        repair_fn=lambda: repairs.append(1),
    )

    assert result is sentinel
    assert repairs == []


def test_one_repair_then_retry() -> None:
    attempts: list[int] = []
    repairs: list[int] = []
    sentinel = object()

    def flaky_import() -> object:
        attempts.append(1)
        if len(attempts) == 1:
            raise ImportError("libGL.so.1: cannot open shared object file")
        return sentinel

    result = _load_rapidocr_class(
        import_fn=flaky_import,
        repair_fn=lambda: repairs.append(1),
    )

    assert result is sentinel
    assert len(attempts) == 2
    assert repairs == [1]


def test_second_failure_propagates() -> None:
    repairs: list[int] = []

    def always_fail() -> object:
        raise ImportError("libGL.so.1: cannot open shared object file")

    with pytest.raises(ImportError):
        _load_rapidocr_class(
            import_fn=always_fail,
            repair_fn=lambda: repairs.append(1),
        )
    assert repairs == [1]


def test_repair_failure_propagates_without_retry() -> None:
    attempts: list[int] = []

    def fail_import() -> object:
        attempts.append(1)
        raise ImportError("libGL.so.1: cannot open shared object file")

    def fail_repair() -> None:
        raise RuntimeError("pip install failed")

    with pytest.raises(RuntimeError):
        _load_rapidocr_class(import_fn=fail_import, repair_fn=fail_repair)
    assert attempts == [1]
