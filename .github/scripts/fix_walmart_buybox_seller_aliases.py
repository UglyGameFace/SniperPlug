from pathlib import Path

path = Path("sniperplug/providers/walmart_page_recovery.py")
text = path.read_text(encoding="utf-8")

old = '''    for key in ("seller", "sellerInfo", "merchant"):\n        child = value.get(key)\n        if isinstance(child, dict):\n            seller_name = seller_name or _first_text(\n                child.get("name"),\n                child.get("sellerName"),\n                child.get("displayName"),\n            )\n            seller_id = seller_id or _first_text(\n                child.get("id"),\n                child.get("sellerId"),\n                child.get("sellerID"),\n            )\n        elif isinstance(child, str) and not seller_name:\n            seller_name = _clean_text(child)\n'''
new = '''    for key in ("seller", "sellerInfo", "merchant", "merchantInfo"):\n        child = value.get(key)\n        if isinstance(child, dict):\n            seller_name = seller_name or _first_text(\n                child.get("name"),\n                child.get("sellerName"),\n                child.get("sellerDisplayName"),\n                child.get("displayName"),\n                child.get("merchantName"),\n            )\n            seller_id = seller_id or _first_text(\n                child.get("id"),\n                child.get("sellerId"),\n                child.get("sellerID"),\n                child.get("merchantId"),\n            )\n        elif isinstance(child, str) and not seller_name:\n            seller_name = _clean_text(child)\n'''
if text.count(old) != 1:
    raise SystemExit(f"expected one nested buy-box seller parser, found {text.count(old)}")
text = text.replace(old, new, 1)
path.write_text(text, encoding="utf-8")
