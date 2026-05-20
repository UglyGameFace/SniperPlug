from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OpportunityCategory:
    key: str
    label: str
    demand_level: int
    min_discount_percent: float
    absolute_price_floor: float | None
    terms: tuple[str, ...]
    economic_drivers: tuple[str, ...]


OPPORTUNITY_CATEGORIES: tuple[OpportunityCategory, ...] = (
    OpportunityCategory(
        key="gpus",
        label="Graphics Cards",
        demand_level=100,
        min_discount_percent=55,
        absolute_price_floor=300,
        terms=("rtx 5090", "rtx 5080", "rtx 5070", "rtx 4090", "gpu", "graphics card", "radeon rx"),
        economic_drivers=("ai demand", "gaming demand", "scarce supply", "component inflation"),
    ),
    OpportunityCategory(
        key="cpus",
        label="CPUs",
        demand_level=92,
        min_discount_percent=50,
        absolute_price_floor=120,
        terms=("ryzen 9", "ryzen 7", "intel i9", "intel i7", "threadripper", "cpu", "processor"),
        economic_drivers=("ai demand", "pc upgrade cycle", "workstation demand"),
    ),
    OpportunityCategory(
        key="ram",
        label="RAM / Memory",
        demand_level=95,
        min_discount_percent=45,
        absolute_price_floor=35,
        terms=("ddr5", "ddr4", "memory kit", "ram kit", "sodimm", "ecc memory"),
        economic_drivers=("memory shortage", "ai server demand", "component inflation"),
    ),
    OpportunityCategory(
        key="ssds",
        label="SSDs / Storage",
        demand_level=90,
        min_discount_percent=45,
        absolute_price_floor=30,
        terms=("nvme", "ssd", "m.2", "4tb", "8tb", "portable ssd", "external ssd"),
        economic_drivers=("nand price swings", "ai/server storage demand", "consumer storage demand"),
    ),
    OpportunityCategory(
        key="apple",
        label="Apple Products",
        demand_level=92,
        min_discount_percent=45,
        absolute_price_floor=100,
        terms=("iphone", "ipad", "macbook", "apple watch", "airpods", "mac mini"),
        economic_drivers=("high resale value", "strong consumer demand"),
    ),
    OpportunityCategory(
        key="gold_jewelry",
        label="Gold / Jewelry",
        demand_level=88,
        min_discount_percent=35,
        absolute_price_floor=50,
        terms=("10k", "14k", "18k", "gold chain", "gold bracelet", "gold coin", "gold bar", "diamond"),
        economic_drivers=("gold price volatility", "inflation hedge", "resale value"),
    ),
    OpportunityCategory(
        key="motor_oil",
        label="Motor Oil / Auto Fluids",
        demand_level=86,
        min_discount_percent=40,
        absolute_price_floor=8,
        terms=("motor oil", "synthetic oil", "5w-30", "0w-20", "diesel oil", "transmission fluid", "def fluid", "coolant"),
        economic_drivers=("oil price pressure", "vehicle maintenance inflation", "bulk household demand"),
    ),
    OpportunityCategory(
        key="tools",
        label="Tools",
        demand_level=84,
        min_discount_percent=50,
        absolute_price_floor=25,
        terms=("milwaukee", "dewalt", "makita", "ryobi", "tool set", "impact driver", "battery kit"),
        economic_drivers=("trade demand", "resale value", "home improvement demand"),
    ),
    OpportunityCategory(
        key="appliances",
        label="Appliances",
        demand_level=80,
        min_discount_percent=50,
        absolute_price_floor=75,
        terms=("washer", "dryer", "refrigerator", "freezer", "dishwasher", "microwave", "air conditioner"),
        economic_drivers=("housing costs", "home replacement demand", "open-box clearance"),
    ),
    OpportunityCategory(
        key="business_bulk",
        label="Business / Bulk Supplies",
        demand_level=82,
        min_discount_percent=45,
        absolute_price_floor=20,
        terms=("amazon business", "business account", "bulk", "case pack", "quantity discount", "office chair", "toner", "printer paper"),
        economic_drivers=("business purchasing", "bulk pricing mistakes", "B2B coupon stacking"),
    ),
)


def category_for_title(title: str) -> OpportunityCategory | None:
    normalized = title.lower()
    matches = [category for category in OPPORTUNITY_CATEGORIES if any(term in normalized for term in category.terms)]
    if not matches:
        return None
    return sorted(matches, key=lambda category: category.demand_level, reverse=True)[0]


def high_demand_categories(limit: int = 10) -> list[OpportunityCategory]:
    return sorted(OPPORTUNITY_CATEGORIES, key=lambda category: category.demand_level, reverse=True)[:limit]
