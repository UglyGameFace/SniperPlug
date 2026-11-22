"""Bisect variant D2: sync modal tests only."""
from __future__ import annotations

from types import SimpleNamespace

import discord
import pytest

from sniperplug.cogs import home_depot_search
from sniperplug.cogs.home_depot_search import HdHuntStopView, HomeDepotSearchCog
from sniperplug.cogs.unified_menu import _MODAL_FIELDS, _MenuModal

DISCORD_LABEL_LIMIT = 45


def test_every_menu_modal_field_label_fits_discord_limit() -> None:
    too_long = [
        f"{key}.{field.key}: {field.label!r} ({len(field.label)} chars)"
        for key, fields in _MODAL_FIELDS.items()
        for field in fields
        if len(field.label) > DISCORD_LABEL_LIMIT
    ]
    assert too_long == []


def test_every_menu_modal_constructs_within_discord_limits() -> None:
    for key in _MODAL_FIELDS:
        modal = _MenuModal(SimpleNamespace(), key)
        assert len(modal.title) <= DISCORD_LABEL_LIMIT
        for child in modal.children:
            assert len(child.label) <= DISCORD_LABEL_LIMIT, (
                f"{key}: label {child.label!r} exceeds {DISCORD_LABEL_LIMIT}"
            )
