from __future__ import annotations

import gzip

import pytest

from sniperplug.target_watcher.parser import (
    merge_fulfillment,
    parse_target_fulfillment_response,
    parse_target_product_response,
    parse_target_sitemap,
    target_product_seeds,
)


TCIN = "91234567"
PRODUCT_URL = f"https://www.target.com/p/example-product/-/A-{TCIN}"


def test_gzip_target_sitemap_index_and_product_seed_parsing() -> None:
    index_xml = b"""<?xml version='1.0' encoding='UTF-8'?>
    <sitemapindex xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>
      <sitemap><loc>https://www.target.com/sitemap_pdp-1.xml.gz</loc></sitemap>
      <sitemap><loc>https://example.com/not-target.xml.gz</loc></sitemap>
    </sitemapindex>"""
    index = parse_target_sitemap(
        gzip.compress(index_xml),
        max_expanded_bytes=1024 * 1024,
    )
    assert index.kind == "sitemapindex"
    assert index.locations == ("https://www.target.com/sitemap_pdp-1.xml.gz",)

    urlset = parse_target_sitemap(
        f"""<?xml version='1.0' encoding='UTF-8'?>
        <urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>
          <url><loc>{PRODUCT_URL}</loc></url>
          <url><loc>https://www.target.com/c/electronics/-/N-5xtg6</loc></url>
        </urlset>""",
        max_expanded_bytes=1024 * 1024,
    )
    assert target_product_seeds(urlset)[0].tcin == TCIN
    assert target_product_seeds(urlset)[0].product_url == PRODUCT_URL


def test_target_product_parser_requires_exact_tcin_and_positive_price() -> None:
    payload = {
        "data": {
            "product": {
                "tcin": TCIN,
                "item": {
                    "product_description": {"title": "Example Console"},
                    "enrichment": {
                        "buy_url": PRODUCT_URL,
                        "images": {"primary_image_url": "https://target.scene7.com/example.jpg"},
                    },
                },
                "price": {"current_retail": 99.99, "reg_retail": 399.99},
                "seller_name": "Target",
            }
        }
    }
    offer = parse_target_product_response(payload, expected_tcin=TCIN)
    assert offer.current_price == 99.99
    assert offer.regular_price == 399.99
    assert offer.discount_percent > 74
    assert offer.product_url == PRODUCT_URL

    with pytest.raises(ValueError, match="different TCIN"):
        parse_target_product_response(payload, expected_tcin="99999999")

    payload["data"]["product"]["price"]["current_retail"] = 0
    with pytest.raises(ValueError, match="positive current price"):
        parse_target_product_response(payload, expected_tcin=TCIN)


def test_target_plus_requires_exact_marketplace_seller_identity() -> None:
    product = {
        "tcin": TCIN,
        "item": {
            "product_description": {"title": "Marketplace Product"},
            "is_target_plus": True,
        },
        "price": {"current_retail": 43.99},
    }
    with pytest.raises(ValueError, match="exact seller identity"):
        parse_target_product_response(
            {"data": {"product": product}},
            expected_tcin=TCIN,
        )

    product["merchant"] = {"display_name": "iHerb"}
    offer = parse_target_product_response(
        {"data": {"product": product}},
        expected_tcin=TCIN,
    )
    assert offer.seller_name == "iHerb"


def test_unavailable_is_never_misread_as_available() -> None:
    payload = {
        "data": {
            "product_summaries": [
                {
                    "tcin": TCIN,
                    "is_out_of_stock_in_all_store_locations": True,
                    "fulfillment": {
                        "shipping_options": {"availability_status": "UNAVAILABLE"},
                        "store_options": [
                            {
                                "order_pickup": {"availability_status": "OUT_OF_STOCK"},
                                "drive_up": {"availability_status": "UNAVAILABLE"},
                            }
                        ],
                    },
                }
            ]
        }
    }
    result = parse_target_fulfillment_response(payload, expected_tcins=[TCIN])[TCIN]
    assert result.shipping_available is False
    assert result.pickup_available is False
    assert result.can_add_to_cart is False


def test_pickup_proof_uses_only_the_configured_target_store() -> None:
    payload = {
        "data": {
            "product_summaries": [
                {
                    "tcin": TCIN,
                    "fulfillment": {
                        "shipping_options": {"availability_status": "UNAVAILABLE"},
                        "store_options": [
                            {
                                "store_id": "1956",
                                "order_pickup": {"availability_status": "OUT_OF_STOCK"},
                                "drive_up": {"availability_status": "UNAVAILABLE"},
                            },
                            {
                                "store_id": "9999",
                                "order_pickup": {"availability_status": "AVAILABLE"},
                                "drive_up": {"availability_status": "AVAILABLE"},
                            },
                        ],
                    },
                }
            ]
        }
    }
    result = parse_target_fulfillment_response(
        payload,
        expected_tcins=[TCIN],
        expected_store_id="1956",
    )[TCIN]
    assert result.shipping_available is False
    assert result.pickup_available is False
    assert result.can_add_to_cart is False
    assert "store:9999" not in result.stock_status

    ambiguous = parse_target_fulfillment_response(
        payload,
        expected_tcins=[TCIN],
    )[TCIN]
    assert ambiguous.pickup_available is None
    assert ambiguous.can_add_to_cart is None


def test_false_global_out_of_stock_flag_does_not_invent_availability() -> None:
    payload = {
        "data": {
            "product_summaries": [
                {
                    "tcin": TCIN,
                    "is_out_of_stock_in_all_store_locations": False,
                    "fulfillment": {
                        "shipping_options": {"availability_status": "UNAVAILABLE"},
                        "store_options": [],
                    },
                }
            ]
        }
    }
    result = parse_target_fulfillment_response(payload, expected_tcins=[TCIN])[TCIN]
    assert result.shipping_available is False
    assert result.pickup_available is None
    assert result.can_add_to_cart is None


def test_fulfillment_only_merges_the_expected_tcin() -> None:
    product_payload = {
        "data": {
            "product": {
                "tcin": TCIN,
                "item": {"product_description": {"title": "Example Console"}},
                "price": {"current_retail": 99.99, "reg_retail": 399.99},
            }
        }
    }
    offer = parse_target_product_response(product_payload, expected_tcin=TCIN)
    fulfillment_payload = {
        "data": {
            "product_summaries": [
                {
                    "tcin": TCIN,
                    "fulfillment": {
                        "shipping_options": {"availability_status": "IN_STOCK"},
                        "store_options": [
                            {"order_pickup": {"availability_status": "AVAILABLE"}}
                        ],
                    },
                },
                {
                    "tcin": "99999999",
                    "fulfillment": {
                        "shipping_options": {"availability_status": "IN_STOCK"}
                    },
                },
            ]
        }
    }
    fulfillment = parse_target_fulfillment_response(
        fulfillment_payload,
        expected_tcins=[TCIN],
    )
    assert set(fulfillment) == {TCIN}
    merged = merge_fulfillment(offer, fulfillment[TCIN])
    assert merged.shipping_available is True
    assert merged.pickup_available is True
    assert merged.can_add_to_cart is True
