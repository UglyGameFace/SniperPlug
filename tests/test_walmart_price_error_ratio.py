from __future__ import annotations

"""Price errors are relative: a $600 TV at $49 must alert even though it is
above the absolute $10 extreme ceiling, while ordinary markdowns above $10
must not."""

import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

import aiosqlite

from sniperplug.services import walmart_extreme_price_fanout as extreme
from sniperplug.services.autoscan_live_guild_reconciliation import (
    LiveAutoScanGuild,
    LiveGuildLoadResult,
)
from sniperplug.services.walmart_exact_verification_queue import (
    QUEUE_TABLE,
    _candidate_snapshot,
    ensure_walmart_exact_verification_queue,
)
from sniperplug.services.walmart_extreme_price_signals import (
    is_price_error_ratio,
    is_priority_extreme_discovery,
)
from tests.test_walmart_extreme_price_alerts_295 import candidate


def priced(*, title: str, price: float, reference: float | None, trusted: bool = True):
    base = candidate(title=title, price=price)
    attrs = dict(base.variant_attributes)
    if reference is not None:
        attrs["referencePriceTrusted"] = "yes" if trusted else "no"
        attrs["trustedReferencePrice"] = f"{reference:.2f}"
    return replace(
        base,
        api_reference_price=reference,
        typical_price=reference,
        variant_attributes=attrs,
    )


TV = 'onn. 65" Class 4K UHD LED Roku Smart Television'
COLOGNE = "Versace Eros Eau de Toilette Spray, Cologne for Men, 3.4 oz"


def test_ratio_helper_boundaries() -> None:
    assert is_price_error_ratio(49.0, 600.0)
    assert is_price_error_ratio(20.0, 100.0)  # exactly 20%
    assert not is_price_error_ratio(20.01, 100.0)
    assert not is_price_error_ratio(5.0, 49.99)  # reference too small
    assert not is_price_error_ratio(0, 100.0)
    assert not is_price_error_ratio(10.0, None)


def test_expensive_tv_price_error_above_ten_dollars_qualifies() -> None:
    tv = priced(title=TV, price=49.0, reference=598.0)
    assert extreme.extreme_rejection_reason(tv) is None
    card = extreme.build_extreme_price_card(tv)
    assert card is not None and extreme.valid_extreme_price_card(card)
    text = str(card.embed.to_dict())
    assert "Possible price error" in text
    assert "$598.00" in text and "$49.00" in text
    assert "no comparison price or discount percentage is claimed" not in text.lower()


def test_cologne_price_error_qualifies_outside_hardware_types() -> None:
    cologne = priced(title=COLOGNE, price=12.0, reference=110.0)
    assert extreme.extreme_rejection_reason(cologne) is None


def test_ordinary_markdown_above_ten_dollars_is_rejected() -> None:
    tv = priced(title=TV, price=299.0, reference=598.0)  # 50% off: a sale, not an error
    assert extreme.extreme_rejection_reason(tv) == "price_mismatch_or_over_limit"


def test_untrusted_or_missing_reference_never_rescues_items_above_ten_dollars() -> None:
    assert extreme.extreme_rejection_reason(
        priced(title=TV, price=49.0, reference=598.0, trusted=False)
    ) == "price_mismatch_or_over_limit"
    assert extreme.extreme_rejection_reason(
        priced(title=TV, price=49.0, reference=None)
    ) == "price_mismatch_or_over_limit"


def test_ratio_path_keeps_accessory_stock_and_proof_gates() -> None:
    tv = priced(title=TV, price=49.0, reference=598.0)
    assert extreme.extreme_rejection_reason(
        replace(tv, title="65 inch TV Wall Mount")
    ) == "accessory_or_parts"
    assert extreme.extreme_rejection_reason(
        replace(tv, stock_status="Out of stock")
    ) == "unavailable_stock"
    assert extreme.extreme_rejection_reason(
        replace(tv, can_add_to_cart=False)
    ) == "not_cart_buyable"
    assert extreme.extreme_rejection_reason(
        replace(tv, current_price=59.0)
    ) == "price_mismatch_or_over_limit"


def test_discovery_priority_for_ratio_errors_skips_accessories() -> None:
    tv = priced(title=TV, price=49.0, reference=598.0)
    assert is_priority_extreme_discovery(tv, current_price=49.0, trusted_reference=598.0)
    assert not is_priority_extreme_discovery(
        replace(tv, title="TV Wall Mount"), current_price=49.0, trusted_reference=598.0,
    )
    assert not is_priority_extreme_discovery(tv, current_price=299.0, trusted_reference=598.0)


def test_fanout_sql_selects_ratio_errors_above_ten_dollars(monkeypatch) -> None:
    async def scenario():
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        db = SimpleNamespace(require_conn=lambda: conn)
        try:
            await ensure_walmart_exact_verification_queue(db)
            await extreme.ensure_extreme_tables(db)
            await extreme.set_extreme_alerts_enabled(db, 306, True)
            now = datetime.now(timezone.utc).isoformat()
            glitch = priced(title=TV, price=49.0, reference=598.0)
            sale = replace(
                priced(title=TV, price=299.0, reference=598.0),
                product_url="https://www.walmart.com/ip/29500012",
                direct_product_url="https://www.walmart.com/ip/29500012",
                product_id="29500012", sku="29500012",
                variant_attributes={
                    **priced(title=TV, price=299.0, reference=598.0).variant_attributes,
                    "exactDetailItemId": "29500012",
                },
            )
            for item_id, cand, cur, ref in (
                ("29500011", glitch, 4900, 59800),
                ("29500012", sale, 29900, 59800),
            ):
                await conn.execute(
                    f"INSERT INTO {QUEUE_TABLE} "
                    "(item_id, first_seen_at, last_seen_at, next_attempt_at, status, "
                    "verified_at, exact_current_cents, exact_reference_cents, snapshot_json) "
                    "VALUES (?, ?, ?, ?, 'verified_reference', ?, ?, ?, ?)",
                    (item_id, now, now, now, now, cur, ref, _candidate_snapshot(cand)),
                )
            await conn.commit()

            async def fake_live(db_arg, bot_arg, *, required_retailer):
                return LiveGuildLoadResult(guilds=(LiveAutoScanGuild(306, 123),))

            posted_titles: list[str] = []

            async def fake_post(**kwargs):
                for card in kwargs.get("cards", ()):
                    posted_titles.append(str(card.embed.title))
                return SimpleNamespace(posted=len(kwargs.get("cards", ())), errors=())

            monkeypatch.setattr(extreme, "list_live_public_alert_guilds", fake_live)
            monkeypatch.setattr(extreme, "maybe_post_public_deal_cards", fake_post)
            result = await extreme.fanout_recent_extreme_walmart_prices(SimpleNamespace(db=db))
            assert result["examined"] == 1  # the 50%-off sale is filtered in SQL
            assert result["posted"] == 1
            assert posted_titles and posted_titles[0].startswith("Possible price error")
        finally:
            await conn.close()

    asyncio.run(scenario())
