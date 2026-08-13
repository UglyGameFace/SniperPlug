from __future__ import annotations

from pathlib import Path


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def patch_bulk() -> None:
    path = Path("sniperplug/services/walmart_global_deal_fanout_bulk.py")
    text = path.read_text(encoding="utf-8")

    text = replace_once(
        text,
        "import asyncio\nimport logging\nimport time\nimport uuid\n",
        "import asyncio\nimport hashlib\nimport json\nimport logging\nimport time\nimport uuid\n",
        label="bulk imports",
    )
    text = replace_once(
        text,
        "from sniperplug.services.dm_deal_alerts import (\n    clear_dm_delivery_failures,\n    dm_receipt_exists,\n    list_enabled_dm_deal_alert_preferences,\n    record_dm_delivery_failure,\n)\nfrom sniperplug.services.dm_deal_matching import match_dm_deal\n",
        "from sniperplug.services.dm_deal_alerts import (\n    card_search_text,\n    clear_dm_delivery_failures,\n    dm_receipt_exists,\n    list_enabled_dm_deal_alert_preferences,\n    record_dm_delivery_failure,\n    walmart_cash_cents,\n)\nfrom sniperplug.services.dm_deal_matching import match_dm_deal\nfrom sniperplug.services.dm_personal_categories import category_key_for_card\n",
        label="bulk dm imports",
    )
    text = replace_once(
        text,
        "FANOUT_SCHEMA_CLEANUP_INTERVAL_SECONDS = 6 * 60 * 60\n",
        "FANOUT_SCHEMA_CLEANUP_INTERVAL_SECONDS = 6 * 60 * 60\nFANOUT_EVENT_KEY_VERSION = \"v2\"\n",
        label="fanout event version constant",
    )
    text = replace_once(
        text,
        "    @property\n    def dm_filter_rejected(self) -> int:\n        return max(0, int(self.dm_preferences_checked) - int(self.dm_matches))\n\n    def summary_line(self) -> str:\n",
        "    @property\n    def dm_filter_rejected(self) -> int:\n        return max(0, int(self.dm_preferences_checked) - int(self.dm_matches))\n\n    @property\n    def deduped_event_versions(self) -> int:\n        return max(0, int(self.exact_cards) - int(self.new_events))\n\n    def summary_line(self) -> str:\n",
        label="fanout dedupe property",
    )
    text = replace_once(
        text,
        "            f\"loaded **{self.candidates_loaded}** • exact cards **{self.exact_cards}** • \"\n            f\"new events **{self.new_events}** • processed **{self.events_processed}** • \"\n",
        "            f\"loaded **{self.candidates_loaded}** • exact cards **{self.exact_cards}** • \"\n            f\"new event versions **{self.new_events}** • deduped versions **{self.deduped_event_versions}** • \"\n            f\"processed **{self.events_processed}** • \"\n",
        label="fanout summary event version wording",
    )
    text = replace_once(
        text,
        "            for preference in preferences:\n                totals[\"dm_preferences_checked\"] += 1\n",
        "            delivery_deal_key = _delivery_deal_key(event.card)\n            for preference in preferences:\n                totals[\"dm_preferences_checked\"] += 1\n",
        label="stable delivery key placement",
    )
    text = replace_once(
        text,
        "                        if await dm_receipt_exists(\n                            db,\n                            user_id=preference.user_id,\n                            deal_key=event.deal_key,\n                        ):\n",
        "                        if await dm_receipt_exists(\n                            db,\n                            user_id=preference.user_id,\n                            deal_key=delivery_deal_key,\n                        ):\n",
        label="dm receipt read key",
    )
    text = replace_once(
        text,
        "                        await record_dm_delivery_receipt(\n                            db,\n                            user_id=preference.user_id,\n                            deal_key=event.deal_key,\n                            category_family=gate.category_family,\n                        )\n",
        "                        await record_dm_delivery_receipt(\n                            db,\n                            user_id=preference.user_id,\n                            deal_key=delivery_deal_key,\n                            category_family=gate.category_family,\n                        )\n",
        label="dm receipt write key",
    )
    text = replace_once(
        text,
        "        retailer = str(getattr(card, \"retailer\", None) or \"walmart\")\n        deal_key = card_deal_key(card, retailer=retailer)\n        event_rows[deal_key] = (\n",
        "        retailer = str(getattr(card, \"retailer\", None) or \"walmart\")\n        deal_key = _fanout_event_key(card, retailer=retailer)\n        event_rows[deal_key] = (\n",
        label="versioned fanout event key",
    )

    helper_anchor = "\ndef _log_public_skip(\n"
    helpers = r'''

def _delivery_deal_key(card: Any) -> str:
    retailer = str(getattr(card, "retailer", None) or "walmart")
    return card_deal_key(card, retailer=retailer)


def _fanout_event_key(card: Any, *, retailer: str) -> str:
    """Version a fanout event by fields that can change alert eligibility.

    Delivery dedupe deliberately remains the legacy current-price key. This
    versioned event key only controls whether a newly verified proof state is
    re-evaluated by public and personal filters. A subscriber who already
    received the same offer at the same current price therefore remains
    deduped, while a previously rejected same-price offer can be reconsidered
    when its trusted reference, score, category/search text, Walmart Cash, or
    exact selected-offer presentation materially changes.
    """

    delivery_key = card_deal_key(card, retailer=retailer)
    payload = {
        "current_cents": _money_cents(
            getattr(card, "api_current_price", None)
            or getattr(card, "current_price", None)
        ),
        "reference_cents": _money_cents(
            getattr(card, "api_reference_price", None)
            or getattr(card, "typical_price", None)
        ),
        "discount_bps": _percent_bps(
            getattr(card, "api_discount_percent", None)
            or getattr(card, "discount", None)
        ),
        "score": _int_value(getattr(card, "score", None)),
        "category": str(category_key_for_card(card) or "uncategorized"),
        "walmart_cash_cents": int(walmart_cash_cents(card)),
        "search_text": " ".join(card_search_text(card).split())[:8000],
        "should_alert": _optional_bool(getattr(card, "should_alert", None)),
        "seller": _compact(getattr(card, "seller_name", None), 240),
        "variant": _compact(getattr(card, "variant_label", None), 240),
        "condition": _compact(getattr(card, "condition", None), 120),
        "fulfillment": _compact(getattr(card, "fulfillment_type", None), 120),
        "stock": _compact(getattr(card, "stock_status", None), 120),
        "can_add_to_cart": _optional_bool(getattr(card, "can_add_to_cart", None)),
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()[:20]
    return f"{delivery_key}:proof:{FANOUT_EVENT_KEY_VERSION}:{digest}"


def _money_cents(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        amount = float(str(value).replace("$", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    if amount <= 0:
        return None
    return int(round(amount * 100))


def _percent_bps(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(round(float(value) * 100))
    except (TypeError, ValueError):
        return None


def _int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(value)
'''
    text = replace_once(
        text,
        helper_anchor,
        helpers + helper_anchor,
        label="fanout key helpers",
    )

    path.write_text(text, encoding="utf-8")


def patch_runner() -> None:
    path = Path("sniperplug/cogs/global_auto_scan_runner.py")
    text = path.read_text(encoding="utf-8")
    old = """                    if (\n                        fanout.new_events\n                        or fanout.events_processed\n                        or fanout.public_posts\n                        or fanout.dm_sent\n                    ):\n                        legacy.log.info(fanout.summary_line())\n                    await self._fanout_external_verified_events()\n                except asyncio.CancelledError:\n"""
    new = """                    if (\n                        fanout.new_events\n                        or fanout.events_processed\n                        or fanout.public_posts\n                        or fanout.dm_sent\n                        or (health_due and fanout.exact_cards)\n                    ):\n                        legacy.log.info(fanout.summary_line())\n                    await self._fanout_external_verified_events()\n                except asyncio.CancelledError:\n"""
    text = replace_once(
        text,
        old,
        new,
        label="exact worker periodic dedupe telemetry",
    )
    path.write_text(text, encoding="utf-8")


def patch_telemetry() -> None:
    path = Path("sniperplug/services/dm_delivery_telemetry.py")
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        '                "No exact deal events have been evaluated for your personal filter since this bot process started.",\n',
        '                "No new exact fanout event versions have reached your personal filter since this bot process started. Reverified deals already seen at the same alert proof are deduped before per-user filtering.",\n',
        label="delivery health no-event wording",
    )
    path.write_text(text, encoding="utf-8")


def add_regression_tests() -> None:
    path = Path("tests/test_walmart_fanout_event_refresh.py")
    if path.exists():
        raise RuntimeError(f"refusing to overwrite existing {path}")
    path.write_text(
        r'''from __future__ import annotations

import asyncio
from types import SimpleNamespace

import aiosqlite

from sniperplug.services import walmart_global_deal_fanout_bulk as bulk


class Database:
    def __init__(self, conn) -> None:
        self.conn = conn

    def require_conn(self):
        return self.conn


def card(*, reference: float, discount: float, score: int = 100):
    return SimpleNamespace(
        retailer="walmart",
        selected_offer_id="offer-123",
        sku="item-123",
        upc="",
        url="https://www.walmart.com/ip/123",
        current_price=10.0,
        api_current_price=10.0,
        typical_price=reference,
        api_reference_price=reference,
        discount=discount,
        api_discount_percent=discount,
        score=score,
        label="Example exact Walmart item",
        embed=None,
        variant_attributes={},
        seller_name="Walmart.com",
        variant_label="Standard",
        condition="new",
        fulfillment_type="shipping",
        stock_status="available",
        can_add_to_cart=True,
        should_alert=True,
    )


def test_same_current_price_with_new_reference_gets_new_event_version() -> None:
    before = card(reference=15.0, discount=33.33)
    after = card(reference=20.0, discount=50.0)

    assert bulk._delivery_deal_key(before) == bulk._delivery_deal_key(after)
    assert bulk._fanout_event_key(before, retailer="walmart") != bulk._fanout_event_key(
        after,
        retailer="walmart",
    )


def test_same_alert_proof_is_stable_across_reverification() -> None:
    first = card(reference=20.0, discount=50.0, score=145)
    repeated = card(reference=20.0, discount=50.0, score=145)

    assert bulk._fanout_event_key(first, retailer="walmart") == bulk._fanout_event_key(
        repeated,
        retailer="walmart",
    )


def test_score_change_reopens_filter_evaluation_without_changing_delivery_key() -> None:
    low_score = card(reference=20.0, discount=50.0, score=70)
    passing_score = card(reference=20.0, discount=50.0, score=90)

    assert bulk._delivery_deal_key(low_score) == bulk._delivery_deal_key(passing_score)
    assert bulk._fanout_event_key(low_score, retailer="walmart") != bulk._fanout_event_key(
        passing_score,
        retailer="walmart",
    )


def test_bulk_ingest_emits_new_version_when_same_price_crosses_threshold(monkeypatch) -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        await conn.execute(
            f"""
            CREATE TABLE {bulk.QUEUE_TABLE} (
                item_id TEXT PRIMARY KEY,
                verified_at TEXT,
                snapshot_json TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending'
            )
            """
        )
        await conn.commit()
        db = Database(conn)
        await bulk.ensure_global_deal_event_tables_once(db)

        cards = {
            "below": card(reference=15.0, discount=33.33),
            "passing": card(reference=20.0, discount=50.0),
        }
        monkeypatch.setattr(bulk, "_candidate_from_snapshot", lambda value: value)
        monkeypatch.setattr(bulk.legacy, "_exact_card_for_candidate", lambda value: cards[value])

        await conn.execute(
            f"""
            INSERT INTO {bulk.QUEUE_TABLE} (item_id, verified_at, snapshot_json, status)
            VALUES ('123', '2026-08-13T00:00:00+00:00', 'below', 'verified_markdown')
            """
        )
        await conn.commit()
        first = await bulk._ingest_verified_queue_events_bulk(db, limit=10)
        assert first.new_events == 1

        await conn.execute(
            f"""
            UPDATE {bulk.QUEUE_TABLE}
            SET verified_at = '2026-08-13T00:01:00+00:00', snapshot_json = 'passing'
            WHERE item_id = '123'
            """
        )
        await conn.commit()
        second = await bulk._ingest_verified_queue_events_bulk(db, limit=10)
        assert second.new_events == 1

        await conn.execute(
            f"""
            UPDATE {bulk.QUEUE_TABLE}
            SET verified_at = '2026-08-13T00:02:00+00:00'
            WHERE item_id = '123'
            """
        )
        await conn.commit()
        third = await bulk._ingest_verified_queue_events_bulk(db, limit=10)
        assert third.new_events == 0

        count = await (
            await conn.execute(f"SELECT COUNT(*) FROM {bulk.legacy.EVENT_TABLE}")
        ).fetchone()
        assert count == (2,)
        await conn.close()

    asyncio.run(run())


def test_bulk_dm_receipts_use_stable_delivery_key_static() -> None:
    source = open(
        "sniperplug/services/walmart_global_deal_fanout_bulk.py",
        encoding="utf-8",
    ).read()
    assert "delivery_deal_key = _delivery_deal_key(event.card)" in source
    assert source.count("deal_key=delivery_deal_key") == 2
    assert "deal_key=event.deal_key" not in source.split("for preference in preferences:", 1)[1].split(
        "if event_errors:",
        1,
    )[0]
''',
        encoding="utf-8",
    )


def main() -> None:
    patch_bulk()
    patch_runner()
    patch_telemetry()
    add_regression_tests()


if __name__ == "__main__":
    main()
