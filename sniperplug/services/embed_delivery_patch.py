from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import discord

from sniperplug.services.embed_delivery import SAFE_EMBED_MESSAGE_LIMIT, batch_embeds_for_limit, embed_text_size, sanitize_embed, sanitize_embeds


_PATCH_ATTR = "_sniperplug_safe_followup_send_installed"
_ORIGINAL_ATTR = "_sniperplug_original_send"


def install_safe_followup_send_patch() -> None:
    """Protect interaction/webhook sends from Discord embed validation failures.

    This wrapper handles both classes of 50035 issues:
    - combined embed text over 6000 chars
    - individual embed field/name/description/footer limits, including 1024-char field values
    """
    webhook_cls = discord.Webhook
    if getattr(webhook_cls.send, _PATCH_ATTR, False):
        return

    original_send = webhook_cls.send

    async def safe_send(self, *args: Any, **kwargs: Any):
        embeds = kwargs.get("embeds")
        embed = kwargs.get("embed")
        if embed is not None:
            embed = sanitize_embed(embed) if isinstance(embed, discord.Embed) else embed
            kwargs["embed"] = embed
        if embeds is not None:
            embeds = sanitize_embeds(embeds)
            kwargs["embeds"] = embeds

        if embeds is None and embed is not None:
            embeds = [embed]
            kwargs.pop("embed", None)
            kwargs["embeds"] = embeds

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


def should_split_embeds(embeds: Any) -> bool:
    if not embeds or not isinstance(embeds, Sequence):
        return False
    if len(embeds) <= 1:
        return False
    if not all(isinstance(embed, discord.Embed) for embed in embeds):
        return False
    total = sum(embed_text_size(embed) for embed in embeds)
    return total > SAFE_EMBED_MESSAGE_LIMIT
