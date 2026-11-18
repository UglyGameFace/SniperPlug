"""Guaranteed-deals refresh (Felix's directive, 2026-11-18): every deal
display gets the Find standard from PR #339 — prices re-verified at
display time through the exact path (verify_single_exact_item), a bounded
pool verified in ranked order, only cards that hold are shown, and the
"re-checked just now" claim appears ONLY when verification actually ran.

Surfaces covered here:
- the shared helper's pool/show parameters (Hunt pool 12 vs Find pool 8),
- /walmart hunt presets in deal_scanner (HuntPresetButton),
- the "Hunt 80%+" button path (DealSearchControlView._hunt_pages),
- the live unified Find display (UnifiedDealScannerCog._send_unified_deal_search)
  and its mode-switch re-display (DealSearchModeView.show_mode),
- the live verified hunt (verified_hunt_button_callback).
"""

from __future__ import annotations

from types import SimpleNamespace

import discord
import pytest

from sniperplug.cogs import deal_scanner, unified_deal_scanner
from sniperplug.cogs.deal_scanner import (
    DealCard,
    DealSearchControlView,
    HUNT_PRESETS,
    HuntPresetButton,
    verify_find_cards,
)
from sniperplug.cogs.unified_deal_scanner import DealSearchModeView, UnifiedDealScannerCog
from sniperplug.models.candidate import SourceCandidate
from sniperplug.providers.base import ProviderScanResult, ProviderStatus
from sniperplug.services import deal_finder_engine, verified_discount_hunt
from sniperplug.services.deal_finder_engine import DealFinderResult
from sniperplug.services.search_expansion import expand_walmart_query
from sniperplug.services.verified_discount_hunt import VerifiedHuntResult
from sniperplug.services.walmart_review_candidates import ReviewCandidateResult


# ---------------------------------------------------------------------------
# Shared fakes (same shapes as tests/test_find_verified_display.py)
# ---------------------------------------------------------------------------


def _card(item_id: str, current: float, reference: float, discount: float) -> DealCard:
    card = DealCard(
        embed=discord.Embed(title=f"Item {item_id}"),
        url=f"https://www.walmart.com/ip/Item-{item_id}/{item_id}",
        label=f"Item {item_id}",
        discount=discount,
        api_current_price=current,
        api_reference_price=reference,
    )
    card.sku = item_id
    card.current_price = current
    card.typical_price = reference
    card.selected_offer_id = item_id
    return card


def _candidate(current: float | None, reference: float | None):
    return SimpleNamespace(
        api_current_price=current,
        current_price=current,
        api_reference_price=reference,
        typical_price=reference,
    )


def _outcome(current: float | None, reference: float | None, classification: str = "verified_markdown"):
    return SimpleNamespace(
        candidate=_candidate(current, reference),
        classification=classification,
        status="verified",
    )


def _unavailable_outcome():
    return SimpleNamespace(candidate=None, classification=None, status="provider_unavailable")


class FakeExactVerify:
    """Counting stand-in for deal_scanner.verify_single_exact_item."""

    def __init__(self, outcomes: dict[str, object]):
        self.outcomes = outcomes
        self.calls: list[str] = []

    async def __call__(self, db, provider, item_id, *, min_discount=None, timeout_seconds=None):
        self.calls.append(item_id)
        outcome = self.outcomes[item_id]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class RecordingFollowup:
    def __init__(self):
        self.calls: list[dict] = []

    async def send(self, *args, **kwargs):
        self.calls.append({"args": args, **kwargs})
        return None


def _field_names(embed: discord.Embed) -> list[str]:
    return [field.name for field in embed.fields]


def _field_values(embed: discord.Embed) -> list[str]:
    return [str(field.value) for field in embed.fields]


def _assert_no_rechecked_claim(embed: discord.Embed) -> None:
    assert "✅ Prices re-checked just now" not in _field_names(embed)
    for value in _field_values(embed):
        assert "was re-checked against Walmart just now" not in value


async def _fake_lock(*args, **kwargs):
    return True


async def _fake_health_ok():
    return None


async def _fake_public_post(**kwargs):
    return SimpleNamespace(any_activity=False, posted=0, skipped_duplicate=0, skipped_not_alertable=0)


# ---------------------------------------------------------------------------
# Helper parameters: one implementation, per-surface pool sizes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hunt_pool_cap_is_twelve_verify_calls():
    cards = [_card(str(100 + i), 50.0, 100.0, 50.0) for i in range(14)]
    outcomes = {}
    for i in range(11):
        outcomes[str(100 + i)] = _outcome(95.0, 100.0, classification="verified_under_threshold")
    for i in range(11, 14):
        outcomes[str(100 + i)] = _outcome(50.0, 100.0)
    fake = FakeVerifyAdapter(outcomes)
    shown, ran, dropped = await verify_find_cards(
        cards, provider=None, threshold=50, verify_one=fake, pool_size=12, show_limit=5
    )
    assert fake.calls == [str(100 + i) for i in range(12)]
    assert shown == [cards[11]]
    assert ran is True
    assert dropped == 11


@pytest.mark.asyncio
async def test_show_limit_parameter_lets_hunt_keep_up_to_twelve():
    cards = [_card(str(200 + i), 50.0, 100.0, 50.0) for i in range(9)]
    outcomes = {str(200 + i): _outcome(50.0, 100.0) for i in range(9)}
    fake = FakeVerifyAdapter(outcomes)
    shown, ran, dropped = await verify_find_cards(
        cards, provider=None, threshold=50, verify_one=fake, pool_size=12, show_limit=12
    )
    assert shown == cards
    assert len(fake.calls) == 9
    assert ran is True
    assert dropped == 0


@pytest.mark.asyncio
async def test_default_pool_is_still_eight_for_find():
    cards = [_card(str(300 + i), 50.0, 100.0, 50.0) for i in range(10)]
    outcomes = {}
    for i in range(7):
        outcomes[str(300 + i)] = _outcome(95.0, 100.0, classification="verified_under_threshold")
    outcomes["307"] = _outcome(50.0, 100.0)
    outcomes["308"] = _outcome(50.0, 100.0)
    outcomes["309"] = _outcome(50.0, 100.0)
    fake = FakeVerifyAdapter(outcomes)
    shown, ran, dropped = await verify_find_cards(cards, provider=None, threshold=50, verify_one=fake)
    assert fake.calls == [str(300 + i) for i in range(8)]
    assert shown == [cards[7]]
    assert ran is True
    assert dropped == 7


class FakeVerifyAdapter:
    """verify_one-shaped adapter over outcome dicts (single item_id arg)."""

    def __init__(self, outcomes: dict[str, object]):
        self.outcomes = outcomes
        self.calls: list[str] = []

    async def __call__(self, item_id: str):
        self.calls.append(item_id)
        outcome = self.outcomes[item_id]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


# ---------------------------------------------------------------------------
# Hunt presets (deal_scanner.HuntPresetButton)
# ---------------------------------------------------------------------------


def _preset_interaction() -> SimpleNamespace:
    return SimpleNamespace(
        id=987654321098,
        guild_id=None,
        user=SimpleNamespace(id=42),
        followup=RecordingFollowup(),
        client=None,
    )


@pytest.mark.asyncio
async def test_preset_hunt_drops_card_that_fails_recheck(monkeypatch):
    preset = HUNT_PRESETS["tech"]
    bad = _card("222", 50.0, 100.0, 50.0)
    good = _card("111", 50.0, 100.0, 50.0)

    async def fake_run_preset_hunt(preset_arg, requested_by):
        return [bad, good], 5, 100, [], 50

    captured: dict = {}

    async def fake_send(interaction, *, summary, cards, view_factory, ephemeral):
        captured["summary"] = summary
        captured["cards"] = cards

    fake_exact = FakeExactVerify(
        {
            "111": _outcome(50.0, 100.0),
            "222": _outcome(95.0, 100.0, classification="verified_under_threshold"),
        }
    )
    monkeypatch.setattr(deal_scanner, "run_preset_hunt", fake_run_preset_hunt)
    monkeypatch.setattr(deal_scanner, "acquire_scan_lock", _fake_lock)
    monkeypatch.setattr(deal_scanner, "provider_health_error_message", _fake_health_ok)
    monkeypatch.setattr(deal_scanner, "provider_registry", SimpleNamespace(get=lambda key: object()))
    monkeypatch.setattr(deal_scanner, "verify_single_exact_item", fake_exact)
    monkeypatch.setattr(deal_scanner, "maybe_post_public_deal_cards", _fake_public_post)
    monkeypatch.setattr(deal_scanner, "send_summary_and_card_batches", fake_send)

    button = HuntPresetButton(preset, row=0)
    await button.callback(_preset_interaction())

    assert fake_exact.calls == ["222", "111"]
    assert captured["cards"] == [good]
    assert "✅ Prices re-checked just now" in _field_names(captured["summary"])
    assert any("didn't hold up on re-check" in value for value in _field_values(captured["summary"]))


@pytest.mark.asyncio
async def test_preset_hunt_unavailable_falls_back_without_claim(monkeypatch):
    preset = HUNT_PRESETS["tech"]
    first = _card("111", 50.0, 100.0, 50.0)
    second = _card("222", 50.0, 100.0, 50.0)

    async def fake_run_preset_hunt(preset_arg, requested_by):
        return [first, second], 5, 100, [], 50

    captured: dict = {}

    async def fake_send(interaction, *, summary, cards, view_factory, ephemeral):
        captured["summary"] = summary
        captured["cards"] = cards

    fake_exact = FakeExactVerify({"111": _unavailable_outcome(), "222": _unavailable_outcome()})
    monkeypatch.setattr(deal_scanner, "run_preset_hunt", fake_run_preset_hunt)
    monkeypatch.setattr(deal_scanner, "acquire_scan_lock", _fake_lock)
    monkeypatch.setattr(deal_scanner, "provider_health_error_message", _fake_health_ok)
    monkeypatch.setattr(deal_scanner, "provider_registry", SimpleNamespace(get=lambda key: object()))
    monkeypatch.setattr(deal_scanner, "verify_single_exact_item", fake_exact)
    monkeypatch.setattr(deal_scanner, "maybe_post_public_deal_cards", _fake_public_post)
    monkeypatch.setattr(deal_scanner, "send_summary_and_card_batches", fake_send)

    button = HuntPresetButton(preset, row=0)
    await button.callback(_preset_interaction())

    # The exact path is down: one attempt, then the search-proven cards are
    # shown unchanged with the unavailable note and no re-checked claim.
    assert fake_exact.calls == ["111"]
    assert captured["cards"] == [first, second]
    assert "Price re-check unavailable" in _field_names(captured["summary"])
    _assert_no_rechecked_claim(captured["summary"])


# ---------------------------------------------------------------------------
# "Hunt 80%+" button path (DealSearchControlView._hunt_pages)
# ---------------------------------------------------------------------------


def _scan_candidate(item_id: str, current: float, typical: float) -> SourceCandidate:
    return SourceCandidate(
        source_key="walmart",
        retailer="Walmart",
        title=f"Gadget {item_id}",
        product_url=f"https://www.walmart.com/ip/{item_id}",
        current_price=current,
        typical_price=typical,
        product_id=item_id,
        selected_offer_id=item_id,
        seller_name="Walmart",
    )


def _hunt_view() -> DealSearchControlView:
    return DealSearchControlView("gadget", 1, 40, 10, None, None, False, True)


def _patch_hunt_pages(monkeypatch, *, scan_results, outcomes):
    captured: dict = {"sent": []}

    async def fake_scan(query, page, max_results, sort_value, order_value, requested_by):
        return scan_results[page]

    async def fake_send(interaction, *, summary, cards, view_factory, ephemeral):
        captured["sent"].append({"summary": summary, "cards": cards})

    fake_exact = FakeExactVerify(outcomes)
    monkeypatch.setattr(deal_scanner, "run_walmart_scan", fake_scan)
    monkeypatch.setattr(deal_scanner, "acquire_scan_lock", _fake_lock)
    monkeypatch.setattr(deal_scanner, "provider_registry", SimpleNamespace(get=lambda key: object()))
    monkeypatch.setattr(deal_scanner, "verify_single_exact_item", fake_exact)
    monkeypatch.setattr(deal_scanner, "maybe_post_public_deal_cards", _fake_public_post)
    monkeypatch.setattr(deal_scanner, "send_summary_and_card_batches", fake_send)
    return captured, fake_exact


def _scan_result(candidates, *, has_next_page=False) -> ProviderScanResult:
    return ProviderScanResult(
        provider_key="walmart",
        candidates=tuple(candidates),
        total_results=len(candidates),
        page=1,
        page_size=25,
        start_index=1,
        has_next_page=has_next_page,
    )


@pytest.mark.asyncio
async def test_hunt_pages_drops_eighty_percent_card_that_fails_recheck(monkeypatch):
    bad = _scan_candidate("111", 10.0, 100.0)  # search: 90% off
    good = _scan_candidate("222", 15.0, 100.0)  # search: 85% off
    captured, fake_exact = _patch_hunt_pages(
        monkeypatch,
        scan_results={1: _scan_result([bad, good])},
        outcomes={
            "111": _outcome(95.0, 100.0, classification="verified_under_threshold"),
            "222": _outcome(15.0, 100.0),
        },
    )
    interaction = _preset_interaction()
    await _hunt_view()._hunt_pages(interaction, min_discount=80, max_pages=5)

    assert len(captured["sent"]) == 1
    shown = captured["sent"][0]["cards"]
    assert [card.url for card in shown] == ["https://www.walmart.com/ip/222"]
    summary = captured["sent"][0]["summary"]
    assert "✅ Prices re-checked just now" in _field_names(summary)
    # Found count stays the search-found total; the field explains the drop.
    assert "Found: **2 candidate(s)**" in summary.description


@pytest.mark.asyncio
async def test_hunt_pages_all_dropped_lands_on_nothing_found(monkeypatch):
    bad = _scan_candidate("111", 10.0, 100.0)  # search: 90% off, re-check: $95
    captured, fake_exact = _patch_hunt_pages(
        monkeypatch,
        scan_results={1: _scan_result([bad])},
        outcomes={"111": _outcome(95.0, 100.0, classification="verified_under_threshold")},
    )
    interaction = _preset_interaction()
    await _hunt_view()._hunt_pages(interaction, min_discount=80, max_pages=5)

    assert captured["sent"] == []
    assert len(interaction.followup.calls) == 1
    summary = interaction.followup.calls[0]["embed"]
    assert "No useful markdowns found yet" in _field_names(summary)
    assert "✅ Prices re-checked just now" in _field_names(summary)
    assert any("didn't hold up on re-check" in value for value in _field_values(summary))


@pytest.mark.asyncio
async def test_hunt_pages_unavailable_shows_search_cards_without_claim(monkeypatch):
    first = _scan_candidate("111", 10.0, 100.0)
    second = _scan_candidate("222", 15.0, 100.0)
    captured, fake_exact = _patch_hunt_pages(
        monkeypatch,
        scan_results={1: _scan_result([first, second])},
        outcomes={"111": _unavailable_outcome(), "222": _unavailable_outcome()},
    )
    interaction = _preset_interaction()
    await _hunt_view()._hunt_pages(interaction, min_discount=80, max_pages=5)

    assert fake_exact.calls == ["111"]
    assert len(captured["sent"]) == 1
    assert len(captured["sent"][0]["cards"]) == 2
    summary = captured["sent"][0]["summary"]
    assert "Price re-check unavailable" in _field_names(summary)
    _assert_no_rechecked_claim(summary)


# ---------------------------------------------------------------------------
# Unified Find display + mode switches
# ---------------------------------------------------------------------------


def _finder_result(cards: list[DealCard]) -> DealFinderResult:
    return DealFinderResult(
        query="widget",
        search_plan=expand_walmart_query("widget"),
        aggregate=ProviderScanResult(provider_key="walmart", candidates=()),
        verified_cards=cards,
        review_candidates=ReviewCandidateResult(cards=[]),
        pages_checked=1,
        products_checked=10,
        warnings=[],
        searches_attempted=1,
        min_discount=40,
    )


class _FakeProvider:
    async def healthcheck(self):
        return SimpleNamespace(status=ProviderStatus.READY)


def _patch_unified(monkeypatch, *, result, outcomes):
    captured: dict = {"controls": []}

    async def fake_find(**kwargs):
        return result

    async def fake_send_embeds(interaction, *, summary, embeds, ephemeral):
        captured["summary"] = summary
        captured["embeds"] = embeds

    async def fake_controls(interaction, view, ranked, freshness, *, replace_existing=False):
        captured["controls"].append(view)

    fake_exact = FakeExactVerify(outcomes)
    monkeypatch.setattr(unified_deal_scanner, "provider_registry", SimpleNamespace(get=lambda key: _FakeProvider()))
    monkeypatch.setattr(unified_deal_scanner, "find_walmart_deals_for_query", fake_find)
    monkeypatch.setattr(unified_deal_scanner, "maybe_post_public_deal_cards", _fake_public_post)
    monkeypatch.setattr(unified_deal_scanner, "send_summary_and_embeds", fake_send_embeds)
    monkeypatch.setattr(unified_deal_scanner, "send_deal_mode_controls", fake_controls)
    monkeypatch.setattr(deal_scanner, "verify_single_exact_item", fake_exact)
    return captured, fake_exact


async def _run_unified(cog, interaction):
    await cog._send_unified_deal_search(
        interaction,
        query="widget",
        min_discount=40,
        page=1,
        max_results=10,
        sort_value=None,
        order_value=None,
        alerts_only=False,
        simple_mode=True,
        mode="best",
    )


@pytest.mark.asyncio
async def test_unified_find_only_shows_cards_that_hold_on_recheck(monkeypatch):
    good = _card("111", 60.0, 100.0, 40.0)
    bad = _card("222", 60.0, 100.0, 40.0)
    result = _finder_result([good, bad])
    captured, fake_exact = _patch_unified(
        monkeypatch,
        result=result,
        outcomes={
            "111": _outcome(60.0, 100.0),
            "222": _outcome(95.0, 100.0, classification="verified_under_threshold"),
        },
    )
    cog = UnifiedDealScannerCog(SimpleNamespace(db=None))
    interaction = _preset_interaction()
    await _run_unified(cog, interaction)

    assert len(captured["embeds"]) == 1
    assert "Verified Walmart deal" in captured["embeds"][0].description
    assert "✅ Prices re-checked just now" in _field_names(captured["summary"])
    # The mode view stores the re-checked set, so mode switches can never
    # resurface the dropped card.
    view = captured["controls"][0]
    assert view.result.verified_cards == [good]
    assert view.verification is not None and view.verification[0] is True


@pytest.mark.asyncio
async def test_unified_find_unavailable_shows_search_cards_without_claim(monkeypatch):
    first = _card("111", 60.0, 100.0, 40.0)
    second = _card("222", 60.0, 100.0, 40.0)
    result = _finder_result([first, second])
    captured, fake_exact = _patch_unified(
        monkeypatch,
        result=result,
        outcomes={"111": _unavailable_outcome(), "222": _unavailable_outcome()},
    )
    cog = UnifiedDealScannerCog(SimpleNamespace(db=None))
    interaction = _preset_interaction()
    await _run_unified(cog, interaction)

    assert fake_exact.calls == ["111"]
    assert len(captured["embeds"]) == 2
    assert "Price re-check unavailable" in _field_names(captured["summary"])
    _assert_no_rechecked_claim(captured["summary"])
    # Fallback keeps the full search-proven set for the mode view.
    view = captured["controls"][0]
    assert view.result.verified_cards == [first, second]


@pytest.mark.asyncio
async def test_mode_switch_keeps_rechecked_claim_and_set(monkeypatch):
    good = _card("111", 60.0, 100.0, 40.0)
    result = _finder_result([good])
    captured: dict = {}

    async def fake_send_embeds(interaction, *, summary, embeds, ephemeral):
        captured["summary"] = summary
        captured["embeds"] = embeds

    async def fake_controls(interaction, view, ranked, freshness, *, replace_existing=False):
        captured["view"] = view

    monkeypatch.setattr(unified_deal_scanner, "send_summary_and_embeds", fake_send_embeds)
    monkeypatch.setattr(unified_deal_scanner, "send_deal_mode_controls", fake_controls)

    cog = UnifiedDealScannerCog(SimpleNamespace(db=None))
    view = DealSearchModeView(
        cog=cog,
        query="widget",
        min_discount=40,
        max_results=10,
        sort_value=None,
        order_value=None,
        alerts_only=False,
        simple_mode=True,
        mode="best",
        result=result,
        ranked=None,
        cached_rows=[],
        freshness=None,
        menu_token="tok",
        owner_id=42,
        verification=(True, 1),
    )
    interaction = SimpleNamespace(
        guild_id=None,
        user=SimpleNamespace(id=42),
        followup=RecordingFollowup(),
        response=SimpleNamespace(is_done=lambda: True),
        client=None,
    )
    await view.show_mode(interaction, "markdown")

    assert len(captured["embeds"]) == 1
    assert "✅ Prices re-checked just now" in _field_names(captured["summary"])
    assert captured["view"].verification == (True, 1)


# ---------------------------------------------------------------------------
# Live verified hunt (verified_hunt_button_callback)
# ---------------------------------------------------------------------------


def _patch_verified_hunt(monkeypatch, *, result, outcomes):
    captured: dict = {}

    async def fake_find_for_preset(**kwargs):
        return result

    async def fake_send_batches(interaction, *, summary, cards, review_cards=None):
        captured["summary"] = summary
        captured["cards"] = cards

    async def fake_public(**kwargs):
        captured["public_cards"] = kwargs.get("cards")
        return SimpleNamespace(any_activity=False, posted=0, skipped_duplicate=0, skipped_not_alertable=0)

    fake_exact = FakeExactVerify(outcomes)
    monkeypatch.setattr(deal_scanner, "acquire_scan_lock", _fake_lock)
    monkeypatch.setattr(deal_scanner, "provider_health_error_message", _fake_health_ok)
    monkeypatch.setattr(deal_scanner, "verify_single_exact_item", fake_exact)
    monkeypatch.setattr(deal_finder_engine, "find_walmart_deals_for_preset", fake_find_for_preset)
    monkeypatch.setattr(verified_discount_hunt, "maybe_post_public_deal_cards", fake_public)
    monkeypatch.setattr(verified_discount_hunt, "send_card_batches", fake_send_batches)
    monkeypatch.setattr(
        verified_discount_hunt,
        "provider_registry",
        SimpleNamespace(get=lambda key: object()),
        raising=False,
    )
    return captured, fake_exact


def _hunt_result(cards: list[DealCard], category_key: str) -> VerifiedHuntResult:
    return VerifiedHuntResult(
        cards=cards,
        pages_checked=5,
        products_checked=100,
        warnings=[],
        searches_attempted=5,
        min_discount=50,
        total_verified_cards=len(cards),
        review_candidates=None,
        category_key=category_key,
    )


@pytest.mark.asyncio
async def test_verified_hunt_drops_card_that_fails_recheck(monkeypatch):
    preset = next(iter(verified_discount_hunt.HUNT_PRESETS.values()))
    bad = _card("222", 50.0, 100.0, 50.0)
    good = _card("111", 50.0, 100.0, 50.0)
    result = _hunt_result([bad, good], preset.key)
    captured, fake_exact = _patch_verified_hunt(
        monkeypatch,
        result=result,
        outcomes={
            "111": _outcome(50.0, 100.0),
            "222": _outcome(95.0, 100.0, classification="verified_under_threshold"),
        },
    )
    button = SimpleNamespace(preset=preset, view=None)
    interaction = SimpleNamespace(
        guild_id=None,
        user=SimpleNamespace(id=42),
        followup=RecordingFollowup(),
        client=SimpleNamespace(db=None),
    )
    await verified_discount_hunt.verified_hunt_button_callback(button, interaction)

    assert captured["cards"] == [good]
    assert captured["public_cards"] == [good]
    summary = captured["summary"]
    assert "Shown now: **1**" in summary.description
    assert "✅ Prices re-checked just now" in _field_names(summary)


@pytest.mark.asyncio
async def test_verified_hunt_unavailable_shows_all_without_claim(monkeypatch):
    preset = next(iter(verified_discount_hunt.HUNT_PRESETS.values()))
    first = _card("111", 50.0, 100.0, 50.0)
    second = _card("222", 50.0, 100.0, 50.0)
    result = _hunt_result([first, second], preset.key)
    captured, fake_exact = _patch_verified_hunt(
        monkeypatch,
        result=result,
        outcomes={"111": _unavailable_outcome(), "222": _unavailable_outcome()},
    )
    button = SimpleNamespace(preset=preset, view=None)
    interaction = SimpleNamespace(
        guild_id=None,
        user=SimpleNamespace(id=42),
        followup=RecordingFollowup(),
        client=SimpleNamespace(db=None),
    )
    await verified_discount_hunt.verified_hunt_button_callback(button, interaction)

    assert fake_exact.calls == ["111"]
    assert captured["cards"] == [first, second]
    summary = captured["summary"]
    assert "Price re-check unavailable" in _field_names(summary)
    _assert_no_rechecked_claim(summary)
