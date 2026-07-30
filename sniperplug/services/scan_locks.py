from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Hashable


SCAN_LOCK_STALE_SECONDS = 180.0


@dataclass(frozen=True)
class ScanLockKey:
    guild_id: int | None
    user_id: int | None
    action: str
    preset: str | None = None
    query: str | None = None
    page: int | None = None
    min_discount: int | None = None

    def as_tuple(self) -> tuple[Hashable, ...]:
        return (
            self.guild_id,
            self.user_id,
            _normalize_text(self.action),
            _normalize_text(self.preset),
            _normalize_text(self.query),
            self.page,
            self.min_discount,
        )


class ScanOperationLocks:
    def __init__(self, *, stale_seconds: float = SCAN_LOCK_STALE_SECONDS) -> None:
        self._guard = asyncio.Lock()
        self._active: dict[tuple[Hashable, ...], float] = {}
        self._stale_seconds = max(1.0, float(stale_seconds))

    async def acquire(self, key: ScanLockKey) -> bool:
        normalized = key.as_tuple()
        now = time.monotonic()
        async with self._guard:
            self._discard_stale(now)
            if normalized in self._active:
                return False
            self._active[normalized] = now
            return True

    async def release(self, key: ScanLockKey) -> None:
        normalized = key.as_tuple()
        async with self._guard:
            self._active.pop(normalized, None)

    async def clear(self) -> None:
        async with self._guard:
            self._active.clear()

    def _discard_stale(self, now: float) -> None:
        stale_before = now - self._stale_seconds
        stale = [key for key, started_at in self._active.items() if started_at <= stale_before]
        for key in stale:
            self._active.pop(key, None)


def _normalize_text(value: object | None) -> str:
    return " ".join(str(value or "").split()).casefold()


scan_operation_locks = ScanOperationLocks()
