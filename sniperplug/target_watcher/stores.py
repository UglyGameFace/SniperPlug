from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TargetStore:
    store_id: str
    name: str
    address_line: str
    city: str
    state: str
    postal_code: str
    latitude: str
    longitude: str
    distance_miles: float | None = None

    @property
    def label(self) -> str:
        return self.name or f"Target {self.store_id}"

    @property
    def description(self) -> str:
        location = ", ".join(
            piece for piece in (self.city, self.state, self.postal_code) if piece
        )
        if self.distance_miles is not None:
            location = f"{location} • {self.distance_miles:.1f} mi" if location else f"{self.distance_miles:.1f} mi"
        return location or self.address_line or f"Store {self.store_id}"


def parse_target_nearby_stores(payload: Any, *, limit: int = 10) -> tuple[TargetStore, ...]:
    """Extract store objects from Target's nearby-stores response.

    Target has changed wrapper names over time, so this parser deliberately
    walks the JSON tree while applying strict store-shape validation. It rejects
    objects without a numeric location ID, postal address, state, and valid
    coordinates instead of guessing.
    """

    if not isinstance(payload, dict):
        raise ValueError("Target nearby-stores response must be a JSON object")
    stores: dict[str, TargetStore] = {}
    for node in _walk_dicts(payload):
        parsed = _parse_store_node(node)
        if parsed is None:
            continue
        existing = stores.get(parsed.store_id)
        if existing is None or _store_score(parsed) > _store_score(existing):
            stores[parsed.store_id] = parsed
    ordered = sorted(
        stores.values(),
        key=lambda store: (
            store.distance_miles is None,
            store.distance_miles if store.distance_miles is not None else 99999.0,
            store.name.lower(),
            store.store_id,
        ),
    )
    if not ordered:
        raise ValueError("Target nearby-stores response contained no complete store records")
    return tuple(ordered[: max(1, min(25, int(limit)))])


def _parse_store_node(node: dict[str, Any]) -> TargetStore | None:
    store_id = _digits(
        node.get("location_id")
        or node.get("store_id")
        or node.get("locationId")
        or node.get("id")
    )
    if not store_id:
        return None

    address = _first_dict(
        node.get("mailing_address"),
        node.get("address"),
        node.get("physical_address"),
        node.get("location_address"),
    )
    if address is None:
        address = node
    address_line = _text(
        address.get("address_line1")
        or address.get("address_line_1")
        or address.get("line1")
        or address.get("street_address")
        or address.get("address1")
    )
    city = _text(address.get("city") or address.get("locality"))
    state = _text(
        address.get("region")
        or address.get("state")
        or address.get("state_code")
        or address.get("administrative_area")
    ).upper()
    postal_code = _text(
        address.get("postal_code")
        or address.get("zip_code")
        or address.get("zip")
    )
    if len(state) != 2 or not state.isalpha() or not _postal_digits(postal_code):
        return None

    coordinates = _first_dict(
        node.get("geographic_specifications"),
        node.get("coordinates"),
        node.get("geolocation"),
        node.get("location"),
    )
    latitude = _coordinate(
        (coordinates or {}).get("latitude")
        or (coordinates or {}).get("lat")
        or node.get("latitude")
        or node.get("lat"),
        minimum=-90,
        maximum=90,
    )
    longitude = _coordinate(
        (coordinates or {}).get("longitude")
        or (coordinates or {}).get("lon")
        or (coordinates or {}).get("lng")
        or node.get("longitude")
        or node.get("lon")
        or node.get("lng"),
        minimum=-180,
        maximum=180,
    )
    if latitude is None or longitude is None:
        return None

    name = _text(
        node.get("location_name")
        or node.get("store_name")
        or node.get("name")
        or node.get("short_name")
    )
    if not name:
        name = f"Target {store_id}"
    distance = _number(
        node.get("distance")
        or node.get("distance_miles")
        or node.get("distance_from_origin")
    )
    return TargetStore(
        store_id=store_id,
        name=name[:100],
        address_line=address_line[:150],
        city=city[:80],
        state=state,
        postal_code=postal_code[:20],
        latitude=latitude,
        longitude=longitude,
        distance_miles=distance,
    )


def _walk_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def _first_dict(*values: Any) -> dict[str, Any] | None:
    for value in values:
        if isinstance(value, dict):
            return value
    return None


def _digits(value: Any) -> str:
    text = _text(value)
    return text if text.isdigit() and 1 <= len(text) <= 8 else ""


def _postal_digits(value: Any) -> str:
    return "".join(character for character in _text(value) if character.isdigit())[:5]


def _coordinate(value: Any, *, minimum: float, maximum: float) -> str | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not minimum <= parsed <= maximum:
        return None
    return f"{parsed:.6f}".rstrip("0").rstrip(".")


def _number(value: Any) -> float | None:
    if isinstance(value, dict):
        value = value.get("value") or value.get("distance")
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _store_score(store: TargetStore) -> int:
    return sum(
        bool(value)
        for value in (
            store.name,
            store.address_line,
            store.city,
            store.state,
            store.postal_code,
            store.latitude,
            store.longitude,
        )
    )


def _text(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split())
