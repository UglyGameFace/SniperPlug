from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER = (ROOT / "sniperplug/cogs/resilient_auto_scan_runner.py").read_text(encoding="utf-8")
REPAIR = (ROOT / "sniperplug/services/setup_self_heal.py").read_text(encoding="utf-8")


def test_scheduler_repairs_all_live_guilds_before_listing_eligible_rows() -> None:
    repair_call = RUNNER.index("repair_all_public_alert_setups(self.bot.db, self.bot)")
    list_call = RUNNER.index("legacy.list_public_alert_guilds(self.bot.db, bot=self.bot)")
    assert repair_call < list_call
    assert "Autoscan eligible live guilds" in RUNNER
    assert "needs_action" in RUNNER


def test_unambiguous_named_deal_channel_can_be_adopted() -> None:
    assert "discover_unambiguous_deal_channel" in REPAIR
    assert '"walmart-deals"' in REPAIR
    assert '"deal-alerts"' in REPAIR
    assert '"deals"' in REPAIR
    assert "not missing_channel_permissions(channel, member)" in REPAIR
    assert 'source = "unambiguous server channel discovery"' in REPAIR


def test_ambiguous_channels_are_never_guessed() -> None:
    assert "multiple possible deal channels were found" in REPAIR
    assert "SniperPlug refused to guess" in REPAIR
    assert "len(discovery_matches) > 1" in REPAIR


def test_manual_command_channel_remains_safe_one_step_repair() -> None:
    assert '"current command channel"' in REPAIR
    assert "Run `/autoscan_now force:true` once inside the exact channel" in REPAIR


def test_runtime_floor_is_not_misrepresented_as_unlimited_execution() -> None:
    assert "runtime six-hour safety floor still applies" in REPAIR
    assert "SCHEDULED_MIN_INTERVAL_SECONDS = 6 * 60 * 60" in RUNNER
