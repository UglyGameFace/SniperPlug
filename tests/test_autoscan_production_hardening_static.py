from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER = (ROOT / "sniperplug/cogs/resilient_auto_scan_runner.py").read_text(encoding="utf-8")
BOT = (ROOT / "sniperplug/bot.py").read_text(encoding="utf-8")
FEEDBACK = (ROOT / "sniperplug/services/bounded_feedback_views.py").read_text(encoding="utf-8")
DISCLOUD = (ROOT / "discloud.config").read_text(encoding="utf-8")


def test_discloud_runtime_is_pinned() -> None:
    assert "VERSION=3.11" in DISCLOUD
    assert "VERSION=latest" not in DISCLOUD


def test_scheduled_scans_are_small_and_serialized() -> None:
    assert "SCHEDULED_QUERY_COUNT = 4" in RUNNER
    assert "SCHEDULED_MIN_INTERVAL_SECONDS = 6 * 60 * 60" in RUNNER
    assert "_WALMART_SCHEDULE_LOCK = asyncio.Lock()" in RUNNER
    assert "async with _WALMART_SCHEDULE_LOCK" in RUNNER
    assert "for guild in guilds:" in RUNNER
    assert "query_count_override=SCHEDULED_QUERY_COUNT" in RUNNER


def test_scheduled_runner_does_not_destroy_whole_scan_on_timeout() -> None:
    scheduled = RUNNER.split("async def _run_scheduled_guild", 1)[1]
    assert "asyncio.wait_for(" not in scheduled
    assert "AUTO_SCAN_GUILD_TIMEOUT_SECONDS" not in scheduled


def test_manual_scan_remains_bounded_without_deep_followup() -> None:
    manual = RUNNER.split("async def _run_autoscan_now_background", 1)[1].split(
        "async def _autoscan_progress_notice", 1
    )[0]
    assert "query_count_override=8" in manual
    assert "AUTO_SCAN_MANUAL_QUERY_COUNT" not in manual
    assert "Deep follow-up" not in manual
    assert "asyncio.wait_for(" not in manual


def test_event_loop_lag_is_observable() -> None:
    assert "EVENT_LOOP_LAG_WARNING_SECONDS = 2.0" in RUNNER
    assert "Event loop lag detected" in RUNNER
    assert "sniperplug-event-loop-watchdog" in RUNNER


def test_all_autoscan_warnings_are_logged() -> None:
    assert "for index, warning in enumerate(tuple(report.warnings or ())" in RUNNER
    assert "Autoscan warning source=%s" in RUNNER


def test_feedback_views_are_bounded() -> None:
    assert "MAX_PERSISTENT_FEEDBACK_VIEWS = 250" in FEEDBACK
    assert "targets[:remaining]" in FEEDBACK
    assert "register_bounded_persistent_feedback_views" in BOT
    assert "bounded cap=250" in BOT


def test_runtime_identity_is_logged() -> None:
    assert "Runtime identity python=%s" in BOT
    assert "platform.python_version()" in BOT
    assert "sys.executable" in BOT
