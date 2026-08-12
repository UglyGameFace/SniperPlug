from __future__ import annotations

import asyncio
import copy
import html as html_lib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Any

from sniperplug.providers.walmart import WalmartProvider, WalmartProviderError
from sniperplug.services.walmart_marketplace_comp import (
    selected_offer_delivery_attributes,
)


PAGE_RECOVERY_STATUS_KEY = "sniperplugPageRecoveryStatus"
PAGE_RECOVERY_DETAIL_KEY = "sniperplugPageRecoveryDetail"
PAGE_RECOVERY_SOURCE_KEY = "sniperplugPageRecoverySource"
PAGE_RECOVERY_PRICE_KEY = "sniperplugPageRecoveryPrice"
PAGE_RECOVERY_SHIPPING_COST_KEY = "sniperplugPageRecoveryShippingCost"
PAGE_RECOVERY_SHIPPING_STATUS_KEY = "sniperplugPageRecoveryShippingStatus"
PAGE_RECOVERY_VERSION = "walmart_product_page_buybox_v2"
PAGE_RECOVERY_REARM_TABLE = "walmart_page_recovery_migrations"
PAGE_RECOVERY_REARM_KEY = "recent_incomplete_identity_rearm_v2"
PAGE_RECOVERY_REARM_LIMIT = 250
PAGE_RECOVERY_REARM_WINDOW_HOURS = 72
PAGE_RECOVERY_MAX_WIRE_BYTES = 8 * 1024 * 1024
PAGE_RECOVERY_MAX_SCRIPT_BYTES = 6 * 1024 * 1024
PAGE_RECOVERY_MAX_STRUCTURED_BYTES = 7 * 1024 * 1024
PAGE_RECOVERY_MAX_JSON_NODES = 75_000
PAGE_RECOVERY_MAX_SCRIPTS = 32
PAGE_RECOVERY_CONCURRENCY = 2

_TRANSIENT_HTTP_CODES = {403, 408, 425, 429, 500, 502, 503, 504}
_CHALLENGE_MARKERS = (
    "captcha",
    "robot or human",
    "verify you are human",
    "verify your identity",
    "access denied",
    "temporarily blocked",
)
_WALMART_SELLER_IDS = {
    "0",
    "F55CDC31AB754BB68FE0B39041159D63",
    "WALMART",
}
_WALMART_SELLER_NAMES = {
    "walmart",
    "walmart.com",
    "walmart stores inc",
    "walmart stores, inc.",
}


class WalmartPageRecoveryTransientError(RuntimeError):
    """A product-page proof attempt failed in a way that should be retried."""

    def __init__(self, message: str, *, reason_code: str = "page_transient") -> None:
        super().__init__(message)
        self.reason_code = str(reason_code or "page_transient").strip() or "page_transient"


@dataclass(frozen=True)
class WalmartPageIdentityProof:
    item_id: str
    seller_name: str | None
    seller_id: str | None
    offer_id: str | None
    current_price: float | None
    marketplace: bool | None
    shipping_cost: float | None
    free_shipping: bool | None
    source: str

    @property
    def seller_key(self) -> str:
        seller_id = _clean_text(self.seller_id)
        if seller_id:
            return f"id:{seller_id.lower()}"
        seller_name = _clean_text(self.seller_name)
        return f"name:{seller_name.lower()}" if seller_name else ""

    @property
    def shipping_status(self) -> str | None:
        if self.shipping_cost is not None:
            return "free" if self.shipping_cost == 0 else "paid"
        if self.free_shipping is True:
            return "free"
        return None


@dataclass(frozen=True)
class WalmartPageRecoveryResult:
    proof: WalmartPageIdentityProof | None
    status: str
    detail: str = ""


class WalmartPageRecoveryProvider(WalmartProvider):
    """Official Walmart detail provider with a strict product-page identity fallback.

    The official item-detail API remains authoritative for the exact item and
    price. The product page is consulted only when the API cannot identify the
    selected seller. Page data may fill seller/offer identity only after exact
    item matching and, when the API has an item price, cent-for-cent price
    agreement. It never replaces a missing official API price or silently picks
    a cheaper seller.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._page_recovery_semaphore = asyncio.Semaphore(PAGE_RECOVERY_CONCURRENCY)

    async def _official_detail_payload(self, item_id: str) -> dict:
        return await super().fetch_product_detail_payload(item_id)

    async def _official_exact_search_payload(self, item_id: str) -> dict:
        return await super().fetch_exact_search_item_payload(item_id)

    async def _load_product_page_scripts(self, item_id: str) -> list[str]:
        timeout = _bounded_float_env(
            "WALMART_PAGE_RECOVERY_TIMEOUT_SECONDS",
            default=8.0,
            minimum=3.0,
            maximum=20.0,
        )
        return await asyncio.to_thread(
            _request_product_page_scripts,
            item_id,
            timeout,
        )

    async def fetch_product_detail_payload(self, item_id: str) -> dict:
        payload = await self._official_detail_payload(item_id)
        clean_item_id = _digits(item_id)
        if not clean_item_id or not _page_recovery_enabled():
            return payload
        if not needs_page_identity_recovery(payload):
            return payload

        # The Affiliate search route is already the healthy global-discovery
        # source. Refresh this exact item ID before relying on Walmart HTML. The
        # base provider accepts only an exact returned item ID, so a relevance
        # neighbor can never donate seller/offer identity.
        try:
            search_payload = await self._official_exact_search_payload(clean_item_id)
        except WalmartProviderError:
            search_payload = None
        if isinstance(search_payload, dict):
            payload = search_payload
            if not needs_page_identity_recovery(payload):
                return payload

        async with self._page_recovery_semaphore:
            try:
                scripts = await self._load_product_page_scripts(clean_item_id)
            except WalmartPageRecoveryTransientError as exc:
                raise WalmartProviderError(
                    "Walmart product-page identity recovery temporarily unavailable "
                    f"for {clean_item_id}: {exc}",
                    retry_kind=exc.reason_code,
                ) from exc

        recovery = recover_page_identity_from_scripts(
            scripts,
            item_id=clean_item_id,
            api_item_price=_api_selected_item_price(payload),
        )
        if recovery.proof is None:
            if recovery.status == "price_mismatch":
                raise WalmartProviderError(
                    "Walmart product-page identity recovery saw a price mismatch for "
                    f"{clean_item_id}: {recovery.detail or 'selected offer changed'}; "
                    "retrying instead of binding a seller",
                    retry_kind="page_price_mismatch",
                )
            return _mark_page_recovery(payload, recovery.status, recovery.detail)

        seller_conflict = _seller_identity_conflict(payload, recovery.proof)
        if seller_conflict:
            return _mark_page_recovery(payload, "seller_conflict", seller_conflict)

        conflict = _marketplace_conflict(payload, recovery.proof)
        if conflict:
            return _mark_page_recovery(payload, "marketplace_conflict", conflict)

        api_price = _api_selected_item_price(payload)
        page_price = recovery.proof.current_price
        if api_price is not None:
            if page_price is None:
                return _mark_page_recovery(
                    payload,
                    "page_price_missing",
                    "exact product page did not expose the selected-offer price needed to bind seller identity",
                )
            if _price_to_cents(api_price) != _price_to_cents(page_price):
                raise WalmartProviderError(
                    "Walmart product-page identity recovery saw an API/page selected-offer "
                    f"price mismatch for {clean_item_id}: api=${api_price:.2f} "
                    f"page=${page_price:.2f}; retrying instead of binding a seller",
                    retry_kind="page_price_mismatch",
                )

        return _merge_verified_page_identity(payload, recovery.proof)

    def _candidate_from_item(self, item: dict, request):  # type: ignore[override]
        candidate = super()._candidate_from_item(item, request=request)
        if candidate is None:
            return None
        status = _clean_text(item.get(PAGE_RECOVERY_STATUS_KEY))
        attrs = dict(candidate.variant_attributes or {})
        official_source = _clean_text(item.get("sniperplugOfficialExactSource"))
        if official_source:
            attrs["officialExactSource"] = official_source
            signal = "Walmart exact item refreshed through official Affiliate search"
            if signal not in candidate.signals:
                candidate.signals.append(signal)
        if not status:
            candidate.variant_attributes = attrs
            return candidate
        attrs["pageIdentityRecoveryStatus"] = status
        source = _clean_text(item.get(PAGE_RECOVERY_SOURCE_KEY))
        if source:
            attrs["pageIdentityRecoverySource"] = source
        detail = _clean_text(item.get(PAGE_RECOVERY_DETAIL_KEY))
        if detail:
            attrs["pageIdentityRecoveryDetail"] = detail
        price = _positive_float(item.get(PAGE_RECOVERY_PRICE_KEY))
        if price is not None:
            attrs["pageIdentityRecoveryPrice"] = f"{price:.2f}"
        shipping_cost = _nonnegative_float(item.get(PAGE_RECOVERY_SHIPPING_COST_KEY))
        if shipping_cost is not None:
            attrs["pageIdentityRecoveryShippingCost"] = f"{shipping_cost:.2f}"
        shipping_status = _clean_text(item.get(PAGE_RECOVERY_SHIPPING_STATUS_KEY))
        if shipping_status:
            attrs["pageIdentityRecoveryShippingStatus"] = shipping_status
        candidate.variant_attributes = attrs
        if status == "verified_identity":
            signal = "Walmart seller/offer buy-box proof recovered from exact product-page structured data"
        else:
            signal = f"Walmart product-page identity recovery: {status.replace('_', ' ')}"
        if signal not in candidate.signals:
            candidate.signals.append(signal)
        return candidate


def needs_page_identity_recovery(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    seller_name, seller_id = _raw_seller_identity(payload)
    attrs = selected_offer_delivery_attributes(payload)
    marketplace = _first_bool(
        attrs.get("selectedOfferMarketplace"),
        _raw_marketplace(payload),
    )
    if not seller_name and not seller_id:
        return marketplace is not False
    return (
        marketplace is True
        and str(attrs.get("selectedOfferShippingStatus") or "").strip().lower()
        == "unknown"
    )


def recover_page_identity_from_scripts(
    scripts: list[str],
    *,
    item_id: str,
    api_item_price: float | None,
) -> WalmartPageRecoveryResult:
    clean_item_id = _digits(item_id)
    if not clean_item_id:
        return WalmartPageRecoveryResult(None, "invalid_item_id")
    if not scripts:
        return WalmartPageRecoveryResult(None, "no_structured_data")

    matching_nodes: list[tuple[dict[str, Any], str]] = []
    decoded_count = 0
    for script_index, script in enumerate(scripts[:PAGE_RECOVERY_MAX_SCRIPTS]):
        decoded = _decode_structured_script(script)
        if decoded is None:
            continue
        decoded_count += 1
        for node, path in _walk_dict_nodes(decoded, prefix=f"script[{script_index}]"):
            if _node_matches_item(node, clean_item_id):
                matching_nodes.append((node, path))

    if decoded_count == 0:
        return WalmartPageRecoveryResult(None, "structured_json_invalid")
    if not matching_nodes:
        return WalmartPageRecoveryResult(None, "no_exact_item_match")

    candidates: list[tuple[int, WalmartPageIdentityProof]] = []
    mismatched_prices: list[float] = []
    for node, path in matching_nodes:
        for offer, offer_path, priority in _offer_nodes(node, path):
            proof = _proof_from_offer(
                product=node,
                offer=offer,
                item_id=clean_item_id,
                source=offer_path,
            )
            if proof is None or not proof.seller_key:
                continue
            if api_item_price is not None:
                if proof.current_price is None:
                    continue
                if _price_to_cents(api_item_price) != _price_to_cents(proof.current_price):
                    mismatched_prices.append(proof.current_price)
                    continue
            score = priority
            if proof.current_price is not None:
                score += 80
            if proof.seller_id:
                score += 30
            if proof.seller_name:
                score += 20
            if proof.offer_id:
                score += 10
            if proof.shipping_status:
                score += 8
            if _is_walmart_seller(proof.seller_name, proof.seller_id):
                score += 5
            candidates.append((score, proof))

    if not candidates:
        if api_item_price is not None and mismatched_prices:
            distinct = sorted({round(price, 2) for price in mismatched_prices})
            sample = ", ".join(f"${price:.2f}" for price in distinct[:3])
            return WalmartPageRecoveryResult(
                None,
                "price_mismatch",
                f"official API item price ${api_item_price:.2f} did not match page offer price(s) {sample}",
            )
        status = "no_price_aligned_seller" if api_item_price is not None else "seller_identity_missing"
        return WalmartPageRecoveryResult(None, status)

    candidates.sort(key=lambda entry: entry[0], reverse=True)
    top_score = candidates[0][0]
    top = [proof for score, proof in candidates if score == top_score]
    unique = {
        (
            proof.seller_key,
            _clean_text(proof.offer_id),
            _price_to_cents(proof.current_price) if proof.current_price is not None else None,
            proof.marketplace,
            _price_to_cents(proof.shipping_cost) if proof.shipping_cost is not None else None,
            proof.shipping_status,
        )
        for proof in top
    }
    if len(unique) != 1:
        return WalmartPageRecoveryResult(
            None,
            "ambiguous_selected_offer",
            "multiple equally strong product-page offers disagreed on selected seller/offer identity",
        )
    return WalmartPageRecoveryResult(top[0], "verified_identity")


async def rearm_recent_identity_rows_for_page_recovery(db: Any) -> int:
    """One-time bounded re-arm of recent seller/offer terminal rows.

    Historical terminal rows stay quarantined. Only the most recent high-priority
    rows are re-armed, avoiding the old multi-thousand-row drain storm while the
    new page fallback immediately applies to all future exact checks.
    """

    from sniperplug.services.walmart_exact_verification_queue import (
        QUEUE_TABLE,
        ensure_walmart_exact_verification_queue,
    )

    await ensure_walmart_exact_verification_queue(db)
    conn = db.require_conn()
    await conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {PAGE_RECOVERY_REARM_TABLE} (
            migration_key TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL,
            rearmed_rows INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    existing_cursor = await conn.execute(
        f"SELECT migration_key FROM {PAGE_RECOVERY_REARM_TABLE} WHERE migration_key = ? LIMIT 1",
        (PAGE_RECOVERY_REARM_KEY,),
    )
    if await existing_cursor.fetchone() is not None:
        return 0

    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(hours=PAGE_RECOVERY_REARM_WINDOW_HOURS)).isoformat()
    update_cursor = await conn.execute(
        f"""
        UPDATE {QUEUE_TABLE}
        SET status = 'pending',
            next_attempt_at = ?,
            lease_token = '',
            lease_until = NULL,
            last_error = CASE
                WHEN last_error = '' THEN ?
                ELSE last_error || ' | ' || ?
            END
        WHERE item_id IN (
            SELECT item_id
            FROM {QUEUE_TABLE}
            WHERE status = 'incomplete_identity'
              AND last_seen_at >= ?
              AND (
                  lower(last_error) LIKE '%seller%'
                  OR lower(last_error) LIKE '%offer%'
                  OR lower(last_error) LIKE '%incomplete exact offer identity%'
              )
              AND (lease_until IS NULL OR lease_until = '' OR lease_until < ?)
            ORDER BY priority_score DESC, last_seen_at DESC
            LIMIT ?
        )
        """,
        (
            now.isoformat(),
            PAGE_RECOVERY_VERSION,
            PAGE_RECOVERY_VERSION,
            cutoff,
            now.isoformat(),
            PAGE_RECOVERY_REARM_LIMIT,
        ),
    )
    rearmed = await _affected_rows(conn, update_cursor)
    await conn.execute(
        f"""
        INSERT INTO {PAGE_RECOVERY_REARM_TABLE} (
            migration_key, applied_at, rearmed_rows
        ) VALUES (?, ?, ?)
        """,
        (PAGE_RECOVERY_REARM_KEY, now.isoformat(), max(0, rearmed)),
    )
    await conn.commit()
    return max(0, rearmed)


def _request_product_page_scripts(item_id: str, timeout: float) -> list[str]:
    clean_item_id = _digits(item_id)
    if not clean_item_id:
        return []
    url = f"https://www.walmart.com/ip/{clean_item_id}"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
            "User-Agent": (
                "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0 Mobile Safari/537.36 SniperPlug/1.0"
            ),
        },
        method="GET",
    )
    parser = _StructuredScriptParser()
    sample_parts: list[str] = []
    total_wire = 0
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            final_url = str(getattr(response, "geturl", lambda: url)() or url)
            final_host = (urllib.parse.urlparse(final_url).hostname or "").lower().rstrip(".")
            if final_host != "walmart.com" and not final_host.endswith(".walmart.com"):
                raise WalmartPageRecoveryTransientError(
                    "product page redirected away from walmart.com",
                    reason_code="page_redirect",
                )
            while True:
                chunk = response.read(64 * 1024)
                if not chunk:
                    break
                total_wire += len(chunk)
                if total_wire > PAGE_RECOVERY_MAX_WIRE_BYTES:
                    raise WalmartPageRecoveryTransientError(
                        "product page exceeded bounded recovery response size",
                        reason_code="page_response_too_large",
                    )
                text = chunk.decode("utf-8", errors="replace")
                if sum(len(part) for part in sample_parts) < 512 * 1024:
                    sample_parts.append(text)
                parser.feed(text)
            parser.close()
    except urllib.error.HTTPError as exc:
        if exc.code in _TRANSIENT_HTTP_CODES:
            raise WalmartPageRecoveryTransientError(
                f"HTTP {exc.code}",
                reason_code=f"page_http_{int(exc.code)}",
            ) from exc
        if exc.code == 404:
            return []
        raise WalmartPageRecoveryTransientError(
            f"HTTP {exc.code}",
            reason_code=f"page_http_{int(exc.code)}",
        ) from exc
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", None)
        retry_kind = "page_timeout" if isinstance(reason, TimeoutError) else "page_network"
        raise WalmartPageRecoveryTransientError(
            f"network error: {reason}",
            reason_code=retry_kind,
        ) from exc
    except TimeoutError as exc:
        raise WalmartPageRecoveryTransientError(
            "request timed out",
            reason_code="page_timeout",
        ) from exc

    sample = " ".join(sample_parts).lower()
    if not parser.scripts and any(marker in sample for marker in _CHALLENGE_MARKERS):
        raise WalmartPageRecoveryTransientError(
            "Walmart returned a challenge page without structured product data",
            reason_code="page_challenge",
        )
    return parser.scripts


class _StructuredScriptParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.scripts: list[str] = []
        self._capture = False
        self._parts: list[str] = []
        self._current_bytes = 0
        self._total_bytes = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "script" or len(self.scripts) >= PAGE_RECOVERY_MAX_SCRIPTS:
            return
        values = {str(key).lower(): str(value or "") for key, value in attrs}
        script_type = values.get("type", "").lower()
        script_id = values.get("id", "").lower()
        self._capture = (
            "json" in script_type
            or script_id == "__next_data__"
            or "json" in script_id
            or "schema" in script_id
        )
        if self._capture:
            self._parts = []
            self._current_bytes = 0

    def handle_data(self, data: str) -> None:
        if not self._capture:
            return
        encoded_len = len(data.encode("utf-8", errors="ignore"))
        if self._current_bytes + encoded_len > PAGE_RECOVERY_MAX_SCRIPT_BYTES:
            self._capture = False
            self._parts = []
            self._current_bytes = 0
            return
        if self._total_bytes + encoded_len > PAGE_RECOVERY_MAX_STRUCTURED_BYTES:
            self._capture = False
            self._parts = []
            return
        self._parts.append(data)
        self._current_bytes += encoded_len
        self._total_bytes += encoded_len

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "script" or not self._capture:
            return
        payload = "".join(self._parts).strip()
        if payload:
            self.scripts.append(payload)
        self._capture = False
        self._parts = []
        self._current_bytes = 0


def _decode_structured_script(script: str) -> Any | None:
    text = str(script or "").strip()
    if not text:
        return None
    for candidate in (text, html_lib.unescape(text)):
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
    return None


def _walk_dict_nodes(value: Any, *, prefix: str):
    stack: list[tuple[Any, str]] = [(value, prefix)]
    seen = 0
    while stack and seen < PAGE_RECOVERY_MAX_JSON_NODES:
        current, path = stack.pop()
        seen += 1
        if isinstance(current, dict):
            yield current, path
            for key, child in reversed(list(current.items())):
                if isinstance(child, (dict, list)):
                    stack.append((child, f"{path}.{key}"))
        elif isinstance(current, list):
            for index in range(len(current) - 1, -1, -1):
                child = current[index]
                if isinstance(child, (dict, list)):
                    stack.append((child, f"{path}[{index}]"))


def _node_matches_item(node: dict[str, Any], item_id: str) -> bool:
    for key in (
        "itemId",
        "usItemId",
        "productId",
        "productID",
        "sku",
        "item_id",
        "us_item_id",
        "walmartItemId",
        "catalogItemId",
    ):
        if _digits(node.get(key)) == item_id:
            return True
    for key in ("url", "canonicalUrl", "canonicalURL", "productUrl", "productURL"):
        value = str(node.get(key) or "")
        if re.search(rf"/ip/(?:[^/?#]+/)?{re.escape(item_id)}(?:[/?#]|$)", value):
            return True
    return False


def _offer_nodes(product: dict[str, Any], path: str):
    yielded: set[int] = set()
    priorities = (
        ("selectedOffer", 120),
        ("buyBoxOffer", 118),
        ("buyBox", 116),
        ("buybox", 116),
        ("buyBoxWinner", 114),
        ("primaryOffer", 110),
        ("offer", 100),
        ("offerInfo", 96),
        ("offers", 90),
    )
    for key, priority in priorities:
        value = product.get(key)
        if isinstance(value, dict):
            yielded.add(id(value))
            yield value, f"{path}.{key}", priority
        elif isinstance(value, list):
            for index, child in enumerate(value[:20]):
                if isinstance(child, dict):
                    yielded.add(id(child))
                    yield child, f"{path}.{key}[{index}]", priority - 5
    if _raw_seller_identity_shallow(product) != (None, None):
        yield product, path, 70

    # Some Next.js payloads put the matched product identity above the buy-box
    # data. Search only a bounded depth beneath that exact product node and keep
    # the score below explicit selected/buy-box nodes.
    stack: list[tuple[Any, str, int]] = [(product, path, 0)]
    scanned = 0
    while stack and scanned < 2_500:
        current, current_path, depth = stack.pop()
        scanned += 1
        if depth > 12:
            continue
        if isinstance(current, dict):
            if (
                id(current) not in yielded
                and _raw_seller_identity_shallow(current) != (None, None)
            ):
                price = _node_price(current)
                if price is not None:
                    yielded.add(id(current))
                    yield current, current_path, 55 - depth
            for key, child in current.items():
                if isinstance(child, (dict, list)):
                    stack.append((child, f"{current_path}.{key}", depth + 1))
        elif isinstance(current, list):
            for index, child in enumerate(current[:40]):
                if isinstance(child, (dict, list)):
                    stack.append((child, f"{current_path}[{index}]", depth + 1))


def _proof_from_offer(
    *,
    product: dict[str, Any],
    offer: dict[str, Any],
    item_id: str,
    source: str,
) -> WalmartPageIdentityProof | None:
    seller_name, seller_id = _raw_seller_identity(offer)
    if not seller_name and not seller_id:
        seller_name, seller_id = _raw_seller_identity_shallow(product)
    if not seller_name and not seller_id:
        return None

    offer_id = _first_text(
        offer.get("offerId"),
        offer.get("offerID"),
        offer.get("id"),
        product.get("selectedOfferId"),
        product.get("buyBoxOfferId"),
        product.get("offerId"),
    )
    marketplace = _first_bool(
        offer.get("isMarketPlaceItem"),
        offer.get("isMarketplaceItem"),
        offer.get("marketplace"),
        product.get("isMarketPlaceItem"),
        product.get("isMarketplaceItem"),
        product.get("marketplace"),
    )
    if marketplace is None:
        if _is_walmart_seller(seller_name, seller_id):
            marketplace = False
        elif seller_name or seller_id:
            marketplace = True

    shipping_cost, free_shipping = _selected_shipping_evidence(offer)

    return WalmartPageIdentityProof(
        item_id=item_id,
        seller_name=seller_name,
        seller_id=seller_id,
        offer_id=offer_id,
        current_price=_node_price(offer) or _node_price(product),
        marketplace=marketplace,
        shipping_cost=shipping_cost,
        free_shipping=free_shipping,
        source=source,
    )


def _api_selected_item_price(payload: dict[str, Any]) -> float | None:
    attrs = selected_offer_delivery_attributes(payload)
    selected = _positive_float(attrs.get("selectedOfferItemPrice"))
    if selected is not None:
        return selected
    for key in ("salePrice", "currentPrice", "price"):
        parsed = _money(payload.get(key))
        if parsed is not None:
            return parsed
    price_info = payload.get("priceInfo")
    if isinstance(price_info, dict):
        parsed = _money(price_info.get("currentPrice"))
        if parsed is not None:
            return parsed
    return None


def _node_price(node: dict[str, Any]) -> float | None:
    for key in ("salePrice", "currentPrice", "price"):
        parsed = _money(node.get(key))
        if parsed is not None:
            return parsed
    price_info = node.get("priceInfo")
    if isinstance(price_info, dict):
        for key in ("currentPrice", "price"):
            parsed = _money(price_info.get(key))
            if parsed is not None:
                return parsed
    return None


def _money(value: Any) -> float | None:
    if isinstance(value, dict):
        for key in ("amount", "price", "value", "currencyAmount", "currencyValue"):
            parsed = _positive_float(value.get(key))
            if parsed is not None:
                return parsed
        return None
    return _positive_float(value)


def _money_allow_zero(value: Any) -> float | None:
    if isinstance(value, dict):
        for key in ("amount", "price", "value", "currencyAmount", "currencyValue"):
            parsed = _nonnegative_float(value.get(key))
            if parsed is not None:
                return parsed
        return None
    return _nonnegative_float(value)


def _selected_shipping_evidence(node: dict[str, Any]) -> tuple[float | None, bool | None]:
    """Extract explicit shipping evidence from this selected-offer subtree only."""

    costs: list[float] = []
    free_flags: list[bool] = []
    stack: list[tuple[Any, str, int]] = [(node, "offer", 0)]
    scanned = 0
    while stack and scanned < 240:
        current, path, depth = stack.pop()
        scanned += 1
        if depth > 6:
            continue
        if isinstance(current, dict):
            for key, value in current.items():
                normalized = str(key).strip().lower()
                child_path = f"{path}.{normalized}"
                if normalized in {
                    "shippingprice",
                    "shippingcost",
                    "shippingfee",
                    "shipprice",
                    "shipcost",
                }:
                    parsed = _money_allow_zero(value)
                    if parsed is not None:
                        costs.append(parsed)
                elif normalized in {
                    "freeshipping",
                    "isfreeshipping",
                    "shippingisfree",
                }:
                    flag = _first_bool(value)
                    if flag is not None:
                        free_flags.append(flag)
                elif normalized in {"price", "cost", "fee"} and "shipping" in path:
                    parsed = _money_allow_zero(value)
                    if parsed is not None:
                        costs.append(parsed)
                if isinstance(value, (dict, list)):
                    stack.append((value, child_path, depth + 1))
        elif isinstance(current, list):
            for index, child in enumerate(current[:30]):
                if isinstance(child, (dict, list)):
                    stack.append((child, f"{path}[{index}]", depth + 1))

    if any(flag is True for flag in free_flags):
        costs.append(0.0)
    if costs:
        cost = round(min(costs), 2)
        return cost, cost == 0
    if free_flags and all(flag is False for flag in free_flags):
        return None, False
    return None, None


def _raw_seller_identity(value: dict[str, Any]) -> tuple[str | None, str | None]:
    seller_name = _first_text(
        value.get("sellerName"),
        value.get("sellerDisplayName"),
    )
    seller_id = _first_text(value.get("sellerId"), value.get("sellerID"))
    for key in ("seller", "sellerInfo", "merchant", "merchantInfo"):
        child = value.get(key)
        if isinstance(child, dict):
            seller_name = seller_name or _first_text(
                child.get("name"),
                child.get("sellerName"),
                child.get("sellerDisplayName"),
                child.get("displayName"),
                child.get("merchantName"),
            )
            seller_id = seller_id or _first_text(
                child.get("id"),
                child.get("sellerId"),
                child.get("sellerID"),
                child.get("merchantId"),
            )
        elif isinstance(child, str) and not seller_name:
            seller_name = _clean_text(child)
    for key in ("selectedOffer", "buyBoxOffer", "primaryOffer", "offer"):
        child = value.get(key)
        if isinstance(child, dict):
            nested_name, nested_id = _raw_seller_identity_shallow(child)
            seller_name = seller_name or nested_name
            seller_id = seller_id or nested_id
            if seller_name or seller_id:
                break
    return seller_name, seller_id


def _raw_seller_identity_shallow(value: dict[str, Any]) -> tuple[str | None, str | None]:
    seller_name = _first_text(value.get("sellerName"), value.get("sellerDisplayName"))
    seller_id = _first_text(value.get("sellerId"), value.get("sellerID"))
    for key in ("seller", "sellerInfo", "merchant", "merchantInfo"):
        seller = value.get(key)
        if isinstance(seller, dict):
            seller_name = seller_name or _first_text(
                seller.get("name"),
                seller.get("sellerName"),
                seller.get("sellerDisplayName"),
                seller.get("displayName"),
                seller.get("merchantName"),
            )
            seller_id = seller_id or _first_text(
                seller.get("id"),
                seller.get("sellerId"),
                seller.get("sellerID"),
                seller.get("merchantId"),
            )
        elif isinstance(seller, str):
            seller_name = seller_name or _clean_text(seller)
    return seller_name, seller_id


def _raw_marketplace(payload: dict[str, Any]) -> bool | None:
    values: list[Any] = [
        payload.get("isMarketPlaceItem"),
        payload.get("isMarketplaceItem"),
        payload.get("marketplace"),
    ]
    for key in ("selectedOffer", "buyBoxOffer", "primaryOffer", "offer"):
        child = payload.get(key)
        if isinstance(child, dict):
            values.extend(
                (
                    child.get("isMarketPlaceItem"),
                    child.get("isMarketplaceItem"),
                    child.get("marketplace"),
                )
            )
    return _first_bool(*values)


def _seller_identity_conflict(
    payload: dict[str, Any],
    proof: WalmartPageIdentityProof,
) -> str:
    api_name, api_id = _raw_seller_identity(payload)
    page_name = _clean_text(proof.seller_name)
    page_id = _clean_text(proof.seller_id)
    if api_id and page_id and api_id.strip().lower() != page_id.strip().lower():
        return "official exact-detail seller ID disagreed with the price-aligned product-page buy box"
    if (
        api_name
        and page_name
        and " ".join(api_name.lower().split()) != " ".join(page_name.lower().split())
    ):
        return "official exact-detail seller name disagreed with the price-aligned product-page buy box"
    return ""


def _marketplace_conflict(
    payload: dict[str, Any],
    proof: WalmartPageIdentityProof,
) -> str:
    api_marketplace = _raw_marketplace(payload)
    if api_marketplace is None or proof.marketplace is None:
        return ""
    if api_marketplace == proof.marketplace:
        return ""
    return (
        "official exact-detail marketplace classification disagreed with the "
        "matching product-page selected seller"
    )


def _merge_verified_page_identity(
    payload: dict[str, Any],
    proof: WalmartPageIdentityProof,
) -> dict[str, Any]:
    merged = copy.deepcopy(payload)
    if proof.seller_name:
        merged["sellerName"] = proof.seller_name
    if proof.seller_id:
        merged["sellerId"] = proof.seller_id
    if proof.offer_id and not _first_text(
        merged.get("offerId"),
        merged.get("offerID"),
        merged.get("selectedOfferId"),
        merged.get("buyBoxOfferId"),
    ):
        merged["offerId"] = proof.offer_id
    if proof.marketplace is not None:
        merged["marketplace"] = proof.marketplace
        merged["isMarketPlaceItem"] = proof.marketplace
        merged["isMarketplaceItem"] = proof.marketplace
    if proof.shipping_cost is not None:
        merged.setdefault("shippingCost", proof.shipping_cost)
    elif proof.free_shipping is True:
        merged.setdefault("freeShipping", True)

    for key in ("selectedOffer", "buyBoxOffer", "primaryOffer", "offer"):
        child = merged.get(key)
        if not isinstance(child, dict):
            continue
        child_price = _node_price(child)
        if (
            proof.current_price is not None
            and child_price is not None
            and _price_to_cents(child_price) != _price_to_cents(proof.current_price)
        ):
            continue
        if proof.seller_name:
            child.setdefault("sellerName", proof.seller_name)
        if proof.seller_id:
            child.setdefault("sellerId", proof.seller_id)
        if proof.offer_id:
            child.setdefault("offerId", proof.offer_id)
        if proof.marketplace is not None:
            child.setdefault("isMarketPlaceItem", proof.marketplace)
        if proof.shipping_cost is not None:
            child.setdefault("shippingCost", proof.shipping_cost)
        elif proof.free_shipping is True:
            child.setdefault("freeShipping", True)
        break

    merged[PAGE_RECOVERY_STATUS_KEY] = "verified_identity"
    merged[PAGE_RECOVERY_SOURCE_KEY] = proof.source
    merged[PAGE_RECOVERY_PRICE_KEY] = (
        f"{proof.current_price:.2f}" if proof.current_price is not None else ""
    )
    merged[PAGE_RECOVERY_SHIPPING_COST_KEY] = (
        f"{proof.shipping_cost:.2f}" if proof.shipping_cost is not None else ""
    )
    merged[PAGE_RECOVERY_SHIPPING_STATUS_KEY] = proof.shipping_status or "unknown"
    return merged


def _mark_page_recovery(payload: dict[str, Any], status: str, detail: str = "") -> dict[str, Any]:
    marked = copy.deepcopy(payload)
    marked[PAGE_RECOVERY_STATUS_KEY] = status
    if detail:
        marked[PAGE_RECOVERY_DETAIL_KEY] = _compact(detail, 300)
    return marked


def _is_walmart_seller(name: Any, seller_id: Any) -> bool:
    normalized_name = " ".join(str(name or "").strip().lower().split())
    normalized_id = str(seller_id or "").strip().upper()
    return normalized_name in _WALMART_SELLER_NAMES or normalized_id in _WALMART_SELLER_IDS


def _first_text(*values: Any) -> str | None:
    for value in values:
        text = _clean_text(value)
        if text:
            return text
    return None


def _clean_text(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, dict):
        return None
    text = " ".join(str(value).split()).strip()
    if text.lower() in {"", "none", "null", "unknown"}:
        return None
    return text


def _first_bool(*values: Any) -> bool | None:
    for value in values:
        if isinstance(value, bool):
            return value
        text = str(value or "").strip().lower()
        if text in {"true", "yes", "1", "y"}:
            return True
        if text in {"false", "no", "0", "n"}:
            return False
    return None


def _positive_float(value: Any) -> float | None:
    parsed = _nonnegative_float(value)
    return parsed if parsed is not None and parsed > 0 else None


def _nonnegative_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        if isinstance(value, str):
            text = value.replace("$", "").replace(",", "").strip()
            match = re.search(r"-?\d+(?:\.\d+)?", text)
            if not match:
                return None
            value = match.group(0)
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return round(parsed, 2) if parsed >= 0 else None


def _digits(value: Any) -> str:
    text = _clean_text(value) or ""
    return text if text.isdigit() else ""


def _price_to_cents(value: float | None) -> int | None:
    if value is None:
        return None
    return int(round(float(value) * 100))


def _page_recovery_enabled() -> bool:
    raw = os.getenv("WALMART_PAGE_RECOVERY_ENABLED")
    if raw is None:
        return True
    return str(raw).strip().lower() not in {"0", "false", "no", "off"}


def _bounded_float_env(name: str, *, default: float, minimum: float, maximum: float) -> float:
    raw = os.getenv(name)
    try:
        parsed = float(raw) if raw not in (None, "") else float(default)
    except (TypeError, ValueError):
        parsed = float(default)
    return max(float(minimum), min(float(maximum), parsed))


async def _affected_rows(conn: Any, cursor: Any) -> int:
    try:
        value = int(getattr(cursor, "rowcount", -1))
    except (TypeError, ValueError, OverflowError):
        value = -1
    if value >= 0:
        return value

    changes_cursor = await conn.execute("SELECT changes()")
    row = await changes_cursor.fetchone()
    if row is None:
        return 0
    try:
        return max(0, int(row[0] or 0))
    except (TypeError, ValueError, KeyError, IndexError):
        try:
            return max(0, int(row["changes()"] or 0))
        except Exception:
            return 0


def _compact(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: max(1, limit - 1)].rstrip() + "…"
