from pathlib import Path


def test_autoscan_now_defers_before_database_work():
    source = Path("sniperplug/cogs/auto_scan_runner.py").read_text(encoding="utf-8")
    start = source.index("async def autoscan_now(")
    end = source.index("async def _run_autoscan_now_background(")
    body = source[start:end]

    assert "await safe_defer(interaction, ephemeral=True, thinking=True)" in body
    assert "asyncio.create_task(self._run_autoscan_now_background" in body
    assert body.index("await safe_defer(interaction") < body.index("asyncio.create_task")


def test_autoscan_now_database_work_is_in_background_helper():
    source = Path("sniperplug/cogs/auto_scan_runner.py").read_text(encoding="utf-8")
    start = source.index("async def autoscan_now(")
    end = source.index("async def _run_autoscan_now_background(")
    immediate_body = source[start:end]

    assert "get_public_alert_config" not in immediate_body
    assert "_run_guild_walmart_discovery" not in immediate_body


def test_autoscan_now_error_decorator_has_own_line():
    source = Path("sniperplug/cogs/auto_scan_runner.py").read_text(encoding="utf-8")

    assert "@autoscan_now.error\n    async def autoscan_now_error" in source


def test_background_autoscan_uses_bounded_observed_memory_rechecks():
    auto = Path("sniperplug/cogs/auto_scan_runner.py").read_text(encoding="utf-8")
    memory = Path("sniperplug/services/autoscan_observed_price_memory.py").read_text(encoding="utf-8")

    assert "run_autoscan_verified_category_with_observed_memory" in auto
    assert "use_price_memory=False" not in auto
    assert "AUTOSCAN_MEMORY_RECHECK_LIMIT = 4" in memory
    assert "remembered_walmart_search_seeds" in memory
    assert "limit=AUTOSCAN_MEMORY_RECHECK_LIMIT" in memory
