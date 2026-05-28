from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from sniperplug.models.deal import utc_now_iso
from sniperplug.services.deal_finder_telemetry import SearchRouteStats


RETAILER_WALMART = "walmart"


@dataclass(frozen=True)
class RouteMemoryUpdate:
    route_query: str
    scans: int = 0
    returned_products: int = 0
    verified_hits: int = 0
    review_hits: int = 0
    flip_hits: int = 0
    blocked_hits: int = 0

    @property
    def score(self) -> float:
        # Verified deals are the strongest signal, flip/review leads matter, raw
        # returned products matter only a little so noisy broad terms do not win.
        return round(
            (self.verified_hits * 18)
            + (self.flip_hits * 10)
            + (self.review_hits * 4)
            + min(self.returned_products, 100) * 0.08
            - (self.blocked_hits * 8),
            2,
        )


@dataclass(frozen=True)
class RouteMemoryRecord:
    route_query: str
    scans: int
    returned_products: int
    verified_hits: int
    review_hits: int
    flip_hits: int
    blocked_hits: int
    last_score: float


def update_from_route_stats(stats: Iterable[SearchRouteStats]) -> list[RouteMemoryUpdate]:
    return [
        RouteMemoryUpdate(
            route_query=stat.query,
            scans=stat.pages_checked,
            returned_products=stat.returned_products,
        )
        for stat in stats
        if stat.query and stat.query != "unknown"
    ]


async def record_route_memory(db, *, guild_id: int | None, retailer: str, updates: Iterable[RouteMemoryUpdate]) -> None:
    if db is None or guild_id is None:
        return
    conn = db.require_conn()
    now = utc_now_iso()
    for update in updates:
        if not update.route_query:
            continue
        await conn.execute(
            """
            INSERT INTO deal_route_memory (
                guild_id, retailer, route_query, scans, returned_products,
                verified_hits, review_hits, flip_hits, blocked_hits, last_score,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, retailer, route_query) DO UPDATE SET
                scans = deal_route_memory.scans + excluded.scans,
                returned_products = deal_route_memory.returned_products + excluded.returned_products,
                verified_hits = deal_route_memory.verified_hits + excluded.verified_hits,
                review_hits = deal_route_memory.review_hits + excluded.review_hits,
                flip_hits = deal_route_memory.flip_hits + excluded.flip_hits,
                blocked_hits = deal_route_memory.blocked_hits + excluded.blocked_hits,
                last_score = excluded.last_score,
                updated_at = excluded.updated_at
            """,
            (
                guild_id,
                retailer,
                update.route_query,
                update.scans,
                update.returned_products,
                update.verified_hits,
                update.review_hits,
                update.flip_hits,
                update.blocked_hits,
                update.score,
                now,
                now,
            ),
        )
    await conn.commit()


async def top_route_memory(db, *, guild_id: int | None, retailer: str, limit: int = 5) -> list[RouteMemoryRecord]:
    if db is None or guild_id is None:
        return []
    conn = db.require_conn()
    cursor = await conn.execute(
        """
        SELECT route_query, scans, returned_products, verified_hits, review_hits, flip_hits, blocked_hits, last_score
        FROM deal_route_memory
        WHERE guild_id = ? AND retailer = ?
        ORDER BY last_score DESC, verified_hits DESC, flip_hits DESC, returned_products DESC
        LIMIT ?
        """,
        (guild_id, retailer, max(1, min(limit, 20))),
    )
    rows = await cursor.fetchall()
    return [
        RouteMemoryRecord(
            route_query=str(row["route_query"]),
            scans=int(row["scans"]),
            returned_products=int(row["returned_products"]),
            verified_hits=int(row["verified_hits"]),
            review_hits=int(row["review_hits"]),
            flip_hits=int(row["flip_hits"]),
            blocked_hits=int(row["blocked_hits"]),
            last_score=float(row["last_score"]),
        )
        for row in rows
    ]


def memory_boost_queries(records: Iterable[RouteMemoryRecord], *, limit: int = 3) -> tuple[str, ...]:
    queries: list[str] = []
    for record in records:
        if record.last_score <= 0:
            continue
        if record.route_query not in queries:
            queries.append(record.route_query)
        if len(queries) >= limit:
            break
    return tuple(queries)


def route_memory_lines(records: Iterable[RouteMemoryRecord], *, limit: int = 5) -> list[str]:
    lines: list[str] = []
    for record in list(records)[:limit]:
        lines.append(
            f"• `{record.route_query}` — score **{record.last_score:.1f}** "
            f"({record.verified_hits} verified, {record.flip_hits} flip, {record.review_hits} review, {record.returned_products} products)"
        )
    return lines
