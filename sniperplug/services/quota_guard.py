from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class QuotaSnapshot:
    provider: str
    monthly_used: int
    monthly_limit: int
    daily_used: int
    daily_limit: int
    hourly_user_used: int
    hourly_user_limit: int
    allowed: bool
    reason: str | None = None

    @property
    def monthly_remaining(self) -> int:
        return max(self.monthly_limit - self.monthly_used, 0)

    @property
    def daily_remaining(self) -> int:
        return max(self.daily_limit - self.daily_used, 0)


@dataclass
class QuotaGuard:
    provider: str
    monthly_limit: int = 250
    safe_monthly_limit: int = 200
    daily_limit: int = 6
    hourly_user_limit: int = 3
    _monthly_counts: dict[str, int] = field(default_factory=dict)
    _daily_counts: dict[str, int] = field(default_factory=dict)
    _hourly_user_counts: dict[tuple[str, int], int] = field(default_factory=dict)

    def check(self, user_id: int, cost: int = 1, now: datetime | None = None) -> QuotaSnapshot:
        now = now or utc_now()
        month_key = now.strftime("%Y-%m")
        day_key = now.strftime("%Y-%m-%d")
        hour_key = now.strftime("%Y-%m-%dT%H")

        monthly_used = self._monthly_counts.get(month_key, 0)
        daily_used = self._daily_counts.get(day_key, 0)
        hourly_used = self._hourly_user_counts.get((hour_key, user_id), 0)

        monthly_limit = min(self.monthly_limit, self.safe_monthly_limit)
        reason = None
        allowed = True
        if monthly_used + cost > monthly_limit:
            allowed = False
            reason = f"Monthly safe SerpApi budget would be exceeded ({monthly_used}/{monthly_limit})."
        elif daily_used + cost > self.daily_limit:
            allowed = False
            reason = f"Daily SerpApi scan budget would be exceeded ({daily_used}/{self.daily_limit})."
        elif hourly_used + cost > self.hourly_user_limit:
            allowed = False
            reason = f"Hourly manual scan limit would be exceeded ({hourly_used}/{self.hourly_user_limit})."

        return QuotaSnapshot(
            provider=self.provider,
            monthly_used=monthly_used,
            monthly_limit=monthly_limit,
            daily_used=daily_used,
            daily_limit=self.daily_limit,
            hourly_user_used=hourly_used,
            hourly_user_limit=self.hourly_user_limit,
            allowed=allowed,
            reason=reason,
        )

    def record(self, user_id: int, cost: int = 1, now: datetime | None = None) -> QuotaSnapshot:
        now = now or utc_now()
        snapshot = self.check(user_id=user_id, cost=cost, now=now)
        if not snapshot.allowed:
            return snapshot

        month_key = now.strftime("%Y-%m")
        day_key = now.strftime("%Y-%m-%d")
        hour_key = now.strftime("%Y-%m-%dT%H")
        self._monthly_counts[month_key] = self._monthly_counts.get(month_key, 0) + cost
        self._daily_counts[day_key] = self._daily_counts.get(day_key, 0) + cost
        self._hourly_user_counts[(hour_key, user_id)] = self._hourly_user_counts.get((hour_key, user_id), 0) + cost
        return self.check(user_id=user_id, cost=0, now=now)


serpapi_quota_guard = QuotaGuard(provider="serpapi_home_depot")
