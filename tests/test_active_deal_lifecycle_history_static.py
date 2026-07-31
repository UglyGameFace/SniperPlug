from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVICE = (ROOT / "sniperplug/services/active_deal_history.py").read_text(encoding="utf-8")
COG = (ROOT / "sniperplug/cogs/active_deal_history.py").read_text(encoding="utf-8")
BOT = (ROOT / "sniperplug/bot.py").read_text(encoding="utf-8")


def test_history_is_database_atomic_and_change_only():
    assert "CREATE TRIGGER IF NOT EXISTS trg_active_deal_cache_history_update" in SERVICE
    assert "AFTER UPDATE OF current_price, discount, status" in SERVICE
    assert "OLD.current_price IS NOT NEW.current_price" in SERVICE
    assert "OLD.discount IS NOT NEW.discount" in SERVICE
    assert "OLD.status IS NOT NEW.status" in SERVICE


def test_history_classifies_price_discount_and_status_changes():
    for event in (
        "marked_stale",
        "reactivated",
        "price_drop",
        "price_increase",
        "discount_unproven",
        "discount_improved",
        "discount_weakened",
    ):
        assert f"'{event}'" in SERVICE


def test_history_is_guild_scoped_and_bounded():
    assert "WHERE guild_id = ?" in SERVICE
    assert "ACTIVE_DEAL_HISTORY_RETENTION_DAYS = 30" in SERVICE
    assert "ACTIVE_DEAL_HISTORY_MAX_ROWS_PER_GUILD = 1000" in SERVICE
    assert "LIMIT ?" in SERVICE
    assert "max(1, min(int(limit), 25))" in SERVICE


def test_history_command_is_owner_safe_and_truthful():
    assert '@app_commands.command(name="active_deal_history"' in COG
    assert "@app_commands.checks.has_permissions(manage_guild=True)" in COG
    assert "ephemeral=True" in COG
    assert "verified rechecks and fresh-scan updates" in COG
    assert "the event label describes only what actually changed" in COG


def test_history_schema_and_cog_are_installed_at_startup():
    assert "from sniperplug.cogs.active_deal_history import ActiveDealHistoryCog" in BOT
    assert "from sniperplug.services.active_deal_history import ensure_active_deal_history" in BOT
    assert "await ensure_active_deal_history(self.db)" in BOT
    assert "await self.add_cog(ActiveDealHistoryCog(self))" in BOT
    assert BOT.index("await ensure_active_deal_history(self.db)") < BOT.index("await self.add_cog(ActiveDealRecheckCog(self))")
