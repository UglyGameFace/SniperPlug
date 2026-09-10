from __future__ import annotations

import logging
from logging.handlers import QueueHandler, RotatingFileHandler
from pathlib import Path

from sniperplug.services import error_logging
from sniperplug.services.gateway_loop_health import (
    RecentLoopLagWindow,
    classify_gateway_loop_health,
    normalize_gateway_latency_seconds,
)
from sniperplug.services.gateway_runtime_monitor import _runtime_context


ROOT = Path(__file__).resolve().parents[1]
PROCESS_RUNTIME = (ROOT / "sniperplug/process_runtime.py").read_text(encoding="utf-8")
GATEWAY_MONITOR = (
    ROOT / "sniperplug/services/gateway_runtime_monitor.py"
).read_text(encoding="utf-8")
ERROR_LOGGING = (ROOT / "sniperplug/services/error_logging.py").read_text(
    encoding="utf-8"
)


def test_gateway_ack_latency_is_not_mislabeled_as_local_loop_lag() -> None:
    health = classify_gateway_loop_health(
        gateway_latency_seconds=41.3,
        recent_loop_lag_seconds=0.08,
        local_loop_warning_seconds=2.0,
    )

    assert health.classification == "gateway_ack_latency_loop_healthy"
    assert health.gateway_high is True
    assert health.gateway_latency_seconds == 41.3


def test_gateway_latency_and_real_loop_lag_are_reported_together() -> None:
    health = classify_gateway_loop_health(
        gateway_latency_seconds=41.5,
        recent_loop_lag_seconds=3.25,
        local_loop_warning_seconds=2.0,
    )

    assert health.classification == "gateway_and_local_loop_lag"
    assert health.gateway_high is True
    assert health.recent_loop_lag_seconds == 3.25


def test_local_loop_lag_can_be_detected_without_gateway_latency() -> None:
    health = classify_gateway_loop_health(
        gateway_latency_seconds=0.12,
        recent_loop_lag_seconds=2.1,
        local_loop_warning_seconds=2.0,
    )

    assert health.classification == "local_loop_lag_only"
    assert health.gateway_high is False


def test_gateway_latency_normalizer_rejects_unknown_values() -> None:
    assert normalize_gateway_latency_seconds(float("nan")) is None
    assert normalize_gateway_latency_seconds(float("inf")) is None
    assert normalize_gateway_latency_seconds(-1) is None
    assert normalize_gateway_latency_seconds("0.25") == 0.25


def test_recent_loop_window_keeps_only_correlated_period() -> None:
    window = RecentLoopLagWindow(window_seconds=75.0)
    window.record(4.0, now_monotonic=100.0)
    window.record(0.2, now_monotonic=150.0)

    assert window.max_recent(now_monotonic=150.0) == 4.0
    assert window.max_recent(now_monotonic=176.0) == 0.2


def test_runtime_context_reports_active_autoscan_phase_without_importing_cog_types() -> None:
    class Cog:
        _runtime_phase = "exact_verification_queue"
        _exact_active = True
        _catalog_active = False
        _reconciliation_active = False

    class Bot:
        cogs = {"autoscan": Cog()}

    phase, active = _runtime_context(Bot())

    assert phase == "exact_verification_queue"
    assert active == "exact"


def test_production_runtime_disables_discord_default_log_handler_and_owns_monitor() -> None:
    assert "bot.run(settings.discord_token, log_handler=None)" in PROCESS_RUNTIME
    assert "monitor_gateway_loop_health(self)" in PROCESS_RUNTIME
    assert 'name="sniperplug-gateway-loop-correlation"' in PROCESS_RUNTIME
    assert "shutdown_runtime_logging()" in PROCESS_RUNTIME


def test_gateway_monitor_has_bounded_liveness_and_correlation_markers() -> None:
    assert 'GATEWAY_MONITOR_MARKER = "gateway_loop_correlation_v284"' in GATEWAY_MONITOR
    assert "GATEWAY_MONITOR_INTERVAL_SECONDS = 5.0" in GATEWAY_MONITOR
    assert "GATEWAY_HEALTH_LOG_INTERVAL_SECONDS = 5 * 60.0" in GATEWAY_MONITOR
    assert "recent_loop_lag_max_s" in GATEWAY_MONITOR
    assert "gateway_latency_s" in GATEWAY_MONITOR
    assert "task_alive=true" in GATEWAY_MONITOR


def test_runtime_logging_uses_queue_listener_for_console_and_file_io(tmp_path, monkeypatch) -> None:
    error_logging.shutdown_runtime_logging()
    log_path = tmp_path / "runtime.log"
    monkeypatch.setenv("SNIPERPLUG_LOG_FILE", str(log_path))
    monkeypatch.setenv("SNIPERPLUG_LOG_LEVEL", "INFO")

    try:
        error_logging.configure_runtime_logging()
        queue_handler = error_logging._RUNTIME_QUEUE_HANDLER
        listener = error_logging._RUNTIME_LOG_LISTENER
        sinks = error_logging._RUNTIME_LOG_SINKS

        assert isinstance(queue_handler, QueueHandler)
        assert listener is not None
        assert queue_handler in logging.getLogger().handlers
        assert any(isinstance(handler, RotatingFileHandler) for handler in sinks)
        assert all(handler is not queue_handler for handler in sinks)

        logging.getLogger("sniperplug.test.gateway284").info("queued-log-proof-v284")
    finally:
        error_logging.shutdown_runtime_logging()

    assert "queued-log-proof-v284" in log_path.read_text(encoding="utf-8")


def test_runtime_logging_source_has_no_direct_root_stream_or_file_sink_install() -> None:
    configure_block = ERROR_LOGGING.split("def configure_runtime_logging", 1)[1].split(
        "def shutdown_runtime_logging", 1
    )[0]
    assert "QueueHandler(log_queue)" in configure_block
    assert "QueueListener(" in configure_block
    assert "root.addHandler(queue_handler)" in configure_block
    assert "root.addHandler(console)" not in configure_block
    assert "root.addHandler(file_handler)" not in configure_block
