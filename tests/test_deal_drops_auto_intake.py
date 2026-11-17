"""Phase D3: #deal-drops auto-intake tests.

Fixture-based only — no Discord connection, no live Walmart calls.
The verification step is stubbed; these tests prove the listener's
guards (channel, author, intent, dedupe, rate limit) and the reply
content, not the verification itself (covered by /check_deal tests).
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from sniperplug.cogs.deal_drops_intake import (
    DealDropsIntakeCog,
    DealDropsIntakeConfig,
    extract_walmart_item_ids,
)
from sniperplug.models.candidate import SourceCandidate
from sniperplug.providers.base import ProviderStatus
from sniperplug.providers.registry import provider_registry
from sniperplug.services.walmart_exact_verification_queue import (
    MANUAL_INTAKE_MIN_DISCOUNT,
    SingleExactVerification,
)

DROPS_CHANNEL_ID = 123456789
OTHER_CHANNEL_ID = 987654321
IPHONE_URL = "https://www.walmart.com/ip/Apple-iPhone-11-AT-T-64GB-Green/425031866"
IPHONE_ID = "425031866"
OTHER_ID = "987654321"


# --- extraction -----------------------------------------------------------


def test_extract_url_with_slug_and_bare_id() -> None:
    assert extract_walmart_item_ids(IPHONE_URL) == [IPHONE_ID]
    assert extract_walmart_item_ids(IPHONE_ID) == [IPHONE_ID]


def test_extract_url_without_slug_and_trailing_punctuation() -> None:
    assert extract_walmart_item_ids(
        "deal: https://www.walmart.com/ip/425031866."
    ) == [IPHONE_ID]
    assert extract_walmart_item_ids(f"check this {IPHONE_URL}!") == [IPHONE_ID]
    assert extract_walmart_item_ids(f"({IPHONE_ID})") == [IPHONE_ID]


def test_extract_multiple_links_deduped_in_order() -> None:
    assert extract_walmart_item_ids(
        f"{IPHONE_URL} and {OTHER_ID} then {IPHONE_URL} again"
    ) == [IPHONE_ID, OTHER_ID]


def test_extract_ignores_non_walmart_links_and_short_numbers() -> None:
    assert extract_walmart_item_ids(
        "https://www.amazon.com/dp/425031866 I bought 2 in 2026 for $19.99"
    ) == []
    assert extract_walmart_item_ids("") == []
    assert extract_walmart_item_ids("no links here, just 42 and 650") == []


# --- fakes ----------------------------------------------------------------


class FakeMessage:
    def __init__(
        self,
        content: str,
        *,
        channel_id: int = DROPS_CHANNEL_ID,
        author_bot: bool = False,
        author_id: int = 111,
    ) -> None:
        self.content = content
        self.channel = SimpleNamespace(id=channel_id)
        self.author = SimpleNamespace(bot=author_bot, id=author_id)
        self.replies: list[dict] = []

    async def reply(self, content: str, **kwargs) -> None:
        self.replies.append({"content": content, **kwargs})


class FakeBot:
    def __init__(self, *, message_content: bool = True) -> None:
        self.intents = SimpleNamespace(message_content=message_content)
        self.user = SimpleNamespace(id=999)
        self.db = None


class FakeProvider:
    def __init__(self, *, ready: bool = True) -> None:
        self._ready = ready
        self.healthcheck_calls = 0

    async def healthcheck(self):
        self.healthcheck_calls += 1
        status = ProviderStatus.READY if self._ready else ProviderStatus.ERROR
        return SimpleNamespace(status=status)


def _iphone_candidate(**overrides):
    fields = {
        "source_key": "walmart",
        "retailer": "walmart",
        "title": "Apple iPhone 11, AT&T, 64GB, Green",
        "product_url": IPHONE_URL,
        "api_current_price": 1.0,
        "api_reference_price": 650.0,
    }
    fields.update(overrides)
    return SourceCandidate(**fields)


class VerifyStub:
    def __init__(self, result_by_id: dict | None = None) -> None:
        self.calls: list[dict] = []
        self.result_by_id = result_by_id or {}

    async def __call__(self, db, provider, item_id, *, min_discount):
        self.calls.append(
            {"item_id": item_id, "min_discount": min_discount, "db": db}
        )
        if item_id in self.result_by_id:
            return self.result_by_id[item_id]
        return SingleExactVerification(
            item_id=item_id,
            candidate=_iphone_candidate(),
            classification="verified_markdown",
            status="verified",
            enqueued=True,
        )


def _cog(monkeypatch, *, verify=None, provider=None, **config_kwargs):
    import sniperplug.cogs.deal_drops_intake as intake

    verify = verify if verify is not None else VerifyStub()
    monkeypatch.setattr(intake, "verify_single_exact_item", verify)
    monkeypatch.setattr(
        provider_registry, "get", lambda key: provider
    )
    kwargs = {
        "enabled": True,
        "channel_id": DROPS_CHANNEL_ID,
        "max_per_minute": 5,
        "dedupe_minutes": 15,
    }
    kwargs.update(config_kwargs)
    config = DealDropsIntakeConfig(**kwargs)
    return DealDropsIntakeCog(FakeBot(), config), verify


# --- listener guards ------------------------------------------------------


@pytest.mark.asyncio
async def test_wrong_channel_ignored(monkeypatch) -> None:
    cog, verify = _cog(monkeypatch)
    message = FakeMessage(IPHONE_URL, channel_id=OTHER_CHANNEL_ID)
    await cog.on_message(message)
    assert message.replies == []
    assert verify.calls == []


@pytest.mark.asyncio
async def test_bot_author_ignored(monkeypatch) -> None:
    cog, verify = _cog(monkeypatch)
    message = FakeMessage(IPHONE_URL, author_bot=True)
    await cog.on_message(message)
    assert message.replies == []
    assert verify.calls == []


@pytest.mark.asyncio
async def test_intent_off_listener_inert(monkeypatch) -> None:
    import sniperplug.cogs.deal_drops_intake as intake

    verify = VerifyStub()
    monkeypatch.setattr(intake, "verify_single_exact_item", verify)
    monkeypatch.setattr(provider_registry, "get", lambda key: FakeProvider())
    bot = FakeBot(message_content=False)
    cog = DealDropsIntakeCog(
        bot,
        DealDropsIntakeConfig(enabled=True, channel_id=DROPS_CHANNEL_ID),
    )
    message = FakeMessage(IPHONE_URL)
    await cog.on_message(message)
    assert message.replies == []
    assert verify.calls == []


@pytest.mark.asyncio
async def test_disabled_or_no_channel_inert(monkeypatch) -> None:
    cog, verify = _cog(monkeypatch, enabled=False)
    message = FakeMessage(IPHONE_URL)
    await cog.on_message(message)
    assert message.replies == []
    assert verify.calls == []

    cog2, verify2 = _cog(monkeypatch, channel_id=0)
    message2 = FakeMessage(IPHONE_URL)
    await cog2.on_message(message2)
    assert message2.replies == []
    assert verify2.calls == []


@pytest.mark.asyncio
async def test_message_without_item_ids_is_silent(monkeypatch) -> None:
    cog, verify = _cog(monkeypatch)
    message = FakeMessage("great drop, check the channel topic")
    await cog.on_message(message)
    assert message.replies == []
    assert verify.calls == []


# --- happy path + reply content -------------------------------------------


@pytest.mark.asyncio
async def test_verified_markdown_reply(monkeypatch) -> None:
    cog, verify = _cog(monkeypatch, provider=FakeProvider())
    message = FakeMessage(IPHONE_URL)
    await cog.on_message(message)
    assert len(verify.calls) == 1
    assert verify.calls[0]["item_id"] == IPHONE_ID
    assert verify.calls[0]["min_discount"] == MANUAL_INTAKE_MIN_DISCOUNT
    assert len(message.replies) == 1
    reply = message.replies[0]
    assert reply["mention_author"] is False
    assert "✅" in reply["content"]
    assert "Apple iPhone 11" in reply["content"]
    assert "$1.00" in reply["content"]
    assert "$650.00" in reply["content"]
    assert "verified markdown" in reply["content"]
    assert IPHONE_ID in reply["content"]


@pytest.mark.asyncio
async def test_under_threshold_reply_claims_no_markdown(monkeypatch) -> None:
    stub = VerifyStub(
        {
            IPHONE_ID: SingleExactVerification(
                item_id=IPHONE_ID,
                candidate=_iphone_candidate(
                    api_current_price=600.0, api_reference_price=650.0
                ),
                classification="verified_under_threshold",
                status="verified",
                enqueued=True,
            )
        }
    )
    cog, _ = _cog(monkeypatch, verify=stub, provider=FakeProvider())
    message = FakeMessage(IPHONE_URL)
    await cog.on_message(message)
    assert len(message.replies) == 1
    content = message.replies[0]["content"]
    assert "🔎" in content
    assert "verified markdown" not in content
    assert "verified_under_threshold" in content


@pytest.mark.asyncio
async def test_page_price_reply_is_labelled_not_a_markdown(monkeypatch) -> None:
    candidate = _iphone_candidate(api_current_price=None, api_reference_price=None)
    candidate.variant_attributes = {
        "pageIdentityRecoveryStatus": "verified_identity",
        "pageIdentityRecoveryPrice": 1.0,
    }
    stub = VerifyStub(
        {
            IPHONE_ID: SingleExactVerification(
                item_id=IPHONE_ID,
                candidate=candidate,
                classification="verified_page_price",
                status="verified",
                enqueued=True,
            )
        }
    )
    cog, _ = _cog(monkeypatch, verify=stub, provider=FakeProvider())
    message = FakeMessage(IPHONE_URL)
    await cog.on_message(message)
    content = message.replies[0]["content"]
    assert "page-observed $1.00" in content
    assert "not a proven markdown" in content


@pytest.mark.asyncio
async def test_failed_verification_reply_notes_queue(monkeypatch) -> None:
    stub = VerifyStub(
        {
            IPHONE_ID: SingleExactVerification(
                item_id=IPHONE_ID,
                candidate=None,
                classification=None,
                status="retry",
                error="api_price_missing",
                enqueued=True,
            )
        }
    )
    cog, _ = _cog(monkeypatch, verify=stub, provider=FakeProvider())
    message = FakeMessage(IPHONE_URL)
    await cog.on_message(message)
    content = message.replies[0]["content"]
    assert "❌" in content
    assert "retry" in content
    assert "queued for the normal exact worker" in content


# --- dedupe + rate limit --------------------------------------------------


@pytest.mark.asyncio
async def test_duplicate_id_repost_is_not_reverified(monkeypatch) -> None:
    cog, verify = _cog(monkeypatch, provider=FakeProvider())
    first = FakeMessage(IPHONE_URL)
    await cog.on_message(first)
    assert len(verify.calls) == 1
    second = FakeMessage(f"again {IPHONE_URL}")
    await cog.on_message(second)
    assert len(verify.calls) == 1
    assert len(second.replies) == 1
    assert "already checked recently" in second.replies[0]["content"]


@pytest.mark.asyncio
async def test_dedupe_expires_after_window(monkeypatch) -> None:
    cog, verify = _cog(monkeypatch, provider=FakeProvider())
    await cog.on_message(FakeMessage(IPHONE_URL))
    assert len(verify.calls) == 1
    cog._verified_at[IPHONE_ID] = time.monotonic() - 16 * 60
    await cog.on_message(FakeMessage(IPHONE_URL))
    assert len(verify.calls) == 2


@pytest.mark.asyncio
async def test_rate_limit_skips_extras_with_a_note(monkeypatch) -> None:
    cog, verify = _cog(
        monkeypatch, provider=FakeProvider(), max_per_minute=1
    )
    message = FakeMessage(f"{IPHONE_URL} {OTHER_ID}")
    await cog.on_message(message)
    assert len(verify.calls) == 1
    assert verify.calls[0]["item_id"] == IPHONE_ID
    content = message.replies[0]["content"]
    assert "max 1/min" in content
    assert "use /walmart check" in content


@pytest.mark.asyncio
async def test_per_message_cap(monkeypatch) -> None:
    cog, verify = _cog(
        monkeypatch, provider=FakeProvider(), max_per_minute=99
    )
    ids = " ".join(str(100000000 + n) for n in range(6))
    message = FakeMessage(ids)
    await cog.on_message(message)
    assert len(verify.calls) == 5
    assert "max 5 per message" in message.replies[0]["content"]


# --- provider problems ----------------------------------------------------


@pytest.mark.asyncio
async def test_provider_missing_replies_without_verifying(monkeypatch) -> None:
    cog, verify = _cog(monkeypatch, provider=None)
    message = FakeMessage(IPHONE_URL)
    await cog.on_message(message)
    assert verify.calls == []
    assert "not connected" in message.replies[0]["content"]


@pytest.mark.asyncio
async def test_provider_not_ready_replies_without_verifying(monkeypatch) -> None:
    cog, verify = _cog(monkeypatch, provider=FakeProvider(ready=False))
    message = FakeMessage(IPHONE_URL)
    await cog.on_message(message)
    assert verify.calls == []
    assert "not ready yet" in message.replies[0]["content"]


# --- config ----------------------------------------------------------------


def test_config_from_env(monkeypatch) -> None:
    monkeypatch.setenv("SNIPERPLUG_DEAL_DROPS_ENABLED", "false")
    monkeypatch.setenv("SNIPERPLUG_DEAL_DROPS_CHANNEL_ID", "4242")
    monkeypatch.setenv("SNIPERPLUG_DEAL_DROPS_MAX_PER_MINUTE", "3")
    monkeypatch.setenv("SNIPERPLUG_DEAL_DROPS_DEDUPE_MINUTES", "30")
    config = DealDropsIntakeConfig.from_env()
    assert config.enabled is False
    assert config.channel_id == 4242
    assert config.max_per_minute == 3
    assert config.dedupe_minutes == 30


def test_config_defaults_are_inert_without_channel(monkeypatch) -> None:
    for var in (
        "SNIPERPLUG_DEAL_DROPS_ENABLED",
        "SNIPERPLUG_DEAL_DROPS_CHANNEL_ID",
        "SNIPERPLUG_DEAL_DROPS_MAX_PER_MINUTE",
        "SNIPERPLUG_DEAL_DROPS_DEDUPE_MINUTES",
    ):
        monkeypatch.delenv(var, raising=False)
    config = DealDropsIntakeConfig.from_env()
    assert config.enabled is True
    assert config.channel_id == 0
    assert config.max_per_minute == 5
    assert config.dedupe_minutes == 15
