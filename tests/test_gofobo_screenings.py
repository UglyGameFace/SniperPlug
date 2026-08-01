from __future__ import annotations

from sniperplug.cogs.multi_source_movie_tickets import build_multi_source_movie_drop_embed
from sniperplug.services.gofobo_screenings import (
    GOFOBO_HOME_URL,
    GOFOBO_SOURCE_KEY,
    extract_gofobo_image_marker,
    extract_gofobo_public_code,
    parse_gofobo_home_html,
    safe_gofobo_image_url,
    safe_gofobo_url,
)


GOFOBO_FIXTURE = """
<!doctype html>
<html>
<head><title>Gofobo | Movie Screenings</title></head>
<body>
  <a href="/main/redeem">REDEEM</a>
  <a href="/main/local_screenings/">Find Screenings</a>

  <h1>UPCOMING SCREENINGS &amp; EVENTS</h1>
  <a href="/MUTINY">
    <img src="https://dk2d6nav3mn9d.cloudfront.net/mutiny.jpg" alt="MUTINY">
    MUTINY
  </a>
  <a href="/main/local_movie_screenings/6298">
    <img src="https://gofobo.com/assets/disclosure-day.jpg" alt="DISCLOSURE DAY">
    DISCLOSURE DAY
  </a>
  <a href="https://example.com/fake-screening">FAKE EXTERNAL EVENT</a>

  <h1>BROWSE OUR NEWLY ADDED MOVIES AND EVENTS</h1>
  <a href="/main/local_movie_screenings/9999">NOT AN UPCOMING SCREENING</a>

  <h1>ENTER OUR FEATURED SWEEPSTAKES</h1>
  <a href="/main/sweepstakes/1">WIN A GIFT CARD</a>
</body>
</html>
"""


def test_gofobo_parser_keeps_only_official_upcoming_screening_cards() -> None:
    result = parse_gofobo_home_html(GOFOBO_FIXTURE)

    assert result.document_valid is True
    assert result.upcoming_section_found is True
    assert {drop.title for drop in result.drops} == {"MUTINY", "DISCLOSURE DAY"}
    assert all(drop.source_key == GOFOBO_SOURCE_KEY for drop in result.drops)
    assert all(drop.classification == "local_screening" for drop in result.drops)
    assert all("ZIP" in " ".join(drop.restrictions) for drop in result.drops)


def test_gofobo_short_public_link_exposes_code_but_detail_page_does_not() -> None:
    drops = {drop.title: drop for drop in parse_gofobo_home_html(GOFOBO_FIXTURE).drops}

    assert drops["MUTINY"].code == "MUTINY"
    assert drops["DISCLOSURE DAY"].code == ""
    assert extract_gofobo_public_code("https://gofobo.com/NHIE") == "NHIE"
    assert extract_gofobo_public_code("https://gofobo.com/main/local_movie_screenings/6298") == ""
    assert extract_gofobo_public_code("https://gofobo.com/redeem") == ""


def test_gofobo_embed_is_honest_about_zip_account_and_admission_limits() -> None:
    drop = next(
        item for item in parse_gofobo_home_html(GOFOBO_FIXTURE).drops
        if item.title == "DISCLOSURE DAY"
    )
    image_url = extract_gofobo_image_marker(drop.raw_text)
    embed = build_multi_source_movie_drop_embed(drop, image_url=image_url)

    rendered = "\n".join(
        [embed.title or "", embed.description or ""]
        + [f"{field.name}\n{field.value}" for field in embed.fields]
    )
    assert embed.title == "🎬 GOFOBO FREE SCREENING ALERT"
    assert "ZIP-local" in rendered
    assert "No public code exposed" in rendered
    assert "not a guaranteed pass" in rendered.lower()
    assert "does not guarantee admission" in rendered.lower()
    assert embed.image.url == image_url


def test_gofobo_url_and_image_allowlists_fail_closed() -> None:
    assert safe_gofobo_url(GOFOBO_HOME_URL) == GOFOBO_HOME_URL
    assert safe_gofobo_url("/MUTINY") == "https://gofobo.com/MUTINY"
    assert safe_gofobo_url("http://gofobo.com/MUTINY") == ""
    assert safe_gofobo_url("https://example.com/fake") == ""

    assert safe_gofobo_image_url("https://dk2d6nav3mn9d.cloudfront.net/poster.jpg")
    assert safe_gofobo_image_url("https://gofobo.com/assets/poster.jpg")
    assert safe_gofobo_image_url("https://example.com/poster.jpg") == ""


def test_changed_gofobo_structure_preserves_cache_instead_of_guessing() -> None:
    result = parse_gofobo_home_html(
        "<html><title>Temporary Error</title><body><a href='/FREECODE'>FREE MOVIE</a></body></html>"
    )
    assert result.document_valid is False
    assert result.drops == ()
