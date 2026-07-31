from sniperplug.cogs.movie_tickets import build_movie_drop_embed
from sniperplug.services.movie_ticket_artwork import (
    extract_atom_movie_image_url,
    normalize_public_code,
    safe_atom_image_url,
)
from sniperplug.services.movie_ticket_drops import MovieTicketDrop


POSTER_URL = (
    "https://atom-tickets-res.cloudinary.com/image/upload/"
    "c_fill,f_auto,g_north,h_160,q_auto,w_107/"
    "v1785343652/ingestion-images-archive-prod/archive/"
    "1785343651883_386514_cops_0.jpg"
)


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
