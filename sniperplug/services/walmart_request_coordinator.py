from __future__ import annotations

import asyncio
import os
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        return default


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class WalmartQuotaSnapshot:
    provider: str = "walmart"
    monthly_used: int = 0
    monthly_limit: int = 0
    daily_used: int = 0
    daily_limit: int = 0
    hourly_used: int = 0
    hourly_limit: int = 0
    allowed: bool = True
    reason: str | None = None

    @property
    def configured(self) -> bool:
        """True when at least one window has a cap; otherwise fail-open."""

        return (
            self.monthly_limit > 0
            or self.daily_limit > 0
            or self.hourly_limit > 0
        )

    @property
    def monthly_remaining(self) -> int:
        return max(self.monthly_limit - self.monthly_used, 0)

    @property
    def daily_remaining(self) -> int:
        return max(self.daily_limit - self.daily_used, 0)

    @property
    def hourly_remaining(self) -> int:
        return max(self.hourly_limit - self.hourly_used, 0)


@dataclass
class WalmartQuotaGuard:
    """Monthly/daily/hourly cap on Walmart Affiliate API requests.

    Mirrors :class:`sniperplug.services.quota_guard.QuotaGuard` (SerpApi /
    Home Depot) but counts every Walmart request the coordinator grants,
    across all callers — the background workers are the volume, manual
    commands are a rounding error, and per-user buckets would undercount
    the shared Affiliate quota.

    A limit of ``0`` means that window is uncapped (fail-open). The class
    defaults are conservative ceilings above current steady-state volume
    (catalog ~4 searches/min, exact batches of up to 20 item IDs per
    lookup); the deployed singleton below reads env and stays fully
    uncapped unless an operator sets a limit deliberately.
    """

    provider: str = "walmart"
    monthly_limit: int = 200_000
    daily_limit: int = 10_000
    hourly_limit: int = 600
    _monthly_counts: dict[str, int] = field(default_factory=dict)
    _daily_counts: dict[str, int] = field(default_factory=dict)
    _hourly_counts: dict[str, int] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def check(self, cost: int = 1, now: datetime | None = None) -> WalmartQuotaSnapshot:
        now = now or _utcnow()
        with self._lock:
            monthly_used = self._monthly_counts.get(now.strftime("%Y-%m"), 0)
            daily_used = self._daily_counts.get(now.strftime("%Y-%m-%d"), 0)
            hourly_used = self._hourly_counts.get(now.strftime("%Y-%m-%dT%H"), 0)

        reason = None
        allowed = True
        if self.monthly_limit > 0 and monthly_used + cost > self.monthly_limit:
            allowed = False
            reason = (
                f"Monthly Walmart budget would be exceeded "
                f"({monthly_used}/{self.monthly_limit})."
            )
        elif self.daily_limit > 0 and daily_used + cost > self.daily_limit:
            allowed = False
            reason = (
                f"Daily Walmart budget would be exceeded "
                f"({daily_used}/{self.daily_limit})."
            )
        elif self.hourly_limit > 0 and hourly_used + cost > self.hourly_limit:
            allowed = False
            reason = (
                f"Hourly Walmart budget would be exceeded "
                f"({hourly_used}/{self.hourly_limit})."
            )

        return WalmartQuotaSnapshot(
            provider=self.provider,
            monthly_used=monthly_used,
            monthly_limit=self.monthly_limit,
            daily_used=daily_used,
            daily_limit=self.daily_limit,
            hourly_used=hourly_used,
            hourly_limit=self.hourly_limit,
            allowed=allowed,
            reason=reason,
        )

    def record(self, cost: int = 1, now: datetime | None = None) -> WalmartQuotaSnapshot:
        """Count a request only when the guard allows it; else deny silently."""

        now = now or _utcnow()
        snapshot = self.check(cost=cost, now=now)
        if not snapshot.allowed:
            return snapshot
        self.note_request(cost=cost, now=now)
        return self.check(cost=0, now=now)

    def note_request(self, cost: int = 1, now: datetime | None = None) -> None:
        """Unconditionally count requests that actually happened.

        The coordinator calls this for every granted lease so usage stays
        honest even while the guard is over its cap (over-cap requests are
        still real Affiliate API calls). Never raises.
        """

        now = now or _utcnow()
        try:
            with self._lock:
                month_key = now.strftime("%Y-%m")
                day_key = now.strftime("%Y-%m-%d")
                hour_key = now.strftime("%Y-%m-%dT%H")
                self._monthly_counts[month_key] = (
                    self._monthly_counts.get(month_key, 0) + max(0, int(cost))
                )
                self._daily_counts[day_key] = (
                    self._daily_counts.get(day_key, 0) + max(0, int(cost))
                )
                self._hourly_counts[hour_key] = (
                    self._hourly_counts.get(hour_key, 0) + max(0, int(cost))
                )
        except Exception:
            pass


def build_walmart_quota_guard_from_env() -> WalmartQuotaGuard:
    """Build the deployed guard: unset env means that window is uncapped."""

    return WalmartQuotaGuard(
        monthly_limit=_env_int("WALMART_MONTHLY_LIMIT", 0),
        daily_limit=_env_int("WALMART_DAILY_LIMIT", 0),
        hourly_limit=_env_int("WALMART_HOURLY_LIMIT", 0),
    )


# Unset env -> every window uncapped -> existing behaviour, no cap enforced.
walmart_quota_guard = build_walmart_quota_guard_from_env()


def walmart_quota_snapshot(cost: int = 1) -> WalmartQuotaSnapshot:
    """Current Walmart quota state; fail-open when anything goes wrong."""

    try:
        return walmart_quota_guard.check(cost=cost)
    except Exception:
        return WalmartQuotaSnapshot(allowed=True)


def walmart_quota_allows(cost: int = 1) -> bool:
    return walmart_quota_snapshot(cost=cost).allowed


EXACT_PRIORITY = 0
INTERACTIVE_SEARCH_PRIORITY = 5
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
        self._waiting_interactive = 0
        self._waiting_search = 0
        self._cooldown_until = 0.0

    def acquire(
        self,
        *,
        priority: int,
        timeout_seconds: float = DEFAULT_WAIT_TIMEOUT_SECONDS,
    ) -> WalmartRequestLease:
        exact = int(priority) <= EXACT_PRIORITY
        interactive = (
            not exact and int(priority) < SEARCH_PRIORITY
        )
        started = time.monotonic()
        deadline = started + max(1.0, float(timeout_seconds))

        with self._condition:
            if exact:
                self._waiting_exact += 1
            elif interactive:
                self._waiting_interactive += 1
            else:
                self._waiting_search += 1
            try:
                while True:
                    now = time.monotonic()
                    cooldown_ready = now >= self._cooldown_until
                    exact_has_priority = (
                        exact
                        or (interactive and self._waiting_exact == 0)
                        or (
                            not exact
                            and not interactive
                            and self._waiting_exact == 0
                            and self._waiting_interactive == 0
                        )
                    )
                    if (
                        cooldown_ready
                        and self._active < self.capacity
                        and exact_has_priority
                    ):
                        self._active += 1
                        # Count the request that is about to happen. The
                        # guard never blocks a grant here: background workers
                        # pre-check it and pause, while a user-initiated call
                        # is too rare to fail. Counting stays honest even
                        # over the cap.
                        walmart_quota_guard.note_request()
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
                elif interactive:
                    self._waiting_interactive = max(0, self._waiting_interactive - 1)
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
                "waiting_interactive": self._waiting_interactive,
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
