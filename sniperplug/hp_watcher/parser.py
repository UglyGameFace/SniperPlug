from __future__ import annotations

from dataclasses import dataclass
import html as html_module
import json
import re
from typing import Any, Iterable
from urllib.parse import urlparse
from xml.etree import ElementTree


_HP_PDP_PATH_MARKERS = ("/us-en/shop/pdp/", "/us-en/shop/products/")


@dataclass(frozen=True)
class SitemapDocument:
    kind: str
    locations: tuple[str, ...]


@dataclass(frozen=True)
class ProductPageIdentity:
    product_url: str
    sku: str
    catalog_entry_id: str
    title: str = ""
    image_url: str = ""


@dataclass(frozen=True)
class HPPriceOffer:
    product_id: str
    part_number: str
    sku: str
    current_price: float
    msrp_price: float | None
    promotion_text: str = ""
    in_stock: bool | None = None
    can_add_to_cart: bool | None = None

    @property
    def discount_percent(self) -> float:
        if self.msrp_price is None or self.msrp_price <= self.current_price:
            return 0.0
        return round((self.msrp_price - self.current_price) / self.msrp_price * 100.0, 2)


def parse_sitemap_xml(xml_text: str) -> SitemapDocument:
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError as error:
        raise ValueError(f"invalid sitemap XML: {error}") from error

    tag = _local_name(root.tag)
    if tag not in {"sitemapindex", "urlset"}:
        raise ValueError(f"unsupported sitemap root: {tag or 'unknown'}")

    locations: list[str] = []
    for element in root.iter():
        if _local_name(element.tag) != "loc":
            continue
        location = str(element.text or "").strip()
        if location.startswith("https://www.hp.com/") and location not in locations:
            locations.append(location)
    return SitemapDocument(kind=tag, locations=tuple(locations))


def hp_us_product_urls(document: SitemapDocument) -> tuple[str, ...]:
    if document.kind != "urlset":
        return ()
    return tuple(location for location in document.locations if is_official_hp_product_url(location))


def is_official_hp_product_url(value: str) -> bool:
    try:
        parsed = urlparse(str(value or "").strip())
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    path = parsed.path.lower()
    return parsed.scheme == "https" and host == "www.hp.com" and any(marker in path for marker in _HP_PDP_PATH_MARKERS)


def parse_product_page_identity(product_url: str, html_text: str) -> ProductPageIdentity:
    if not is_official_hp_product_url(product_url):
        raise ValueError("product URL is not an official US HP Store product URL")
    if not str(html_text or "").strip():
        raise ValueError("empty HP product page")

    decoded = html_module.unescape(html_text)
    json_objects = list(_script_json_objects(decoded))
    candidates = list(_identity_candidates(json_objects))

    regex_sku = _first_match(
        decoded,
        (
            r'(?i)"(?:sku|partNumber|part_number)"\s*:\s*"([^"<>]{3,80})"',
            r'(?i)data-(?:sku|part-number)\s*=\s*["\']([^"\']+)',
            r'(?i)(?:Product\s*#|Product\s*number)\s*</?[^>]*>?\s*([A-Z0-9][A-Z0-9#-]{4,40})',
        ),
    )
    regex_product_id = _first_match(
        decoded,
        (
            r'(?i)"(?:catentryId|catEntryId|catentry_id|catalogEntryId)"\s*:\s*"?(\d{4,20})"?',
            r'(?i)data-(?:catentry-id|product-id)\s*=\s*["\'](\d{4,20})["\']',
        ),
    )

    selected = _select_identity_candidate(candidates, preferred_sku=regex_sku)
    sku = normalize_hp_sku((selected or {}).get("sku") or regex_sku)
    product_id = _digits((selected or {}).get("product_id") or regex_product_id)
    title = _clean_text((selected or {}).get("title") or _html_title(decoded))
    image_url = _clean_url((selected or {}).get("image_url"))

    if not sku:
        raise ValueError("HP product page did not expose an exact SKU/part number")
    if not product_id:
        raise ValueError("HP product page did not expose a numeric catalog entry ID")
    return ProductPageIdentity(
        product_url=product_url,
        sku=sku,
        catalog_entry_id=product_id,
        title=title,
        image_url=image_url,
    )


def parse_hp_services_price_response(
    payload: str | bytes | bytearray | dict[str, Any],
    *,
    expected_products: dict[str, str] | None = None,
) -> tuple[HPPriceOffer, ...]:
    data = _load_json_payload(payload)
    price_rows = data.get("priceData") if isinstance(data, dict) else None
    if not isinstance(price_rows, list):
        raise ValueError("HP structured response is missing priceData")

    inventory = _inventory_by_product_id(data)
    expected = {
        _digits(product_id): normalize_hp_sku(sku)
        for product_id, sku in dict(expected_products or {}).items()
        if _digits(product_id) and normalize_hp_sku(sku)
    }
    offers: list[HPPriceOffer] = []
    seen: set[tuple[str, str]] = set()

    for row in price_rows:
        if not isinstance(row, dict):
            continue
        product_id = _digits(row.get("productId") or row.get("catentryId"))
        part_number = _clean_text(row.get("partNumber") or row.get("sku"))
        sku = normalize_hp_sku(part_number)
        if not product_id or not sku:
            continue
        if expected:
            expected_sku = expected.get(product_id)
            if not expected_sku or expected_sku != sku:
                continue

        current = _positive_money(row.get("price"))
        if current is None:
            current = _positive_money(row.get("gsPrice"))
        if current is None:
            continue
        reference = _positive_money(row.get("lPrice"))
        if reference is not None and reference <= current:
            reference = None

        inventory_bits = inventory.get(product_id, {})
        in_stock = _optional_bool(
            row.get("inStock"),
            row.get("availableOnline"),
            inventory_bits.get("in_stock"),
        )
        can_add = _optional_bool(
            row.get("canAddToCart"),
            row.get("buyable"),
            inventory_bits.get("can_add_to_cart"),
        )
        if can_add is None and in_stock is True:
            can_add = True
        if in_stock is None and can_add is True:
            in_stock = True

        key = (product_id, sku)
        if key in seen:
            continue
        seen.add(key)
        offers.append(
            HPPriceOffer(
                product_id=product_id,
                part_number=part_number,
                sku=sku,
                current_price=current,
                msrp_price=reference,
                promotion_text=_clean_text(row.get("jPromMsg") or row.get("promotionText")),
                in_stock=in_stock,
                can_add_to_cart=can_add,
            )
        )
    return tuple(offers)


def normalize_hp_sku(value: Any) -> str:
    text = _clean_text(value).upper().replace(" ", "")
    if not text:
        return ""
    text = text.split("#", 1)[0]
    text = re.sub(r"[^A-Z0-9-]", "", text)
    return text if len(text) >= 5 and any(character.isdigit() for character in text) else ""


def _script_json_objects(html_text: str) -> Iterable[Any]:
    pattern = re.compile(
        r"<script\b[^>]*(?:type=[\"']application/(?:ld\+json|json)[\"'])?[^>]*>(.*?)</script>",
        re.IGNORECASE | re.DOTALL,
    )
    for match in pattern.finditer(html_text):
        text = match.group(1).strip()
        if not text or text[0] not in "[{":
            continue
        try:
            yield json.loads(text)
        except json.JSONDecodeError:
            continue


def _identity_candidates(objects: Iterable[Any]) -> Iterable[dict[str, str]]:
    for root in objects:
        for node in _walk_json(root):
            if not isinstance(node, dict):
                continue
            sku = normalize_hp_sku(
                node.get("sku")
                or node.get("partNumber")
                or node.get("part_number")
                or node.get("productNumber")
            )
            product_id = _digits(
                node.get("catentryId")
                or node.get("catEntryId")
                or node.get("catalogEntryId")
                or node.get("productId")
            )
            if not sku and not product_id:
                continue
            image = node.get("image") or node.get("imageUrl") or ""
            if isinstance(image, list):
                image = image[0] if image else ""
            if isinstance(image, dict):
                image = image.get("url") or image.get("contentUrl") or ""
            yield {
                "sku": sku,
                "product_id": product_id,
                "title": _clean_text(node.get("name") or node.get("title")),
                "image_url": _clean_url(image),
            }


def _select_identity_candidate(
    candidates: list[dict[str, str]],
    *,
    preferred_sku: str,
) -> dict[str, str] | None:
    normalized_preferred = normalize_hp_sku(preferred_sku)
    complete = [item for item in candidates if item.get("sku") and item.get("product_id")]
    if normalized_preferred:
        for item in complete:
            if item.get("sku") == normalized_preferred:
                return item
    return complete[0] if complete else None


def _walk_json(value: Any) -> Iterable[Any]:
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json(child)


def _load_json_payload(value: str | bytes | bytearray | dict[str, Any]) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, (bytes, bytearray)):
        text = value.decode("utf-8", errors="replace")
    else:
        text = str(value or "")
    text = text.strip()
    if not text:
        raise ValueError("empty HP structured response")
    if not text.startswith("{"):
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("HP structured response was not JSON")
        text = text[start : end + 1]
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid HP structured JSON: {error}") from error
    if not isinstance(data, dict):
        raise ValueError("HP structured response root must be an object")
    return data


def _inventory_by_product_id(data: dict[str, Any]) -> dict[str, dict[str, bool | None]]:
    results: dict[str, dict[str, bool | None]] = {}
    for node in _walk_json(data):
        if not isinstance(node, dict):
            continue
        product_id = _digits(node.get("productId") or node.get("catentryId") or node.get("catEntryId"))
        if not product_id:
            continue
        status_text = _clean_text(
            node.get("inventoryStatus")
            or node.get("availability")
            or node.get("status")
        ).lower()
        in_stock = _optional_bool(node.get("inStock"), node.get("availableOnline"))
        can_add = _optional_bool(node.get("canAddToCart"), node.get("buyable"))
        if in_stock is None and status_text:
            if any(term in status_text for term in ("in stock", "available", "low stock")):
                in_stock = True
            elif any(term in status_text for term in ("out of stock", "unavailable", "sold out")):
                in_stock = False
        current = results.setdefault(product_id, {"in_stock": None, "can_add_to_cart": None})
        if in_stock is not None:
            current["in_stock"] = in_stock
        if can_add is not None:
            current["can_add_to_cart"] = can_add
    return results


def _optional_bool(*values: Any) -> bool | None:
    for value in values:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)) and value in {0, 1}:
            return bool(value)
        text = str(value or "").strip().lower()
        if text in {"true", "yes", "1", "available", "in_stock", "instock"}:
            return True
        if text in {"false", "no", "0", "unavailable", "out_of_stock", "outofstock"}:
            return False
    return None


def _positive_money(value: Any) -> float | None:
    if isinstance(value, str):
        value = value.replace("$", "").replace(",", "").strip()
    try:
        parsed = round(float(value), 2)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _first_match(text: str, patterns: Iterable[str]) -> str:
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return _clean_text(match.group(1))
    return ""


def _html_title(text: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", text, re.IGNORECASE | re.DOTALL)
    return _clean_text(re.sub(r"<[^>]+>", " ", match.group(1))) if match else ""


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _clean_url(value: Any) -> str:
    text = _clean_text(value)
    return text if text.startswith("https://") else ""


def _digits(value: Any) -> str:
    text = re.sub(r"\D", "", str(value or ""))
    return text if 4 <= len(text) <= 20 else ""


def _local_name(tag: str) -> str:
    return str(tag or "").rsplit("}", 1)[-1].lower()
