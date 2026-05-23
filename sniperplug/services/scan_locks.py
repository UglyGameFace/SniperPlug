from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Hashable


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
            self.action.strip().lower(),
            (self.preset or "").strip().lower(),
            (self.query or "").strip().lower(),
            self.page,
            self.min_discount,
        )


class ScanOperationLocks:
    def __init__(self) -> None:
        self._guard = asyncio.Lock()
        self._active: set[tuple[Hashable, ...]] = set()

    async def acquire(self, key: ScanLockKey) -> bool:
        normalized = key.as_tuple()
        async with self._guard:
            if normalized in self._active:
                return False
            self._active.add(normalized)
            return True

    async def release(self, key: ScanLockKey) -> None:
        normalized = key.as_tuple()
        async with self._guard:
            self._active.discard(normalized)


scan_operation_locks = ScanOperationLocks()
