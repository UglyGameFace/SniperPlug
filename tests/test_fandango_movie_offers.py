from __future__ import annotations

from sniperplug.cogs.multi_source_movie_tickets import build_multi_source_movie_drop_embed
from sniperplug.services.fandango_movie_offers import (
    FANDANGO_OFFERS_URL,
    FANDANGO_SOURCE_KEY,
    extract_fandango_image_marker,
    fandango_purchase_required,
    parse_fandango_offers_html,
    safe_fandango_image_url,
    safe_fandango_url,
)


FANDANGO_FIXTURE = """
<!doctype html>
<html>
<head><title>Special Offers | Fandango</title></head>
<body>
  <h1>OFFERS</h1>
  <h2>Special Offers</h2>

  <a href="https://www.fandangomovietickets.com/offer/paw-patrol">
    <img src="https://images.fandango.com/ImageRenderer/820/0/redesign/static/img/default_poster.png" alt="PAW Patrol offer">
  </a>
  <h3>See PAW Patrol first Sat 8/8 at 2pm Early Access</h3>
  <p>Buy 3 tickets, get 1 ticket free (equal or lesser price, up to $15 total ticket price and fees) with code PAWPATROLB3G1</p>

  <a href="https://www.fandangomovietickets.com/offer/summerslam">
    <img src="https://images.fandango.com/summerslam.jpg" alt="WWE SummerSlam">
  </a>
  <h3>Special WWE SummerSlam Offer for Kids!</h3>
  <p>Get 1 kids ticket free with purchase of an adult ticket with code WWESUMMERSLAM. While supplies last.</p>

  <h3>Apple Pay Wednesday</h3>
  <p>Get $5 off with code APPLEPAYWED.</p>

  <h3>Join FanClub to get 1 Free Ticket</h3>
  <p>Join a paid membership and the offer is automatically applied at checkout.</p>

  <h3>Chance to win a premiere trip</h3>
  <p>Enter the sweepstakes with code PREMIEREWIN for a chance to win.</p>

  <h2>New &amp; Coming soon</h2>
</body>
</html>
"""


def test_fandango_parser_keeps_only_public_codes_that_grant_a_free_ticket() -> None:
    result = parse_fandango_offers_html(FANDANGO_FIXTURE)

    assert result.document_valid is True
    assert result.offers_section_found is True
    assert [drop.code for drop in result.drops] == ["WWESUMMERSLAM", "PAWPATROLB3G1"]
    assert all(drop.source_key == FANDANGO_SOURCE_KEY for drop in result.drops)
    assert all(drop.classification == "public_reusable" for drop in result.drops)
    assert all(fandango_purchase_required(drop.raw_text) for drop in result.drops)


def test_fandango_parser_excludes_discount_membership_and_sweepstakes_noise() -> None:
    result = parse_fandango_offers_html(FANDANGO_FIXTURE)
    codes = {drop.code for drop in result.drops}

    assert "APPLEPAYWED" not in codes
    assert "PREMIEREWIN" not in codes
    assert all("fanclub" not in drop.title.lower() for drop in result.drops)


def test_fandango_offer_embed_is_honest_about_purchase_requirement() -> None:
    drop = next(
        item for item in parse_fandango_offers_html(FANDANGO_FIXTURE).drops
        if item.code == "PAWPATROLB3G1"
    )
    image_url = extract_fandango_image_marker(drop.raw_text)
    embed = build_multi_source_movie_drop_embed(drop, image_url=image_url)

    rendered = "\n".join(
        [embed.title or "", embed.description or ""]
        + [f"{field.name}\n{field.value}" for field in embed.fields]
    )
    assert embed.title == "🎟️ FREE FANDANGO TICKET OFFER"
    assert "qualifying purchase" in rendered.lower()
    assert "PAWPATROLB3G1" in rendered
    assert "Public code • qualifying purchase required" in rendered
    assert embed.image.url == image_url


def test_fandango_url_and_image_allowlists_fail_closed() -> None:
    assert safe_fandango_url(FANDANGO_OFFERS_URL) == FANDANGO_OFFERS_URL
    assert safe_fandango_url("https://www.fandangomovietickets.com/offer/example")
    assert safe_fandango_url("http://www.fandango.com/offers") == ""
    assert safe_fandango_url("https://example.com/fake") == ""

    assert safe_fandango_image_url("https://images.fandango.com/poster.jpg")
    assert safe_fandango_image_url("https://example.com/poster.jpg") == ""


def test_changed_fandango_page_structure_preserves_cache_instead_of_guessing() -> None:
    result = parse_fandango_offers_html(
        "<html><title>Temporary Error</title><body>Get one ticket free with code BADGUESS</body></html>"
    )
    assert result.document_valid is False
    assert result.drops == ()
