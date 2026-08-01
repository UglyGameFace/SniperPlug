from __future__ import annotations

from sniperplug.cogs.multi_source_movie_tickets import MovieTicketsCog as MultiSourceMovieTicketsCog
from sniperplug.services.movie_ticket_snowflake_store import SnowflakeSafeMovieTicketStore


class MovieTicketsCog(MultiSourceMovieTicketsCog, name="movies"):
    """Register the multi-source implementation under the existing `/movies` group."""

    def __init__(self, bot):
        super().__init__(bot)
        self.store = SnowflakeSafeMovieTicketStore(bot.db)
