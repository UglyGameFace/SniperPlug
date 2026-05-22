from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

from sniperplug.models.candidate import SourceCandidate


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class StoreLocation:
    retailer: str
    store_id: str
    name: str
    address: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    distance_miles: float | None = None

    @property
    def display_name(self) -> str:
        location = ", ".join(part for part in (self.city, self.state) if part)
        return f"{self.name} — {location}" if location else self.name


@dataclass
class LocalStockOffer:
    retailer: str
    title: str
    product_url: str
    sku: str | None = None
    upc: str | None = None
    image_url: str | None = None
    store: StoreLocation | None = None
    local_price: float | None = None
    regular_price: float | None = None
    national_price: float | None = None
    stock_quantity: int | None = None
    stock_status: str | None = None
    aisle: str | None = None
    bay: str | None = None
    location_note: str | None = None
    zip_code: str | None = None
    source_key: str = "local_stock"
    signals: list[str] = field(default_factory=list)
    offer_id: str = field(default_factory=lambda: uuid4().hex)
    checked_at: str = field(default_factory=utc_now_iso)

    @property
    def best_reference_price(self) -> float | None:
        return self.regular_price or self.national_price

    @property
    def discount_percent(self) -> float | None:
        reference = self.best_reference_price
        if self.local_price is None or not reference or reference <= 0:
            return None
        return max(0.0, round((reference - self.local_price) / reference * 100, 2))

    @property
    def location_summary(self) -> str:
        parts: list[str] = []
        if self.store:
            parts.append(self.store.display_name)
        if self.aisle:
            parts.append(f"Aisle {self.aisle}")
        if self.bay:
            parts.append(f"Bay {self.bay}")
        if self.stock_quantity is not None:
            parts.append(f"{self.stock_quantity} in stock")
        elif self.stock_status:
            parts.append(self.stock_status)
        return " • ".join(parts) if parts else "Local stock details unavailable"

    def to_source_candidate(self) -> SourceCandidate:
        signals = list(self.signals)
        if self.zip_code:
            signals.append(f"ZIP checked: {self.zip_code}")
        if self.store:
            signals.append(f"Store: {self.store.display_name}")
        if self.stock_quantity is not None:
            signals.append(f"Local stock quantity: {self.stock_quantity}")
        if self.aisle or self.bay:
            signals.append(f"Store location: {self.aisle or 'unknown aisle'} / {self.bay or 'unknown bay'}")
        if self.discount_percent is not None:
            signals.append(f"Local discount: {self.discount_percent}%")

        return SourceCandidate(
            source_key=self.source_key,
            retailer=self.retailer,
            title=self.title,
            product_url=self.product_url,
            current_price=self.local_price,
            typical_price=self.best_reference_price,
            image_url=self.image_url,
            product_id=self.sku or self.upc,
            product_id_type="sku" if self.sku else ("upc" if self.upc else None),
            sku=self.sku,
            upc=self.upc,
            stock_status=self.stock_status or self.location_summary,
            can_add_to_cart=self.stock_quantity is None or self.stock_quantity > 0,
            signals=signals[:10],
        )


@dataclass(frozen=True)
class LocalStockRequest:
    retailer_key: str
    sku: str
    zip_code: str
    radius_miles: int = 50
    max_stores: int = 10


@dataclass(frozen=True)
class LocalStockResult:
    retailer_key: str
    sku: str
    zip_code: str
    offers: tuple[LocalStockOffer, ...]
    warnings: tuple[str, ...] = ()
