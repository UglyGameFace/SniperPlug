from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RetailSource:
    key: str
    name: str
    source_type: str
    priority: int
    categories: tuple[str, ...]
    notes: str


SOURCE_API = "api"
SOURCE_FEED = "feed"
SOURCE_MONITOR = "monitor"
SOURCE_PARTNER = "partner_access"


RETAIL_SOURCES: tuple[RetailSource, ...] = (
    RetailSource(
        key="amazon",
        name="Amazon",
        source_type=SOURCE_API,
        priority=100,
        categories=("electronics", "computer_parts", "business", "household", "automotive", "warehouse_deals"),
        notes="Use official/product-data providers for ASIN, price, offers, and primary images where available.",
    ),
    RetailSource(
        key="best_buy",
        name="Best Buy",
        source_type=SOURCE_API,
        priority=95,
        categories=("electronics", "computer_parts", "gaming", "appliances"),
        notes="Strong source for GPUs, CPUs, monitors, appliances, open-box and product-catalog anomalies.",
    ),
    RetailSource(
        key="walmart",
        name="Walmart",
        source_type=SOURCE_PARTNER,
        priority=94,
        categories=("electronics", "household", "automotive", "grocery", "clearance"),
        notes="Watch online pricing, marketplace seller changes, rollback/clearance mismatches, and store-specific later.",
    ),
    RetailSource(
        key="target",
        name="Target",
        source_type=SOURCE_MONITOR,
        priority=88,
        categories=("electronics", "home", "toys", "household", "clearance"),
        notes="Monitor hot categories and clearance-like online price drops where allowed.",
    ),
    RetailSource(
        key="costco",
        name="Costco",
        source_type=SOURCE_MONITOR,
        priority=92,
        categories=("warehouse", "electronics", "appliances", "jewelry", "bulk", "automotive"),
        notes="Warehouse-club pricing, member-only offers, gold/jewelry, appliances, and bulk misprices.",
    ),
    RetailSource(
        key="sams_club",
        name="Sam's Club",
        source_type=SOURCE_MONITOR,
        priority=91,
        categories=("warehouse", "electronics", "bulk", "household", "automotive"),
        notes="Member pricing, instant savings, bulk packs, and checkout-only pricing anomalies.",
    ),
    RetailSource(
        key="bjs",
        name="BJ's Wholesale Club",
        source_type=SOURCE_MONITOR,
        priority=86,
        categories=("warehouse", "bulk", "household", "appliances", "automotive"),
        notes="Warehouse-club and coupon-stack anomalies.",
    ),
    RetailSource(
        key="woot",
        name="Woot",
        source_type=SOURCE_MONITOR,
        priority=89,
        categories=("electronics", "home", "tools", "clearance"),
        notes="Fast-moving online deals, Amazon-owned closeouts, and limited-quantity anomalies.",
    ),
    RetailSource(
        key="newegg",
        name="Newegg",
        source_type=SOURCE_MONITOR,
        priority=94,
        categories=("computer_parts", "electronics", "gaming"),
        notes="GPUs, CPUs, RAM, SSDs, bundles, combo errors, and marketplace seller changes.",
    ),
    RetailSource(
        key="micro_center",
        name="Micro Center",
        source_type=SOURCE_MONITOR,
        priority=90,
        categories=("computer_parts", "electronics", "gaming"),
        notes="CPU/GPU/RAM/SSD deals, bundles, local inventory, and store-specific pricing later.",
    ),
    RetailSource(
        key="bh_photo",
        name="B&H Photo",
        source_type=SOURCE_MONITOR,
        priority=84,
        categories=("electronics", "cameras", "computer_parts", "pro_audio"),
        notes="Pro electronics, cameras, storage, and checkout-price anomalies.",
    ),
    RetailSource(
        key="adorama",
        name="Adorama",
        source_type=SOURCE_MONITOR,
        priority=82,
        categories=("electronics", "cameras", "computer_parts", "pro_audio"),
        notes="Camera/computer bundles, coupon mistakes, and pro-equipment price drops.",
    ),
    RetailSource(
        key="msi_store",
        name="MSI Store",
        source_type=SOURCE_MONITOR,
        priority=93,
        categories=("computer_parts", "gaming", "laptops"),
        notes="Official manufacturer store for GPU/laptop/accessory price errors.",
    ),
    RetailSource(
        key="dell",
        name="Dell",
        source_type=SOURCE_MONITOR,
        priority=85,
        categories=("computers", "monitors", "business", "electronics"),
        notes="Stacking coupons, business pricing, monitor/laptop misprices.",
    ),
    RetailSource(
        key="lenovo",
        name="Lenovo",
        source_type=SOURCE_MONITOR,
        priority=82,
        categories=("computers", "business", "electronics"),
        notes="Business-account pricing, laptops, monitors, coupons, and cart price mismatches.",
    ),
    RetailSource(
        key="home_depot",
        name="Home Depot",
        source_type=SOURCE_MONITOR,
        priority=90,
        categories=("tools", "home", "appliances", "automotive", "clearance"),
        notes="Tools, appliances, bulk items, online clearance, and later store-specific inventory.",
    ),
    RetailSource(
        key="lowes",
        name="Lowe's",
        source_type=SOURCE_MONITOR,
        priority=89,
        categories=("tools", "home", "appliances", "automotive", "clearance"),
        notes="Tools, appliances, online clearance, coupon stacking, and local inventory later.",
    ),
    RetailSource(
        key="harbor_freight",
        name="Harbor Freight",
        source_type=SOURCE_MONITOR,
        priority=80,
        categories=("tools", "automotive", "garage"),
        notes="Tool pricing, coupons, and clearance anomalies.",
    ),
    RetailSource(
        key="autozone",
        name="AutoZone",
        source_type=SOURCE_MONITOR,
        priority=83,
        categories=("automotive", "motor_oil", "fluids", "parts"),
        notes="Motor oil, fluids, batteries, parts, multi-buy mistakes, and coupon-stack anomalies.",
    ),
    RetailSource(
        key="advance_auto",
        name="Advance Auto Parts",
        source_type=SOURCE_MONITOR,
        priority=83,
        categories=("automotive", "motor_oil", "fluids", "parts"),
        notes="Motor oil, filters, batteries, fluids, and code/coupon-stack mistakes.",
    ),
    RetailSource(
        key="oreilly",
        name="O'Reilly Auto Parts",
        source_type=SOURCE_MONITOR,
        priority=80,
        categories=("automotive", "motor_oil", "fluids", "parts"),
        notes="Auto fluids, parts, and regional pricing anomalies.",
    ),
    RetailSource(
        key="napa",
        name="NAPA Auto Parts",
        source_type=SOURCE_MONITOR,
        priority=78,
        categories=("automotive", "motor_oil", "fluids", "parts"),
        notes="Auto fluids, parts, shop supplies, and business-account pricing.",
    ),
    RetailSource(
        key="staples",
        name="Staples",
        source_type=SOURCE_MONITOR,
        priority=78,
        categories=("business", "office", "electronics", "bulk"),
        notes="Business supplies, bulk packs, toner, chairs, electronics, and coupon mistakes.",
    ),
    RetailSource(
        key="office_depot",
        name="Office Depot / OfficeMax",
        source_type=SOURCE_MONITOR,
        priority=76,
        categories=("business", "office", "electronics", "bulk"),
        notes="Business supplies, bulk pricing, coupons, and office electronics.",
    ),
)


def source_by_key(key: str) -> RetailSource | None:
    normalized = key.strip().lower()
    return next((source for source in RETAIL_SOURCES if source.key == normalized), None)


def sources_for_category(category: str) -> list[RetailSource]:
    normalized = category.strip().lower()
    return [source for source in RETAIL_SOURCES if normalized in source.categories]


def high_priority_sources(limit: int = 10) -> list[RetailSource]:
    return sorted(RETAIL_SOURCES, key=lambda source: source.priority, reverse=True)[:limit]
