from __future__ import annotations

import sniperplug.services.movie_user_location as movie_location


def test_city_parser_accepts_only_explicit_free_entries_in_playing_section() -> None:
    html = """
    <html>
      <body>
        <h2>What's playing in New York</h2>
        <div><span>Free</span><a href="/movies/practical-magic-2">Practical Magic 2</a></div>
        <div><span>Paid</span><a href="/movies/hope">Hope</a></div>
        <div><a href="/movies/unknown-status">Unknown Status</a></div>
        <h2>Releasing soon in New York</h2>
        <div><span>Free</span><a href="/movies/not-a-local-screening">Not a local screening</a></div>
      </body>
    </html>
    """

    parser = movie_location._SeeItEarlyCityParser()
    parser.feed(html)
    parser.close()

    assert parser.movie_slugs == {"practical-magic-2"}


def test_city_parser_accepts_free_badge_inside_movie_link() -> None:
    html = """
    <h2>What's playing in Greater Bridgeport</h2>
    <a href="https://seeitearly.com/movies/the-uprising"><span>Free pass</span>The Uprising</a>
    <h2>Film events in Greater Bridgeport</h2>
    """

    parser = movie_location._SeeItEarlyCityParser()
    parser.feed(html)
    parser.close()

    assert parser.movie_slugs == {"the-uprising"}


def test_city_parser_rejects_offsite_movie_links() -> None:
    html = """
    <h2>What's playing in New Haven</h2>
    <span>Free</span><a href="https://evil.example/movies/fake-free-pass">Fake</a>
    <h2>Where it's happening</h2>
    """

    parser = movie_location._SeeItEarlyCityParser()
    parser.feed(html)
    parser.close()

    assert parser.movie_slugs == set()
