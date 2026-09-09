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


GOFOBO_LIVE_IMAGE_CARD_FIXTURE = """
<!doctype html>
<html>
<head><title>Gofobo | Movie Screenings</title></head>
<body>
  <h1>UPCOMING SCREENINGS &amp; EVENTS</h1>
  <div class="upscreen">
    <div class="flex-col">
      <a class="image-trans" href="/main/local_movie_screenings/6384">
        <img class="hover_image_upscreen" src="https://d21ld3wfckjpu2.cloudfront.net/practical.jpg">
      </a>
      <p class="ssp-b white text-center margin-auto uppercase">PRACTICAL MAGIC 2</p>
    </div>
    <div class="flex-col">
      <a class="image-trans" href="/main/local_movie_screenings/6308">
        <img class="hover_image_upscreen" src="https://d21ld3wfckjpu2.cloudfront.net/digger.jpg">
      </a>
      <p class="ssp-b white text-center margin-auto uppercase">DIGGER</p>
    </div>
    <div class="flex-col">
      <a class="image-trans" href="/main/local_movie_screenings/6623">
        <img class="hover_image_upscreen" src="https://d21ld3wfckjpu2.cloudfront.net/heart.jpg">
      </a>
      <p class="ssp-b white text-center margin-auto uppercase">HEART OF THE BEAST</p>
    </div>
    <div class="flex-col">
      <a class="image-trans" href="/main/local_movie_screenings/6483">
        <img class="hover_image_upscreen" src="https://d21ld3wfckjpu2.cloudfront.net/forgotten.jpg">
      </a>
      <p class="ssp-b white text-center margin-auto uppercase">FORGOTTEN ISLAND</p>
    </div>
  </div>

  <h1>BROWSE OUR NEWLY ADDED MOVIES AND EVENTS</h1>
  <h1>ENTER OUR FEATURED SWEEPSTAKES</h1>
</body>
</html>
"""


GOFOBO_PARTIAL_DRIFT_FIXTURE = """
<!doctype html>
<html>
<head><title>Gofobo | Movie Screenings</title></head>
<body>
  <h1>UPCOMING SCREENINGS &amp; EVENTS</h1>
  <div class="flex-col">
    <a href="/main/local_movie_screenings/6384">
      <img src="https://d21ld3wfckjpu2.cloudfront.net/practical.jpg">
    </a>
    <p>PRACTICAL MAGIC 2</p>
  </div>
  <div class="flex-col">
    <a href="/main/local_movie_screenings/6308">
      <img src="https://d21ld3wfckjpu2.cloudfront.net/digger.jpg">
    </a>
  </div>
  <h1>BROWSE OUR NEWLY ADDED MOVIES AND EVENTS</h1>
  <h1>ENTER OUR FEATURED SWEEPSTAKES</h1>
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


def test_gofobo_live_image_cards_pair_sibling_titles_with_detail_links() -> None:
    result = parse_gofobo_home_html(GOFOBO_LIVE_IMAGE_CARD_FIXTURE)

    assert result.document_valid is True
    assert result.candidate_count == 4
    by_title = {drop.title: drop for drop in result.drops}
    assert set(by_title) == {
        "PRACTICAL MAGIC 2",
        "DIGGER",
        "HEART OF THE BEAST",
        "FORGOTTEN ISLAND",
    }
    assert by_title["PRACTICAL MAGIC 2"].offer_url.endswith("/main/local_movie_screenings/6384")
    assert by_title["DIGGER"].offer_url.endswith("/main/local_movie_screenings/6308")
    assert by_title["HEART OF THE BEAST"].offer_url.endswith("/main/local_movie_screenings/6623")
    assert by_title["FORGOTTEN ISLAND"].offer_url.endswith("/main/local_movie_screenings/6483")


def test_gofobo_partial_visible_card_extraction_fails_closed() -> None:
    result = parse_gofobo_home_html(GOFOBO_PARTIAL_DRIFT_FIXTURE)

    assert result.upcoming_section_found is True
    assert result.document_valid is False
    assert result.drops == ()


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
