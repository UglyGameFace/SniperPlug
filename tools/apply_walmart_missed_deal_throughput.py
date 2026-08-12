from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"expected source block not found in {path}: {old[:120]!r}")
    if text.count(old) != 1:
        raise RuntimeError(f"expected exactly one source block in {path}, found {text.count(old)}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


# 1) Reuse Walmart's healthy Affiliate search endpoint as a strict exact-item
# fallback whenever the item-detail endpoint is unavailable.
replace_once(
    "sniperplug/providers/walmart.py",
    '''        url = f"{self.search_url}?{urllib.parse.urlencode(params)}"\n        return self._request_json(url)\n\n\n    async def fetch_product_detail_payload(self, item_id: str) -> dict:\n        return await asyncio.to_thread(self._product_detail_payload, item_id)\n''',
    '''        url = f"{self.search_url}?{urllib.parse.urlencode(params)}"\n        return self._request_json(url)\n\n    async def fetch_exact_search_item_payload(self, item_id: str) -> dict:\n        """Refresh one exact Walmart item through the Affiliate search API.\n\n        Catalog discovery already uses this endpoint successfully in production.\n        An exact-ID search is accepted only when one returned row's item ID is\n        exactly the requested ID; relevance ordering is never trusted as proof.\n        """\n\n        return await asyncio.to_thread(self._exact_search_item_payload, item_id)\n\n    def _exact_search_item_payload(self, item_id: str) -> dict:\n        clean_id = str(item_id or "").strip()\n        if not clean_id:\n            raise WalmartProviderError(\n                "Walmart exact Affiliate search skipped: missing item id.",\n                retry_kind="api_exact_search_missing_id",\n            )\n        request = ProviderScanRequest(\n            source_key="walmart_exact_search",\n            query=clean_id,\n            max_results=25,\n            page=1,\n            metadata={"exact_item_lookup": "yes"},\n        )\n        payload = self._search(query=clean_id, request=request, page_size=25)\n        item = _exact_item_from_search_payload(payload, clean_id)\n        if item is None:\n            raise WalmartProviderError(\n                f"Walmart Affiliate search returned no exact item match for {clean_id}.",\n                retry_kind="api_exact_search_no_match",\n            )\n        exact = dict(item)\n        exact["sniperplugOfficialExactSource"] = "affiliate_search_exact_item"\n        return exact\n\n    async def fetch_product_detail_payload(self, item_id: str) -> dict:\n        return await asyncio.to_thread(self._product_detail_payload, item_id)\n''',
)

replace_once(
    "sniperplug/providers/walmart.py",
    '''        retry_kind = str(getattr(last_error, "retry_kind", "") or "").strip()\n        raise WalmartProviderError(\n            f"Walmart detail promo endpoint unavailable for {clean_id}: {last_error}",\n            retry_kind=retry_kind or "api_detail",\n        )\n''',
    '''        try:\n            return self._exact_search_item_payload(clean_id)\n        except WalmartProviderError as search_error:\n            detail_kind = str(getattr(last_error, "retry_kind", "") or "").strip()\n            search_kind = str(getattr(search_error, "retry_kind", "") or "").strip()\n            raise WalmartProviderError(\n                "Walmart exact item refresh unavailable for "\n                f"{clean_id}: detail={last_error}; exact_search={search_error}",\n                retry_kind=search_kind or detail_kind or "api_detail",\n            ) from search_error\n''',
)

replace_once(
    "sniperplug/providers/walmart.py",
    '''def _first_detail_item(payload: Any) -> dict | None:\n    if isinstance(payload, dict):\n        if payload.get("itemId") or payload.get("usItemId") or payload.get("name"):\n            return payload\n\n        for key in ("item", "product", "data"):\n            child = payload.get(key)\n            if isinstance(child, dict):\n                found = _first_detail_item(child)\n                if found:\n                    return found\n\n        for key in ("items", "products", "itemResponse", "results"):\n            child = payload.get(key)\n            if isinstance(child, list):\n                for item in child:\n                    found = _first_detail_item(item)\n                    if found:\n                        return found\n\n    return None\n''',
    '''def _first_detail_item(payload: Any) -> dict | None:\n    if isinstance(payload, dict):\n        if payload.get("itemId") or payload.get("usItemId") or payload.get("name"):\n            return payload\n\n        for key in ("item", "product", "data"):\n            child = payload.get(key)\n            if isinstance(child, dict):\n                found = _first_detail_item(child)\n                if found:\n                    return found\n\n        for key in ("items", "products", "itemResponse", "results"):\n            child = payload.get(key)\n            if isinstance(child, list):\n                for item in child:\n                    found = _first_detail_item(item)\n                    if found:\n                        return found\n\n    return None\n\n\ndef _exact_item_from_search_payload(payload: Any, item_id: str) -> dict | None:\n    """Return only a search row whose Walmart item ID exactly matches."""\n\n    clean_id = str(item_id or "").strip()\n    if not clean_id or not isinstance(payload, dict):\n        return None\n    items = payload.get("items")\n    if not isinstance(items, list):\n        return None\n    for item in items:\n        if not isinstance(item, dict):\n            continue\n        returned_id = item.get("itemId") or item.get("usItemId")\n        if str(returned_id or "").strip() == clean_id:\n            return item\n    return None\n''',
)

# 2) If detail succeeds but seller identity is incomplete, refresh the exact ID
# through Affiliate search before paying the reliability cost of Walmart HTML.
replace_once(
    "sniperplug/providers/walmart_page_recovery.py",
    '''    async def _official_detail_payload(self, item_id: str) -> dict:\n        return await super().fetch_product_detail_payload(item_id)\n\n    async def _load_product_page_scripts(self, item_id: str) -> list[str]:\n''',
    '''    async def _official_detail_payload(self, item_id: str) -> dict:\n        return await super().fetch_product_detail_payload(item_id)\n\n    async def _official_exact_search_payload(self, item_id: str) -> dict:\n        return await super().fetch_exact_search_item_payload(item_id)\n\n    async def _load_product_page_scripts(self, item_id: str) -> list[str]:\n''',
)

replace_once(
    "sniperplug/providers/walmart_page_recovery.py",
    '''        if not needs_page_identity_recovery(payload):\n            return payload\n\n        async with self._page_recovery_semaphore:\n''',
    '''        if not needs_page_identity_recovery(payload):\n            return payload\n\n        # The Affiliate search route is already the healthy global-discovery\n        # source. Refresh this exact item ID before relying on Walmart HTML. The\n        # base provider accepts only an exact returned item ID, so a relevance\n        # neighbor can never donate seller/offer identity.\n        try:\n            search_payload = await self._official_exact_search_payload(clean_item_id)\n        except WalmartProviderError:\n            search_payload = None\n        if isinstance(search_payload, dict):\n            payload = search_payload\n            if not needs_page_identity_recovery(payload):\n                return payload\n\n        async with self._page_recovery_semaphore:\n''',
)

replace_once(
    "sniperplug/providers/walmart_page_recovery.py",
    '''        status = _clean_text(item.get(PAGE_RECOVERY_STATUS_KEY))\n        if not status:\n            return candidate\n        attrs = dict(candidate.variant_attributes or {})\n        attrs["pageIdentityRecoveryStatus"] = status\n''',
    '''        status = _clean_text(item.get(PAGE_RECOVERY_STATUS_KEY))\n        attrs = dict(candidate.variant_attributes or {})\n        official_source = _clean_text(item.get("sniperplugOfficialExactSource"))\n        if official_source:\n            attrs["officialExactSource"] = official_source\n            signal = "Walmart exact item refreshed through official Affiliate search"\n            if signal not in candidate.signals:\n                candidate.signals.append(signal)\n        if not status:\n            candidate.variant_attributes = attrs\n            return candidate\n        attrs["pageIdentityRecoveryStatus"] = status\n''',
)

# 3) Surface concrete retry_kind counts, not just broad page/API buckets.
replace_once(
    "sniperplug/services/walmart_exact_queue_runtime.py",
    '''    retry_candidate: int = 0\n    retry_other: int = 0\n    pending_total: int = 0\n''',
    '''    retry_candidate: int = 0\n    retry_other: int = 0\n    retry_reasons: tuple[tuple[str, int], ...] = ()\n    pending_total: int = 0\n''',
)

replace_once(
    "sniperplug/services/walmart_exact_queue_runtime.py",
    '''        timing = (\n            f"claim **{self.claim_seconds:.2f}s** • "\n            f"fetch **{self.fetch_seconds:.2f}s** • "\n            f"store **{self.store_seconds:.2f}s**"\n        )\n        return (\n''',
    '''        timing = (\n            f"claim **{self.claim_seconds:.2f}s** • "\n            f"fetch **{self.fetch_seconds:.2f}s** • "\n            f"store **{self.store_seconds:.2f}s**"\n        )\n        retry_reasons = ""\n        if self.retry_reasons:\n            retry_reasons = " • retry kinds " + ", ".join(\n                f"**{name}={count}**" for name, count in self.retry_reasons[:6]\n            )\n        return (\n''',
)

replace_once(
    "sniperplug/services/walmart_exact_queue_runtime.py",
    '''            f"identity unavailable / safely blocked **{self.identity_blocked}** "\n            f"({identity_detail}) • transient failures **{self.failed}** ({retry_detail}) • "\n            f"terminal rows quarantined **{self.terminal_quarantined}** • "\n''',
    '''            f"identity unavailable / safely blocked **{self.identity_blocked}** "\n            f"({identity_detail}) • transient failures **{self.failed}** ({retry_detail})"\n            f"{retry_reasons} • terminal rows quarantined **{self.terminal_quarantined}** • "\n''',
)

replace_once(
    "sniperplug/services/walmart_exact_queue_runtime.py",
    '''def _retry_error_bucket(*, status: str, error: str) -> str:\n''',
    '''def _retry_kind_from_error(error: str) -> str:\n    for token in str(error or "").split():\n        if not token.startswith("retry_kind="):\n            continue\n        value = token.split("=", 1)[1].strip().strip(",;:()[]{}")\n        if value:\n            return value[:80]\n    return "unclassified"\n\n\ndef _retry_error_bucket(*, status: str, error: str) -> str:\n''',
)

replace_once(
    "sniperplug/services/walmart_exact_queue_bulk_runtime.py",
    '''    _identity_error_bucket,\n    _retry_error_bucket,\n)\n''',
    '''    _identity_error_bucket,\n    _retry_error_bucket,\n    _retry_kind_from_error,\n)\n''',
)

replace_once(
    "sniperplug/services/walmart_exact_queue_bulk_runtime.py",
    '''    persistence_outcomes: list[ExactQueuePersistenceOutcome] = []\n    for claim, outcome in outcomes:\n''',
    '''    persistence_outcomes: list[ExactQueuePersistenceOutcome] = []\n    retry_reason_counts: dict[str, int] = {}\n    for claim, outcome in outcomes:\n''',
)

replace_once(
    "sniperplug/services/walmart_exact_queue_bulk_runtime.py",
    '''            else:\n                counts["failed"] += 1\n                counts[_retry_error_bucket(status=status, error=error)] += 1\n            continue\n''',
    '''            else:\n                counts["failed"] += 1\n                counts[_retry_error_bucket(status=status, error=error)] += 1\n                retry_kind = _retry_kind_from_error(error)\n                retry_reason_counts[retry_kind] = retry_reason_counts.get(retry_kind, 0) + 1\n            continue\n''',
)

replace_once(
    "sniperplug/services/walmart_exact_queue_bulk_runtime.py",
    '''        store_seconds=store_seconds,\n        **counts,\n    )\n''',
    '''        store_seconds=store_seconds,\n        retry_reasons=tuple(\n            sorted(\n                retry_reason_counts.items(),\n                key=lambda item: (-item[1], item[0]),\n            )[:6]\n        ),\n        **counts,\n    )\n''',
)

# 4) Strict exact cards may scrub stale presentation text that says watchlist.
# Non-exact cards never enter this normalizer and remain blocked by the public gate.
replace_once(
    "sniperplug/services/walmart_exact_public_lane.py",
    '''    "review only",\n    "scout-only",\n''',
    '''    "review only",\n    "review/watchlist",\n    "review / watchlist",\n    "watchlist",\n    "scout-only",\n''',
)

# 5) Focused regressions for both production failure shapes.
replace_once(
    "tests/test_walmart_page_identity_recovery.py",
    '''    async def _load_product_page_scripts(self, item_id: str) -> list[str]:\n        self.page_calls += 1\n        return list(self.scripts)\n''',
    '''    async def _official_exact_search_payload(self, item_id: str) -> dict:\n        raise WalmartProviderError(\n            f"fixture exact search unavailable for {item_id}",\n            retry_kind="api_fixture_unavailable",\n        )\n\n    async def _load_product_page_scripts(self, item_id: str) -> list[str]:\n        self.page_calls += 1\n        return list(self.scripts)\n''',
)

append_page_tests = '''\n\nclass ExactAffiliateRecoveryProvider(FixturePageProvider):\n    def __init__(self, detail_payload: dict, exact_search_payload: dict, scripts: list[str] | None = None):\n        super().__init__(detail_payload, scripts or [])\n        self.exact_search_payload = exact_search_payload\n        self.exact_search_calls = 0\n\n    async def _official_exact_search_payload(self, item_id: str) -> dict:\n        self.exact_search_calls += 1\n        assert str(self.exact_search_payload.get("itemId")) == str(item_id)\n        payload = copy.deepcopy(self.exact_search_payload)\n        payload["sniperplugOfficialExactSource"] = "affiliate_search_exact_item"\n        return payload\n\n\ndef test_missing_detail_seller_uses_exact_affiliate_search_before_product_page() -> None:\n    provider = ExactAffiliateRecoveryProvider(\n        {\n            "itemId": 22395155,\n            "name": "Bounty Everyday Disposable Paper Napkins",\n            "salePrice": 5.74,\n            "wasPrice": 14.49,\n            "availableOnline": True,\n        },\n        {\n            "itemId": 22395155,\n            "name": "Bounty Everyday Disposable Paper Napkins",\n            "salePrice": 5.74,\n            "wasPrice": 14.49,\n            "sellerName": "Walmart",\n            "isMarketPlaceItem": False,\n            "availableOnline": True,\n        },\n    )\n\n    payload = asyncio.run(provider.fetch_product_detail_payload("22395155"))\n    candidate = provider._candidate_from_item(\n        payload,\n        ProviderScanRequest(source_key="test", query="22395155", max_results=1),\n    )\n\n    assert provider.exact_search_calls == 1\n    assert provider.page_calls == 0\n    assert payload["sellerName"] == "Walmart"\n    assert candidate is not None\n    assert candidate.seller_name == "Walmart"\n    assert candidate.variant_attributes["officialExactSource"] == "affiliate_search_exact_item"\n    assert any("official Affiliate search" in signal for signal in candidate.signals)\n\n\ndef test_exact_affiliate_search_cannot_accept_relevance_neighbor(monkeypatch) -> None:\n    from sniperplug.providers.walmart import WalmartProvider\n\n    provider = WalmartProvider(\n        WalmartAffiliateConfig(\n            enabled=True,\n            consumer_id="test",\n            private_key_b64="unused",\n        )\n    )\n\n    def fake_search(*, query, request, page_size):\n        assert query == "2814230491"\n        return {\n            "items": [\n                {\n                    "itemId": 999999,\n                    "name": "Different DEWALT kit",\n                    "salePrice": 180.0,\n                    "sellerName": "Wrong Seller",\n                }\n            ]\n        }\n\n    monkeypatch.setattr(provider, "_search", fake_search)\n\n    try:\n        provider._exact_search_item_payload("2814230491")\n    except WalmartProviderError as exc:\n        assert exc.retry_kind == "api_exact_search_no_match"\n    else:\n        raise AssertionError("exact Affiliate fallback must reject a relevance neighbor")\n'''
page_test_path = ROOT / "tests/test_walmart_page_identity_recovery.py"
page_text = page_test_path.read_text(encoding="utf-8")
if "test_missing_detail_seller_uses_exact_affiliate_search_before_product_page" not in page_text:
    page_test_path.write_text(page_text + append_page_tests, encoding="utf-8")

replace_once(
    "tests/test_walmart_exact_public_lane.py",
    '''def test_exact_markdown_clears_stale_review_only_markers() -> None:\n    card = exact_card()\n\n    assert normalize_exact_verified_walmart_card(card, min_discount=50) is True\n    assert "review-only" not in card.label.lower()\n    assert "review/scout-only" not in str(card.embed.description or "").lower()\n    assert not any("staff review" in str(field.value or "").lower() for field in card.embed.fields)\n    assert quality.is_review_or_watchlist(card) is False\n''',
    '''def test_exact_markdown_clears_stale_review_only_markers() -> None:\n    card = exact_card()\n    card.embed.add_field(\n        name="Old scan liveness",\n        value="Useful discount. Good for watchlist, but not a true glitch yet.",\n        inline=False,\n    )\n\n    assert normalize_exact_verified_walmart_card(card, min_discount=50) is True\n    assert "review-only" not in card.label.lower()\n    assert "review/scout-only" not in str(card.embed.description or "").lower()\n    assert not any("staff review" in str(field.value or "").lower() for field in card.embed.fields)\n    assert not any("watchlist" in str(field.value or "").lower() for field in card.embed.fields)\n    assert quality.is_review_or_watchlist(card) is False\n\n\ndef test_non_exact_watchlist_card_remains_blocked() -> None:\n    card = exact_card()\n    card.variant_attributes["exactDetailPriceProof"] = "no"\n    card.embed.add_field(\n        name="Watchlist",\n        value="Good for watchlist; staff review required.",\n        inline=False,\n    )\n\n    assert normalize_exact_verified_walmart_card(card, min_discount=50) is False\n    assert quality.is_review_or_watchlist(card) is True\n''',
)

# A compact unit test for retry-kind aggregation without requiring a live provider.
append_runtime_test = '''\n\ndef test_retry_kind_parser_keeps_concrete_reason() -> None:\n    from sniperplug.services.walmart_exact_queue_runtime import _retry_kind_from_error\n\n    assert _retry_kind_from_error("retry_kind=page_http_403 Walmart blocked request") == "page_http_403"\n    assert _retry_kind_from_error("retry_kind=api_http_429 throttled") == "api_http_429"\n    assert _retry_kind_from_error("plain failure") == "unclassified"\n'''
runtime_test_path = ROOT / "tests/test_walmart_exact_queue_runtime.py"
runtime_text = runtime_test_path.read_text(encoding="utf-8")
if "test_retry_kind_parser_keeps_concrete_reason" not in runtime_text:
    runtime_test_path.write_text(runtime_text + append_runtime_test, encoding="utf-8")

print("Applied Walmart missed-deal throughput patch")
