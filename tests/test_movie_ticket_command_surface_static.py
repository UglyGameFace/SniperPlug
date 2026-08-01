from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOT = (ROOT / "sniperplug/bot.py").read_text(encoding="utf-8")
COG = (ROOT / "sniperplug/cogs/movie_tickets.py").read_text(encoding="utf-8")
REGISTERED = (ROOT / "sniperplug/cogs/registered_multi_source_movies.py").read_text(encoding="utf-8")
SERVICE = (ROOT / "sniperplug/services/movie_ticket_drops.py").read_text(encoding="utf-8")


def test_runtime_registers_exactly_one_movies_cog() -> None:
    assert "from sniperplug.cogs.registered_multi_source_movies import MovieTicketsCog" in BOT
    assert BOT.count("await self.add_cog(MovieTicketsCog(self))") == 1
    assert "class MovieTicketsCog(commands.GroupCog, name=\"movies\")" in COG
    assert 'class MovieTicketsCog(MultiSourceMovieTicketsCog, name="movies")' in REGISTERED


def test_movies_command_group_has_complete_server_owner_surface() -> None:
    for command in ("setup", "status", "latest", "scan", "test-alert", "disable", "sources"):
        assert f'@app_commands.command(name="{command}"' in COG
    assert "alert_channel: discord.TextChannel" in COG
    assert "has_permissions(manage_guild=True)" in COG


def test_monitor_is_bounded_first_party_and_deduplicated() -> None:
    assert 'ATOM_PROMOTIONS_URL = "https://www.atomtickets.com/promotions"' in SERVICE
    assert "ATOM_ALLOWED_HOSTS" in SERVICE
    assert "ATOM_MAX_RESPONSE_BYTES" in SERVICE
    assert "aiohttp.ClientTimeout" in SERVICE
    assert 'classification="public_reusable"' in SERVICE
    assert "movie_ticket_deliveries" in SERVICE
    assert "reserve_delivery" in COG
    assert "MOVIE_POLL_SECONDS = 60" in COG
    assert "_source_lock = asyncio.Lock()" in COG


def test_monitor_does_not_claim_private_channels_are_automated() -> None:
    assert "Official but not connected" in COG
    assert "Atom app push notifications" in COG
    assert "official movie/studio/distributor accounts" in COG
    assert "No Atom API or account login is used" in COG


def test_no_hardcoded_discord_destination_ids() -> None:
    assert "1514374173517152418" not in COG + SERVICE
    assert "alert_channel_id INTEGER" in SERVICE
    assert "guild_id INTEGER PRIMARY KEY" in SERVICE
