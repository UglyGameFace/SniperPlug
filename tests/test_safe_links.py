from sniperplug.models.candidate import SourceCandidate
from sniperplug.services.safe_links import normalize_product_url, product_link_choices


def test_home_depot_api_link_normalizes_to_public_url():
    result = normalize_product_url(
        retailer="Home Depot",
        url="http://apionline.homedepot.com/p/Ryobi-Drill/123456789?foo=bar",
        product_id="123456789",
    )

    assert result.url == "https://www.homedepot.com/p/Ryobi-Drill/123456789"
    assert "product link normalized to Home Depot public URL" in result.notes


def test_walmart_internal_or_template_link_rebuilds_from_product_id():
    result = normalize_product_url(
        retailer="Walmart",
        url="https://developer.api.walmart.com/v3/items/49692798?x=bad",
        product_id="49692798",
    )

    assert result.url == "https://www.walmart.com/ip/49692798"
    assert "product link rebuilt as direct Walmart public URL" in result.notes


def test_amazon_link_normalizes_to_dp_and_keeps_tag():
    result = normalize_product_url(
        retailer="Amazon",
        url="https://www.amazon.com/gp/product/B0ABC12345?tag=sniper-20&psc=1&ref_=abc",
    )

    assert result.url == "https://www.amazon.com/dp/B0ABC12345?tag=sniper-20"


def test_candidate_normalizes_link_on_creation():
    candidate = SourceCandidate(
        source_key="home_depot_serpapi",
        retailer="Home Depot",
        title="Ryobi Drill",
        product_url="http://apionline.homedepot.com/p/Ryobi-Drill/123456789",
        product_id="123456789",
        sku="123456789",
    )

    assert candidate.product_url == "https://www.homedepot.com/p/Ryobi-Drill/123456789"
    assert "product link normalized to Home Depot public URL" in candidate.signals


def test_product_link_choices_include_app_web_and_browser_search():
    choices = product_link_choices(
        retailer="Home Depot",
        product_url="https://www.homedepot.com/p/Ryobi-Drill/123456789",
        title="Ryobi Drill",
        product_id="123456789",
    )

    labels = [choice.label for choice in choices]
    assert "Open App/Web" in labels
    assert "Browser Search" in labels
    assert choices[0].url == "https://www.homedepot.com/p/Ryobi-Drill/123456789"
    assert "google.com/search" in choices[1].url
