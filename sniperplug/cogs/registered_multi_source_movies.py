from __future__ import annotations

from sniperplug.cogs.multi_source_movie_tickets import MovieTicketsCog as MultiSourceMovieTicketsCog


class MovieTicketsCog(MultiSourceMovieTicketsCog, name="movies"):
    """Register the multi-source implementation under the existing `/movies` group."""
