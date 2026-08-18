from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class OperationLockTicket:
    key: str


class AsyncOperationLockRegistry:
    """In-process duplicate blocker for user-triggered Discord operations."""

    def __init__(self) -> None:
        self._guard = asyncio.Lock()
        self._active: set[str] = set()

    async def acquire(self, key: str) -> OperationLockTicket | None:
        normalized = normalize_lock_part(key)
        async with self._guard:
            if normalized in self._active:
                return None
            self._active.add(normalized)
        return OperationLockTicket(normalized)

    async def release(self, ticket_or_key: OperationLockTicket | str | None) -> None:
        if ticket_or_key is None:
            return
        key = ticket_or_key.key if isinstance(ticket_or_key, OperationLockTicket) else ticket_or_key
        normalized = normalize_lock_part(key)
        async with self._guard:
            self._active.discard(normalized)

    async def is_active(self, key: str) -> bool:
        normalized = normalize_lock_part(key)
        async with self._guard:
            return normalized in self._active

    async def reset(self) -> None:
        async with self._guard:
            self._active.clear()


def normalize_lock_part(value: object) -> str:
    text = "" if value is None else str(value)
    text = " ".join(text.strip().lower().split())
    return text or "none"


def make_operation_key(*parts: object) -> str:
    return ":".join(normalize_lock_part(part) for part in parts)


def make_scan_operation_key(
    *,
    guild_id: object,
    user_id: object,
    action: str,
    parts: Iterable[object] = (),
) -> str:
    return make_operation_key("discord-scan", guild_id or "dm", user_id or "unknown-user", action, *parts)


scan_operation_locks = AsyncOperationLockRegistry()
