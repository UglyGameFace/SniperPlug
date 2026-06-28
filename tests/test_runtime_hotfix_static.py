from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_bot_logging_call_has_no_extra_args() -> None:
    source = read("sniperplug/bot.py")
    assert "configure_runtime_logging()" in source
    assert "configure_runtime_logging(settings=" not in source
    assert "db_path=settings.database_path" not in source


def test_unified_deals_uses_safe_embed_delivery() -> None:
    source = read("sniperplug/cogs/unified_deal_scanner.py")
    assert "sanitize_embed" in source
    assert "send_summary_and_embeds" in source
    assert "batch_cards_for_limit" in source
    assert "send_private_card_batches" in source
