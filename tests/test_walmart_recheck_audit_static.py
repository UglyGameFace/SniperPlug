from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUDIT = (ROOT / "sniperplug/services/walmart_recheck_audit.py").read_text(encoding="utf-8")
RECHECK = (ROOT / "sniperplug/cogs/active_deal_recheck.py").read_text(encoding="utf-8")
HISTORY = (ROOT / "sniperplug/cogs/active_deal_history.py").read_text(encoding="utf-8")
BOT = (ROOT / "sniperplug/bot.py").read_text(encoding="utf-8")
MAINTENANCE = (ROOT / "sniperplug/services/storage_maintenance.py").read_text(encoding="utf-8")


def test_recheck_audit_schema_is_guild_scoped_and_bounded():
    assert "CREATE TABLE IF NOT EXISTS guild_walmart_recheck_audit" in AUDIT
    assert "guild_id INTEGER NOT NULL" in AUDIT
    assert "WALMART_RECHECK_AUDIT_RETENTION_DAYS = 30" in AUDIT
    assert "WALMART_RECHECK_AUDIT_MAX_ROWS_PER_GUILD = 2000" in AUDIT
    assert "newer.guild_id = audit.guild_id" in AUDIT


def test_audit_preserves_source_actor_result_and_proof_values():
    for token in (
        "trigger_source",
        "actor_user_id",
        "actor_name",
        "result_status",
        "reused",
        "old_price",
        "new_price",
        "old_discount",
        "new_discount",
        "reference_price",
        "cache_status",
        "message",
    ):
        assert token in AUDIT


def test_single_and_batch_slash_rechecks_record_before_persistence():
    assert 'trigger_source="slash_single"' in RECHECK
    assert 'trigger_source="slash_batch"' in RECHECK
    assert RECHECK.count("await record_recheck_attempt(") >= 2
    single_audit = RECHECK.index('trigger_source="slash_single"')
    single_persist = RECHECK.index("await persist_walmart_recheck", single_audit)
    assert single_audit < single_persist


def test_audit_is_not_limited_to_persisted_statuses():
    assert "await record_recheck_attempt(" in RECHECK
    assert "if result.status not in _NON_PERSISTED_STATUSES" in RECHECK
    assert "result.status != \"timeout\"" in RECHECK
    assert "bool(getattr(result, \"reused\", False))" in AUDIT


def test_existing_history_command_has_separate_recheck_view():
    assert 'app_commands.Choice(name="Lifecycle changes", value="lifecycle")' in HISTORY
    assert 'app_commands.Choice(name="Walmart recheck audit", value="rechecks")' in HISTORY
    assert "list_walmart_recheck_audit" in HISTORY
    assert "Cache persistence remains separate" in HISTORY
    assert "owner-triggered slash recheck attempt" in HISTORY


def test_startup_and_storage_maintenance_install_and_prune_audit():
    assert "await ensure_walmart_recheck_audit(self.db)" in BOT
    assert "Walmart recheck audit installed" in BOT
    assert "prune_walmart_recheck_audit" in MAINTENANCE
    assert "old_walmart_recheck_audit: int = 0" in MAINTENANCE
    assert "old_walmart_recheck_audit = await prune_walmart_recheck_audit(db)" in MAINTENANCE
    assert '"old_walmart_recheck_audit": self.old_walmart_recheck_audit' in MAINTENANCE
