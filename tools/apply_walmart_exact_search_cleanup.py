from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise RuntimeError(f"expected one block in {path}, found {text.count(old)}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "sniperplug/providers/walmart_page_recovery.py",
    '''        # The Affiliate search route is already the healthy global-discovery\n        # source. Refresh this exact item ID before relying on Walmart HTML. The\n        # base provider accepts only an exact returned item ID, so a relevance\n        # neighbor can never donate seller/offer identity.\n        try:\n            search_payload = await self._official_exact_search_payload(clean_item_id)\n        except WalmartProviderError:\n            search_payload = None\n        if isinstance(search_payload, dict):\n            payload = search_payload\n            if not needs_page_identity_recovery(payload):\n                return payload\n''',
    '''        # The Affiliate search route is already the healthy global-discovery\n        # source. Refresh this exact item ID before relying on Walmart HTML. The\n        # base provider accepts only an exact returned item ID, so a relevance\n        # neighbor can never donate seller/offer identity. If the base detail\n        # fallback already used this exact search, do not issue the same request\n        # twice before falling back to the page.\n        already_exact_search = (\n            _clean_text(payload.get("sniperplugOfficialExactSource"))\n            == "affiliate_search_exact_item"\n        )\n        if not already_exact_search:\n            try:\n                search_payload = await self._official_exact_search_payload(clean_item_id)\n            except WalmartProviderError:\n                search_payload = None\n            if isinstance(search_payload, dict):\n                payload = search_payload\n                if not needs_page_identity_recovery(payload):\n                    return payload\n''',
)

path = ROOT / "tests/test_walmart_page_identity_recovery.py"
text = path.read_text(encoding="utf-8")
addition = '''\n\ndef test_detail_fallback_exact_search_is_not_repeated_before_page() -> None:\n    class AlreadyExactSearchProvider(FixturePageProvider):\n        def __init__(self):\n            super().__init__(\n                {\n                    "itemId": 777001,\n                    "name": "Exact search still lacks seller",\n                    "salePrice": 12.00,\n                    "sniperplugOfficialExactSource": "affiliate_search_exact_item",\n                },\n                [jsonld_product(item_id="777001", price=12.00, seller="Walmart.com")],\n            )\n            self.exact_search_calls = 0\n\n        async def _official_exact_search_payload(self, item_id: str) -> dict:\n            self.exact_search_calls += 1\n            raise AssertionError("exact Affiliate fallback must not run twice")\n\n    provider = AlreadyExactSearchProvider()\n    payload = asyncio.run(provider.fetch_product_detail_payload("777001"))\n\n    assert provider.exact_search_calls == 0\n    assert provider.page_calls == 1\n    assert payload["sellerName"] == "Walmart.com"\n''' 
if "test_detail_fallback_exact_search_is_not_repeated_before_page" not in text:
    path.write_text(text + addition, encoding="utf-8")

print("Applied exact Affiliate duplicate-call cleanup")
