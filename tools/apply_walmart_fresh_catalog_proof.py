from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one match, found {count}: {old[:120]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


def main() -> None:
    queue = "sniperplug/services/walmart_exact_verification_queue.py"
    replace_once(
        queue,
        """class _QueueClaim:\n    item_id: str\n    title: str\n    product_url: str\n    image_url: str\n    apparent_current_cents: int | None\n    apparent_reference_cents: int | None\n    route_hint: str\n    lease_token: str\n    attempt_count: int\n""",
        """class _QueueClaim:\n    item_id: str\n    title: str\n    product_url: str\n    image_url: str\n    apparent_current_cents: int | None\n    apparent_reference_cents: int | None\n    route_hint: str\n    lease_token: str\n    attempt_count: int\n    discovery_snapshot_json: str = \"\"\n    discovery_snapshot_at: str | None = None\n""",
    )
    replace_once(
        queue,
        """            exact_condition_key TEXT NOT NULL DEFAULT '',\n            exact_fulfillment_key TEXT NOT NULL DEFAULT '',\n            snapshot_json TEXT NOT NULL DEFAULT ''\n        )\n        \"\"\"\n    )\n    await conn.execute(\n        f\"CREATE INDEX IF NOT EXISTS idx_{QUEUE_TABLE}_due \"\n""",
        """            exact_condition_key TEXT NOT NULL DEFAULT '',\n            exact_fulfillment_key TEXT NOT NULL DEFAULT '',\n            snapshot_json TEXT NOT NULL DEFAULT '',\n            discovery_snapshot_json TEXT NOT NULL DEFAULT '',\n            discovery_snapshot_at TEXT\n        )\n        \"\"\"\n    )\n    column_cursor = await conn.execute(f\"PRAGMA table_info({QUEUE_TABLE})\")\n    columns = {\n        str(_row_get(row, \"name\", index=1) or \"\")\n        for row in await column_cursor.fetchall()\n    }\n    if \"discovery_snapshot_json\" not in columns:\n        await conn.execute(\n            f\"ALTER TABLE {QUEUE_TABLE} ADD COLUMN discovery_snapshot_json TEXT NOT NULL DEFAULT ''\"\n        )\n    if \"discovery_snapshot_at\" not in columns:\n        await conn.execute(\n            f\"ALTER TABLE {QUEUE_TABLE} ADD COLUMN discovery_snapshot_at TEXT\"\n        )\n    await conn.execute(\n        f\"CREATE INDEX IF NOT EXISTS idx_{QUEUE_TABLE}_due \"\n""",
    )
    replace_once(
        queue,
        """            apparent_current_cents, apparent_reference_cents,\n            route_hint, attempt_count\n        FROM {QUEUE_TABLE}\n""",
        """            apparent_current_cents, apparent_reference_cents,\n            route_hint, attempt_count,\n            discovery_snapshot_json, discovery_snapshot_at\n        FROM {QUEUE_TABLE}\n""",
    )
    replace_once(
        queue,
        """                route_hint=str(_row_get(row, \"route_hint\", index=6) or \"\"),\n                lease_token=token,\n                attempt_count=int(_row_get(row, \"attempt_count\", index=7) or 0),\n            )\n""",
        """                route_hint=str(_row_get(row, \"route_hint\", index=6) or \"\"),\n                lease_token=token,\n                attempt_count=int(_row_get(row, \"attempt_count\", index=7) or 0),\n                discovery_snapshot_json=str(\n                    _row_get(row, \"discovery_snapshot_json\", index=8) or \"\"\n                ),\n                discovery_snapshot_at=(\n                    str(_row_get(row, \"discovery_snapshot_at\", index=9) or \"\")\n                    or None\n                ),\n            )\n""",
    )

    bulk = "sniperplug/services/walmart_exact_verification_queue_bulk.py"
    replace_once(
        bulk,
        """from sniperplug.services.walmart_exact_queue_pressure import (\n    load_walmart_exact_queue_pressure,\n)\n""",
        """from sniperplug.services.walmart_exact_queue_pressure import (\n    load_walmart_exact_queue_pressure,\n)\nfrom sniperplug.services.walmart_fresh_discovery_proof import (\n    DISCOVERY_PROOF_REFRESH_INTERVAL_SECONDS,\n    build_fresh_discovery_offer_snapshot,\n)\n""",
    )
    replace_once(
        bulk,
        """# Sixteen parameters per row keeps sixty-row writes below SQLite's conservative\n# historical 999-variable ceiling while reducing a 75-100 item catalog pass to\n# at most two writes when every row is genuinely new or changed.\nQUEUE_UPSERT_CHUNK_SIZE = 60\nQUEUE_EXISTING_LOOKUP_CHUNK_SIZE = 500\nQUEUE_DISCOVERY_REFRESH_INTERVAL_SECONDS = 6 * 60 * 60\n_QUEUE_COLUMNS_PER_ROW = 16\n""",
        """# Eighteen parameters per row keeps 55-row writes below SQLite's conservative\n# historical 999-variable ceiling while preserving bounded selected-offer proof.\nQUEUE_UPSERT_CHUNK_SIZE = 55\nQUEUE_EXISTING_LOOKUP_CHUNK_SIZE = 500\nQUEUE_DISCOVERY_REFRESH_INTERVAL_SECONDS = 6 * 60 * 60\n_QUEUE_COLUMNS_PER_ROW = 18\n""",
    )
    replace_once(
        bulk,
        """    status: str\n    next_attempt_at: str\n""",
        """    status: str\n    next_attempt_at: str\n    discovery_snapshot_json: str\n    discovery_snapshot_at: str\n""",
    )
    replace_once(
        bulk,
        """    refresh_cutoff = (\n        now - timedelta(seconds=QUEUE_DISCOVERY_REFRESH_INTERVAL_SECONDS)\n    ).isoformat()\n    rows_to_write = [\n""",
        """    refresh_cutoff = (\n        now - timedelta(seconds=QUEUE_DISCOVERY_REFRESH_INTERVAL_SECONDS)\n    ).isoformat()\n    snapshot_refresh_cutoff = (\n        now - timedelta(seconds=DISCOVERY_PROOF_REFRESH_INTERVAL_SECONDS)\n    ).isoformat()\n    rows_to_write = [\n""",
    )
    replace_once(
        bulk,
        """            existing.get(str(row[0])),\n            refresh_cutoff=refresh_cutoff,\n        )\n""",
        """            existing.get(str(row[0])),\n            refresh_cutoff=refresh_cutoff,\n            snapshot_refresh_cutoff=snapshot_refresh_cutoff,\n        )\n""",
    )
    replace_once(
        bulk,
        """                discovered_count, first_seen_at, last_seen_at, status,\n                attempt_count, next_attempt_at\n            ) VALUES {placeholders}\n""",
        """                discovered_count, first_seen_at, last_seen_at, status,\n                attempt_count, next_attempt_at,\n                discovery_snapshot_json, discovery_snapshot_at\n            ) VALUES {placeholders}\n""",
    )
    replace_once(
        bulk,
        """                source_label = excluded.source_label,\n                discovered_count = discovered_count + 1,\n                last_seen_at = excluded.last_seen_at,\n                next_attempt_at = CASE\n                    WHEN status IN ('pending', 'retry', 'failed')\n                    THEN MIN(next_attempt_at, excluded.next_attempt_at)\n                    ELSE next_attempt_at END\n""",
        """                source_label = excluded.source_label,\n                discovered_count = discovered_count + 1,\n                last_seen_at = excluded.last_seen_at,\n                discovery_snapshot_json = CASE\n                    WHEN excluded.discovery_snapshot_json <> ''\n                    THEN excluded.discovery_snapshot_json\n                    ELSE discovery_snapshot_json END,\n                discovery_snapshot_at = CASE\n                    WHEN excluded.discovery_snapshot_json <> ''\n                    THEN excluded.discovery_snapshot_at\n                    ELSE discovery_snapshot_at END,\n                next_attempt_at = CASE\n                    WHEN status IN ('pending', 'retry', 'failed')\n                    THEN MIN(next_attempt_at, excluded.next_attempt_at)\n                    WHEN status = 'page_retry'\n                         AND excluded.discovery_snapshot_json <> ''\n                    THEN MIN(next_attempt_at, excluded.next_attempt_at)\n                    ELSE next_attempt_at END\n""",
    )
    replace_once(
        bulk,
        """                title, product_url, image_url, route_hint, source_label,\n                last_seen_at, status, next_attempt_at\n            FROM {QUEUE_TABLE}\n""",
        """                title, product_url, image_url, route_hint, source_label,\n                last_seen_at, status, next_attempt_at,\n                discovery_snapshot_json, discovery_snapshot_at\n            FROM {QUEUE_TABLE}\n""",
    )
    replace_once(
        bulk,
        """                next_attempt_at=str(\n                    _row_get(row, \"next_attempt_at\", index=12) or \"\"\n                ),\n            )\n""",
        """                next_attempt_at=str(\n                    _row_get(row, \"next_attempt_at\", index=12) or \"\"\n                ),\n                discovery_snapshot_json=str(\n                    _row_get(row, \"discovery_snapshot_json\", index=13) or \"\"\n                ),\n                discovery_snapshot_at=str(\n                    _row_get(row, \"discovery_snapshot_at\", index=14) or \"\"\n                ),\n            )\n""",
    )
    replace_once(
        bulk,
        """def _queue_row_needs_write(\n    row: tuple[Any, ...],\n    existing: _ExistingQueueRow | None,\n    *,\n    refresh_cutoff: str,\n) -> bool:\n""",
        """def _queue_row_needs_write(\n    row: tuple[Any, ...],\n    existing: _ExistingQueueRow | None,\n    *,\n    refresh_cutoff: str,\n    snapshot_refresh_cutoff: str,\n) -> bool:\n""",
    )
    replace_once(
        bulk,
        """    if (\n        existing.status in _REARM_STATUSES\n        and (\n            not existing.next_attempt_at\n            or existing.next_attempt_at > str(row[15] or \"\")\n        )\n    ):\n        return True\n    return False\n""",
        """    incoming_snapshot = str(row[16] or \"\")\n    if incoming_snapshot:\n        if incoming_snapshot != existing.discovery_snapshot_json:\n            return True\n        if (\n            not existing.discovery_snapshot_at\n            or existing.discovery_snapshot_at <= snapshot_refresh_cutoff\n        ):\n            return True\n        if existing.status == \"page_retry\" and (\n            not existing.next_attempt_at\n            or existing.next_attempt_at > str(row[15] or \"\")\n        ):\n            return True\n    if (\n        existing.status in _REARM_STATUSES\n        and (\n            not existing.next_attempt_at\n            or existing.next_attempt_at > str(row[15] or \"\")\n        )\n    ):\n        return True\n    return False\n""",
    )
    replace_once(
        bulk,
        """    route_hint = _compact_text(\n        attrs.get(\"finderSourceQuery\")\n        or attrs.get(\"finderSourceQueries\")\n        or \"\",\n        240,\n    )\n    return (\n""",
        """    route_hint = _compact_text(\n        attrs.get(\"finderSourceQuery\")\n        or attrs.get(\"finderSourceQueries\")\n        or \"\",\n        240,\n    )\n    discovery_snapshot = build_fresh_discovery_offer_snapshot(candidate)\n    return (\n""",
    )
    replace_once(
        bulk,
        """        \"pending\",\n        0,\n        now_iso,\n    )\n""",
        """        \"pending\",\n        0,\n        now_iso,\n        discovery_snapshot,\n        now_iso if discovery_snapshot else None,\n    )\n""",
    )

    drain = "sniperplug/services/walmart_exact_queue_drain.py"
    replace_once(
        drain,
        """            apparent_current_cents, apparent_reference_cents,\n            route_hint, attempt_count\n        FROM {QUEUE_TABLE}\n""",
        """            apparent_current_cents, apparent_reference_cents,\n            route_hint, attempt_count,\n            discovery_snapshot_json, discovery_snapshot_at\n        FROM {QUEUE_TABLE}\n""",
    )
    replace_once(
        drain,
        """                route_hint=str(_row_get(row, \"route_hint\", index=6) or \"\"),\n                lease_token=batch_token,\n                attempt_count=int(_row_get(row, \"attempt_count\", index=7) or 0),\n            )\n""",
        """                route_hint=str(_row_get(row, \"route_hint\", index=6) or \"\"),\n                lease_token=batch_token,\n                attempt_count=int(_row_get(row, \"attempt_count\", index=7) or 0),\n                discovery_snapshot_json=str(\n                    _row_get(row, \"discovery_snapshot_json\", index=8) or \"\"\n                ),\n                discovery_snapshot_at=(\n                    str(_row_get(row, \"discovery_snapshot_at\", index=9) or \"\")\n                    or None\n                ),\n            )\n""",
    )

    runtime = "sniperplug/services/walmart_exact_queue_runtime.py"
    replace_once(
        runtime,
        """from sniperplug.services.walmart_global_offer_memory import (\n    ensure_global_offer_memory_table,\n    exact_offer_identity,\n    maybe_prune_global_offer_memory,\n    observe_exact_offer,\n)\n""",
        """from sniperplug.services.walmart_global_offer_memory import (\n    ensure_global_offer_memory_table,\n    exact_offer_identity,\n    maybe_prune_global_offer_memory,\n    observe_exact_offer,\n)\nfrom sniperplug.services.walmart_fresh_discovery_proof import (\n    promote_fresh_discovery_offer_snapshot,\n)\n""",
    )
    replace_once(
        runtime,
        """    claimed: int = 0\n    verified: int = 0\n    official_references: int = 0\n""",
        """    claimed: int = 0\n    verified: int = 0\n    fresh_catalog_verified: int = 0\n    official_references: int = 0\n""",
    )
    replace_once(
        runtime,
        """            f\"claimed **{self.claimed}** • verified **{self.verified}** • \"\n            f\"official was prices **{self.official_references}** • \"\n""",
        """            f\"claimed **{self.claimed}** • verified **{self.verified}** • \"\n            f\"fresh catalog proof **{self.fresh_catalog_verified}** • \"\n            f\"official was prices **{self.official_references}** • \"\n""",
    )
    replace_once(
        runtime,
        """        \"verified\": 0,\n        \"official_references\": 0,\n""",
        """        \"verified\": 0,\n        \"fresh_catalog_verified\": 0,\n        \"official_references\": 0,\n""",
    )
    replace_once(
        runtime,
        """        counts[\"verified\"] += 1\n        if _trusted_reference(candidate) is not None:\n""",
        """        counts[\"verified\"] += 1\n        if (\n            str(\n                dict(getattr(candidate, \"variant_attributes\", None) or {}).get(\n                    \"exactDetailPriceProofSource\"\n                )\n                or \"\"\n            ).strip()\n            == \"affiliate_catalog_fresh_snapshot\"\n        ):\n            counts[\"fresh_catalog_verified\"] += 1\n        if _trusted_reference(candidate) is not None:\n""",
    )
    replace_once(
        runtime,
        """async def _fetch_exact_candidate_off_loop(\n    provider: Any,\n    claim: Any,\n    *,\n    timeout_seconds: float,\n) -> tuple[SourceCandidate | None, str, str]:\n    detail_fetcher = getattr(provider, \"fetch_product_detail_payload\", None)\n""",
        """async def _fetch_exact_candidate_off_loop(\n    provider: Any,\n    claim: Any,\n    *,\n    timeout_seconds: float,\n) -> tuple[SourceCandidate | None, str, str]:\n    fresh = promote_fresh_discovery_offer_snapshot(\n        str(getattr(claim, \"discovery_snapshot_json\", \"\") or \"\"),\n        snapshot_at=getattr(claim, \"discovery_snapshot_at\", None),\n        item_id=str(getattr(claim, \"item_id\", \"\") or \"\"),\n        apparent_current_cents=getattr(claim, \"apparent_current_cents\", None),\n    )\n    if fresh is not None:\n        return fresh, \"verified\", \"\"\n\n    detail_fetcher = getattr(provider, \"fetch_product_detail_payload\", None)\n""",
    )

    bulk_runtime = "sniperplug/services/walmart_exact_queue_bulk_runtime.py"
    replace_once(
        bulk_runtime,
        """        \"verified\": 0,\n        \"official_references\": 0,\n""",
        """        \"verified\": 0,\n        \"fresh_catalog_verified\": 0,\n        \"official_references\": 0,\n""",
    )
    replace_once(
        bulk_runtime,
        """        counts[\"verified\"] += 1\n        if _trusted_reference(candidate) is not None:\n""",
        """        counts[\"verified\"] += 1\n        if (\n            str(\n                dict(getattr(candidate, \"variant_attributes\", None) or {}).get(\n                    \"exactDetailPriceProofSource\"\n                )\n                or \"\"\n            ).strip()\n            == \"affiliate_catalog_fresh_snapshot\"\n        ):\n            counts[\"fresh_catalog_verified\"] += 1\n        if _trusted_reference(candidate) is not None:\n""",
    )

    memory = "sniperplug/services/walmart_global_offer_memory.py"
    replace_once(
        memory,
        """    Public observed-price proof is allowed only after the exact Walmart detail\n    endpoint verified the item ID. Search-result identity alone is discovery,\n    never historical price proof.\n""",
        """    Public observed-price proof is allowed only after a current official\n    exact-item proof boundary. That may be the detail endpoint or a tightly\n    bounded fresh Affiliate catalog selected-offer snapshot whose item, payable\n    price, seller, offer, variant, and marketplace shipping were revalidated.\n    Ordinary search-result identity alone remains discovery, never price proof.\n""",
    )

    suppression_test = "tests/test_walmart_catalog_queue_write_suppression.py"
    replace_once(
        suppression_test,
        """    assert QUEUE_UPSERT_CHUNK_SIZE == 60\n    assert QUEUE_UPSERT_CHUNK_SIZE * 16 <= 999\n""",
        """    assert QUEUE_UPSERT_CHUNK_SIZE == 55\n    assert QUEUE_UPSERT_CHUNK_SIZE * 18 <= 999\n""",
    )

    active = "ACTIVE_TASK.md"
    replace_once(
        active,
        """**Active — SP-WM-MISSED-DEALS-236. PR #236 Milestone 1 is merged into `main` at `9ca3a32b456eb38fff09396e3aac64649dd9b7dd`, and the real merge commit passed all canonical `main` workflows. The overall task is intentionally NOT closed: live production must now prove that exact-verification throughput materially improves and reveal any remaining concrete retry classes. The Single Active Task Lock remains engaged.**\n""",
        """**Active — SP-WM-MISSED-DEALS-236. PR #236 Milestone 1 passed code validation but FAILED the live throughput gate: production still produced repeated 8/8 transient exact-verification losses, `api_exact_search_no_match` / `page_challenge` dominated, and actionable due climbed to 107. Milestone 2 is implementing bounded fresh official catalog selected-offer proof reuse inside the same canonical exact worker. The Single Active Task Lock remains engaged.**\n""",
    )
    replace_once(
        active,
        """## Safety invariants\n""",
        """## Milestone 2 — fresh official catalog selected-offer proof\n- Live production proved the numeric Affiliate keyword-search fallback is not a reliable exact-item lookup: catalog discovery had just returned the item, but later exact searches frequently returned `api_exact_search_no_match`.\n- The catalog `SourceCandidate` already normalizes selected item ID, selected offer, seller, variant, condition, fulfillment, item price, marketplace shipping, delivered total, and same-offer reference proof. The queue currently discards most of that structure.\n- Milestone 2 persists only a bounded normalized selected-offer snapshot (never the raw Walmart payload) and permits a zero-network fast path only while that snapshot is fresh, exact-item matched, cent-for-cent matched to the queued payable price, seller/offer complete, and marketplace shipping complete.\n- Sibling/cross-offer references remain context only. The Aiho 3-Piece Gray production example is `$73.99 + $19.99 shipping = $93.98 delivered`; sibling variant strike-through prices must not become its reference price.\n- Page/API recovery remains the fallback whenever the fresh snapshot is absent, stale, price-conflicting, variant-ambiguous, or otherwise incomplete. No challenge bypass is introduced.\n\n## Safety invariants\n""",
    )


if __name__ == "__main__":
    main()
