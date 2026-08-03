from __future__ import annotations

import pytest

from sniperplug.target_watcher.stores import parse_target_nearby_stores


def test_target_store_parser_extracts_and_sorts_complete_stores() -> None:
    payload = {
        "data": {
            "nearby_stores": {
                "stores": [
                    {
                        "location_id": "2002",
                        "location_name": "Target Farther",
                        "distance": 8.4,
                        "mailing_address": {
                            "address_line1": "200 Example Ave",
                            "city": "Fairfield",
                            "region": "CT",
                            "postal_code": "06824",
                        },
                        "geographic_specifications": {
                            "latitude": 41.15,
                            "longitude": -73.25,
                        },
                    },
                    {
                        "location_id": "1956",
                        "location_name": "Target Nearest",
                        "distance": {"value": 2.3},
                        "mailing_address": {
                            "address_line1": "120 Hawley Ln",
                            "city": "Trumbull",
                            "region": "CT",
                            "postal_code": "06611",
                        },
                        "geographic_specifications": {
                            "latitude": 41.23,
                            "longitude": -73.15,
                        },
                    },
                ]
            }
        }
    }
    stores = parse_target_nearby_stores(payload)
    assert [store.store_id for store in stores] == ["1956", "2002"]
    assert stores[0].name == "Target Nearest"
    assert stores[0].postal_code == "06611"
    assert stores[0].distance_miles == 2.3


def test_target_store_parser_rejects_incomplete_or_invalid_records() -> None:
    payload = {
        "data": {
            "stores": [
                {
                    "location_id": "1956",
                    "location_name": "Missing Coordinates",
                    "mailing_address": {
                        "city": "Trumbull",
                        "region": "CT",
                        "postal_code": "06611",
                    },
                },
                {
                    "location_id": "not-numeric",
                    "location_name": "Bad ID",
                    "mailing_address": {
                        "city": "Trumbull",
                        "region": "CT",
                        "postal_code": "06611",
                    },
                    "geographic_specifications": {
                        "latitude": 41.23,
                        "longitude": -73.15,
                    },
                },
            ]
        }
    }
    with pytest.raises(ValueError, match="no complete store records"):
        parse_target_nearby_stores(payload)
