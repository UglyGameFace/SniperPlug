from __future__ import annotations

import pytest

from sniperplug.hp_watcher.parser import (
    hp_us_product_urls,
    normalize_hp_sku,
    parse_hp_services_price_response,
    parse_product_page_identity,
    parse_sitemap_xml,
)


PRODUCT_URL = "https://www.hp.com/us-en/shop/pdp/hp-travel-25-liter-156-iron-grey-laptop-backpack"


def test_sitemap_index_and_product_url_filtering() -> None:
    index = parse_sitemap_xml(
        """<?xml version="1.0" encoding="UTF-8"?>
        <sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          <sitemap><loc>https://www.hp.com/sitemap-store-us-en-products-1.xml</loc></sitemap>
          <sitemap><loc>https://example.com/not-hp.xml</loc></sitemap>
        </sitemapindex>
        """
    )
    assert index.kind == "sitemapindex"
    assert index.locations == ("https://www.hp.com/sitemap-store-us-en-products-1.xml",)

    urlset = parse_sitemap_xml(
        f"""<?xml version="1.0" encoding="UTF-8"?>
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          <url><loc>{PRODUCT_URL}</loc></url>
          <url><loc>https://www.hp.com/us-en/shop/cat/accessories</loc></url>
          <url><loc>https://www.hp.com/us-en/shop/pdp/another-product</loc></url>
        </urlset>
        """
    )
    assert hp_us_product_urls(urlset) == (
        PRODUCT_URL,
        "https://www.hp.com/us-en/shop/pdp/another-product",
    )


def test_product_page_identity_prefers_exact_json_product() -> None:
    html = """
    <html><head><title>HP Travel Backpack | HP Store</title></head><body>
    <script type="application/ld+json">
    {
      "@type": "Product",
      "name": "HP Travel 25 Liter 15.6 Iron Grey Laptop Backpack",
      "sku": "6B8U4AA#ABA",
      "catentryId": "3074457345619999999",
      "image": "https://ssl-product-images.www8-hp.com/example.png"
    }
    </script>
    </body></html>
    """
    identity = parse_product_page_identity(PRODUCT_URL, html)
    assert identity.sku == "6B8U4AA"
    assert identity.catalog_entry_id == "3074457345619999999"
    assert identity.title.startswith("HP Travel 25 Liter")
    assert identity.image_url.startswith("https://")


def test_product_page_identity_rejects_missing_exact_catalog_identity() -> None:
    with pytest.raises(ValueError, match="catalog entry"):
        parse_product_page_identity(
            PRODUCT_URL,
            '<html><script type="application/ld+json">{"sku":"6B8U4AA"}</script></html>',
        )


def test_hp_services_price_parser_requires_exact_product_and_sku() -> None:
    payload = {
        "priceData": [
            {
                "productId": "3074457345619999999",
                "partNumber": "6B8U4AA#ABA",
                "price": "$12.99",
                "lPrice": "$54.99",
                "availableOnline": True,
                "canAddToCart": True,
                "jPromMsg": "Clearance",
            },
            {
                "productId": "3074457345618888888",
                "partNumber": "WRONG1AA#ABA",
                "price": "$1.00",
                "lPrice": "$999.00",
            },
        ]
    }
    offers = parse_hp_services_price_response(
        payload,
        expected_products={"3074457345619999999": "6B8U4AA"},
    )
    assert len(offers) == 1
    offer = offers[0]
    assert offer.product_id == "3074457345619999999"
    assert offer.sku == "6B8U4AA"
    assert offer.current_price == 12.99
    assert offer.msrp_price == 54.99
    assert offer.discount_percent == 76.38
    assert offer.in_stock is True
    assert offer.can_add_to_cart is True


def test_hp_services_price_parser_rejects_zero_and_cross_product_rows() -> None:
    payload = {
        "priceData": [
            {
                "productId": "3074457345619999999",
                "partNumber": "6B8U4AA#ABA",
                "price": "$0.00",
                "lPrice": "$54.99",
            },
            {
                "productId": "3074457345619999999",
                "partNumber": "MISMATCH1",
                "price": "$12.99",
                "lPrice": "$54.99",
            },
        ]
    }
    assert parse_hp_services_price_response(
        payload,
        expected_products={"3074457345619999999": "6B8U4AA"},
    ) == ()


def test_hp_services_price_parser_fails_closed_on_schema_drift() -> None:
    with pytest.raises(ValueError, match="priceData"):
        parse_hp_services_price_response({"prices": []})


def test_hp_sku_normalization_removes_region_suffix_only() -> None:
    assert normalize_hp_sku("6B8U4AA#ABA") == "6B8U4AA"
    assert normalize_hp_sku(" 6b8u4aa ") == "6B8U4AA"
    assert normalize_hp_sku("not-a-sku") == ""
