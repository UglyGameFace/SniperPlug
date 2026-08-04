from __future__ import annotations

import asyncio
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass


EXACT_PRIORITY = 0
SEARCH_PRIORITY = 10
DEFAULT_CAPACITY = 3
DEFAULT_WAIT_TIMEOUT_SECONDS = 45.0


@dataclass(frozen=True)
class WalmartRequestLease:
    priority: int
    waited_seconds: float


class WalmartRequestCoordinator:
    """Thread-safe, exact-priority gate shared by every Walmart request.

    Walmart calls can originate from the bot loop, helper thread loops, and
    manual/background workers. A threading condition therefore owns the real
    concurrency state while async callers wait through ``asyncio.to_thread``.

    Exact item-detail requests have priority over catalog/search requests. Once
    an exact request is waiting, a newly arriving search cannot take the next
    released slot. Existing HTTP calls are never cancelled mid-flight.
    """

    def __init__(self, capacity: int = DEFAULT_CAPACITY) -> None:
        self.capacity = max(1, int(capacity))
        self._condition = threading.Condition()
        self._active = 0
        self._waiting_exact = 0
        self._waiting_search = 0
        self._cooldown_until = 0.0

    def acquire(
        self,
        *,
        priority: int,
        timeout_seconds: float = DEFAULT_WAIT_TIMEOUT_SECONDS,
    ) -> WalmartRequestLease:
        exact = int(priority) <= EXACT_PRIORITY
        started = time.monotonic()
        deadline = started + max(1.0, float(timeout_seconds))

        with self._condition:
            if exact:
                self._waiting_exact += 1
            else:
                self._waiting_search += 1
            try:
                while True:
                    now = time.monotonic()
                    cooldown_ready = now >= self._cooldown_until
                    exact_has_priority = exact or self._waiting_exact == 0
                    if (
                        cooldown_ready
                        and self._active < self.capacity
                        and exact_has_priority
                    ):
                        self._active += 1
                        return WalmartRequestLease(
                            priority=int(priority),
                            waited_seconds=max(0.0, now - started),
                        )

                    remaining = deadline - now
                    if remaining <= 0:
                        raise TimeoutError(
                            "Walmart request coordinator wait exceeded "
                            f"{max(1.0, float(timeout_seconds)):.1f}s"
                        )
                    cooldown_wait = max(0.0, self._cooldown_until - now)
                    wait_for = min(remaining, max(0.05, cooldown_wait or 0.25))
                    self._condition.wait(wait_for)
            finally:
                if exact:
                    self._waiting_exact = max(0, self._waiting_exact - 1)
                else:
                    self._waiting_search = max(0, self._waiting_search - 1)

    def release(self) -> None:
        with self._condition:
            if self._active <= 0:
                raise RuntimeError("Walmart request coordinator release without lease")
            self._active -= 1
            self._condition.notify_all()

    def apply_cooldown(self, seconds: float) -> None:
        delay = max(0.0, float(seconds))
        if delay <= 0:
            return
        with self._condition:
            self._cooldown_until = max(
                self._cooldown_until,
                time.monotonic() + delay,
            )
            self._condition.notify_all()

    def snapshot(self) -> dict[str, int | float]:
        with self._condition:
            return {
                "capacity": self.capacity,
                "active": self._active,
                "waiting_exact": self._waiting_exact,
                "waiting_search": self._waiting_search,
                "cooldown_seconds": max(
                    0.0,
                    self._cooldown_until - time.monotonic(),
                ),
            }


walmart_request_coordinator = WalmartRequestCoordinator()


@asynccontextmanager
async def walmart_request_slot(
    *,
    priority: int,
    timeout_seconds: float = DEFAULT_WAIT_TIMEOUT_SECONDS,
):
    lease = await asyncio.to_thread(
        walmart_request_coordinator.acquire,
        priority=int(priority),
        timeout_seconds=float(timeout_seconds),
    )
    try:
        yield lease
    finally:
        walmart_request_coordinator.release()
