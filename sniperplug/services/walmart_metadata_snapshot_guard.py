from __future__ import annotations

from typing import Any

from sniperplug.services import walmart_product_metadata as metadata


MAX_SNAPSHOT_NODES = 2_500
MAX_SNAPSHOT_DEPTH = 14
MAX_LIST_ITEMS_PER_CONTAINER = 80
MAX_SNAPSHOT_LEAVES = 4_000

_INSTALL_FLAG = "_sniperplug_bounded_snapshot_installed"
_ORIGINAL_ATTR = "_sniperplug_original_snapshot_payload"


def install_bounded_walmart_metadata_snapshot() -> None:
    """Prevent oversized Walmart payloads from monopolizing the event loop.

    Exact-detail payloads can contain deeply nested merchandising, reviews,
    recommendations, and fulfillment structures. The metadata extractor only
    needs a bounded factual sample. This replacement preserves direct fields and
    normal product structures while refusing to recursively index an unbounded
    response on Discord's main event loop.
    """

    if bool(getattr(metadata, _INSTALL_FLAG, False)):
        return
    original = getattr(metadata, "_snapshot_payload", None)
    if callable(original):
        setattr(metadata, _ORIGINAL_ATTR, original)
    metadata._snapshot_payload = bounded_snapshot_payload
    setattr(metadata, _INSTALL_FLAG, True)


def bounded_snapshot_payload(value: Any) -> metadata.PayloadSnapshot:
    containers: list[tuple[str, dict[str, Any]]] = []
    leaves: list[tuple[str, Any]] = []
    stack: list[tuple[Any, str, int]] = [(value, "", 0)]
    visited_nodes = 0

    while stack and visited_nodes < MAX_SNAPSHOT_NODES:
        current, prefix, depth = stack.pop()
        visited_nodes += 1

        if isinstance(current, dict):
            containers.append((prefix, current))
            if depth >= MAX_SNAPSHOT_DEPTH:
                continue
            # Reverse insertion preserves the original left-to-right traversal
            # order when entries are popped from the stack.
            entries = list(current.items())
            for key, child in reversed(entries):
                path = f"{prefix}.{key}" if prefix else str(key)
                stack.append((child, path, depth + 1))
            continue

        if isinstance(current, list):
            if depth >= MAX_SNAPSHOT_DEPTH:
                continue
            capped = current[:MAX_LIST_ITEMS_PER_CONTAINER]
            for index in range(len(capped) - 1, -1, -1):
                stack.append((capped[index], f"{prefix}[{index}]", depth + 1))
            continue

        if len(leaves) < MAX_SNAPSHOT_LEAVES:
            leaves.append((prefix, current))

    return metadata.PayloadSnapshot(tuple(containers), tuple(leaves))
