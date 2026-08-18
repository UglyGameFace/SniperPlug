from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from sniperplug.models.candidate import SourceCandidate
from sniperplug.providers import walmart as base_walmart
from sniperplug.providers.base import ProviderScanRequest

WalmartAffiliateConfig = base_walmart.WalmartAffiliateConfig
WalmartProviderError = base_walmart.WalmartProviderError
walmart_config_from_env = base_walmart.walmart_config_from_env


@dataclass(frozen=True)
class WalmartVariantProof:
    selected_offer_id: str | None = None
    variant_label: str | None = None
    variant_attributes: dict[str, str] | None = None
    pack_size: str | None = None
    color: str | None = None
    platform: str | None = None
    model: str | None = None
    parent_title: str | None = None
    option_mismatch_warning: str | None = None
    requires_staff_review: bool = False
    has_variant_structure: bool = False


class WalmartProvider(base_walmart.WalmartProvider):
    def _candidate_from_item(self, item: dict, request: ProviderScanRequest) -> SourceCandidate | None:
        raw_title = str(item.get('name') or '').strip()
        raw_tracking_url = str(item.get('productTrackingUrl') or '').strip()
        item_id = item.get('itemId')
        direct_product_url = base_walmart._direct_walmart_url(item_id)
        product_url = raw_tracking_url or direct_product_url

        signals = self._item_signals(item)
        proof = extract_variant_proof(item, raw_title)

        if proof.selected_offer_id:
            signals.append(f'selected offer ID: {proof.selected_offer_id}')
        if proof.variant_label:
            signals.append(f'selected option: {proof.variant_label}')
        if proof.option_mismatch_warning:
            signals.append(proof.option_mismatch_warning)

        title = display_title(raw_title, proof)

        if product_url and '|PUBID|' in product_url and direct_product_url:
            product_url = direct_product_url
            signals.append('tracking link unavailable; direct Walmart link used')

        if not title or not product_url:
            return None

        current_price = float_or_none(item.get('salePrice'))
        typical_price, reference_signal = trusted_reference_price(
            item=item,
            title=title,
            current_price=current_price,
            variant_conflict=bool(proof.option_mismatch_warning),
        )
        if reference_signal:
            signals.append(reference_signal)

        category_path = str(item.get('categoryPath') or '').strip()
        if category_path:
            signals.append(f'Walmart category: {category_path}')

        return SourceCandidate(
            source_key=self.provider_key,
            retailer='Walmart',
            title=title,
            product_url=product_url,
            current_price=current_price,
            typical_price=typical_price,
            image_url=str(item.get('largeImage') or item.get('mediumImage') or item.get('thumbnailImage') or '') or None,
            product_id=str(item_id) if item_id is not None else None,
            product_id_type='sku' if item_id is not None else None,
            sku=str(item_id) if item_id is not None else None,
            upc=str(item.get('upc')) if item.get('upc') else None,
            selected_offer_id=proof.selected_offer_id,
            variant_label=proof.variant_label,
            variant_attributes=dict(proof.variant_attributes or {}),
            pack_size=proof.pack_size,
            color=proof.color,
            platform=proof.platform,
            model=proof.model,
            parent_title=proof.parent_title,
            option_mismatch_warning=proof.option_mismatch_warning,
            requires_staff_review=proof.requires_staff_review,
            stock_status=str(item.get('stock') or '') or None,
            can_add_to_cart=bool(item.get('availableOnline')) if 'availableOnline' in item else None,
            is_business_offer=False,
            is_member_only=False,
            is_checkout_price=False,
            signals=signals[:10],
        )


def trusted_reference_price(
    item: dict,
    title: str,
    current_price: float | None,
    variant_conflict: bool = False,
) -> tuple[float | None, str | None]:
    references = [
        ('msrp', float_or_none(item.get('msrp'))),
        ('listPrice', float_or_none(item.get('listPrice'))),
    ]
    references.extend(best_marketplace_reference_prices(item))

    if current_price is None or current_price <= 0:
        return first_positive_reference(references), None

    for source, value in references:
        if value is None or value <= current_price:
            continue
        if variant_conflict:
            return value, f'Walmart reference price source: {source}; staff review required because selected option differs'
        if reference_price_looks_suspicious(title=title, current_price=current_price, reference_price=value):
            return None, f'ignored suspicious Walmart {source} reference price: ${value:,.2f}'
        return value, f'Walmart reference price source: {source}'

    return None, None


def best_marketplace_reference_prices(item: dict) -> list[tuple[str, float | None]]:
    best_marketplace = item.get('bestMarketplacePrice')
    if not isinstance(best_marketplace, dict):
        return []
    return [('bestMarketplacePrice.price', float_or_none(best_marketplace.get('price')))]


def first_positive_reference(references: list[tuple[str, float | None]]) -> float | None:
    for _, value in references:
        if value and value > 0:
            return value
    return None


def reference_price_looks_suspicious(title: str, current_price: float, reference_price: float) -> bool:
    ratio = reference_price / current_price
    if ratio <= 1:
        return True
    if current_price <= 10 and reference_price >= 75:
        return True
    if current_price <= 20 and ratio >= 12:
        return True
    if is_variant_sensitive_item(title) and ratio >= 8:
        return True
    if is_size_sensitive_essential(title):
        if ratio >= 6:
            return True
        if current_price <= 15 and reference_price >= 60:
            return True
    return False


def extract_variant_proof(item: dict[str, Any], raw_title: str) -> WalmartVariantProof:
    attrs: dict[str, str] = {}
    has_variant_structure = any(item.get(key) for key in ('variantAttributes', 'variants', 'productVariants', 'swatches', 'selectedVariant'))

    root_key_map = {
        'size': ('pack_size', 'Size'),
        'color': ('color', 'Color'),
        'platform': ('platform', 'Platform'),
        'edition': ('edition', 'Edition'),
        'packSize': ('pack_size', 'Pack Size'),
        'numberOfRolls': ('pack_size', 'Number of Rolls'),
        'count': ('pack_size', 'Count'),
        'pieceCount': ('pack_size', 'Piece Count'),
        'modelNumber': ('model', 'Model'),
    }
    for raw_key, (_, label) in root_key_map.items():
        value = clean_option_value(item.get(raw_key))
        if value:
            attrs[label] = value

    nested_attrs = item.get('attributes') if isinstance(item.get('attributes'), dict) else {}
    for raw_key, (_, label) in root_key_map.items():
        value = clean_option_value(nested_attrs.get(raw_key) or nested_attrs.get(raw_key.lower()))
        if value and label not in attrs:
            attrs[label] = value

    merge_variant_attributes(attrs, item.get('variantAttributes'))

    selected_variant = item.get('selectedVariant')
    if isinstance(selected_variant, dict):
        has_variant_structure = True
        merge_variant_attributes(attrs, selected_variant)
    elif selected_variant:
        has_variant_structure = True
        selected_text = clean_option_value(selected_variant)
        if selected_text:
            attrs.setdefault('Selected Variant', selected_text)

    for collection_name in ('variants', 'productVariants', 'swatches'):
        collection = item.get(collection_name)
        if collection:
            has_variant_structure = True
        merge_selected_collection_variant(attrs, collection, item)

    platform = pick_attr(attrs, 'Platform', 'Compatible With', 'System', 'Gaming System')
    pack_size = pick_attr(attrs, 'Pack Size', 'Number of Rolls', 'Count', 'Piece Count', 'Size')
    color = pick_attr(attrs, 'Color')
    model = pick_attr(attrs, 'Model', 'Model Number', 'Style')
    mismatch_warning = option_mismatch_warning(raw_title, platform, pack_size, color)
    variant_label = build_variant_label(platform, pack_size, color, model, attrs)

    requires_staff_review = bool(mismatch_warning)
    if has_variant_structure and not variant_label:
        mismatch_warning = 'Variant proof incomplete: Walmart returned variant data but no selected option label; staff review required'
        requires_staff_review = True

    parent_title = raw_title if mismatch_warning or (variant_label and variant_label.lower() not in raw_title.lower()) else None

    return WalmartVariantProof(
        selected_offer_id=str(item.get('offerId')) if item.get('offerId') else None,
        variant_label=variant_label,
        variant_attributes=attrs,
        pack_size=pack_size,
        color=color,
        platform=platform,
        model=model,
        parent_title=parent_title,
        option_mismatch_warning=mismatch_warning,
        requires_staff_review=requires_staff_review,
        has_variant_structure=has_variant_structure,
    )


def merge_variant_attributes(output: dict[str, str], data: Any) -> None:
    if isinstance(data, dict):
        for key, value in data.items():
            label = humanize_variant_key(key)
            cleaned = clean_option_value(value)
            if cleaned and label not in output:
                output[label] = cleaned
        return

    if isinstance(data, list):
        for entry in data:
            if not isinstance(entry, dict):
                continue
            name = entry.get('name') or entry.get('attributeName') or entry.get('displayName') or entry.get('key')
            value = entry.get('value') or entry.get('attributeValue') or entry.get('displayValue') or entry.get('selectedValue')
            if isinstance(value, dict):
                value = value.get('name') or value.get('value')
            label = humanize_variant_key(name)
            cleaned = clean_option_value(value)
            if label and cleaned and label not in output:
                output[label] = cleaned


def merge_selected_collection_variant(output: dict[str, str], collection: Any, item: dict[str, Any]) -> None:
    if not isinstance(collection, list):
        return

    item_id = str(item.get('itemId') or '')
    offer_id = str(item.get('offerId') or '')
    upc = str(item.get('upc') or '')

    for entry in collection:
        if not isinstance(entry, dict):
            continue
        entry_ids = {
            str(entry.get('itemId') or ''),
            str(entry.get('productId') or ''),
            str(entry.get('offerId') or ''),
            str(entry.get('id') or ''),
            str(entry.get('upc') or ''),
        }
        is_selected = bool(entry.get('selected') or entry.get('isSelected') or entry.get('default'))
        is_same = bool({item_id, offer_id, upc}.intersection(entry_ids - {''}))
        if not is_selected and not is_same:
            continue
        merge_variant_attributes(output, entry)
        merge_variant_attributes(output, entry.get('variantAttributes'))
        return


def humanize_variant_key(key: object) -> str:
    text = '' if key is None else str(key)
    text = re.sub(r'[_\-]+', ' ', text).strip()
    text = re.sub(r'(?<!^)(?=[A-Z])', ' ', text)
    return ' '.join(text.split()).title()


def pick_attr(attrs: dict[str, str], *keys: str) -> str | None:
    lower_to_value = {key.lower(): value for key, value in attrs.items()}
    for key in keys:
        value = lower_to_value.get(key.lower())
        if value:
            return value
    return None


def build_variant_label(platform: str | None, pack_size: str | None, color: str | None, model: str | None, attrs: dict[str, str]) -> str | None:
    parts: list[str] = []
    for value in (platform, pack_size, color, model):
        cleaned = clean_option_value(value)
        if cleaned and cleaned.lower() not in {part.lower() for part in parts}:
            parts.append(cleaned)
    if not parts:
        for label in ('Selected Variant', 'Edition', 'Style', 'Size'):
            value = clean_option_value(attrs.get(label))
            if value and value.lower() not in {part.lower() for part in parts}:
                parts.append(value)
    return ', '.join(parts[:3]) if parts else None


def display_title(raw_title: str, proof: WalmartVariantProof) -> str:
    if proof.variant_label and proof.option_mismatch_warning:
        return f'Selected option: {proof.variant_label} • Parent listing: {raw_title}'
    if proof.variant_label and proof.variant_label.lower() not in raw_title.lower():
        return f'{raw_title} • Option: {proof.variant_label}'
    return raw_title


def option_mismatch_warning(title: str, platform: str | None, pack_size: str | None, color: str | None) -> str | None:
    title_platforms = platform_tokens(title)
    variant_platforms = platform_tokens(platform or '')
    if title_platforms and variant_platforms and title_platforms.isdisjoint(variant_platforms):
        return f"Variant mismatch: parent listing mentions {', '.join(sorted(title_platforms))}, but selected option is {', '.join(sorted(variant_platforms))}; staff review required"

    title_packs = pack_tokens(title)
    variant_packs = pack_tokens(pack_size or '')
    if title_packs and variant_packs and title_packs.isdisjoint(variant_packs):
        return f"Variant mismatch: parent listing pack/size appears to be {', '.join(sorted(title_packs))}, but selected option is {', '.join(sorted(variant_packs))}; staff review required"

    title_colors = color_tokens(title)
    variant_colors = color_tokens(color or '')
    if title_colors and variant_colors and title_colors.isdisjoint(variant_colors):
        return f"Variant mismatch: parent listing color appears to be {', '.join(sorted(title_colors))}, but selected option is {', '.join(sorted(variant_colors))}; staff review required"

    return None


def platform_tokens(text: str) -> set[str]:
    lower = text.lower()
    tokens: set[str] = set()
    if any(x in lower for x in ('xbox', 'series x', 'series s')):
        tokens.add('Xbox')
    if any(x in lower for x in ('ps5', 'playstation 5', 'playstation')):
        tokens.add('PlayStation')
    if 'switch' in lower or 'nintendo' in lower:
        tokens.add('Nintendo')
    if re.search(r'\bpc\b', lower):
        tokens.add('PC')
    return tokens


def pack_tokens(text: str) -> set[str]:
    lower = text.lower()
    tokens: set[str] = set()
    if re.search(r'\bsingle\b', lower):
        tokens.add('single')
    for match in re.finditer(r'\b(\d+)\s*(?:pack|pk|ct|count|piece|pieces|roll|rolls|mega roll|mega rolls)\b', lower):
        tokens.add(match.group(1))
    return tokens


def color_tokens(text: str) -> set[str]:
    lower = text.lower()
    colors = ('black', 'white', 'blue', 'red', 'green', 'yellow', 'pink', 'purple', 'gray', 'grey', 'silver', 'gold', 'brown', 'orange')
    return {color for color in colors if re.search(rf'\b{re.escape(color)}\b', lower)}


def clean_option_value(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, (list, tuple, set)):
        value = ', '.join(str(item) for item in value if item is not None)
    text = ' '.join(str(value).replace('_', ' ').split())
    if not text or text.lower() in {'n/a', 'none', 'null', 'no', 'yes', 'each'}:
        return None
    if len(text) > 80:
        return None
    return text


def is_variant_sensitive_item(title: str) -> bool:
    text = title.lower()
    keywords = (
        'option:', 'selected option:', 'parent listing:', 'ps5', 'playstation', 'xbox', 'nintendo', 'switch',
        'edition', 'pack', 'count', 'ct', 'oz', 'ounce', 'fl oz', 'roll', 'inch', 'size', 'tire',
        'car wash', 'wax', 'cleaning', 'detergent',
    )
    return any(keyword in text for keyword in keywords)


def is_size_sensitive_essential(title: str) -> bool:
    text = title.lower()
    keywords = (
        'toilet paper', 'toilet tissue', 'bath tissue', 'paper towel', 'paper towels', 'tissue', 'napkin',
        'detergent', 'laundry', 'trash bag', 'dish soap', 'cleaner', 'wipes', 'diaper', 'razor',
        'disposable', 'shampoo', 'conditioner', 'body wash', 'soap', 'toothpaste', 'toothbrush',
    )
    return any(keyword in text for keyword in keywords)


def float_or_none(value) -> float | None:
    if value is None or value == '':
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
