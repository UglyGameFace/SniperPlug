from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from sniperplug.services.public_deal_posts import ensure_public_post_tables
from sniperplug.services.public_posting import SUPPORTED_RETAILERS, format_retailers, normalize_retailer_key


DEFAULT_STALE_AFTER_HOURS = 24
ACTIVE_DEALS_MAX_PAGE_SIZE = 15
ACTIVE_DEALS_DEFAULT_PAGE_SIZE = 10
ACTIVE_DEALS_DEFAULT_MIN_DISCOUNT = 1
ACTIVE_DEAL_SORTS = {
    "recent": "last_seen_at DESC",
    "discount": "discount DESC, last_seen_at DESC",
    "score": "score DESC, last_seen_at DESC",
    "price_low": "current_price ASC, last_seen_at DESC",
    "price_high": "current_price DESC, last_seen_at DESC",
}


@dataclass(frozen=True)
class ActiveDealPage:
    rows: list[dict[str, Any]]
    total: int
    page: int
    page_size: int
    retailer: str | None = None
    query: str | None = None
    min_discount: int | None = None
    sort: str = "recent"
    public_quality_only: bool = True

    @property
    def total_pages(self) -> int:
        return max(1, math.ceil(self.total / max(1, self.page_size)))

    @property
    def clamped_page(self) -> int:
        return max(1, min(self.page, self.total_pages))

    @property
    def has_previous(self) -> bool:
        return self.clamped_page > 1

    @property
    def has_next(self) -> bool:
        return self.clamped_page < self.total_pages


class ActiveDealsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="active_deals", description="Page through deals SniperPlug observed recently.")
    @app_commands.describe(
        retailer="Optional store filter.",
        limit="Rows per page. Max 15.",
        page="Page number to open.",
        search="Optional title/source search.",
        min_discount="Only show cached rows at or above this markdown percent.",
        sort="How to sort cached rows.",
    )
    @app_commands.choices(
        sort=[
            app_commands.Choice(name="Most recent", value="recent"),
            app_commands.Choice(name="Biggest discount", value="discount"),
            app_commands.Choice(name="Highest score", value="score"),
            app_commands.Choice(name="Lowest price", value="price_low"),
            app_commands.Choice(name="Highest price", value="price_high"),
        ]
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def active_deals(
        self,
        interaction: discord.Interaction,
        retailer: str | None = None,
        limit: app_commands.Range[int, 1, ACTIVE_DEALS_MAX_PAGE_SIZE] = ACTIVE_DEALS_DEFAULT_PAGE_SIZE,
        page: app_commands.Range[int, 1, 999] = 1,
        search: str | None = None,
        min_discount: app_commands.Range[int, 0, 95] | None = None,
        sort: app_commands.Choice[str] | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        if interaction.guild_id is None:
            await interaction.followup.send("Use this in a server so I know which recent deal cache to read.", ephemeral=True)
            return
        key = normalize_retailer_key(retailer) if retailer else None
        if key and key not in SUPPORTED_RETAILERS:
            await interaction.followup.send(f"Unknown retailer `{retailer}`. Supported: {format_retailers(tuple(sorted(SUPPORTED_RETAILERS)))}", ephemeral=True)
            return
        sort_key = normalize_sort(sort.value if sort else None)
        page_data = await list_active_deals(
            self.bot.db,
            interaction.guild_id,
            retailer=key,
            limit=int(limit),
            page=int(page),
            search=search,
            min_discount=int(min_discount) if min_discount is not None else None,
            sort=sort_key,
            public_quality_only=min_discount is None,
        )
        view = ActiveDealsPageView(page_data) if page_data.total_pages > 1 else None
        await interaction.followup.send(embed=build_active_deals_embed(interaction.guild_id, page_data), view=view, ephemeral=True)

    @app_commands.command(name="active_deals_cleanup", description="Mark deals stale when SniperPlug has not observed them again.")
    @app_commands.describe(stale_after_hours="Mark deals stale if not observed again after this many hours.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def active_deals_cleanup(self, interaction: discord.Interaction, stale_after_hours: app_commands.Range[int, 1, 168] = DEFAULT_STALE_AFTER_HOURS) -> None:
        await interaction.response.defer(ephemeral=True)
        if interaction.guild_id is None:
            await interaction.followup.send("Use this in a server so I know which cache to clean.", ephemeral=True)
            return
        updated = await mark_stale_deals(self.bot.db, interaction.guild_id, stale_after_hours=int(stale_after_hours))
        await interaction.followup.send(
            f"Marked **{updated}** cached deal(s) stale because SniperPlug had not observed them again in **{stale_after_hours}h**. "
            "Stale means the observation aged out; it does not prove the retailer listing is dead.",
            ephemeral=True,
        )


class ActiveDealsPageView(discord.ui.View):
    def __init__(self, page_data: ActiveDealPage):
        super().__init__(timeout=300)
        self.page_data = page_data
        self.previous_page.disabled = not page_data.has_previous
        self.next_page.disabled = not page_data.has_next

    @discord.ui.button(label="Previous", emoji="⬅️", style=discord.ButtonStyle.secondary)
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.show_page(interaction, self.page_data.clamped_page - 1)

    @discord.ui.button(label="Refresh", emoji="🔄", style=discord.ButtonStyle.secondary)
    async def refresh_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.show_page(interaction, self.page_data.clamped_page)

    @discord.ui.button(label="Next", emoji="➡️", style=discord.ButtonStyle.primary)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.show_page(interaction, self.page_data.clamped_page + 1)

    async def show_page(self, interaction: discord.Interaction, page: int) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message("Use this in a server so I know which recent deal cache to read.", ephemeral=True)
            return
        await interaction.response.defer()
        db = getattr(interaction.client, "db", None)
        if db is None:
            await interaction.followup.send("Database is unavailable right now.", ephemeral=True)
            return
        page_data = await list_active_deals(
            db,
            interaction.guild_id,
            retailer=self.page_data.retailer,
            limit=self.page_data.page_size,
            page=page,
            search=self.page_data.query,
            min_discount=self.page_data.min_discount,
            sort=self.page_data.sort,
            public_quality_only=self.page_data.public_quality_only,
        )
        await interaction.edit_original_response(embed=build_active_deals_embed(interaction.guild_id, page_data), view=ActiveDealsPageView(page_data) if page_data.total_pages > 1 else None)


async def list_active_deals(
    db,
    guild_id: int,
    *,
    retailer: str | None = None,
    limit: int = ACTIVE_DEALS_DEFAULT_PAGE_SIZE,
    page: int = 1,
    search: str | None = None,
    min_discount: int | None = None,
    sort: str = "recent",
    public_quality_only: bool = True,
) -> ActiveDealPage:
    await mark_stale_deals(db, guild_id, stale_after_hours=DEFAULT_STALE_AFTER_HOURS)
    conn = db.require_conn()
    safe_limit = max(1, min(int(limit), ACTIVE_DEALS_MAX_PAGE_SIZE))
    safe_sort = normalize_sort(sort)
    filters = ["guild_id = ?", "status = 'active'"]
    params: list[Any] = [guild_id]
    if retailer:
        filters.append("retailer = ?")
        params.append(retailer)
    if min_discount is not None:
        filters.append("discount IS NOT NULL AND discount >= ?")
        params.append(int(min_discount))
    elif public_quality_only:
        filters.append(
            """(
                (discount IS NOT NULL AND discount >= ?)
                OR LOWER(title) LIKE '%walmart cash%'
                OR LOWER(source_label) LIKE '%walmart_cash%'
                OR LOWER(source_label) LIKE '%cash%'
            )"""
        )
        params.append(ACTIVE_DEALS_DEFAULT_MIN_DISCOUNT)
        filters.append("LOWER(source_label) NOT LIKE '%watchlist%'")
        filters.append("LOWER(source_label) NOT LIKE '%review%'")
        filters.append("LOWER(source_label) NOT LIKE '%scout%'")
    clean_search = " ".join(str(search or "").split())
    if clean_search:
        filters.append("(LOWER(title) LIKE ? OR LOWER(source_label) LIKE ? OR LOWER(retailer) LIKE ?)")
        pattern = f"%{clean_search.lower()}%"
        params.extend([pattern, pattern, pattern])
    where = " AND ".join(filters)
    cursor = await conn.execute(f"SELECT COUNT(*) AS count FROM guild_active_deal_cache WHERE {where}", tuple(params))
    row = await cursor.fetchone()
    total = int(row["count"] if row and row["count"] is not None else 0)
    total_pages = max(1, math.ceil(total / safe_limit))
    safe_page = max(1, min(int(page), total_pages))
    offset = (safe_page - 1) * safe_limit
    cursor = await conn.execute(
        f"""
        SELECT retailer, title, url, current_price, discount, score, source_label, status, first_seen_at, last_seen_at
        FROM guild_active_deal_cache
        WHERE {where}
        ORDER BY {ACTIVE_DEAL_SORTS[safe_sort]}
        LIMIT ? OFFSET ?
        """,
        tuple([*params, safe_limit, offset]),
    )
    rows = await cursor.fetchall()
    return ActiveDealPage(
        rows=[dict(row) for row in rows],
        total=total,
        page=safe_page,
        page_size=safe_limit,
        retailer=retailer,
        query=clean_search or None,
        min_discount=min_discount,
        sort=safe_sort,
    )


async def active_deal_counts(db, guild_id: int) -> dict[str, int]:
    await mark_stale_deals(db, guild_id, stale_after_hours=DEFAULT_STALE_AFTER_HOURS)
    conn = db.require_conn()
    cursor = await conn.execute("SELECT retailer, COUNT(*) AS count FROM guild_active_deal_cache WHERE guild_id = ? AND status = 'active' GROUP BY retailer ORDER BY retailer", (guild_id,))
    rows = await cursor.fetchall()
    return {str(row["retailer"]): int(row["count"] or 0) for row in rows}


async def mark_stale_deals(db, guild_id: int, *, stale_after_hours: int = DEFAULT_STALE_AFTER_HOURS) -> int:
    await ensure_public_post_tables(db)
    conn = db.require_conn()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max(1, int(stale_after_hours)))
    cursor = await conn.execute("UPDATE guild_active_deal_cache SET status = 'stale' WHERE guild_id = ? AND status = 'active' AND last_seen_at < ?", (guild_id, cutoff.isoformat()))
    await conn.commit()
    return int(getattr(cursor, "rowcount", 0) or 0)


def build_active_deals_embed(guild_id: int, page_data: ActiveDealPage) -> discord.Embed:
    filters = []
    if page_data.retailer:
        filters.append(f"retailer `{page_data.retailer}`")
    if page_data.query:
        filters.append(f"search `{page_data.query}`")
    if page_data.min_discount is not None:
        filters.append(f"{page_data.min_discount}%+ markdown")
    filter_text = " • ".join(filters) if filters else "none"
    embed = discord.Embed(
        title="🟢 Recently Observed Deals",
        description=(
            f"Server: `{guild_id}`\n"
            f"Page: **{page_data.clamped_page}/{page_data.total_pages}** • Matching recent observations: **{page_data.total}**\n"
            f"Filters: {filter_text} • Sort: `{page_data.sort}`\n\n"
            f"Rows remain here only while SniperPlug has observed them within the last **{DEFAULT_STALE_AFTER_HOURS} hours**. "
            "This cache is not a live retailer guarantee—open the listing and verify the current price, seller, variant, and stock before buying.\n\n"
            "Default view hides 0% watchlist/review/scout junk. Use `min_discount:0` only for raw cache debugging."
        ),
        color=discord.Color.green() if page_data.rows else discord.Color.dark_gold(),
    )
    if not page_data.rows:
        embed.add_field(
            name="No recent observations matched",
            value="Try lowering filters, changing page to 1, or run a fresh `/deals`, `/hunt`, or `/discover` scan.",
            inline=False,
        )
        return embed
    start_index = (page_data.clamped_page - 1) * page_data.page_size + 1
    for offset, row in enumerate(page_data.rows):
        discount = row.get("discount")
        discount_text = f"{float(discount):.0f}%" if discount is not None else "n/a"
        score = row.get("score") if row.get("score") is not None else "n/a"
        url = str(row.get("url") or "")
        open_text = f"[Open and verify deal]({url})" if url.startswith("http") else "No link saved"
        last_seen = row.get("last_seen_at")
        embed.add_field(
            name=f"#{start_index + offset} • {row.get('retailer', 'retailer')} • {trim(str(row.get('title') or 'deal'), 72)}",
            value=(
                f"Observed price: **{money(row.get('current_price'))}** • Discount: **{discount_text}** • Score: `{score}`\n"
                f"Source: `{trim(str(row.get('source_label') or 'unknown'), 42)}` • Observed: **{observation_age(last_seen)}**\n"
                f"{open_text}"
            ),
            inline=False,
        )
    embed.set_footer(text="Recently observed does not mean currently in stock or unchanged. Verify the retailer page before acting.")
    return embed


def observation_age(value: Any, *, now: datetime | None = None) -> str:
    if not value:
        return "unknown time ago"
    try:
        observed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return "unknown time ago"
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    seconds = max(0, int((current - observed.astimezone(timezone.utc)).total_seconds()))
    if seconds < 60:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 48:
        return f"{hours}h ago"
    return f"{hours // 24}d ago"


def normalize_sort(value: str | None) -> str:
    key = str(value or "recent").strip().lower()
    return key if key in ACTIVE_DEAL_SORTS else "recent"


def money(value) -> str:
    if value is None:
        return "N/A"
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return "N/A"


def trim(value: str, limit: int) -> str:
    text = " ".join(value.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
