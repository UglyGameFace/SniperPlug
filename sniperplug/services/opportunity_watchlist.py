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
    OpportunityCategory("gpus", "Graphics Cards", 100, 55, 300, ("rtx 5090", "rtx 5080", "rtx 5070", "rtx 4090", "gpu", "graphics card", "radeon rx"), ("ai demand", "gaming demand", "scarce supply", "component inflation")),
    OpportunityCategory("brand_direct_electronics", "Brand-Direct Electronics", 96, 45, 100, ("samsung", "lg oled", "sony bravia", "pixel", "galaxy", "surface", "xbox", "playstation", "oled tv", "qled", "monitor"), ("high resale value", "brand-direct coupon stacks", "trade-in pricing mistakes")),
    OpportunityCategory("sneakers", "Sneakers / Streetwear", 94, 45, 25, ("nike", "jordan", "air max", "dunk", "adidas", "yeezy", "puma", "new balance", "asics", "sneaker", "foot locker", "jd sports"), ("resale value", "drop demand", "member pricing", "checkout/coupon mistakes")),
    OpportunityCategory("cpus", "CPUs", 92, 50, 120, ("ryzen 9", "ryzen 7", "intel i9", "intel i7", "threadripper", "cpu", "processor"), ("ai demand", "pc upgrade cycle", "workstation demand")),
    OpportunityCategory("apple", "Apple Products", 92, 45, 100, ("iphone", "ipad", "macbook", "apple watch", "airpods", "mac mini"), ("high resale value", "strong consumer demand")),
    OpportunityCategory("ram", "RAM / Memory", 95, 45, 35, ("ddr5", "ddr4", "memory kit", "ram kit", "sodimm", "ecc memory"), ("memory shortage", "ai server demand", "component inflation")),
    OpportunityCategory("ssds", "SSDs / Storage", 90, 45, 30, ("nvme", "ssd", "m.2", "4tb", "8tb", "portable ssd", "external ssd"), ("nand price swings", "ai/server storage demand", "consumer storage demand")),
    OpportunityCategory("premium_apparel", "Premium Apparel", 86, 55, 20, ("lululemon", "north face", "patagonia", "carhartt", "arc'teryx", "columbia", "under armour", "sportswear", "hoodie", "jacket"), ("brand demand", "resale value", "seasonal clearance", "coupon stacking")),
    OpportunityCategory("gold_jewelry", "Gold / Jewelry", 88, 35, 50, ("10k", "14k", "18k", "gold chain", "gold bracelet", "gold coin", "gold bar", "diamond", "tennis chain", "zales", "kay jewelers"), ("gold price volatility", "inflation hedge", "resale value")),
    OpportunityCategory("watches", "Watches", 82, 45, 40, ("g-shock", "casio", "seiko", "citizen", "bulova", "fossil", "watch", "smartwatch"), ("resale value", "gift demand", "clearance cycles", "coupon stacking")),
    OpportunityCategory("fragrance_beauty", "Fragrance / Beauty", 78, 55, 15, ("cologne", "perfume", "fragrance", "eau de parfum", "eau de toilette", "dyson airwrap", "shark flexstyle"), ("gift demand", "brand demand", "coupon stacking", "holiday clearance")),
    OpportunityCategory("motor_oil", "Motor Oil / Auto Fluids", 86, 40, 8, ("motor oil", "synthetic oil", "5w-30", "0w-20", "diesel oil", "transmission fluid", "def fluid", "coolant"), ("oil price pressure", "vehicle maintenance inflation", "bulk household demand")),
    OpportunityCategory(
        "tools",
        "Tools",
        84,
        50,
        25,
        (
            "milwaukee", "dewalt", "makita", "ryobi", "hart", "hyper tough", "tool set", "impact driver", "battery kit",
            "tire inflator", "air inflator", "portable inflator", "air compressor", "compressor", "pressure washer", "drill", "driver", "socket set",
        ),
        ("trade demand", "resale value", "home improvement demand", "garage utility"),
    ),
    OpportunityCategory("appliances", "Appliances", 80, 50, 75, ("washer", "dryer", "refrigerator", "freezer", "dishwasher", "microwave", "air conditioner"), ("housing costs", "home replacement demand", "open-box clearance")),
    OpportunityCategory("mobile_accessories", "Mobile Accessories / Chargers", 98, 35, 20, ("wireless charging station", "3 in 1 charger", "3-in-1 charger", "magsafe", "magnetic charger", "phone charger", "iphone charger", "apple watch charger", "airpods charger", "charging dock", "charging stand", "charging station", "usb c charger", "power bank", "anker charger", "belkin charger", "phone case", "screen protector", "car phone mount"), ("high household demand", "giftable tech", "apple accessory demand", "desk setup demand")),
    OpportunityCategory("viral_gadgets", "Viral / Giftable Gadgets", 90, 40, 25, ("tiktok", "viral", "gadget", "led light", "sunset lamp", "mini printer", "portable blender", "ice maker", "neck fan", "handheld fan", "projector", "massage gun", "desk gadget"), ("viral demand", "gift demand", "impulse-buy demand")),
    OpportunityCategory("smart_home", "Smart Home / Security", 88, 40, 30, ("security camera", "doorbell camera", "smart lock", "smart plug", "smart bulb", "wifi camera", "robot vacuum", "dash cam", "ring camera", "blink camera", "wyze camera", "eufy camera"), ("home security demand", "smart home upgrades", "bundle markdowns")),
    OpportunityCategory("office_school", "Office / School / Desk Setup", 84, 45, 20, ("office chair", "desk", "standing desk", "printer", "label maker", "toner", "ink cartridge", "backpack", "school supplies", "desk organizer", "monitor arm", "laptop stand"), ("work-from-home demand", "school season", "business purchasing")),
    OpportunityCategory("household_essentials", "Household Essentials", 82, 35, 15, ("laundry detergent", "tide", "gain detergent", "paper towels", "toilet paper", "trash bags", "dish soap", "cleaning supplies", "swiffer", "lysol", "clorox", "soap", "body wash", "razor", "toothpaste"), ("repeat household demand", "inflation pressure", "coupon stacking")),
    OpportunityCategory("baby_kids", "Baby / Kids", 80, 35, 20, ("diaper", "baby wipes", "baby monitor", "stroller", "car seat", "booster seat", "crib", "baby formula", "toddler"), ("family necessity", "high repeat demand", "registry/gift demand")),
    OpportunityCategory("pet_supplies", "Pet Supplies", 78, 35, 15, ("dog food", "cat food", "cat litter", "pet bed", "dog crate", "pet carrier", "flea", "aquarium", "pet toy"), ("repeat pet demand", "bulk buying", "subscription replacement demand")),
    OpportunityCategory("outdoor_sports", "Outdoor / Sports", 82, 45, 25, ("bike", "electric scooter", "treadmill", "exercise bike", "weights", "dumbbell", "tent", "cooler", "grill", "fishing", "camping", "pool", "basketball hoop"), ("seasonal demand", "fitness demand", "outdoor clearance")),
    OpportunityCategory("shoes_apparel", "Shoes / Apparel", 84, 50, 20, ("nike", "jordan", "adidas", "puma", "new balance", "sneaker", "shoes", "boots", "hoodie", "jacket", "carhartt", "north face", "columbia", "under armour"), ("brand demand", "seasonal clearance", "resale value")),
    OpportunityCategory("seasonal_holiday", "Seasonal / Holiday", 76, 50, 15, ("christmas", "halloween", "easter", "valentine", "back to school", "pool clearance", "patio clearance", "holiday clearance", "seasonal clearance"), ("seasonal liquidation", "clearance markdowns", "gift demand")),
    OpportunityCategory("walmart_cash", "Walmart Cash Offers", 99, 0, None, ("walmart cash", "walmart cash eligible", "cash offer", "cashback", "cash back", "onepay", "onepay cashrewards"), ("extra Walmart Cash value", "cashback stacking", "hidden value beyond sticker price")),
    OpportunityCategory("home_kitchen", "Home / Kitchen", 84, 40, 20, ("air fryer", "coffee maker", "keurig", "ninja", "vacuum", "shark vacuum", "bissell", "cookware", "mattress", "furniture", "patio furniture", "storage shelf", "humidifier", "heater", "fan"), ("home demand", "gift demand", "seasonal clearance")),
    OpportunityCategory("grocery_pantry", "Grocery / Pantry", 76, 35, 10, ("snacks", "coffee", "energy drink", "protein", "cereal", "soda", "water bottle", "pantry", "case pack", "grocery", "candy", "chips"), ("repeat grocery demand", "bulk buying", "coupon stacking")),
    OpportunityCategory("toys_collectibles", "Toys / Collectibles", 86, 40, 20, ("lego", "pokemon", "trading cards", "board game", "video game", "collectible", "hot wheels", "barbie", "nerf", "squishmallow", "toy clearance"), ("gift demand", "collector demand", "holiday clearance", "resale potential")),
    OpportunityCategory("open_box_restored", "Open Box / Restored / Refurbished", 88, 35, 30, ("restored", "refurbished", "open box", "open-box", "like new", "like-new", "restored: like new", "restored: good", "restored: fair", "used", "excellent condition"), ("condition-based markdowns", "electronics flips", "hidden variant pricing")),
    OpportunityCategory("health_wellness", "Health / Wellness", 74, 35, 15, ("vitamin", "protein powder", "supplement", "fitness tracker", "blood pressure", "massage gun", "heating pad", "first aid", "personal care"), ("repeat demand", "coupon stacking", "household need")),
    OpportunityCategory("business_bulk", "Business / Bulk Supplies", 82, 45, 20, ("amazon business", "business account", "bulk", "case pack", "quantity discount", "office chair", "toner", "printer paper"), ("business purchasing", "bulk pricing mistakes", "B2B coupon stacking")),
)


def category_for_title(title: str) -> OpportunityCategory | None:
    normalized = title.lower()
    matches = [category for category in OPPORTUNITY_CATEGORIES if any(term in normalized for term in category.terms)]
    if not matches:
        return None
    return sorted(matches, key=lambda category: category.demand_level, reverse=True)[0]


def high_demand_categories(limit: int = 10) -> list[OpportunityCategory]:
    return sorted(OPPORTUNITY_CATEGORIES, key=lambda category: category.demand_level, reverse=True)[:limit]
