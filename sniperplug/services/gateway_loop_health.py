from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass


# discord.py 2.7.1 emits its "Can't keep up ... websocket is ... behind"
# warning from KeepAliveHandler.ack() when heartbeat-to-ACK latency exceeds 10s.
DISCORD_GATEWAY_ACK_WARNING_SECONDS = 10.0
# Keep slightly more than one default 60s heartbeat-timeout window so a late ACK
# can be correlated with any local scheduling stall that happened while it was in
# flight instead of comparing only the instant the ACK finally arrived.
GATEWAY_LOOP_CORRELATION_WINDOW_SECONDS = 75.0


@dataclass(frozen=True)
class GatewayLoopHealth:
    classification: str
    gateway_latency_seconds: float | None
    recent_loop_lag_seconds: float

    @property
    def gateway_high(self) -> bool:
        return self.classification in {
            "gateway_ack_latency_loop_healthy",
            "gateway_and_local_loop_lag",
        }


class RecentLoopLagWindow:
    """Retain a tiny monotonic window of watchdog scheduling delays."""

    def __init__(self, *, window_seconds: float = GATEWAY_LOOP_CORRELATION_WINDOW_SECONDS) -> None:
        self.window_seconds = max(5.0, float(window_seconds))
        self._samples: deque[tuple[float, float]] = deque()

    def record(self, lag_seconds: float, *, now_monotonic: float) -> None:
        now_value = float(now_monotonic)
        lag_value = max(0.0, float(lag_seconds))
        self._samples.append((now_value, lag_value))
        self._prune(now_value)

    def max_recent(self, *, now_monotonic: float) -> float:
        now_value = float(now_monotonic)
        self._prune(now_value)
        if not self._samples:
            return 0.0
        return max(sample[1] for sample in self._samples)

    def _prune(self, now_monotonic: float) -> None:
        cutoff = float(now_monotonic) - self.window_seconds
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()


def normalize_gateway_latency_seconds(value: object) -> float | None:
    """Return a finite non-negative Discord heartbeat latency when available."""

    try:
        latency = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(latency) or latency < 0:
        return None
    return latency


def classify_gateway_loop_health(
    *,
    gateway_latency_seconds: object,
    recent_loop_lag_seconds: float,
    local_loop_warning_seconds: float,
    gateway_warning_seconds: float = DISCORD_GATEWAY_ACK_WARNING_SECONDS,
) -> GatewayLoopHealth:
    """Separate Discord protocol latency from actual local asyncio starvation."""

    gateway_latency = normalize_gateway_latency_seconds(gateway_latency_seconds)
    loop_lag = max(0.0, float(recent_loop_lag_seconds))
    loop_high = loop_lag >= max(0.001, float(local_loop_warning_seconds))
    gateway_high = (
        gateway_latency is not None
        and gateway_latency >= max(0.001, float(gateway_warning_seconds))
    )

    if gateway_high and loop_high:
        classification = "gateway_and_local_loop_lag"
    elif gateway_high:
        classification = "gateway_ack_latency_loop_healthy"
    elif loop_high:
        classification = "local_loop_lag_only"
    elif gateway_latency is None:
        classification = "gateway_unknown"
    else:
        classification = "healthy"

    return GatewayLoopHealth(
        classification=classification,
        gateway_latency_seconds=gateway_latency,
        recent_loop_lag_seconds=loop_lag,
    )


__all__ = [
    "DISCORD_GATEWAY_ACK_WARNING_SECONDS",
    "GATEWAY_LOOP_CORRELATION_WINDOW_SECONDS",
    "GatewayLoopHealth",
    "RecentLoopLagWindow",
    "classify_gateway_loop_health",
    "normalize_gateway_latency_seconds",
]
