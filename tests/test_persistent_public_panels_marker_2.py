from pathlib import Path

BOT = Path("sniperplug/bot.py").read_text(encoding="utf-8")


def test_bot_boot_logs_persistent_public_panels():
    assert "Persistent public panel views registered" in BOT
