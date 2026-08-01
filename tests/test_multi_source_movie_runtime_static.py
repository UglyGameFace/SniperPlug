from __future__ import annotations

from pathlib import Path

from sniperplug.cogs.multi_source_movie_tickets import MovieTicketsCog


ROOT = Path(__file__).resolve().parents[1]
BOT = (ROOT / "sniperplug/bot.py").read_text(encoding="utf-8")
RUNTIME = (ROOT / "sniperplug/cogs/multi_source_movie_tickets.py").read_text(encoding="utf-8")
FANDANGO = (ROOT / "sniperplug/services/fandango_movie_offers.py").read_text(encoding="utf-8")


def test_bot_registers_exactly_one_multi_source_movies_runtime() -> None:
    assert "from sniperplug.cogs.multi_source_movie_tickets import MovieTicketsCog" in BOT
    assert "from sniperplug.cogs.movie_tickets import MovieTicketsCog\n" not in BOT
    assert BOT.count("await self.add_cog(MovieTicketsCog(self))") == 1
    assert MovieTicketsCog.__cog_name__ == "movies"


def test_existing_movies_command_surface_is_inherited() -> None:
    command_names = {command.name for command in MovieTicketsCog.__cog_app_commands__}
    assert {"setup", "status", "latest", "scan", "test-alert", "disable", "sources"}.issubset(command_names)


def test_multi_source_scan_runs_atom_and_fandango_without_duplicate_runtime() -> None:
    assert "await super()._scan_official_source" in RUNTIME
    assert "await self._scan_fandango_source" in RUNTIME
    assert "FandangoOffersClient" in RUNTIME
    assert "replace_active_drops(FANDANGO_SOURCE_KEY" in RUNTIME
    assert "reserve_delivery" in RUNTIME
    assert "mark_delivery_sent" in RUNTIME
    assert "MovieTicketFeedbackView" in RUNTIME


def test_fandango_adapter_is_bounded_official_and_fail_closed() -> None:
    assert 'FANDANGO_OFFERS_URL = "https://www.fandango.com/offers"' in FANDANGO
    assert "FANDANGO_ALLOWED_HOSTS" in FANDANGO
    assert "FANDANGO_IMAGE_ALLOWED_HOSTS" in FANDANGO
    assert "aiohttp.ClientTimeout" in FANDANGO
    assert "FANDANGO_MAX_RESPONSE_BYTES" in FANDANGO
    assert "document_valid" in FANDANGO
    assert "join fanclub" in FANDANGO
    assert "sweepstakes" in FANDANGO
    assert "qualifying ticket purchase" in FANDANGO


def test_fandango_does_not_add_an_api_or_account_dependency() -> None:
    combined = RUNTIME + FANDANGO
    assert "FANDANGO_API_KEY" not in combined
    assert "password" not in combined.lower()
    assert "Authorization" not in combined
