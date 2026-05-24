from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import discord

from sniperplug.services.embed_delivery import SAFE_EMBED_MESSAGE_LIMIT, batch_embeds_for_limit, embed_text_size


_PATCH_ATTR = "_sniperplug_safe_followup_send_installed"
_ORIGINAL_ATTR = "_sniperplug_original_send"


def install_safe_followup_send_patch() -> None:
    """Protect all interaction followup sends from Discord's 6000-char embed cap.

    Several scan paths historically called `followup.send(embeds=[summary] + cards)`.
    Discord validates the combined text from every embed in one message, so a few
    rich proof cards can reject the whole response. This wrapper keeps existing
    command code stable while safely splitting oversized embed payloads into:

    1. first safe embed batch
    2. later safe embed batches

    The view is attached only to the final batch so action buttons remain visible
    after all product cards are sent.
    """
    webhook_cls = discord.Webhook
    if getattr(webhook_cls.send, _PATCH_ATTR, False):
        return

    original_send = webhook_cls.send

    async def safe_send(self, *args: Any, **kwargs: Any):
        embeds = kwargs.get("embeds")
        embed = kwargs.get("embed")
        if embeds is None and embed is not None:
            embeds = [embed]
            kwargs.pop("embed", None)
            kwargs["embeds"] = embeds

        if not should_split_embeds(embeds):
            return await original_send(self, *args, **kwargs)

        embed_list = list(embeds)
        batches = batch_embeds_for_limit(embed_list, limit=SAFE_EMBED_MESSAGE_LIMIT)
        if len(batches) <= 1:
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


def should_split_embeds(embeds: Any) -> bool:
    if not embeds or not isinstance(embeds, Sequence):
        return False
    if len(embeds) <= 1:
        return False
    if not all(isinstance(embed, discord.Embed) for embed in embeds):
        return False
    total = sum(embed_text_size(embed) for embed in embeds)
    return total > SAFE_EMBED_MESSAGE_LIMIT
