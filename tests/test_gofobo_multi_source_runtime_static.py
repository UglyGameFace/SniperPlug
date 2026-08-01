from __future__ import annotations

from pathlib import Path

from sniperplug.cogs.registered_multi_source_movies import MovieTicketsCog


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = (ROOT / "sniperplug/cogs/multi_source_movie_tickets.py").read_text(encoding="utf-8")
FEEDBACK = (ROOT / "sniperplug/cogs/movie_ticket_feedback.py").read_text(encoding="utf-8")
GOFOBO = (ROOT / "sniperplug/services/gofobo_screenings.py").read_text(encoding="utf-8")


def test_movies_command_surface_remains_one_group_with_multi_source_overrides() -> None:
    command_names = {command.name for command in MovieTicketsCog.__cog_app_commands__}
    assert {"setup", "status", "latest", "scan", "test-alert", "disable", "sources"}.issubset(command_names)
    assert MovieTicketsCog.__cog_name__ == "movies"
    assert len([name for name in command_names if name == "latest"]) == 1
    assert len([name for name in command_names if name == "sources"]) == 1


def test_multi_source_scan_runs_each_official_source_independently() -> None:
    assert "self._scan_atom_source" in RUNTIME
    assert "self._scan_fandango_source" in RUNTIME
    assert "self._scan_gofobo_source" in RUNTIME
    assert "GofoboUpcomingClient" in RUNTIME
    assert "replace_active_drops(GOFOBO_SOURCE_KEY" in RUNTIME
    assert "All official movie-ticket sources failed" in RUNTIME
    assert "await super()._scan_official_source" not in RUNTIME


def test_atom_not_modified_path_no_longer_loads_every_source_drop() -> None:
    atom_start = RUNTIME.index("async def _scan_atom_source")
    atom_end = RUNTIME.index("async def _scan_fandango_source", atom_start)
    atom_scan = RUNTIME[atom_start:atom_end]
    assert "await self._list_source_drops(ATOM_SOURCE_KEY)" in atom_scan
    assert "await self.store.list_active_drops(limit=100)" not in atom_scan


def test_gofobo_local_screenings_are_deliverable_without_fake_codes() -> None:
    assert 'DELIVERABLE_CLASSIFICATIONS = frozenset({"public_reusable", "local_screening"})' in RUNTIME
    assert 'drop.classification == "public_reusable" and not drop.code' in RUNTIME
    assert 'drop.classification == "local_screening"' not in RUNTIME or "GOFOBO FREE SCREENING ALERT" in RUNTIME
    assert "No public code exposed" in RUNTIME
    assert "verify your ZIP" in RUNTIME


def test_feedback_supports_gofobo_alert_title_and_link_label() -> None:
    assert '"🎬 GOFOBO FREE SCREENING ALERT"' in FEEDBACK
    assert '"Open official Gofobo screening"' in FEEDBACK
    assert "ZIP" in FEEDBACK


def test_sources_and_status_no_longer_claim_atom_only() -> None:
    assert "official Atom promotions, Fandango offers, and Gofobo upcoming screenings" in RUNTIME
    assert "Fandango Special Offers" in RUNTIME
    assert "Gofobo Upcoming Screenings" in RUNTIME
    assert "Reusable ticket codes" in RUNTIME
    assert "Local screening leads" in RUNTIME


def test_gofobo_adapter_has_no_login_or_private_api_dependency() -> None:
    assert 'GOFOBO_HOME_URL = "https://gofobo.com/"' in GOFOBO
    assert "GOFOBO_ALLOWED_HOSTS" in GOFOBO
    assert "aiohttp.ClientTimeout" in GOFOBO
    assert "GOFOBO_MAX_RESPONSE_BYTES" in GOFOBO
    assert "password" not in GOFOBO.lower()
    assert "authorization" not in GOFOBO.lower()
    assert "api_key" not in GOFOBO.lower()
