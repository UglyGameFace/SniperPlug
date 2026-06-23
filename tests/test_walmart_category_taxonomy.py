from sniperplug.services.opportunity_watchlist import OPPORTUNITY_CATEGORIES, category_for_title
from sniperplug.services.verified_discount_hunt import HUNT_PRESETS


SCREENSHOT_TITLE = (
    "3 in 1 Wireless Charging Station, 2026 Upgraded Fast Desk Charger Station "
    "for iPhone 17 16 15 14 13 12 Pro Max Plus, Charger Stand for Apple Watch "
    "10 9 8 7 6 5 4 3 2 SE, Airpods 4 3 2 Pro, Black"
)


def test_wireless_charging_station_gets_high_demand_category():
    category = category_for_title(SCREENSHOT_TITLE)

    assert category is not None
    assert category.key == "mobile_accessories"
    assert category.label == "Mobile Accessories / Chargers"
    assert category.min_discount_percent <= 35


def test_deal_week_routes_include_mobile_accessory_queries():
    queries = " | ".join(HUNT_PRESETS["deal_week"].queries).lower()

    for expected in (
        "wireless charging station",
        "3 in 1 charger",
        "magsafe charger",
        "iphone charger rollback",
        "apple watch charger",
        "phone accessories clearance",
        "desk gadget",
    ):
        assert expected in queries


def test_all_walmart_routes_include_broad_non_popular_surfaces():
    queries = " | ".join(HUNT_PRESETS["all"].queries).lower()

    for expected in (
        "phone accessories clearance",
        "wireless charger rollback",
        "smart home clearance",
        "pet supplies clearance",
        "shoe clearance",
        "seasonal clearance",
    ):
        assert expected in queries


def test_taxonomy_does_not_forget_major_departments():
    keys = {category.key for category in OPPORTUNITY_CATEGORIES}

    expected = {
        "gpus",
        "cpus",
        "ram",
        "ssds",
        "apple",
        "mobile_accessories",
        "viral_gadgets",
        "smart_home",
        "office_school",
        "household_essentials",
        "baby_kids",
        "pet_supplies",
        "outdoor_sports",
        "shoes_apparel",
        "seasonal_holiday",
        "gold_jewelry",
        "watches",
        "fragrance_beauty",
        "motor_oil",
        "tools",
        "appliances",
        "business_bulk",
    }

    assert expected <= keys
