from sniperplug.services.movie_ticket_drops import (
    ATOM_PROMOTIONS_URL,
    extract_public_code,
    parse_atom_promotions_html,
    safe_atom_url,
)


CURRENT_ATOM_STYLE_HTML = """
<!doctype html>
<html>
<head><title>Atom Tickets Promotions | Promo Hub</title></head>
<body>
  <h2>Atom Tickets Promotions</h2>
  <h3>Film Promotions</h3>

  <h3><a href="/movies/nimrods">NIMRODS - Use Your Promo Code to Get up to 2 Free Tickets</a></h3>
  <ul>
    <li>Promotional ticket(s) CANNOT be redeemed for cash or theater credit and are not for resale.</li>
    <li>You must include at least one ticket in your basket and enter promo code NIMRODSATOM during checkout, while supplies last (one time use per customer).</li>
    <li>Your promotion code is valid from July 29, 2026 (1:00 pm PT) through August 23, 2026 (11:59 pm PT), while supplies last.</li>
    <li>Offer is valid in the United States.</li>
  </ul>

  <h3><a href="https://www.atomtickets.com/movies/ice-cream-man">ICE CREAM MAN - Use Your Promo Code to Get up to 2 Free Tickets</a></h3>
  <ul>
    <li>You must include at least one ticket and enter promo code ATOMICECREAM during checkout.</li>
    <li>This limited time promo code is valid for one-time use only, so both tickets must be included in the same order.</li>
    <li>Your promotion code is valid from July 31, 2026 (11:00 am PT) through August 23, 2026 (11:59 pm PT), while supplies last.</li>
  </ul>

  <h3>Partner Promotions</h3>
  <h3>Samsung Wallet Customers - Learn How to Get $10 OFF movie tickets on Atom</h3>
  <ul>
    <li>You must use the promotional code given to you directly via Samsung.</li>
    <li>Enter the promo code given to you in the Promo Code field at checkout.</li>
  </ul>
</body>
</html>
"""


def test_current_atom_film_promotions_are_extracted_as_public_free_codes() -> None:
    result = parse_atom_promotions_html(CURRENT_ATOM_STYLE_HTML)

    assert result.document_valid is True
    assert result.film_section_found is True
    assert result.partner_section_found is True
    assert [drop.code for drop in result.drops] == ["ATOMICECREAM", "NIMRODSATOM"]

    by_code = {drop.code: drop for drop in result.drops}
    nimrods = by_code["NIMRODSATOM"]
    assert nimrods.title == "NIMRODS"
    assert nimrods.ticket_limit == 2
    assert nimrods.classification == "public_reusable"
    assert nimrods.offer_url == "https://www.atomtickets.com/movies/nimrods"
    assert "July 29, 2026" in nimrods.validity_text
    assert any("one time use" in restriction.lower() for restriction in nimrods.restrictions)


def test_partner_issued_and_account_specific_codes_are_not_published() -> None:
    result = parse_atom_promotions_html(CURRENT_ATOM_STYLE_HTML)

    assert all("samsung" not in drop.title.lower() for drop in result.drops)
    assert all(drop.code not in {"DIRECTLY", "FIELD", "GIVEN"} for drop in result.drops)


def test_invalid_or_changed_document_structure_fails_closed() -> None:
    html = "<html><title>Temporary error</title><body>promo code BADGUESS for two free tickets</body></html>"
    result = parse_atom_promotions_html(html)

    assert result.document_valid is False
    assert result.drops == ()


def test_sweepstakes_copy_is_not_treated_as_an_instant_free_ticket_drop() -> None:
    html = """
    <html><title>Atom Tickets Promotions</title><body>
      <h3>Film Promotions</h3>
      <h3>WIN A MOVIE NIGHT - Use Promo Code ENTER2WIN for up to 2 Free Tickets</h3>
      <li>Enter this sweepstakes for a chance to win.</li>
      <h3>Partner Promotions</h3>
    </body></html>
    """
    result = parse_atom_promotions_html(html)

    assert result.document_valid is True
    assert result.drops == ()


def test_code_extraction_rejects_generic_instruction_words() -> None:
    assert extract_public_code("Enter promo code NIMRODSATOM during checkout") == "NIMRODSATOM"
    assert extract_public_code("Enter the promo code received directly in the field") == ""


def test_atom_url_allowlist_rejects_redirects_to_other_hosts() -> None:
    assert safe_atom_url("/promotions") == ATOM_PROMOTIONS_URL
    assert safe_atom_url("https://www.atomtickets.com/movies/example") == "https://www.atomtickets.com/movies/example"
    assert safe_atom_url("http://www.atomtickets.com/promotions") == ""
    assert safe_atom_url("https://example.com/fake-atom-code") == ""
