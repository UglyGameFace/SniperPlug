from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from sniperplug.services.gateway_loop_health import (
    RecentLoopLagWindow,
    classify_gateway_loop_health,
)
from sniperplug.services.walmart_request_coordinator import walmart_request_coordinator


log = logging.getLogger("sniperplug.gateway_health")
GATEWAY_MONITOR_INTERVAL_SECONDS = 5.0
LOCAL_LOOP_WARNING_SECONDS = 2.0
GATEWAY_HEALTH_LOG_INTERVAL_SECONDS = 5 * 60.0
GATEWAY_MONITOR_MARKER = "gateway_loop_correlation_v284"


async def monitor_gateway_loop_health(bot: Any) -> None:
    """Correlate Discord heartbeat ACK latency with local asyncio scheduling lag.

    discord.py's ``Can't keep up ... websocket is ... behind`` warning is emitted
    from heartbeat ACK latency, not from its heartbeat-send-blocked path. This
    monitor keeps a 75-second local scheduling window so production can tell a
    transport/gateway delay from a real SniperPlug loop stall instead of blaming
    whichever background worker happened to be active at the same timestamp.
    """

    await bot.wait_until_ready()
    lag_window = RecentLoopLagWindow()
    expected = time.monotonic() + GATEWAY_MONITOR_INTERVAL_SECONDS
    last_health_log = time.monotonic() - GATEWAY_HEALTH_LOG_INTERVAL_SECONDS
    last_gateway_high_classification: str | None = None

    try:
        while True:
            await asyncio.sleep(GATEWAY_MONITOR_INTERVAL_SECONDS)
            now = time.monotonic()
            lag = max(0.0, now - expected)
            expected = now + GATEWAY_MONITOR_INTERVAL_SECONDS
            lag_window.record(lag, now_monotonic=now)
            recent_loop_lag = lag_window.max_recent(now_monotonic=now)
            health = classify_gateway_loop_health(
                gateway_latency_seconds=getattr(bot, "latency", None),
                recent_loop_lag_seconds=recent_loop_lag,
                local_loop_warning_seconds=LOCAL_LOOP_WARNING_SECONDS,
            )
            phase, active_workers = _runtime_context(bot)
            request_state = walmart_request_coordinator.snapshot()
            gateway_latency_text = _seconds_text(health.gateway_latency_seconds)

            if health.gateway_high:
                if health.classification != last_gateway_high_classification:
                    log.warning(
                        "Discord gateway heartbeat latency high marker=%s "
                        "classification=%s gateway_latency_s=%s "
                        "recent_loop_lag_max_s=%.2f phase=%s active_workers=%s "
                        "walmart_requests=%s",
                        GATEWAY_MONITOR_MARKER,
                        health.classification,
                        gateway_latency_text,
                        health.recent_loop_lag_seconds,
                        phase,
                        active_workers,
                        request_state,
                    )
                last_gateway_high_classification = health.classification
            elif last_gateway_high_classification is not None:
                log.info(
                    "Discord gateway heartbeat latency recovered marker=%s "
                    "previous_classification=%s gateway_latency_s=%s "
                    "recent_loop_lag_max_s=%.2f phase=%s active_workers=%s",
                    GATEWAY_MONITOR_MARKER,
                    last_gateway_high_classification,
                    gateway_latency_text,
                    health.recent_loop_lag_seconds,
                    phase,
                    active_workers,
                )
                last_gateway_high_classification = None

            if now - last_health_log >= GATEWAY_HEALTH_LOG_INTERVAL_SECONDS:
                log.info(
                    "Gateway watchdog health marker=%s classification=%s "
                    "gateway_latency_s=%s recent_loop_lag_max_s=%.2f "
                    "phase=%s active_workers=%s task_alive=true",
                    GATEWAY_MONITOR_MARKER,
                    health.classification,
                    gateway_latency_text,
                    health.recent_loop_lag_seconds,
                    phase,
                    active_workers,
                )
                last_health_log = now
    except asyncio.CancelledError:
        raise


def _runtime_context(bot: Any) -> tuple[str, str]:
    phases: list[str] = []
    active: list[str] = []
    for cog in tuple(getattr(bot, "cogs", {}).values()):
        phase = str(getattr(cog, "_runtime_phase", "") or "").strip()
        if phase and phase not in {"idle", "startup"}:
            phases.append(phase)
        if bool(getattr(cog, "_exact_active", False)):
            active.append("exact")
        if bool(getattr(cog, "_catalog_active", False)):
            active.append("catalog")
        if bool(getattr(cog, "_reconciliation_active", False)):
            active.append("reconciliation")

    phase_text = ",".join(dict.fromkeys(phases)) or "idle"
    active_text = ",".join(dict.fromkeys(active)) or "none"
    return phase_text[:240], active_text[:120]


def _seconds_text(value: float | None) -> str:
    if value is None:
        return "unknown"
    return f"{max(0.0, float(value)):.2f}"


__all__ = [
    "GATEWAY_HEALTH_LOG_INTERVAL_SECONDS",
    "GATEWAY_MONITOR_INTERVAL_SECONDS",
    "GATEWAY_MONITOR_MARKER",
    "LOCAL_LOOP_WARNING_SECONDS",
    "monitor_gateway_loop_health",
]
