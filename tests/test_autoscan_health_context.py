"""Behavioral pin for the health context behind /walmart health.

The route-error flag and the manual/scheduled report labeling used to
live in the dead per-guild health embed in cogs/public_alerts.py. They
now live in services/autoscan_history.py and feed the live global
autoscan health embed; these tests pin the detection against real saved
reports.
"""

from __future__ import annotations

import aiosqlite
import pytest

from sniperplug.services.autoscan_history import (
    latest_autoscan_health_context,
    save_autoscan_report,
)


SCAN_KEY = "autoscan:walmart_discovery"


class _Db:
    def __init__(self, conn):
        self._conn = conn

    def require_conn(self):
        return self._conn


async def _make_db() -> _Db:
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    return _Db(conn)


async def _context(db, guild_id: int = 1):
    return await latest_autoscan_health_context(
        db,
        guild_id=guild_id,
        retailer="walmart",
        scan_key=SCAN_KEY,
    )


@pytest.mark.asyncio
async def test_no_report_is_scheduled_and_route_clean():
    db = await _make_db()
    try:
        ctx = await _context(db)

        assert ctx.last_run_has_route_error is False
        assert ctx.is_manual is False
        assert "No detailed auto-scan report saved yet" in ctx.report_line
    finally:
        await db.require_conn().close()


@pytest.mark.asyncio
async def test_ghost_guild_route_error_flags_last_run():
    db = await _make_db()
    try:
        await save_autoscan_report(
            db,
            guild_id=1,
            retailer="walmart",
            scan_key=SCAN_KEY,
            payload={
                "errors": (
                    "public channel lookup failed: saved route uses ghost guild `999`, "
                    "saved channel <#123> belongs to live guild `1`",
                ),
                "settings": {},
            },
        )

        ctx = await _context(db)

        assert ctx.last_run_has_route_error is True
        assert ctx.is_manual is False
    finally:
        await db.require_conn().close()


@pytest.mark.asyncio
async def test_manual_force_report_is_labeled_manual():
    db = await _make_db()
    try:
        await save_autoscan_report(
            db,
            guild_id=1,
            retailer="walmart",
            scan_key=SCAN_KEY,
            payload={"errors": (), "settings": {"forced": True}},
        )

        ctx = await _context(db)

        assert ctx.is_manual is True
        assert ctx.last_run_has_route_error is False
    finally:
        await db.require_conn().close()
