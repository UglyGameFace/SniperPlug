"""Bisect variant: imports only, no tests."""
from __future__ import annotations

from types import SimpleNamespace

import discord
import pytest

from sniperplug.cogs import home_depot_search
from sniperplug.cogs.home_depot_search import HdHuntStopView, HomeDepotSearchCog
from sniperplug.cogs.unified_menu import _MODAL_FIELDS, _MenuModal
