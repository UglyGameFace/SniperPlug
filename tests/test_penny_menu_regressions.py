"""Bisect variant D9: two trivial async tests, no production imports."""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_trivial_async_one() -> None:
    assert True


@pytest.mark.asyncio
async def test_trivial_async_two() -> None:
    assert True
