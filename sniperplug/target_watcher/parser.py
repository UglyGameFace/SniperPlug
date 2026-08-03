from __future__ import annotations

from dataclasses import dataclass, field
import gzip
import html
import io
import json
from typing import Any, Iterable
from urllib.parse import urlparse
import xml.etree.ElementTree as ET


TARGET_HOSTS = {"target.com", "www.target.com"}


@dataclass(frozen=True)
class TargetSitemap:
    kind: str
    locations: tuple[str, ...]


@dataclass(frozen=True)
class TargetProductSeed:
    tcin: str
    product_url: str


@dataclass(frozen=True)
class TargetOffer:
    tcin: str
    title: str
    product_url: str
    current_price: float
    regular_price: float | None = None
    image_url: str = ""
    seller_name: str = "Target"
    promotion_text: str = ""
    shipping_available: bool | None = None
    pickup_available: bool | None = None
    stock_status: str = ""
    can_add_to_cart: bool | None = None
    variant_label: str = ""
    variant_attributes: dict[str, str] = field(default_factory=dict)

    @property
    def discount_percent(self) -> float:
        if self.regular_price is None or self.regular_price <= self.current_price:
            return 0.0
        return round(
            (self.regular_price - self.current_price) / self.regular_price * 100.0,
            2,
        )


@dataclass(frozen=True)
class TargetFulfillment:
    tcin: str
    shipping_available: bool | None = None
    pickup_available: bool | None = None
    stock_status: str = ""
    can_add_to_cart: bool | None = None


def parse_target_sitemap(
    payload: bytes | str,
    *,
    max_expanded_bytes: int,
) -> TargetSitemap:
    raw = payload.encode("utf-8") if isinstance(payload, str) else bytes(payload)
    if raw[:2] == b"\x1f\x8b":
        raw = _safe_gzip_decompress(raw, max_expanded_bytes=max_expanded_bytes)
    if len(raw) > max(1, int(max_expanded_bytes)):
        raise ValueError("Target sitemap exceeded the expanded-size safety limit")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as error:
        raise ValueError("Target sitemap was not valid XML") from error

    kind = _local_name(root.tag)
    if kind not in {"sitemapindex", "urlset"}:
        raise ValueError("Target sitemap root must be sitemapindex or urlset")

    locations: list[str] = []
    for node in root.iter():
        if _local_name(node.tag) != "loc" or not node.text:
            continue
        url = " ".join(node.text.split())
        if _is_official_target_url(url) and url not in locations:
            locations.append(url)
    return TargetSitemap(kind=kind, locations=tuple(locations))


def target_product_seeds(sitemap: TargetSitemap) -> tuple[TargetProductSeed, ...]:
    if sitemap.kind != "urlset":
        return ()
    seeds: list[TargetProductSeed] = []
    seen: set[str] = set()
    for url in sitemap.locations:
        tcin = tcin_from_target_url(url)
        if not tcin or tcin in seen:
            continue
        seen.add(tcin)
        seeds.append(
            TargetProductSeed(
                tcin=tcin,
                product_url=_safe_target_url(url, expected_tcin=tcin),
            )
        )
    return tuple(seeds)


def parse_target_search_response(payload: Any) -> tuple[TargetOffer, ...]:
    data = _json_object(payload)
    products = _path(data, "data", "search", "products")
    if not isinstance(products, list):
        raise ValueError("Target RedSky search response is missing data.search.products")

    offers: list[TargetOffer] = []
    seen: set[str] = set()
    for raw in products:
        try:
            offer = _parse_product(raw)
        except ValueError:
            continue
        if offer.tcin in seen:
            continue
        seen.add(offer.tcin)
        offers.append(offer)
    return tuple(offers)


def parse_target_product_response(payload: Any, *, expected_tcin: str) -> TargetOffer:
    clean_expected = normalize_tcin(expected_tcin)
    if not clean_expected:
        raise ValueError("Target product parser requires an expected numeric TCIN")
    data = _json_object(payload)
    raw = _path(data, "data", "product")
    if not isinstance(raw, dict):
        raise ValueError("Target RedSky PDP response is missing data.product")
    offer = _parse_product(raw)
    if offer.tcin != clean_expected:
        raise ValueError("Target RedSky PDP response returned a different TCIN")
    return offer


def parse_target_fulfillment_response(
    payload: Any,
    *,
    expected_tcins: Iterable[str],
    expected_store_id: str | None = None,
) -> dict[str, TargetFulfillment]:
    expected = {
        clean for clean in (normalize_tcin(value) for value in expected_tcins) if clean
    }
    if not expected:
        raise ValueError("Target fulfillment parser requires expected TCINs")

    clean_store_id = _clean_text(expected_store_id)
    if clean_store_id and not clean_store_id.isdigit():
        raise ValueError("Target fulfillment parser requires a numeric store id")

    data = _json_object(payload)
    summaries = _normalize_summary_rows(_path(data, "data", "product_summaries"))
    if summaries is None:
        raise ValueError(
            "Target RedSky fulfillment response is missing data.product_summaries"
        )

    results: dict[str, TargetFulfillment] = {}
    for raw in summaries:
        if not isinstance(raw, dict):
            continue
        tcin = normalize_tcin(raw.get("tcin") or _path(raw, "item", "tcin"))
        if tcin not in expected:
            continue

        fulfillment = raw.get("fulfillment") or {}
        if not isinstance(fulfillment, dict):
            fulfillment = {}

        shipping_available, shipping_labels = _shipping_state(
            fulfillment.get("shipping_options")
        )
        store_options = _normalize_object_rows(fulfillment.get("store_options"))
        selected_store_options = _select_store_options(
            store_options,
            expected_store_id=clean_store_id,
        )

        pickup_states: list[bool] = []
        stock_labels: list[str] = list(shipping_labels)
        for option in selected_store_options:
            store_id = _store_id(option)
            label_prefix = f"store:{store_id}:" if store_id else "store:"
            for key in ("order_pickup", "drive_up", "ship_to_store"):
                method = option.get(key) or {}
                status = _clean_text(
                    method.get("availability_status")
                    if isinstance(method, dict)
                    else ""
                )
                available = _availability_bool(status)
                if available is not None:
                    pickup_states.append(available)
                if status:
                    stock_labels.append(f"{label_prefix}{key}:{status}")

        pickup_available = _aggregate_availability(pickup_states)
        explicit_can_add = _first_optional_bool(
            raw.get("is_add_to_cart"),
            raw.get("can_add_to_cart"),
            raw.get("purchasable"),
            fulfillment.get("is_add_to_cart"),
            fulfillment.get("can_add_to_cart"),
        )
        out_everywhere = _optional_bool(
            raw.get("is_out_of_stock_in_all_store_locations")
        )

        if out_everywhere is True:
            can_add = False
        elif explicit_can_add is not None:
            can_add = explicit_can_add
        elif shipping_available is True or pickup_available is True:
            can_add = True
        elif shipping_available is False and pickup_available is False:
            can_add = False
        else:
            can_add = None

        results[tcin] = TargetFulfillment(
            tcin=tcin,
            shipping_available=shipping_available,
            pickup_available=pickup_available,
            stock_status="; ".join(dict.fromkeys(stock_labels)),
            can_add_to_cart=can_add,
        )
    return results


def merge_fulfillment(
    offer: TargetOffer,
    fulfillment: TargetFulfillment | None,
) -> TargetOffer:
    if fulfillment is None:
        return offer
    return TargetOffer(
        tcin=offer.tcin,
        title=offer.title,
        product_url=offer.product_url,
        current_price=offer.current_price,
        regular_price=offer.regular_price,
        image_url=offer.image_url,
        seller_name=offer.seller_name,
        promotion_text=offer.promotion_text,
        shipping_available=fulfillment.shipping_available,
        pickup_available=fulfillment.pickup_available,
        stock_status=fulfillment.stock_status,
        can_add_to_cart=fulfillment.can_add_to_cart,
        variant_label=offer.variant_label,
        variant_attributes=dict(offer.variant_attributes),
    )


def exact_target_offers_match(first: TargetOffer, second: TargetOffer) -> bool:
    return bool(
        first.tcin == second.tcin
        and _money_equal(first.current_price, second.current_price)
        and _optional_money_equal(first.regular_price, second.regular_price)
        and _clean_text(first.seller_name).casefold()
        == _clean_text(second.seller_name).casefold()
    )


def normalize_tcin(value: Any) -> str:
    text = _clean_text(value)
    return text if text.isdigit() and 5 <= len(text) <= 12 else ""


def tcin_from_target_url(value: Any) -> str:
    text = _clean_text(value)
    try:
        parsed = urlparse(text)
    except ValueError:
        return ""
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in TARGET_HOSTS:
        return ""
    marker = "/A-"
    upper_path = parsed.path.upper()
    index = upper_path.rfind(marker)
    if index < 0:
        return ""
    candidate = parsed.path[index + len(marker) :].split("/", 1)[0]
    return normalize_tcin(candidate)


def canonical_target_product_url(tcin: str) -> str:
    clean = normalize_tcin(tcin)
    if not clean:
        raise ValueError("Target product URL requires a numeric TCIN")
    return f"https://www.target.com/p/-/A-{clean}"


def _parse_product(raw: Any) -> TargetOffer:
    if not isinstance(raw, dict):
        raise ValueError("Target product row is not an object")
    item = raw.get("item") or {}
    if not isinstance(item, dict):
        item = {}

    tcin = normalize_tcin(raw.get("tcin") or item.get("tcin"))
    if not tcin:
        raise ValueError("Target product row is missing a numeric TCIN")

    description = item.get("product_description") or {}
    if not isinstance(description, dict):
        description = {}
    title = html.unescape(_clean_text(description.get("title") or raw.get("title")))
    if not title:
        raise ValueError("Target product row is missing a title")

    enrichment = item.get("enrichment") or {}
    if not isinstance(enrichment, dict):
        enrichment = {}
    product_url = _safe_target_url(
        enrichment.get("buy_url")
        or raw.get("buy_url")
        or canonical_target_product_url(tcin),
        expected_tcin=tcin,
    )

    price = raw.get("price") or {}
    if not isinstance(price, dict):
        price = {}
    current = _money(
        price.get("current_retail")
        or price.get("current_price")
        or price.get("formatted_current_price")
    )
    if current is None or current <= 0:
        raise ValueError("Target product row is missing a positive current price")
    regular = _money(
        price.get("reg_retail")
        or price.get("regular_retail")
        or price.get("comparison_retail")
        or price.get("formatted_comparison_price")
    )
    if regular is not None and regular <= current:
        regular = None

    image_url = _primary_image_url(enrichment)
    seller = _seller_name(raw, item)
    promotion = _promotion_text(raw)
    variant_attributes = _variant_attributes(raw, item)
    variant_label = " • ".join(
        value
        for key in ("color", "size", "style", "pattern")
        if (value := variant_attributes.get(key))
    )

    availability = _clean_text(
        raw.get("availability_status")
        or _path(raw, "fulfillment", "shipping_options", "availability_status")
    )
    shipping_available = _availability_bool(availability)
    can_add = _first_optional_bool(
        raw.get("is_add_to_cart"),
        raw.get("can_add_to_cart"),
        raw.get("purchasable"),
    )
    if can_add is None and shipping_available is not None:
        can_add = shipping_available

    return TargetOffer(
        tcin=tcin,
        title=title,
        product_url=product_url,
        current_price=current,
        regular_price=regular,
        image_url=image_url,
        seller_name=seller,
        promotion_text=promotion,
        shipping_available=shipping_available,
        pickup_available=None,
        stock_status=availability,
        can_add_to_cart=can_add,
        variant_label=variant_label,
        variant_attributes=variant_attributes,
    )


def _safe_gzip_decompress(raw: bytes, *, max_expanded_bytes: int) -> bytes:
    limit = max(1, int(max_expanded_bytes))
    output = io.BytesIO()
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(raw), mode="rb") as archive:
            while True:
                remaining = limit + 1 - output.tell()
                if remaining <= 0:
                    raise ValueError(
                        "Target sitemap exceeded the expanded-size safety limit"
                    )
                chunk = archive.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                output.write(chunk)
                if output.tell() > limit:
                    raise ValueError(
                        "Target sitemap exceeded the expanded-size safety limit"
                    )
    except OSError as error:
        raise ValueError("Target sitemap gzip payload was invalid") from error
    return output.getvalue()


def _primary_image_url(enrichment: dict[str, Any]) -> str:
    images = enrichment.get("images") or {}
    if isinstance(images, dict):
        direct = _clean_text(images.get("primary_image_url"))
        if _safe_https_url(direct):
            return direct

    image_info = enrichment.get("image_info") or {}
    if not isinstance(image_info, dict):
        return ""
    primary = image_info.get("primary_image") or {}
    if not isinstance(primary, dict):
        primary = {}
    direct = _clean_text(primary.get("url"))
    if _safe_https_url(direct):
        return direct

    name = _clean_text(primary.get("image_name"))
    base = _clean_text(image_info.get("base_url"))
    if base.startswith("//"):
        base = "https:" + base
    if name and _safe_https_url(base):
        return f"{base.rstrip('/')}/{name.lstrip('/')}"
    return ""


def _seller_name(raw: dict[str, Any], item: dict[str, Any]) -> str:
    candidates = (
        raw.get("seller_name"),
        raw.get("merchant_name"),
        raw.get("sold_by"),
        _path(raw, "seller", "name"),
        _path(raw, "seller", "display_name"),
        _path(raw, "merchant", "name"),
        _path(raw, "merchant", "display_name"),
        _path(raw, "marketplace", "seller_name"),
        item.get("seller_name"),
        item.get("merchant_name"),
        _path(item, "seller", "name"),
        _path(item, "merchant", "name"),
    )
    for value in candidates:
        text = _clean_text(value)
        if text and text.casefold() not in {"owned", "unknown", "n/a"}:
            return text

    if _has_target_plus_marker(raw) or _has_target_plus_marker(item):
        raise ValueError("Target Plus product is missing exact seller identity")
    return "Target"


def _has_target_plus_marker(value: Any, *, depth: int = 0) -> bool:
    if depth > 4:
        return False
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = _clean_key(key)
            if key_text in {
                "is_target_plus",
                "target_plus",
                "target_plus_item",
                "target_plus_partner",
                "is_marketplace",
            } and _optional_bool(child) is True:
                return True
            if "target_plus" in key_text and _optional_bool(child) is not False:
                return True
            if _has_target_plus_marker(child, depth=depth + 1):
                return True
        return False
    if isinstance(value, list):
        return any(_has_target_plus_marker(child, depth=depth + 1) for child in value)
    if isinstance(value, str):
        normalized = value.casefold().replace("™", "")
        return "target plus" in normalized or "target_plus" in normalized
    return False


def _promotion_text(raw: dict[str, Any]) -> str:
    messages: list[str] = []
    price = raw.get("price") or {}
    if isinstance(price, dict):
        for key in ("promotion_text", "formatted_promotion_price"):
            text = _clean_text(price.get(key))
            if text:
                messages.append(text)

    for collection_key in ("promotions", "circle_offers", "offers"):
        collection = raw.get(collection_key) or []
        if isinstance(collection, dict):
            collection = [collection]
        for entry in collection if isinstance(collection, list) else ():
            if not isinstance(entry, dict):
                continue
            for key in ("description", "promotion_text", "offer_text", "name"):
                text = _clean_text(entry.get(key))
                if text:
                    messages.append(text)
                    break
    return " | ".join(dict.fromkeys(messages))[:800]


def _variant_attributes(raw: dict[str, Any], item: dict[str, Any]) -> dict[str, str]:
    attrs: dict[str, str] = {}
    sources = (
        raw.get("variation"),
        raw.get("variation_attributes"),
        item.get("variation"),
    )
    for source in sources:
        if isinstance(source, dict):
            for key, value in source.items():
                text = _clean_text(
                    value.get("value") if isinstance(value, dict) else value
                )
                if text:
                    attrs[_clean_key(key)] = text
        elif isinstance(source, list):
            for entry in source:
                if not isinstance(entry, dict):
                    continue
                key = _clean_key(
                    entry.get("name") or entry.get("type") or entry.get("key")
                )
                value = _clean_text(
                    entry.get("value")
                    or entry.get("display_value")
                    or entry.get("label")
                )
                if key and value:
                    attrs[key] = value
    return attrs


def _shipping_state(value: Any) -> tuple[bool | None, tuple[str, ...]]:
    options = _normalize_object_rows(value)
    if not options and isinstance(value, dict):
        options = [value]
    states: list[bool] = []
    labels: list[str] = []
    for option in options:
        status = _clean_text(option.get("availability_status"))
        available = _availability_bool(status)
        if available is not None:
            states.append(available)
        if status:
            labels.append(f"shipping:{status}")
    return _aggregate_availability(states), tuple(dict.fromkeys(labels))


def _select_store_options(
    options: list[dict[str, Any]],
    *,
    expected_store_id: str,
) -> list[dict[str, Any]]:
    if not options:
        return []
    if expected_store_id:
        return [option for option in options if _store_id(option) == expected_store_id]

    selected = [
        option
        for option in options
        if _first_optional_bool(
            option.get("is_current_store"),
            option.get("is_selected_store"),
            option.get("selected"),
        )
        is True
    ]
    if selected:
        return selected
    return options if len(options) == 1 else []


def _store_id(option: dict[str, Any]) -> str:
    candidates = (
        option.get("store_id"),
        option.get("location_id"),
        _path(option, "store", "store_id"),
        _path(option, "store", "id"),
        _path(option, "location", "id"),
    )
    for value in candidates:
        text = _clean_text(value)
        if text.isdigit():
            return text
    return ""


def _normalize_summary_rows(value: Any) -> list[dict[str, Any]] | None:
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    if isinstance(value, dict):
        for key in ("products", "items", "summaries"):
            rows = value.get(key)
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)]
        rows = [row for row in value.values() if isinstance(row, dict)]
        return rows or None
    return None


def _normalize_object_rows(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    if isinstance(value, dict):
        return [value]
    return []


def _aggregate_availability(states: Iterable[bool]) -> bool | None:
    values = list(states)
    if any(values):
        return True
    if values:
        return False
    return None


def _safe_target_url(value: Any, *, expected_tcin: str) -> str:
    text = _clean_text(value)
    if text.startswith("/"):
        text = "https://www.target.com" + text
    try:
        parsed = urlparse(text)
    except ValueError:
        parsed = None
    if (
        parsed is not None
        and parsed.scheme == "https"
        and (parsed.hostname or "").lower() in TARGET_HOSTS
        and tcin_from_target_url(text) == expected_tcin
    ):
        return text.split("#", 1)[0]
    return canonical_target_product_url(expected_tcin)


def _is_official_target_url(value: Any) -> bool:
    try:
        parsed = urlparse(_clean_text(value))
    except ValueError:
        return False
    return parsed.scheme == "https" and (parsed.hostname or "").lower() in TARGET_HOSTS


def _safe_https_url(value: Any) -> bool:
    try:
        parsed = urlparse(_clean_text(value))
    except ValueError:
        return False
    return parsed.scheme == "https" and bool(parsed.hostname)


def _availability_bool(value: Any) -> bool | None:
    text = _clean_text(value).casefold().replace("_", " ")
    if not text:
        return None
    if any(token in text for token in ("out of stock", "unavailable", "not sold")):
        return False
    if any(token in text for token in ("in stock", "limited stock", "available")):
        return True
    return None


def _first_optional_bool(*values: Any) -> bool | None:
    for value in values:
        parsed = _optional_bool(value)
        if parsed is not None:
            return parsed
    return None


def _optional_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = _clean_text(value).casefold()
    if text in {"true", "yes", "1", "available"}:
        return True
    if text in {"false", "no", "0", "unavailable"}:
        return False
    return None


def _money(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, dict):
        for key in ("value", "amount", "price"):
            parsed = _money(value.get(key))
            if parsed is not None:
                return parsed
        return None
    text = _clean_text(value).replace("$", "").replace(",", "")
    try:
        parsed = float(text)
    except (TypeError, ValueError):
        return None
    return round(parsed, 2) if parsed > 0 else None


def _money_equal(left: float, right: float) -> bool:
    return int(round(float(left) * 100)) == int(round(float(right) * 100))


def _optional_money_equal(left: float | None, right: float | None) -> bool:
    if left is None or right is None:
        return left is right
    return _money_equal(left, right)


def _json_object(payload: Any) -> dict[str, Any]:
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as error:
            raise ValueError("Target RedSky response was not valid JSON") from error
    if not isinstance(payload, dict):
        raise ValueError("Target RedSky response must be a JSON object")
    return payload


def _path(value: Any, *parts: Any) -> Any:
    current = value
    for part in parts:
        if isinstance(part, int):
            if not isinstance(current, list) or part >= len(current):
                return None
            current = current[part]
        else:
            if not isinstance(current, dict):
                return None
            current = current.get(part)
    return current


def _local_name(value: str) -> str:
    return value.rsplit("}", 1)[-1].lower()


def _clean_key(value: Any) -> str:
    return "_".join(_clean_text(value).casefold().replace("-", " ").split())


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split())
