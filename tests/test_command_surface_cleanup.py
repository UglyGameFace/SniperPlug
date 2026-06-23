from pathlib import Path


def test_removed_duplicate_commands_are_not_registered_in_source():
    sniperplug = Path("sniperplug/cogs/sniperplug.py").read_text(encoding="utf-8")
    public_alerts = Path("sniperplug/cogs/public_alerts.py").read_text(encoding="utf-8")

    assert 'name="setup"' not in sniperplug
    assert 'name="status"' not in sniperplug
    assert 'name="providers"' not in sniperplug
    assert 'name="autoscan_setup"' not in public_alerts


def test_doctor_no_longer_depends_on_embed_monkey_patch():
    dashboard = Path("sniperplug/cogs/settings_dashboard.py").read_text(encoding="utf-8")

    assert "_sniperplug_safe_followup_send_installed" not in dashboard
    assert "Native embed delivery" in dashboard


def test_verizon_shine_does_not_own_slash_command_sync():
    verizon = Path("sniperplug/cogs/verizon_shine.py").read_text(encoding="utf-8")

    assert "_sync_all_joined_guilds_once" not in verizon
    assert "_sync_guild_commands" not in verizon
    assert "copy_global_to" not in verizon
    assert ".tree.sync" not in verizon
