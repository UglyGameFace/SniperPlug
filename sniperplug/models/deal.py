from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from uuid import uuid4


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class NormalizedDeal:
    title: str
    retailer: str
    product_url: str

    current_price: float | None = None
    typical_price: float | None = None
    discount_percent: float | None = None
    savings_amount: float | None = None

    source: str = "manual_test"
    marketplace: str = "US"
    image_url: str | None = None

    asin: str | None = None
    sku: str | None = None
    upc: str | None = None

    seller_name: str | None = None
    fulfilled_by_amazon: bool | None = None
    fulfillment_type: str | None = None
    condition: str | None = None
    availability_message: str | None = None

    is_possible_price_error: bool = False
    is_ymmv: bool = False
    risk_level: str = "low"
    confidence_score: int = 50

    risk_flags: list[str] = field(default_factory=list)
    alert_tags: list[str] = field(default_factory=list)

    deal_id: str = field(default_factory=lambda: uuid4().hex)
    first_seen_at: str = field(default_factory=utc_now_iso)
    last_checked_at: str = field(default_factory=utc_now_iso)
    expires_at: str | None = None

    def recalculate_prices(self) -> None:
        if self.current_price is not None and self.typical_price:
            self.savings_amount = round(max(self.typical_price - self.current_price, 0), 2)
            self.discount_percent = round((self.savings_amount / self.typical_price) * 100, 2)

    def to_dict(self) -> dict:
        return asdict(self)
