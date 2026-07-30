from __future__ import annotations

from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        if new in text:
            return text
        raise SystemExit(f"{label} not found")
    return text.replace(old, new, 1)


def main() -> None:
    truth = Path("sniperplug/services/walmart_cash_api_truth.py")
    text = truth.read_text()
    text = replace_once(
        text,
        "PROMO_CONTEXT_MARKERS = (\n",
        "WALMART_CASH_AMOUNT_KEY_MARKERS = (\n"
        "    \"amount\",\n"
        "    \"value\",\n"
        "    \"savings\",\n"
        "    \"saving\",\n"
        "    \"reward\",\n"
        "    \"rebate\",\n"
        ")\n\n\nPROMO_CONTEXT_MARKERS = (\n",
        "cash amount markers",
    )
    text = replace_once(
        text,
        '''def _amount_from_cash_object(obj: dict[str, Any], *, path: str, text: str) -> float | None:\n    best: float | None = None\n\n    for key, value in obj.items():\n        key_norm = _norm(key)\n        path_norm = _norm(path)\n        is_cash_key = "walmartcash" in key_norm or "walmartcash" in path_norm\n        is_amount_key = any(token in key_norm for token in ("amount", "value", "savings", "saving", "reward", "cash"))\n\n        if not (is_cash_key or is_amount_key):\n            continue\n\n        parsed = _float_or_none(value)\n        if parsed is not None and (best is None or parsed > best):\n            best = parsed\n\n    nearby = _money_near_walmart_cash(text)\n    if nearby is not None and (best is None or nearby > best):\n        best = nearby\n\n    return best\n''',
        '''def _amount_from_cash_object(obj: dict[str, Any], *, path: str, text: str) -> float | None:\n    best: float | None = None\n\n    for key, value in obj.items():\n        key_norm = _norm(key)\n        if not _is_dedicated_cash_amount_key(key_norm):\n            continue\n        parsed = _float_or_none(value)\n        if parsed is not None and (best is None or parsed > best):\n            best = parsed\n\n    nearby = _money_near_walmart_cash(text)\n    if nearby is not None and (best is None or nearby > best):\n        best = nearby\n\n    return best\n''',
        "cash object amount parser",
    )
    text = replace_once(
        text,
        '''def _amount_from_leaf(path: str, value: Any, *, text: str) -> float | None:\n    path_norm = _norm(path)\n    if "walmartcash" in path_norm or any(token in path_norm for token in ("amount", "value", "savings", "saving", "reward", "cash")):\n        parsed = _float_or_none(value)\n        if parsed is not None:\n            return parsed\n\n    return _money_near_walmart_cash(text)\n''',
        '''def _amount_from_leaf(path: str, value: Any, *, text: str) -> float | None:\n    path_norm = _norm(path)\n    leaf_key = _norm(path.rsplit(".", 1)[-1].split("[", 1)[0])\n    if "walmartcash" in path_norm and _is_dedicated_cash_amount_key(leaf_key):\n        parsed = _float_or_none(value)\n        if parsed is not None:\n            return parsed\n\n    return _money_near_walmart_cash(text)\n''',
        "cash leaf amount parser",
    )
    text = replace_once(
        text,
        '''def _money_near_walmart_cash(text: str) -> float | None:\n    raw = str(text or "")\n    lowered = raw.lower()\n    normalized = _norm(lowered)\n\n    if "walmart cash" not in lowered and "walmartcash" not in normalized:\n        return None\n\n    matches = list(re.finditer(r"\\$\\s*(\\d+(?:\\.\\d{1,2})?)", raw))\n    if not matches:\n        return None\n\n    cash_positions = [idx for idx in (lowered.find("walmart cash"), normalized.find("walmartcash")) if idx >= 0]\n    cash_index = min(cash_positions or [0])\n    best = min(matches, key=lambda m: abs(m.start() - cash_index))\n    return _float_or_none(best.group(1))\n''',
        '''def _money_near_walmart_cash(text: str) -> float | None:\n    raw = " ".join(str(text or "").split())\n    lowered = raw.lower()\n    normalized = _norm(lowered)\n\n    if "walmart cash" not in lowered and "walmartcash" not in normalized:\n        return None\n\n    patterns = (\n        r"(?:get|earn|receive|claim)\\s+\\$\\s*(\\d+(?:\\.\\d{1,2})?)\\s+(?:in\\s+)?walmart\\s+cash",\n        r"\\$\\s*(\\d+(?:\\.\\d{1,2})?)\\s+walmart\\s+cash",\n        r"walmart\\s+cash[^$0-9]{0,40}\\$\\s*(\\d+(?:\\.\\d{1,2})?)",\n    )\n    for pattern in patterns:\n        match = re.search(pattern, lowered)\n        if match:\n            return _float_or_none(match.group(1))\n    return None\n\n\ndef _is_dedicated_cash_amount_key(key_norm: str) -> bool:\n    if not key_norm:\n        return False\n    if "walmartcash" in key_norm:\n        return any(marker in key_norm for marker in WALMART_CASH_AMOUNT_KEY_MARKERS)\n    return key_norm in WALMART_CASH_AMOUNT_KEY_MARKERS or any(\n        key_norm.endswith(marker) for marker in WALMART_CASH_AMOUNT_KEY_MARKERS\n    )\n''',
        "cash action amount parser",
    )
    truth.write_text(text)

    pdp = Path("sniperplug/services/walmart_pdp_cash_proof.py")
    text = pdp.read_text()
    text = replace_once(
        text,
        '''    patterns = (\n        r"(?:earn|get|receive|save)\\s+(\\d+(?:\\.\\d{1,2})?)\\s+(?:in\\s+)?walmart\\s+cash",\n        r"walmart\\s+cash[^0-9$]{0,80}(\\d+(?:\\.\\d{1,2})?)",\n    )\n''',
        '''    patterns = (\n        r"(?:earn|get|receive|claim)\\s+\\$?\\s*(\\d+(?:\\.\\d{1,2})?)\\s+(?:in\\s+)?walmart\\s+cash",\n        r"\\$\\s*(\\d+(?:\\.\\d{1,2})?)\\s+walmart\\s+cash",\n        r"walmart\\s+cash[^0-9$]{0,40}\\$\\s*(\\d+(?:\\.\\d{1,2})?)",\n    )\n''',
        "PDP Cash amount parser",
    )
    pdp.write_text(text)

    offers = Path("sniperplug/services/walmart_cash_offers.py")
    text = offers.read_text()
    text = replace_once(
        text,
        'DEFAULT_CASH_QUERIES = (\n    "walmart cash offers",\n',
        'DEFAULT_CASH_QUERIES = (\n    "manufacturer offers",\n    "get walmart cash",\n    "walmart cash offers",\n',
        "manufacturer-offer discovery terms",
    )
    offers.write_text(text)

    test = Path("tests/test_walmart_cash_proven_filter.py")
    test.write_text('''from sniperplug.services.walmart_cash_api_truth import extract_walmart_cash_api_truth\nfrom sniperplug.services.walmart_cash_offers import walmart_cash_search_terms\nfrom sniperplug.services.walmart_pdp_cash_proof import extract_walmart_cash_from_pdp_html\n\n\ndef test_walmart_cash_object_does_not_treat_price_or_item_id_as_cash_amount():\n    item = {\n        "itemId": 987654321,\n        "price": 29.97,\n        "walmartCash": {\n            "eligible": True,\n            "itemId": 987654321,\n            "currentPrice": 29.97,\n        },\n    }\n    assert extract_walmart_cash_api_truth(item, current_price=29.97) is None\n\n\ndef test_dedicated_walmart_cash_amount_field_is_accepted():\n    item = {\n        "itemId": "123",\n        "manufacturerOffer": {\n            "label": "Get Walmart Cash",\n            "walmartCashAmount": 5.00,\n        },\n    }\n    proof = extract_walmart_cash_api_truth(item, current_price=20.00)\n    assert proof is not None\n    assert proof.amount == 5.00\n\n\ndef test_exact_action_text_with_amount_is_accepted_but_nearby_price_is_not():\n    valid = {"promotion": {"text": "Get $4.00 Walmart Cash after purchase"}}\n    invalid = {"promotion": {"text": "Walmart Cash available", "currentPrice": "$24.98"}}\n    assert extract_walmart_cash_api_truth(valid, current_price=24.98).amount == 4.00\n    assert extract_walmart_cash_api_truth(invalid, current_price=24.98) is None\n\n\ndef test_pdp_requires_cash_action_amount_not_any_nearby_dollar_value():\n    valid_html = '<html><body><label>Get $3.00 Walmart Cash</label></body></html>'\n    invalid_html = '<html><body><h1>Walmart Cash</h1><span>Price $18.97</span></body></html>'\n    assert extract_walmart_cash_from_pdp_html(valid_html, current_price=18.97).amount == 3.00\n    assert extract_walmart_cash_from_pdp_html(invalid_html, current_price=18.97) is None\n\n\ndef test_default_discovery_uses_official_manufacturer_offer_language():\n    terms = walmart_cash_search_terms(None)\n    assert terms[0] == "manufacturer offers"\n    assert "get walmart cash" in terms\n''')


if __name__ == "__main__":
    main()
