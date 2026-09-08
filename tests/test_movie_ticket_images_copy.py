from sniperplug.cogs.movie_tickets import build_movie_drop_embed
from sniperplug.services.movie_ticket_artwork import (
    extract_atom_movie_image_url,
    normalize_public_code,
    safe_atom_image_url,
    safe_movie_image_url,
    safe_seeitearly_movie_url,
)
from sniperplug.services.movie_ticket_drops import MovieTicketDrop


POSTER_URL = (
    "https://atom-tickets-res.cloudinary.com/image/upload/"
    "c_fill,f_auto,g_north,h_160,q_auto,w_107/"
    "v1785343652/ingestion-images-archive-prod/archive/"
    "1785343651883_386514_cops_0.jpg"
)
TMDB_POSTER_URL = "https://image.tmdb.org/t/p/w500/7jCV8dkRFK3DGJ4dTqlvrMTFPwZ.jpg"
SEEITEARLY_MOVIE_URL = "https://seeitearly.com/movies/practical-magic-2"


def _drop(code: str = "ATOMICECREAM") -> MovieTicketDrop:
    return MovieTicketDrop(
        drop_id="poster-copy-test",
        source_key="atom_official_promotions",
        source_label="Official Atom Promotions Hub",
        title="Ice Cream Man",
        code=code,
        classification="public_reusable",
        ticket_limit=2,
        offer_url="https://www.atomtickets.com/movies/ice-cream-man/386514",
        validity_text="While supplies last.",
        restrictions=("One time use per customer.",),
        raw_text="",
    )


def test_atom_movie_page_uses_official_poster_metadata() -> None:
    html = f"""
    <html><head>
      <meta property="og:image" content="{POSTER_URL}">
    </head><body></body></html>
    """
    assert extract_atom_movie_image_url(html) == POSTER_URL


def test_atom_movie_page_can_fall_back_to_actual_poster_image() -> None:
    html = f'<html><body><img src="{POSTER_URL}" alt="Movie Poster"></body></html>'
    assert extract_atom_movie_image_url(html) == POSTER_URL


def test_movie_artwork_allowlist_rejects_non_atom_assets() -> None:
    assert safe_atom_image_url(POSTER_URL) == POSTER_URL
    assert safe_atom_image_url("https://images.atomtickets.com/poster/example.jpg")
    assert safe_atom_image_url("https://example.com/fake-poster.jpg") == ""
    assert safe_atom_image_url("http://atom-tickets-res.cloudinary.com/poster.jpg") == ""


def test_seeitearly_movie_page_can_supply_tmdb_poster_without_an_extra_api_key() -> None:
    proxied = (
        "https://seeitearly.com/_next/image?"
        "q=75&url=https%3A%2F%2Fimage.tmdb.org%2Ft%2Fp%2Fw500%2F"
        "7jCV8dkRFK3DGJ4dTqlvrMTFPwZ.jpg&w=3840"
    )
    html = f'<html><body><img src="{proxied}" alt="Practical Magic 2 poster"></body></html>'
    assert safe_seeitearly_movie_url(SEEITEARLY_MOVIE_URL) == SEEITEARLY_MOVIE_URL
    assert safe_movie_image_url(proxied, page_url=SEEITEARLY_MOVIE_URL) == TMDB_POSTER_URL
    assert extract_atom_movie_image_url(html, page_url=SEEITEARLY_MOVIE_URL) == TMDB_POSTER_URL


def test_seeitearly_artwork_fails_closed_for_wrong_pages_or_external_images() -> None:
    assert safe_seeitearly_movie_url("https://seeitearly.com/upcoming") == ""
    assert safe_seeitearly_movie_url("https://example.com/movies/practical-magic-2") == ""
    assert safe_movie_image_url("https://example.com/poster.jpg", page_url=SEEITEARLY_MOVIE_URL) == ""
    evil_proxy = (
        "https://seeitearly.com/_next/image?"
        "url=https%3A%2F%2Fexample.com%2Fposter.jpg&w=3840"
    )
    assert safe_movie_image_url(evil_proxy, page_url=SEEITEARLY_MOVIE_URL) == ""


def test_promo_code_normalization_removes_quotes_backticks_and_spaces() -> None:
    assert normalize_public_code('`" ATOMICECREAM "`') == "ATOMICECREAM"
    assert normalize_public_code("“NIMRODSATOM”") == "NIMRODSATOM"
    assert normalize_public_code("  atom-icecream  ") == "ATOM-ICECREAM"


def test_embed_has_clean_copyable_code_and_full_movie_image() -> None:
    embed = build_movie_drop_embed(
        _drop('`"ATOMICECREAM"`'),
        image_url=POSTER_URL,
    )

    promo_field = next(field for field in embed.fields if field.name.startswith("Promo code"))
    assert promo_field.value == "ATOMICECREAM"
    assert "`" not in promo_field.value
    assert '"' not in promo_field.value
    assert embed.image.url == POSTER_URL
