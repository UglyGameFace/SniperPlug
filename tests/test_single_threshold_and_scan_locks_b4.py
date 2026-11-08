"""Phase B4: single threshold authority + scan locks on the manual commands.

Threshold authority: resolve_min_discount (guild setting, else 40) is the one
place display/public thresholds come from. The public gate, the /deals finder,
and the manual commands must all agree on it.

Scan locks: /deals, /walmart_scan, /check_deal and /hunt (live override in
VerifiedDealScannerCog, plus the base definition) refuse a second concurrent
invocation politely instead of double-running.

/check_deal intake is deliberately NOT part of the authority: verification
stays at MANUAL_INTAKE_MIN_DISCOUNT (10%); only its display threshold goes
through resolve_min_discount.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace

import aiosqlite
import discord
import pytest

import sniperplug.cogs.deal_scanner as deal_scanner_mod
from sniperplug.cogs.deal_scanner import (
    DUPLICATE_SCAN_MESSAGE,
    DealCard,
    DealScannerCog,
)
from sniperplug.cogs.verified_deal_scanner import VerifiedDealScannerCog
from sniperplug.providers.base import ProviderStatus
from sniperplug.services.deal_finder_engine import find_walmart_deals_for_query
from sniperplug.services.deal_threshold_settings import (
    DEFAULT_STARTING_DEAL_PERCENT,
    resolve_min_discount,
    set_starting_deal_percent,
)
from sniperplug.services.fresh_deal_filter import select_fresh_deal_cards
from sniperplug.services.public_deal_posts import (
    cache_active_deal_cards,
    maybe_post_public_deal_cards,
)
from sniperplug.services.public_deal_quality import (
    has_verified_api_threshold_discount,
    is_public_deal_candidate,
    prepare_public_deal_candidate,
    public_deal_rejection_reason,
    select_public_deal_candidates,
)
from sniperplug.services.public_quality_diagnostics import public_quality_block_reason
from sniperplug.services.scan_locks import ScanLockKey, scan_operation_locks
from sniperplug.services.walmart_exact_verification_queue import (
    MANUAL_INTAKE_MIN_DISCOUNT,
)

IPHONE_LINK = "https://www.walmart.com/ip/AT-T-Apple-iPhone-11-64GB-Green/425031866"


class FakeDatabase:
    def __init__(self, conn):
        self.conn = conn

    def require_conn(self):
        return self.conn


class FakeResponse:
    def __init__(self):
        self._done = False
        self.sent: list[tuple[tuple, dict]] = []
        self.deferred = False

    def is_done(self):
        return self._done

    async def defer(self, *args, **kwargs):
        self._done = True
        self.deferred = True

    async def send_message(self, *args, **kwargs):
        self._done = True
        self.sent.append((args, kwargs))

    async def edit_message(self, *args, **kwargs):
        self.sent.append((args, kwargs))


class FakeFollowup:
    def __init__(self):
        self.calls: list[tuple[tuple, dict]] = []

    async def send(self, *args, **kwargs):
        self.calls.append((args, kwargs))


class FakeInteraction:
    def __init__(self, *, guild_id=11, user_id=7):
        self.guild_id = guild_id
        self.user = SimpleNamespace(id=user_id)
        self.response = FakeResponse()
        self.followup = FakeFollowup()
        self.client = SimpleNamespace(db=None)
        self.id = 123456789
        self.message = None

    def sent_text(self) -> str:
        parts: list[str] = []
        for args, kwargs in list(self.response.sent) + list(self.followup.calls):
            parts.extend(str(arg) for arg in args)
            content = kwargs.get("content")
            if content:
                parts.append(str(content))
        return "\n".join(parts)


def public_gate_card(*, discount=45, price=10.0, score=150):
    return SimpleNamespace(
        discount=discount,
        current_price=price,
        score=score,
        label="Trusted API markdown",
        url="https://www.walmart.com/ip/123",
        embed=None,
    )


async def open_db():
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    return conn, FakeDatabase(conn)


# --- (1) Single authority -------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_min_discount_defaults_to_40_without_setting():
    assert DEFAULT_STARTING_DEAL_PERCENT == 40
    assert await resolve_min_discount(None, None) == 40
    assert await resolve_min_discount(None, 123) == 40
    conn, db = await open_db()
    try:
        assert await resolve_min_discount(db, 999) == 40
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_resolve_min_discount_prefers_guild_setting():
    conn, db = await open_db()
    try:
        await set_starting_deal_percent(db, 42, 60)
        assert await resolve_min_discount(db, 42) == 60
        assert await resolve_min_discount(db, 43) == 40
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_resolve_min_discount_explicit_wins_over_guild_setting():
    conn, db = await open_db()
    try:
        await set_starting_deal_percent(db, 42, 60)
        assert await resolve_min_discount(db, 42, explicit=25) == 25
        assert await resolve_min_discount(db, 42, explicit=None) == 60
    finally:
        await conn.close()


def test_public_gate_default_is_40_not_50():
    # A 45% verified markdown passes the public gate with no explicit
    # threshold (default 40) and fails when 50 is asked for explicitly.
    card = public_gate_card(discount=45)
    assert is_public_deal_candidate(card, source_label="deals") is True
    assert is_public_deal_candidate(card, source_label="deals", min_discount=50) is False
    assert (
        is_public_deal_candidate(public_gate_card(discount=35), source_label="deals")
        is False
    )


# --- (2) No hardcoded 50%/30% defaults in the call sites ------------------


def test_public_gate_and_finder_defaults_are_the_single_authority():
    for fn in (
        public_deal_rejection_reason,
        has_verified_api_threshold_discount,
        is_public_deal_candidate,
        prepare_public_deal_candidate,
        select_public_deal_candidates,
    ):
        assert (
            inspect.signature(fn).parameters["min_discount"].default
            == DEFAULT_STARTING_DEAL_PERCENT
        )
    assert (
        inspect.signature(maybe_post_public_deal_cards)
        .parameters["min_public_discount"]
        .default
        == DEFAULT_STARTING_DEAL_PERCENT
    )
    assert (
        inspect.signature(cache_active_deal_cards)
        .parameters["min_discount"]
        .default
        == DEFAULT_STARTING_DEAL_PERCENT
    )
    assert (
        inspect.signature(find_walmart_deals_for_query)
        .parameters["min_discount"]
        .default
        == DEFAULT_STARTING_DEAL_PERCENT
    )
    assert (
        inspect.signature(select_fresh_deal_cards)
        .parameters["min_public_discount"]
        .default
        == DEFAULT_STARTING_DEAL_PERCENT
    )
    assert (
        inspect.signature(public_quality_block_reason)
        .parameters["min_discount"]
        .default
        == DEFAULT_STARTING_DEAL_PERCENT
    )


def test_no_hardcoded_50_or_30_defaults_in_threshold_call_sites():
    quality = Path("sniperplug/services/public_deal_quality.py").read_text(
        encoding="utf-8"
    )
    posts = Path("sniperplug/services/public_deal_posts.py").read_text(encoding="utf-8")
    finder = Path("sniperplug/services/deal_finder_engine.py").read_text(
        encoding="utf-8"
    )
    scanner = Path("sniperplug/cogs/deal_scanner.py").read_text(encoding="utf-8")
    for source in (quality, posts, finder):
        assert "min_discount: int = 50" not in source
        assert "min_public_discount: int = 50" not in source
        assert "min_discount=50" not in source
    # /deals no longer passes a hardcoded 50 to the scan.
    assert "await self._send_walmart_scan(interaction, search, 50," not in scanner
    # /walmart_scan no longer defaults to 30.
    assert "app_commands.Range[int, 0, 95] = 30" not in scanner
    # The /deals fallback ladder no longer starts at a hardcoded 50.
    assert "BEGINNER_FALLBACK_DISCOUNTS = (50, 30, 10, 0)" not in scanner


# --- (1) Commands resolve through the authority ----------------------------


@pytest.mark.asyncio
async def test_deals_passes_guild_threshold_and_defaults_to_40():
    await scan_operation_locks.clear()
    conn, db = await open_db()
    try:
        await set_starting_deal_percent(db, 11, 60)
        cog = DealScannerCog(SimpleNamespace(db=db))
        seen: dict = {}

        async def fake_send(interaction, query, min_discount, *rest):
            seen["query"] = query
            seen["min_discount"] = min_discount

        cog._send_walmart_scan = fake_send  # type: ignore[method-assign]
        await cog.deals.callback(cog, FakeInteraction(guild_id=11), search="oled tv")
        assert seen == {"query": "oled tv", "min_discount": 60}

        # No guild setting: the resolved value is 40, not the old 50.
        conn2, db2 = await open_db()
        try:
            cog2 = DealScannerCog(SimpleNamespace(db=db2))
            seen2: dict = {}

            async def fake_send2(interaction, query, min_discount, *rest):
                seen2["min_discount"] = min_discount

            cog2._send_walmart_scan = fake_send2  # type: ignore[method-assign]
            await cog2.deals.callback(
                cog2, FakeInteraction(guild_id=99), search="lego"
            )
            assert seen2 == {"min_discount": 40}
        finally:
            await conn2.close()
    finally:
        await conn.close()
        await scan_operation_locks.clear()


@pytest.mark.asyncio
async def test_walmart_scan_omitted_threshold_resolves_to_40_explicit_overrides():
    await scan_operation_locks.clear()
    conn, db = await open_db()
    try:
        cog = DealScannerCog(SimpleNamespace(db=db))
        seen: dict = {}

        async def fake_send(interaction, query, min_discount, *rest):
            seen["min_discount"] = min_discount

        cog._send_walmart_scan = fake_send  # type: ignore[method-assign]
        await cog.walmart_scan.callback(
            cog, FakeInteraction(guild_id=11), query="lego"
        )
        assert seen == {"min_discount": 40}

        await cog.walmart_scan.callback(
            cog, FakeInteraction(guild_id=11), query="lego", min_discount=25
        )
        assert seen == {"min_discount": 25}
    finally:
        await conn.close()
        await scan_operation_locks.clear()


# --- (3) Second concurrent invocation is refused, no double-run ------------


@pytest.mark.asyncio
async def test_second_concurrent_deals_is_refused_and_does_not_double_run():
    await scan_operation_locks.clear()
    try:
        cog = DealScannerCog(SimpleNamespace(db=None))
        calls: list = []

        async def fake_send(*args, **kwargs):
            calls.append(args)

        cog._send_walmart_scan = fake_send  # type: ignore[method-assign]
        key = ScanLockKey(guild_id=11, user_id=7, action="deals", query="oled tv")
        assert await scan_operation_locks.acquire(key) is True

        interaction = FakeInteraction(guild_id=11, user_id=7)
        await cog.deals.callback(cog, interaction, search="oled tv")
        assert calls == []
        assert DUPLICATE_SCAN_MESSAGE in interaction.sent_text()

        await scan_operation_locks.release(key)
        await cog.deals.callback(
            cog, FakeInteraction(guild_id=11, user_id=7), search="oled tv"
        )
        assert len(calls) == 1
    finally:
        await scan_operation_locks.clear()


@pytest.mark.asyncio
async def test_second_concurrent_walmart_scan_is_refused():
    await scan_operation_locks.clear()
    try:
        conn, db = await open_db()
        try:
            cog = DealScannerCog(SimpleNamespace(db=db))
            calls: list = []

            async def fake_send(*args, **kwargs):
                calls.append(args)

            cog._send_walmart_scan = fake_send  # type: ignore[method-assign]
            key = ScanLockKey(
                guild_id=11,
                user_id=7,
                action="walmart_scan",
                query="lego",
                page=1,
                min_discount=None,
            )
            assert await scan_operation_locks.acquire(key) is True

            interaction = FakeInteraction(guild_id=11, user_id=7)
            await cog.walmart_scan.callback(cog, interaction, query="lego")
            assert calls == []
            assert DUPLICATE_SCAN_MESSAGE in interaction.sent_text()

            await scan_operation_locks.release(key)
            await cog.walmart_scan.callback(
                cog, FakeInteraction(guild_id=11, user_id=7), query="lego"
            )
            assert len(calls) == 1
        finally:
            await conn.close()
    finally:
        await scan_operation_locks.clear()


@pytest.mark.asyncio
async def test_second_concurrent_check_deal_is_refused():
    await scan_operation_locks.clear()
    try:
        cog = DealScannerCog(SimpleNamespace(db=None))
        calls: list = []

        async def fake_check(interaction, link):
            calls.append(link)

        cog._send_check_deal = fake_check  # type: ignore[method-assign]
        key = ScanLockKey(
            guild_id=11, user_id=7, action="check_deal", query="425031866"
        )
        assert await scan_operation_locks.acquire(key) is True

        interaction = FakeInteraction(guild_id=11, user_id=7)
        await cog.check_deal.callback(cog, interaction, link=IPHONE_LINK)
        assert calls == []
        assert DUPLICATE_SCAN_MESSAGE in interaction.sent_text()

        await scan_operation_locks.release(key)
        await cog.check_deal.callback(
            cog, FakeInteraction(guild_id=11, user_id=7), link=IPHONE_LINK
        )
        assert calls == [IPHONE_LINK]
    finally:
        await scan_operation_locks.clear()


@pytest.mark.asyncio
async def test_second_concurrent_hunt_is_refused_on_live_override_and_base():
    await scan_operation_locks.clear()
    try:
        key = ScanLockKey(guild_id=11, user_id=7, action="hunt")
        assert await scan_operation_locks.acquire(key) is True

        # Live command: the VerifiedDealScannerCog override.
        vcog = VerifiedDealScannerCog(SimpleNamespace(db=None))
        interaction = FakeInteraction(guild_id=11, user_id=7)
        await vcog.hunt.callback(vcog, interaction)
        assert DUPLICATE_SCAN_MESSAGE in interaction.sent_text()
        assert not any(
            "embed" in kwargs for _, kwargs in interaction.response.sent
        )

        # Base definition carries the same lock.
        cog = DealScannerCog(SimpleNamespace(db=None))
        interaction2 = FakeInteraction(guild_id=11, user_id=7)
        await cog.hunt.callback(cog, interaction2)
        assert DUPLICATE_SCAN_MESSAGE in interaction2.sent_text()

        await scan_operation_locks.release(key)
        interaction3 = FakeInteraction(guild_id=11, user_id=7)
        await vcog.hunt.callback(vcog, interaction3)
        assert DUPLICATE_SCAN_MESSAGE not in interaction3.sent_text()
        assert any("embed" in kwargs for _, kwargs in interaction3.response.sent)
    finally:
        await scan_operation_locks.clear()


# --- (4) /check_deal intake stays at 10%, display via the authority -------


class FakeReadyProvider:
    async def healthcheck(self):
        return SimpleNamespace(status=ProviderStatus.READY)


@pytest.mark.asyncio
async def test_check_deal_verification_gate_stays_10_and_display_uses_authority(
    monkeypatch,
):
    assert MANUAL_INTAKE_MIN_DISCOUNT == 10
    await scan_operation_locks.clear()
    conn, db = await open_db()
    try:
        await set_starting_deal_percent(db, 11, 60)
        cog = DealScannerCog(SimpleNamespace(db=db))
        seen_verify: dict = {}
        seen_cards: dict = {}

        async def fake_verify(db_arg, provider_arg, item_id, min_discount):
            seen_verify["min_discount"] = min_discount
            seen_verify["item_id"] = item_id
            candidate = SimpleNamespace(
                title="Apple iPhone 11 - AT&T - 64GB Green",
                api_current_price=1.00,
                api_reference_price=650.00,
                current_price=1.00,
                typical_price=650.00,
            )
            return SimpleNamespace(
                candidate=candidate,
                classification="verified_markdown",
                status="verified",
                enqueued=False,
                error=None,
            )

        def fake_build_cards(result, min_discount, alerts_only):
            seen_cards["min_discount"] = min_discount
            return [
                DealCard(
                    embed=discord.Embed(title="t"),
                    url="https://example.com",
                    label="t",
                )
            ]

        async def fake_send_batches(*args, **kwargs):
            return None

        monkeypatch.setattr(
            deal_scanner_mod,
            "provider_registry",
            SimpleNamespace(get=lambda key: FakeReadyProvider()),
        )
        monkeypatch.setattr(
            deal_scanner_mod, "verify_single_exact_item", fake_verify
        )
        monkeypatch.setattr(
            deal_scanner_mod, "build_walmart_cards", fake_build_cards
        )
        monkeypatch.setattr(
            deal_scanner_mod, "send_summary_and_card_batches", fake_send_batches
        )

        await cog.check_deal.callback(
            cog, FakeInteraction(guild_id=11), link=IPHONE_LINK
        )
        assert seen_verify == {"min_discount": 10, "item_id": "425031866"}
        assert seen_cards == {"min_discount": 60}

        # No guild setting: display falls back to 40, intake still 10.
        conn2, db2 = await open_db()
        try:
            cog2 = DealScannerCog(SimpleNamespace(db=db2))
            await cog2.check_deal.callback(
                cog2, FakeInteraction(guild_id=99), link=IPHONE_LINK
            )
            assert seen_verify == {"min_discount": 10, "item_id": "425031866"}
            assert seen_cards == {"min_discount": 40}
        finally:
            await conn2.close()
    finally:
        await conn.close()
        await scan_operation_locks.clear()
