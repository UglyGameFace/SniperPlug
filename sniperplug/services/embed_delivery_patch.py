from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import discord

from sniperplug.services.embed_delivery import SAFE_EMBED_MESSAGE_LIMIT, batch_embeds_for_limit, embed_text_size, sanitize_embed, sanitize_embeds


_PATCH_ATTR = "_sniperplug_safe_embed_send_installed"
_ORIGINAL_ATTR = "_sniperplug_original_send"
_RESPONSE_PATCH_ATTR = "_sniperplug_safe_response_send_installed"
_RESPONSE_ORIGINAL_ATTR = "_sniperplug_original_response_send_message"


def install_safe_followup_send_patch() -> None:
    """Protect Discord sends from embed validation failures.

    This wrapper handles both classes of 50035 issues:
    - combined embed text over 6000 chars
    - individual embed field/name/description/footer limits, including 1024-char field values

    `interaction.followup.send` is a Webhook send and can be split into multiple
    follow-up messages. `interaction.response.send_message` can only create one
    initial response, so it is sanitized and clipped to one safe batch instead of
    failing the interaction.
    """
    install_safe_webhook_send_patch()
    install_safe_interaction_response_patch()


def install_safe_webhook_send_patch() -> None:
    webhook_cls = discord.Webhook
    if getattr(webhook_cls.send, _PATCH_ATTR, False):
        return

    original_send = webhook_cls.send

    async def safe_send(self, *args: Any, **kwargs: Any):
        kwargs = prepare_safe_send_kwargs(kwargs)
        embeds = kwargs.get("embeds")

        if not should_split_embeds(embeds):
            return await original_send(self, *args, **kwargs)

        embed_list = [sanitize_embed(item) for item in list(embeds) if isinstance(item, discord.Embed)]
        batches = batch_embeds_for_limit(embed_list, limit=SAFE_EMBED_MESSAGE_LIMIT)
        if len(batches) <= 1:
            kwargs["embeds"] = batches[0] if batches else []
            return await original_send(self, *args, **kwargs)

        view = kwargs.pop("view", None)
        content = kwargs.pop("content", None)
        kwargs.pop("embed", None)
        kwargs.pop("embeds", None)

        last_message = None
        for index, batch in enumerate(batches):
            batch_kwargs = dict(kwargs)
            batch_kwargs["embeds"] = batch
            if index == 0 and content is not None:
                batch_kwargs["content"] = content
            if index == len(batches) - 1 and view is not None:
                batch_kwargs["view"] = view
            last_message = await original_send(self, *args, **batch_kwargs)
        return last_message

    setattr(safe_send, _PATCH_ATTR, True)
    setattr(safe_send, _ORIGINAL_ATTR, original_send)
    webhook_cls.send = safe_send


def install_safe_interaction_response_patch() -> None:
    response_cls = discord.InteractionResponse
    if getattr(response_cls.send_message, _RESPONSE_PATCH_ATTR, False):
        return

    original_send_message = response_cls.send_message

    async def safe_send_message(self, *args: Any, **kwargs: Any):
        kwargs = prepare_safe_send_kwargs(kwargs)
        embeds = kwargs.get("embeds")
        if should_split_embeds(embeds):
            embed_list = [sanitize_embed(item) for item in list(embeds) if isinstance(item, discord.Embed)]
            batches = batch_embeds_for_limit(embed_list, limit=SAFE_EMBED_MESSAGE_LIMIT)
            kwargs["embeds"] = batches[0] if batches else []
            kwargs.pop("embed", None)
            if len(batches) > 1:
                content = kwargs.get("content")
                suffix = "\n\n⚠️ Extra embeds were split/trimmed by SniperPlug's Discord safety guard. Use the menu/follow-up controls for the rest."
                kwargs["content"] = f"{content}{suffix}" if content else suffix.strip()
        return await original_send_message(self, *args, **kwargs)

    setattr(safe_send_message, _RESPONSE_PATCH_ATTR, True)
    setattr(safe_send_message, _RESPONSE_ORIGINAL_ATTR, original_send_message)
    response_cls.send_message = safe_send_message


def prepare_safe_send_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    safe_kwargs = dict(kwargs)
    embed = safe_kwargs.get("embed")
    embeds = safe_kwargs.get("embeds")

    if embed is not None:
        safe_kwargs["embed"] = sanitize_embed(embed) if isinstance(embed, discord.Embed) else embed
    if embeds is not None:
        safe_kwargs["embeds"] = sanitize_embeds(embeds)

    if safe_kwargs.get("embeds") is None and safe_kwargs.get("embed") is not None:
        safe_kwargs["embeds"] = [safe_kwargs.pop("embed")]
    return safe_kwargs


def should_split_embeds(embeds: Any) -> bool:
    if not embeds or not isinstance(embeds, Sequence):
        return False
    if len(embeds) <= 1:
        return False
    if not all(isinstance(embed, discord.Embed) for embed in embeds):
        return False
    total = sum(embed_text_size(embed) for embed in embeds)
    return total > SAFE_EMBED_MESSAGE_LIMIT
