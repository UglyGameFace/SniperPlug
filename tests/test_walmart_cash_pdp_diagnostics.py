from sniperplug.services.walmart_pdp_cash_proof import walmart_pdp_cash_proof_from_html


def test_pdp_no_walmart_cash_failure_includes_url_and_page_diagnostics():
    html = "<html><head><title>Walmart product</title></head><body><script>{}</script>Plain product page</body></html>"

    proof = walmart_pdp_cash_proof_from_html(html, current_price=8.98, url="https://www.walmart.com/ip/123")

    assert proof.attempted
    assert proof.checked
    assert not proof.wording_seen
    assert proof.cash_truth is None
    assert "https://www.walmart.com/ip/123" in proof.failure_reason
    assert "PDP diagnostics:" in proof.failure_reason
    assert "html_chars=" in proof.failure_reason
    assert "scripts=1" in proof.failure_reason
    assert "title=Walmart product" in proof.failure_reason


def test_pdp_blocked_or_thin_page_is_called_out():
    html = "<html><head><title>Robot or Human?</title></head><body>Robot or human?</body></html>"

    proof = walmart_pdp_cash_proof_from_html(html, current_price=8.98, url="https://www.walmart.com/ip/456")

    assert "possible_block=yes" in proof.failure_reason
    assert "thin_html=yes" in proof.failure_reason
    assert proof.html_length == len(html)
    assert "PDP diagnostics:" in proof.page_diagnostic


def test_pdp_walmart_cash_wording_without_amount_keeps_diagnostics():
    html = "<html><head><title>Walmart product</title></head><body>Walmart Cash available on this item</body></html>"

    proof = walmart_pdp_cash_proof_from_html(html, current_price=8.98, url="https://www.walmart.com/ip/789")

    assert proof.wording_seen
    assert proof.cash_truth is None
    assert "Walmart Cash wording found" in proof.failure_reason
    assert "no sane dollar amount" in proof.failure_reason
    assert "PDP diagnostics:" in proof.failure_reason
