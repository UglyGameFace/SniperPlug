from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class InventoryProofLevel(str, Enum):
    NONE = "none"
    PRODUCT_SEEN = "product_seen"
    LOCAL_PAGE_SEEN = "local_page_seen"
    LOCAL_PRICE_SEEN = "local_price_seen"
    MULTI_STORE_PATTERN = "multi_store_pattern"
    IN_STORE_SCAN_CONFIRMED = "in_store_scan_confirmed"


class ClearanceStage(str, Enum):
    UNKNOWN = "unknown"
    CLEARANCE_CANDIDATE = "clearance_candidate"
    FINAL_MARKDOWN_CANDIDATE = "final_markdown_candidate"
    PENNY_CANDIDATE = "penny_candidate"
    USER_CONFIRMED = "user_confirmed"


@dataclass(frozen=True)
class ClearanceSignal:
    stage: ClearanceStage
    reason: str
    price_ending: str | None = None
    confidence: int = 0


@dataclass(frozen=True)
class LocalInventoryRequest:
    retailer: str
    product_id: str | None = None
    sku: str | None = None
    upc: str | None = None
    store_id: str | None = None
    zip_code: str | None = None
    observed_price: float | None = None
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass
class LocalInventoryProof:
    retailer: str
    product_id: str | None = None
    sku: str | None = None
    upc: str | None = None
    store_id: str | None = None
    zip_code: str | None = None

    local_price: float | None = None
    online_price: float | None = None
    quantity_available: int | None = None
    availability_text: str | None = None

    proof_level: InventoryProofLevel = InventoryProofLevel.NONE
    clearance_signal: ClearanceSignal | None = None
    warnings: list[str] = field(default_factory=list)
    source: str = "manual"
    checked_at: str = field(default_factory=utc_now_iso)

    @property
    def should_public_alert(self) -> bool:
        return self.proof_level in {
            InventoryProofLevel.MULTI_STORE_PATTERN,
            InventoryProofLevel.IN_STORE_SCAN_CONFIRMED,
        }

    @property
    def should_staff_review(self) -> bool:
        return self.proof_level != InventoryProofLevel.NONE and not self.should_public_alert


def clearance_signal_from_price(price: float | None) -> ClearanceSignal | None:
    if price is None:
        return None

    ending = price_ending(price)
    if ending is None:
        return None

    if ending == "01":
        return ClearanceSignal(
            stage=ClearanceStage.PENNY_CANDIDATE,
            reason="Observed price ends in .01. Treat as a possible in-store penny/clearance lead until verified by scan.",
            price_ending=ending,
            confidence=45,
        )
    if ending == "03":
        return ClearanceSignal(
            stage=ClearanceStage.FINAL_MARKDOWN_CANDIDATE,
            reason="Observed price ends in .03. Treat as a final-markdown candidate that may move fast locally.",
            price_ending=ending,
            confidence=35,
        )
    if ending == "06":
        return ClearanceSignal(
            stage=ClearanceStage.CLEARANCE_CANDIDATE,
            reason="Observed price ends in .06. Treat as an early clearance candidate, not a confirmed penny deal.",
            price_ending=ending,
            confidence=25,
        )
    return None


def price_ending(price: float) -> str | None:
    cents = int(round((price - int(price)) * 100))
    if cents < 0 or cents > 99:
        return None
    return f"{cents:02d}"


def make_unsupported_inventory_proof(provider_key: str, request: LocalInventoryRequest) -> LocalInventoryProof:
    return LocalInventoryProof(
        retailer=provider_key,
        product_id=request.product_id,
        sku=request.sku,
        upc=request.upc,
        store_id=request.store_id,
        zip_code=request.zip_code,
        local_price=request.observed_price,
        proof_level=InventoryProofLevel.NONE,
        warnings=[f"{provider_key} does not support local inventory checks yet."],
        source="unsupported",
    )
