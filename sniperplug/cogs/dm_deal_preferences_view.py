from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

import discord

from sniperplug.services.dm_deal_alerts import (
    DmDealAlertPreference,
    UNLIMITED_MAX_ALERTS_PER_DAY,
    daily_cap_label,
    save_dm_deal_alert_preference,
)
from sniperplug.services.dm_deal_routes import route_labels, split_dm_route_preferences
from sniperplug.services.dm_personal_categories import (
    all_personal_categories,
    category_label,
    compose_category_preferences,
    compose_exclude_terms,
    flip_settings,
    muted_category_preferences,
    personal_category_pages,
    split_category_preferences,
    split_exclude_terms,
)
from sniperplug.services.dm_product_interests import (
    brand_label,
    normalize_brands,
    product_interest_label,
    product_interest_pages,
    split_product_preferences,
    update_product_preferences,
)


SCREEN_HOME = "home"
SCREEN_ROUTES = "routes"
SCREEN_PRODUCTS = "products"
SCREEN_CATEGORIES = "categories"

TAB_FAVORITES = "favorites"
TAB_MUTED = "muted"
TAB_ALLOWLIST = "allowlist"
TAB_LABELS = {
    TAB_FAVORITES: "⭐ Favorites",
    TAB_MUTED: "🔕 Muted",
    TAB_ALLOWLIST: "🎯 Only these categories",
}
DM_MODE_ORDER = ("smart", "all", "custom")
DM_MODE_LABELS = {
    "smart": "Smart",
    "all": "All",
    "custom": "Custom",
}
_MAX_PRICE_CLEAR_TERMS = {
    "off",
    "none",
    "clear",
    "no limit",
    "no-limit",
    "nolimit",
}
_DAILY_CAP_UNLIMITED_TERMS = {
    "0",
    "off",
    "none",
    "unlimited",
    "no limit",
    "no-limit",
    "nolimit",
}


@dataclass(frozen=True)
class DmDealRuleDraft:
    """Only the personal-DM rule values the user chose to change."""

    min_discount: int | None = None
    min_savings_cents: int | None = None
    min_score: int | None = None
    max_price_cents: int | None = None
    clear_max_price: bool = False
    daily_cap: int | None = None

    @property
    def has_updates(self) -> bool:
        return any(
            value is not None
            for value in (
                self.min_discount,
                self.min_savings_cents,
                self.min_score,
                self.max_price_cents,
                self.daily_cap,
            )
        ) or self.clear_max_price


def parse_dm_rule_values(
    *,
    threshold_text: str,
    savings_text: str,
    score_text: str,
    max_price_text: str,
    daily_cap_text: str,
) -> DmDealRuleDraft:
    """Parse only fields the user filled; blank fields mean keep current values."""

    threshold_raw = str(threshold_text or "").strip()
    threshold: int | None = None
    if threshold_raw:
        try:
            threshold = int(threshold_raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "DM Deal Threshold must be a whole number from 0 to 95."
            ) from exc
        if not 0 <= threshold <= 95:
            raise ValueError("DM Deal Threshold must be between 0% and 95%.")

    savings_raw = str(savings_text or "").strip()
    min_savings_cents: int | None = None
    if savings_raw:
        savings = _parse_dollars(savings_raw, field_name="Minimum savings")
        if not 0 <= savings <= 100_000:
            raise ValueError("Minimum savings must be between $0 and $100,000.")
        min_savings_cents = int(round(savings * 100))

    score_raw = str(score_text or "").strip()
    score: int | None = None
    if score_raw:
        try:
            score = int(score_raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "Minimum Sniper score must be a whole number from 0 to 250."
            ) from exc
        if not 0 <= score <= 250:
            raise ValueError("Minimum Sniper score must be between 0 and 250.")

    max_price_raw = str(max_price_text or "").strip()
    max_price_cents: int | None = None
    clear_max_price = False
    if max_price_raw:
        if max_price_raw.lower() in _MAX_PRICE_CLEAR_TERMS:
            clear_max_price = True
        else:
            max_price = _parse_dollars(
                max_price_raw,
                field_name="Maximum item price",
            )
            if not 0.01 <= max_price <= 100_000:
                raise ValueError(
                    "Maximum item price must be between $0.01 and $100,000, "
                    "or type `off` to remove the limit."
                )
            max_price_cents = int(round(max_price * 100))

    daily_cap_raw = str(daily_cap_text or "").strip()
    daily_cap: int | None = None
    if daily_cap_raw:
        if daily_cap_raw.lower() in _DAILY_CAP_UNLIMITED_TERMS:
            daily_cap = UNLIMITED_MAX_ALERTS_PER_DAY
        else:
            try:
                daily_cap = int(daily_cap_raw)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    "Daily DM limit must be 1 to 100, or `unlimited`."
                ) from exc
            if not 1 <= daily_cap <= 100:
                raise ValueError(
                    "Daily DM limit must be between 1 and 100 alerts, or `unlimited`."
                )

    return DmDealRuleDraft(
        min_discount=threshold,
        min_savings_cents=min_savings_cents,
        min_score=score,
        max_price_cents=max_price_cents,
        clear_max_price=clear_max_price,
        daily_cap=daily_cap,
    )


def _parse_dollars(value: str, *, field_name: str) -> float:
    raw = str(value or "").replace("$", "").replace(",", "").strip()
    try:
        return float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{field_name} must be a dollar amount such as 20 or 49.99."
        ) from exc


def _mode_help(mode: str) -> str:
    if mode == "smart":
        return "adapts quality requirements upward by price while keeping your floors hard"
    if mode == "custom":
        return "uses your manual floors exactly"
    return "uses your saved hard floors without Smart price-based increases"


class DmDealPreferencesView(discord.ui.View):
    """Guided private editor for one personal-DM subscriber."""

    def __init__(
        self,
        *,
        bot: Any,
        user_id: int,
        preference: DmDealAlertPreference,
    ) -> None:
        super().__init__(timeout=15 * 60)
        self.bot = bot
        self.user_id = int(user_id)
        self.preference = preference.normalized()

        selected, favorites = split_category_preferences(self.preference.categories)
        product_interests, product_brands = split_product_preferences(
            self.preference.categories
        )
        keyword_excludes, legacy_muted = split_exclude_terms(
            self.preference.exclude_keywords
        )
        stored_muted = muted_category_preferences(self.preference.categories)
        flip_enabled, flip_profit = flip_settings(self.preference.categories)

        self.selected_categories = set(selected)
        self.favorite_categories = set(favorites)
        self.muted_categories = set((*legacy_muted, *stored_muted))
        self.product_interests = set(product_interests)
        self.product_brands = set(product_brands)
        self.selected_routes = set(split_dm_route_preferences(self.preference.categories))
        self.exclude_keywords = tuple(keyword_excludes)
        self.flip_enabled = bool(flip_enabled)
        self.flip_min_profit_cents = int(flip_profit)
        self._sync_dm_rule_draft(self.preference)

        self.screen = SCREEN_HOME
        self.tab = TAB_FAVORITES
        self.search_query = ""
        self.page_index = 0
        self.product_page_index = 0
        self.saved = False
        self.dirty = False
        self._rebuild_items()

    def _sync_dm_rule_draft(self, preference: DmDealAlertPreference) -> None:
        normalized = preference.normalized()
        self.dm_mode = normalized.mode
        self.dm_min_discount = int(normalized.min_discount)
        self.dm_min_savings_cents = int(normalized.min_savings_cents)
        self.dm_min_score = int(normalized.min_score)
        self.dm_max_price_cents = normalized.max_price_cents
        self.dm_daily_cap = int(normalized.max_alerts_per_day)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) == self.user_id:
            return True
        await interaction.response.send_message(
            "This is someone else's private SniperPlug preference menu.",
            ephemeral=True,
        )
        return False

    def build_embed(self, *, status: str = "") -> discord.Embed:
        if self.screen == SCREEN_ROUTES:
            embed = self._build_routes_embed()
        elif self.screen == SCREEN_PRODUCTS:
            embed = self._build_products_embed()
        elif self.screen == SCREEN_CATEGORIES:
            embed = self._build_categories_embed()
        else:
            embed = self._build_home_embed()

        if status:
            embed.description = (
                f"{status}\n\n{embed.description}"
                if embed.description
                else status
            )
        embed.set_footer(
            text=(
                "Unsaved changes. Press Save changes before closing."
                if self.dirty
                else "Everything shown is saved."
            )
        )
        return embed

    def _build_routes_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="🚨 DM deal routes",
            description=(
                "Choose the **kinds of verified deals** you want, without memorizing "
                "products or categories. Multiple selections work as OR.\n\n"
                "When routes are selected, they replace product/category inclusion "
                "filters, but price, markdown, score, savings, keywords and daily "
                "limits still apply. Clear routes to use product/category filters again."
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Your selected routes", value=self._route_preview(), inline=False)
        return embed

    def _route_preview(self) -> str:
        labels = dict(route_labels())
        return (
            ", ".join(labels[route] for route in labels if route in self.selected_routes)
            if self.selected_routes
            else "None (product/category personalization is active)"
        )

    def _build_home_embed(self) -> discord.Embed:
        max_price_text = (
            f"${self.dm_max_price_cents / 100:,.2f}"
            if self.dm_max_price_cents is not None
            else "No limit"
        )
        embed = discord.Embed(
            title="🎯 Personal Walmart Deal Alerts",
            description=(
                "**Deal routes** lets you follow Price Glitches, Hot Deals and other "
                "verified deal types without choosing products. Products and categories "
                "remain optional for specific interests.\n\n"
                "Selected routes take priority over old product/category filters. "
                "Your hard deal rules always apply."
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="🚨 Deal routes",
            value=f"**{self._route_preview()}**",
            inline=False,
        )
        embed.add_field(
            name="🎯 Products & brands",
            value=(
                f"Products: **{len(self.product_interests)}** • Brands: **{len(self.product_brands)}**\n"
                f"{self._product_interest_preview()}"
            ),
            inline=False,
        )
        embed.add_field(
            name="🗂️ Categories",
            value=(
                f"Favorites **{len(self.favorite_categories)}** • "
                f"Muted **{len(self.muted_categories)}** • "
                f"Only-these **{len(self.selected_categories) if self.selected_categories else 'off'}**"
            ),
            inline=False,
        )
        embed.add_field(
            name="🔔 Deal rules",
            value=(
                f"**{self.dm_min_discount}%+** markdown • "
                f"${self.dm_min_savings_cents / 100:,.2f}+ savings • "
                f"score {self.dm_min_score}/250\n"
                f"Mode **{DM_MODE_LABELS.get(self.dm_mode, self.dm_mode.title())}** • "
                f"Max price **{max_price_text}** • "
                f"Daily **{daily_cap_label(self.dm_daily_cap)}**"
            ),
            inline=False,
        )
        embed.add_field(
            name="💰 Flip Override",
            value=(
                f"**{'ON' if self.flip_enabled else 'OFF'}** • minimum estimated net profit "
                f"**${self.flip_min_profit_cents / 100:,.0f}+**"
            ),
            inline=False,
        )
        return embed

    def _build_products_embed(self) -> discord.Embed:
        pages = product_interest_pages()
        page_total = max(1, len(pages))
        page_number = min(self.product_page_index + 1, page_total)
        embed = discord.Embed(
            title="🎯 Choose specific products",
            description=(
                "Select the product types you actually want. No product codes and no typing "
                "guesswork. Selections on other pages stay selected.\n\n"
                f"Page **{page_number}/{page_total}** • Selected **{len(self.product_interests)}**"
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="Selected products",
            value=self._product_interest_preview(),
            inline=False,
        )
        embed.add_field(
            name="Optional brands",
            value=(
                f"**{self._brand_preview()}**\n"
                "Brands narrow the selected products further. If both are set, a deal must "
                "match a selected product and a selected brand."
            ),
            inline=False,
        )
        return embed

    def _build_categories_embed(self) -> discord.Embed:
        categories = all_personal_categories()
        pages = self._pages()
        page_total = max(1, len(pages))
        page_number = min(self.page_index + 1, page_total)
        search_text = self.search_query or "all"
        tab_help = {
            TAB_FAVORITES: "Favorites get a small Smart-mode priority boost.",
            TAB_MUTED: "Muted categories are hidden only when no specific Walmart product/brand is selected.",
            TAB_ALLOWLIST: "Optional. Leave empty unless you want category-only alerts.",
        }[self.tab]
        embed = discord.Embed(
            title="🗂️ Category preferences",
            description=(
                "Categories are the broad fallback layer. Use **Products & brands** for precise "
                "Walmart alerts.\n\n"
                f"Editing **{TAB_LABELS[self.tab]}** • Page **{page_number}/{page_total}** • "
                f"Search **{search_text}**\n{tab_help}\n"
                f"All {len(categories)} live categories remain available."
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="Selected in this category mode",
            value=self._selection_preview(),
            inline=False,
        )
        return embed

    def _pages(self):
        return personal_category_pages(self.search_query)

    def _product_pages(self):
        return product_interest_pages()

    def _active_set(self) -> set[str]:
        if self.tab == TAB_MUTED:
            return self.muted_categories
        if self.tab == TAB_ALLOWLIST:
            return self.selected_categories
        return self.favorite_categories

    def _current_page(self):
        pages = self._pages()
        if not pages:
            self.page_index = 0
            return ()
        self.page_index = max(0, min(self.page_index, len(pages) - 1))
        return pages[self.page_index]

    def _current_product_page(self):
        pages = self._product_pages()
        if not pages:
            self.product_page_index = 0
            return ()
        self.product_page_index = max(
            0,
            min(self.product_page_index, len(pages) - 1),
        )
        return pages[self.product_page_index]

    def _selection_preview(self) -> str:
        selected = sorted(
            self._active_set(),
            key=lambda key: category_label(key).lower(),
        )
        if not selected:
            return (
                "None"
                if self.tab != TAB_ALLOWLIST
                else "Off — all categories remain eligible"
            )
        labels = [category_label(key) for key in selected[:10]]
        suffix = f" • +{len(selected) - 10} more" if len(selected) > 10 else ""
        return ", ".join(labels) + suffix

    def _product_interest_preview(self) -> str:
        if not self.product_interests:
            return "No specific products selected"
        labels = [
            product_interest_label(key)
            for key in sorted(self.product_interests)
        ]
        return ", ".join(labels[:7]) + (
            f" • +{len(labels) - 7} more" if len(labels) > 7 else ""
        )

    def _brand_preview(self) -> str:
        if not self.product_brands:
            return "All brands"
        labels = [brand_label(slug) for slug in sorted(self.product_brands)]
        return ", ".join(labels[:8]) + (
            f" • +{len(labels) - 8} more" if len(labels) > 8 else ""
        )

    def _rebuild_items(self) -> None:
        self.clear_items()
        if self.screen == SCREEN_ROUTES:
            self._build_route_items()
        elif self.screen == SCREEN_PRODUCTS:
            self._build_product_items()
        elif self.screen == SCREEN_CATEGORIES:
            self._build_category_items()
        else:
            self._build_home_items()

    def _build_home_items(self) -> None:
        routes = discord.ui.Button(
            label=f"Deal routes ({len(self.selected_routes)})",
            emoji="🚨",
            style=discord.ButtonStyle.primary,
            row=0,
        )
        routes.callback = self._show_routes
        self.add_item(routes)

        products = discord.ui.Button(
            label=f"Products & brands ({len(self.product_interests)})",
            emoji="🎯",
            style=discord.ButtonStyle.primary,
            row=0,
        )
        products.callback = self._show_products
        self.add_item(products)

        categories = discord.ui.Button(
            label="Categories",
            emoji="🗂️",
            style=discord.ButtonStyle.secondary,
            row=0,
        )
        categories.callback = self._show_categories
        self.add_item(categories)

        dm_rules = discord.ui.Button(
            label=f"Deal rules: {self.dm_min_discount}%+",
            emoji="🔔",
            style=discord.ButtonStyle.secondary,
            row=1,
        )
        dm_rules.callback = self._open_dm_rules
        self.add_item(dm_rules)

        mode = discord.ui.Button(
            label=f"Mode: {DM_MODE_LABELS.get(self.dm_mode, self.dm_mode.title())}",
            emoji="🎚️",
            style=discord.ButtonStyle.secondary,
            row=1,
        )
        mode.callback = self._cycle_mode
        self.add_item(mode)

        flip = discord.ui.Button(
            label=f"Flip Override: {'ON' if self.flip_enabled else 'OFF'}",
            emoji="💰",
            style=(
                discord.ButtonStyle.success
                if self.flip_enabled
                else discord.ButtonStyle.secondary
            ),
            row=2,
        )
        flip.callback = self._toggle_flip
        self.add_item(flip)

        profit = discord.ui.Button(
            label=f"Flip Profit: ${self.flip_min_profit_cents / 100:,.0f}+",
            emoji="📈",
            style=discord.ButtonStyle.secondary,
            row=2,
        )
        profit.callback = self._open_profit
        self.add_item(profit)

        self._add_save_close(row=3)

    def _build_route_items(self) -> None:
        self.add_item(_DmRouteSelect(self))
        clear = discord.ui.Button(
            label="Clear selected routes",
            emoji="🧹",
            style=discord.ButtonStyle.secondary,
            row=1,
            disabled=not bool(self.selected_routes),
        )
        clear.callback = self._clear_routes
        self.add_item(clear)
        dashboard = discord.ui.Button(
            label="Dashboard",
            emoji="⬅️",
            style=discord.ButtonStyle.secondary,
            row=2,
        )
        dashboard.callback = self._show_home
        self.add_item(dashboard)
        self._add_save_close(row=2)

    def _build_product_items(self) -> None:
        self.add_item(_ProductPageSelect(self))
        pages = self._product_pages()
        last_index = max(0, len(pages) - 1)

        previous = discord.ui.Button(
            label="Back",
            emoji="◀️",
            style=discord.ButtonStyle.secondary,
            row=1,
            disabled=self.product_page_index <= 0,
        )
        previous.callback = self._previous_product_page
        self.add_item(previous)

        page = discord.ui.Button(
            label=(
                f"Page {min(self.product_page_index + 1, max(1, len(pages)))}/"
                f"{max(1, len(pages))}"
            ),
            style=discord.ButtonStyle.secondary,
            row=1,
            disabled=True,
        )
        self.add_item(page)

        next_button = discord.ui.Button(
            label="Next",
            emoji="▶️",
            style=discord.ButtonStyle.secondary,
            row=1,
            disabled=(not pages or self.product_page_index >= last_index),
        )
        next_button.callback = self._next_product_page
        self.add_item(next_button)

        brands = discord.ui.Button(
            label=f"Brands: {len(self.product_brands)}",
            emoji="🏷️",
            style=(
                discord.ButtonStyle.primary
                if self.product_brands
                else discord.ButtonStyle.secondary
            ),
            row=2,
        )
        brands.callback = self._open_brands
        self.add_item(brands)

        clear_products = discord.ui.Button(
            label="Clear products",
            emoji="🧹",
            style=discord.ButtonStyle.secondary,
            row=2,
            disabled=not bool(self.product_interests),
        )
        clear_products.callback = self._clear_products
        self.add_item(clear_products)

        clear_brands = discord.ui.Button(
            label="Clear brands",
            emoji="🧹",
            style=discord.ButtonStyle.secondary,
            row=2,
            disabled=not bool(self.product_brands),
        )
        clear_brands.callback = self._clear_brands
        self.add_item(clear_brands)

        dashboard = discord.ui.Button(
            label="Dashboard",
            emoji="⬅️",
            style=discord.ButtonStyle.secondary,
            row=3,
        )
        dashboard.callback = self._show_home
        self.add_item(dashboard)
        self._add_save_close(row=3)

    def _build_category_items(self) -> None:
        self.add_item(_PreferenceTabSelect(self))
        self.add_item(_CategoryPageSelect(self))

        pages = self._pages()
        last_index = max(0, len(pages) - 1)

        previous = discord.ui.Button(
            label="Back",
            emoji="◀️",
            style=discord.ButtonStyle.secondary,
            row=2,
            disabled=self.page_index <= 0,
        )
        previous.callback = self._previous_page
        self.add_item(previous)

        page = discord.ui.Button(
            label=(
                f"Page {min(self.page_index + 1, max(1, len(pages)))}/"
                f"{max(1, len(pages))}"
            ),
            style=discord.ButtonStyle.secondary,
            row=2,
            disabled=True,
        )
        self.add_item(page)

        next_button = discord.ui.Button(
            label="Next",
            emoji="▶️",
            style=discord.ButtonStyle.secondary,
            row=2,
            disabled=(not pages or self.page_index >= last_index),
        )
        next_button.callback = self._next_page
        self.add_item(next_button)

        search = discord.ui.Button(
            label="Search",
            emoji="🔎",
            style=discord.ButtonStyle.secondary,
            row=3,
        )
        search.callback = self._open_search
        self.add_item(search)

        clear_search = discord.ui.Button(
            label="All categories",
            emoji="🧹",
            style=discord.ButtonStyle.secondary,
            row=3,
            disabled=not bool(self.search_query),
        )
        clear_search.callback = self._clear_search
        self.add_item(clear_search)

        clear_tab = discord.ui.Button(
            label=f"Clear {TAB_LABELS[self.tab].split(' ', 1)[1]}",
            emoji="🗑️",
            style=discord.ButtonStyle.secondary,
            row=3,
            disabled=not bool(self._active_set()),
        )
        clear_tab.callback = self._clear_current_tab
        self.add_item(clear_tab)

        dashboard = discord.ui.Button(
            label="Dashboard",
            emoji="⬅️",
            style=discord.ButtonStyle.secondary,
            row=4,
        )
        dashboard.callback = self._show_home
        self.add_item(dashboard)
        self._add_save_close(row=4)

    def _add_save_close(self, *, row: int) -> None:
        save = discord.ui.Button(
            label="Save changes" if self.dirty else "Saved",
            emoji="✅",
            style=discord.ButtonStyle.success,
            row=row,
            disabled=not self.dirty,
        )
        save.callback = self._save
        self.add_item(save)

        close = discord.ui.Button(
            label="Discard & close" if self.dirty else "Close",
            emoji="✖️",
            style=(
                discord.ButtonStyle.danger
                if self.dirty
                else discord.ButtonStyle.secondary
            ),
            row=row,
        )
        close.callback = self._close
        self.add_item(close)

    async def _refresh(
        self,
        interaction: discord.Interaction,
        *,
        status: str = "",
    ) -> None:
        self._rebuild_items()
        await interaction.response.edit_message(
            embed=self.build_embed(status=status),
            view=self,
        )

    async def _show_home(self, interaction: discord.Interaction) -> None:
        self.screen = SCREEN_HOME
        await self._refresh(interaction)

    async def _show_routes(self, interaction: discord.Interaction) -> None:
        self.screen = SCREEN_ROUTES
        await self._refresh(interaction)

    async def _clear_routes(self, interaction: discord.Interaction) -> None:
        self.selected_routes.clear()
        self.dirty = True
        await self._refresh(interaction, status="Route selection cleared. Product/category filters apply.")

    async def _show_products(self, interaction: discord.Interaction) -> None:
        self.screen = SCREEN_PRODUCTS
        await self._refresh(interaction)

    async def _show_categories(self, interaction: discord.Interaction) -> None:
        self.screen = SCREEN_CATEGORIES
        await self._refresh(interaction)

    async def _previous_product_page(self, interaction: discord.Interaction) -> None:
        self.product_page_index = max(0, self.product_page_index - 1)
        await self._refresh(interaction)

    async def _next_product_page(self, interaction: discord.Interaction) -> None:
        pages = self._product_pages()
        self.product_page_index = min(
            max(0, len(pages) - 1),
            self.product_page_index + 1,
        )
        await self._refresh(interaction)

    async def _previous_page(self, interaction: discord.Interaction) -> None:
        self.page_index = max(0, self.page_index - 1)
        await self._refresh(interaction)

    async def _next_page(self, interaction: discord.Interaction) -> None:
        pages = self._pages()
        self.page_index = min(
            max(0, len(pages) - 1),
            self.page_index + 1,
        )
        await self._refresh(interaction)

    async def _open_search(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(_CategorySearchModal(self))

    async def _open_dm_rules(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(_DmDealRulesModal(self))

    async def _open_brands(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(_BrandFilterModal(self))

    async def _cycle_mode(self, interaction: discord.Interaction) -> None:
        try:
            index = DM_MODE_ORDER.index(self.dm_mode)
        except ValueError:
            index = -1
        self.dm_mode = DM_MODE_ORDER[(index + 1) % len(DM_MODE_ORDER)]
        self.dirty = True
        await self._refresh(
            interaction,
            status=(
                f"🎚️ Mode changed to **{DM_MODE_LABELS[self.dm_mode]}** — "
                f"{_mode_help(self.dm_mode)}."
            ),
        )

    async def _clear_search(self, interaction: discord.Interaction) -> None:
        self.search_query = ""
        self.page_index = 0
        await self._refresh(interaction)

    async def _clear_current_tab(self, interaction: discord.Interaction) -> None:
        active = self._active_set()
        changed = bool(active)
        if changed:
            active.clear()
            self.dirty = True
        await self._refresh(
            interaction,
            status=(
                f"🧹 Cleared {TAB_LABELS[self.tab]}."
                if changed
                else f"{TAB_LABELS[self.tab]} is already empty."
            ),
        )

    async def _clear_products(self, interaction: discord.Interaction) -> None:
        changed = bool(self.product_interests)
        if changed:
            self.product_interests.clear()
            self.dirty = True
        await self._refresh(
            interaction,
            status=(
                "🧹 Cleared specific products."
                if changed
                else "No specific products were selected."
            ),
        )

    async def _clear_brands(self, interaction: discord.Interaction) -> None:
        changed = bool(self.product_brands)
        if changed:
            self.product_brands.clear()
            self.dirty = True
        await self._refresh(
            interaction,
            status=(
                "🧹 Cleared brand filters."
                if changed
                else "No brand filters were selected."
            ),
        )

    async def _toggle_flip(self, interaction: discord.Interaction) -> None:
        self.flip_enabled = not self.flip_enabled
        self.dirty = True
        await self._refresh(
            interaction,
            status=(
                "💰 Flip Override enabled."
                if self.flip_enabled
                else "💰 Flip Override disabled."
            ),
        )

    async def _open_profit(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(_FlipProfitModal(self))

    async def _save(self, interaction: discord.Interaction) -> None:
        categories = compose_category_preferences(
            self.selected_categories,
            self.favorite_categories,
            muted=self.muted_categories,
            flip_enabled=self.flip_enabled,
            flip_min_profit_cents=self.flip_min_profit_cents,
            routes=self.selected_routes,
        )
        categories = update_product_preferences(
            categories,
            replace_interests=self.product_interests,
            replace_brands=self.product_brands,
        )
        excludes = compose_exclude_terms(self.exclude_keywords)
        updated = replace(
            self.preference,
            mode=self.dm_mode,
            min_discount=self.dm_min_discount,
            min_savings_cents=self.dm_min_savings_cents,
            min_score=self.dm_min_score,
            max_price_cents=self.dm_max_price_cents,
            max_alerts_per_day=self.dm_daily_cap,
            categories=categories,
            exclude_keywords=excludes,
            failure_count=0,
            last_error="",
        ).normalized()
        saved = await save_dm_deal_alert_preference(self.bot.db, updated)
        self.preference = saved
        self._sync_dm_rule_draft(saved)
        self.saved = True
        self.dirty = False
        self.screen = SCREEN_HOME
        self._rebuild_items()
        await interaction.response.edit_message(
            embed=self.build_embed(
                status="✅ Saved. The menu is still open if you want to change anything else."
            ),
            view=self,
        )

    async def _close(self, interaction: discord.Interaction) -> None:
        discarded = self.dirty
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            embed=self.build_embed(
                status=(
                    "🗑️ Closed. Unsaved changes were discarded; your last saved settings remain active."
                    if discarded
                    else "✅ Closed. Your saved settings remain active."
                )
            ),
            view=self,
        )
        self.stop()


class _DmRouteSelect(discord.ui.Select):
    def __init__(self, owner: DmDealPreferencesView) -> None:
        self.owner = owner
        options = [
            discord.SelectOption(label=label, value=route, default=route in owner.selected_routes)
            for route, label in route_labels()
        ]
        super().__init__(
            placeholder="Select which verified deal types to DM",
            min_values=1,
            max_values=len(options),
            options=options,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        self.owner.selected_routes = set(self.values)
        self.owner.dirty = True
        await self.owner._refresh(interaction, status="Updated routes. Save changes to apply.")


class _ProductPageSelect(discord.ui.Select):
    def __init__(self, owner: DmDealPreferencesView) -> None:
        self.owner = owner
        page = owner._current_product_page()
        options = [
            discord.SelectOption(
                label=profile.label[:100],
                value=profile.key,
                emoji=profile.emoji,
                description=(
                    profile.description
                    or "Select this exact product type for Walmart DMs."
                )[:100],
                default=profile.key in owner.product_interests,
            )
            for profile in page
        ]
        if not options:
            options = [
                discord.SelectOption(
                    label="No product profiles available",
                    value="__none__",
                )
            ]
        super().__init__(
            placeholder="Select the specific products you want",
            min_values=0,
            max_values=len(options),
            options=options,
            disabled=not bool(page),
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        page_keys = {profile.key for profile in self.owner._current_product_page()}
        selected = {value for value in self.values if value != "__none__"}
        before = set(self.owner.product_interests)
        self.owner.product_interests.difference_update(page_keys)
        self.owner.product_interests.update(selected)
        if before != self.owner.product_interests:
            self.owner.dirty = True
        await self.owner._refresh(
            interaction,
            status=(
                f"🎯 Product choices updated. **{len(self.owner.product_interests)}** selected."
                if before != self.owner.product_interests
                else "No product changes on this page."
            ),
        )


class _PreferenceTabSelect(discord.ui.Select):
    def __init__(self, owner: DmDealPreferencesView) -> None:
        self.owner = owner
        options = [
            discord.SelectOption(
                label="Favorites",
                value=TAB_FAVORITES,
                emoji="⭐",
                description="Prioritize without hiding other categories.",
                default=owner.tab == TAB_FAVORITES,
            ),
            discord.SelectOption(
                label="Muted",
                value=TAB_MUTED,
                emoji="🔕",
                description="Hide broad categories when no specific product is set.",
                default=owner.tab == TAB_MUTED,
            ),
            discord.SelectOption(
                label="Only these categories",
                value=TAB_ALLOWLIST,
                emoji="🎯",
                description="Optional broad allowlist. Empty means all categories.",
                default=owner.tab == TAB_ALLOWLIST,
            ),
        ]
        super().__init__(
            placeholder="Choose the category setting to edit",
            min_values=1,
            max_values=1,
            options=options,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        self.owner.tab = self.values[0]
        self.owner.page_index = 0
        await self.owner._refresh(interaction)


class _CategoryPageSelect(discord.ui.Select):
    def __init__(self, owner: DmDealPreferencesView) -> None:
        self.owner = owner
        page = owner._current_page()
        active = owner._active_set()
        if not page:
            options = [
                discord.SelectOption(
                    label="No categories match this search",
                    value="__none__",
                    description="Search again or press All categories.",
                )
            ]
            super().__init__(
                placeholder="No matching categories",
                min_values=1,
                max_values=1,
                options=options,
                disabled=True,
                row=1,
            )
            return

        options = [
            discord.SelectOption(
                label=category.label[:100],
                value=category.key,
                description=(
                    f"Scan priority {category.demand_level}/100 • discovery baseline "
                    f"{category.min_discount_percent:g}%"
                )[:100],
                default=category.key in active,
            )
            for category in page
        ]
        super().__init__(
            placeholder=f"Select {TAB_LABELS[owner.tab].split(' ', 1)[1].lower()}",
            min_values=0,
            max_values=len(options),
            options=options,
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        page_keys = {category.key for category in self.owner._current_page()}
        selected = {value for value in self.values if value != "__none__"}
        active = self.owner._active_set()
        before = set(active)
        active.difference_update(page_keys)
        active.update(selected)

        opposite_changed = False
        if self.owner.tab == TAB_FAVORITES:
            opposite_before = set(self.owner.muted_categories)
            self.owner.muted_categories.difference_update(selected)
            opposite_changed = opposite_before != self.owner.muted_categories
        elif self.owner.tab == TAB_MUTED:
            opposite_before = set(self.owner.favorite_categories)
            self.owner.favorite_categories.difference_update(selected)
            opposite_changed = opposite_before != self.owner.favorite_categories

        changed = before != active or opposite_changed
        if changed:
            self.owner.dirty = True
        await self.owner._refresh(
            interaction,
            status=(
                f"🗂️ Category choices updated. {len(selected)} selected on this page."
                if changed
                else "No category changes on this page."
            ),
        )


class _CategorySearchModal(discord.ui.Modal, title="Search every deal category"):
    query = discord.ui.TextInput(
        label="Category or interest",
        placeholder="Examples: tech, gaming, baby, sneakers, tools, coffee",
        required=False,
        max_length=100,
    )

    def __init__(self, owner: DmDealPreferencesView) -> None:
        super().__init__()
        self.owner = owner
        self.query.default = owner.search_query

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.owner.search_query = " ".join(
            str(self.query.value or "").split()
        )
        self.owner.page_index = 0
        await self.owner._refresh(
            interaction,
            status=(
                f"🔎 Showing category matches for **{self.owner.search_query}**."
                if self.owner.search_query
                else "Showing every category."
            ),
        )


class _BrandFilterModal(discord.ui.Modal, title="Optional Walmart brands"):
    brands = discord.ui.TextInput(
        label="Brands, separated by commas",
        placeholder="Anker, Apple, Dior, Versace, LEGO, DeWalt",
        required=False,
        max_length=400,
        style=discord.TextStyle.paragraph,
    )

    def __init__(self, owner: DmDealPreferencesView) -> None:
        super().__init__()
        self.owner = owner
        self.brands.default = ", ".join(
            brand_label(slug)
            for slug in sorted(owner.product_brands)
        )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        new_brands = set(normalize_brands(str(self.brands.value or "")))
        changed = new_brands != self.owner.product_brands
        if changed:
            self.owner.product_brands = new_brands
            self.owner.dirty = True
        await self.owner._refresh(
            interaction,
            status=(
                f"🏷️ Brand filters updated. **{len(new_brands)}** selected."
                if changed
                else "Brand filters were already the same."
            ),
        )


class _DmDealRulesModal(
    discord.ui.Modal,
    title="Change only the DM rules you want",
):
    threshold = discord.ui.TextInput(
        label="DM Deal Threshold (%) — optional",
        placeholder="Blank keeps current",
        required=False,
        max_length=2,
    )
    savings = discord.ui.TextInput(
        label="Minimum savings ($) — optional",
        placeholder="Blank keeps current",
        required=False,
        max_length=12,
    )
    score = discord.ui.TextInput(
        label="Minimum Sniper score — optional",
        placeholder="Blank keeps current",
        required=False,
        max_length=3,
    )
    max_price = discord.ui.TextInput(
        label="Maximum item price ($) — optional",
        placeholder="Blank keeps current • type off for no limit",
        required=False,
        max_length=12,
    )
    daily_cap = discord.ui.TextInput(
        label="Daily DM limit — optional",
        placeholder="Blank keeps current • type unlimited for no cap",
        required=False,
        max_length=12,
    )

    def __init__(self, owner: DmDealPreferencesView) -> None:
        super().__init__()
        self.owner = owner
        self.threshold.placeholder = f"Current: {owner.dm_min_discount}% • blank keeps"
        self.savings.placeholder = (
            f"Current: ${owner.dm_min_savings_cents / 100:,.2f} • blank keeps"
        )
        self.score.placeholder = f"Current: {owner.dm_min_score}/250 • blank keeps"
        if owner.dm_max_price_cents is None:
            self.max_price.placeholder = "Current: no limit • enter an amount to set one"
        else:
            self.max_price.placeholder = (
                f"Current: ${owner.dm_max_price_cents / 100:,.2f} • "
                "blank keeps • type off to remove"
            )
        self.daily_cap.placeholder = (
            f"Current: {daily_cap_label(owner.dm_daily_cap)} • blank keeps"
        )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            rules = parse_dm_rule_values(
                threshold_text=str(self.threshold.value or ""),
                savings_text=str(self.savings.value or ""),
                score_text=str(self.score.value or ""),
                max_price_text=str(self.max_price.value or ""),
                daily_cap_text=str(self.daily_cap.value or ""),
            )
        except ValueError as error:
            await interaction.response.send_message(
                f"⚠️ {error}",
                ephemeral=True,
            )
            return

        changed: list[str] = []
        if rules.min_discount is not None and rules.min_discount != self.owner.dm_min_discount:
            self.owner.dm_min_discount = rules.min_discount
            changed.append(f"threshold **{rules.min_discount}%+**")
        if (
            rules.min_savings_cents is not None
            and rules.min_savings_cents != self.owner.dm_min_savings_cents
        ):
            self.owner.dm_min_savings_cents = rules.min_savings_cents
            changed.append(
                f"minimum savings **${rules.min_savings_cents / 100:,.2f}**"
            )
        if rules.min_score is not None and rules.min_score != self.owner.dm_min_score:
            self.owner.dm_min_score = rules.min_score
            changed.append(f"minimum score **{rules.min_score}/250**")
        if rules.clear_max_price:
            if self.owner.dm_max_price_cents is not None:
                self.owner.dm_max_price_cents = None
                changed.append("maximum price **removed**")
        elif (
            rules.max_price_cents is not None
            and rules.max_price_cents != self.owner.dm_max_price_cents
        ):
            self.owner.dm_max_price_cents = rules.max_price_cents
            changed.append(
                f"maximum price **${rules.max_price_cents / 100:,.2f}**"
            )
        if rules.daily_cap is not None and rules.daily_cap != self.owner.dm_daily_cap:
            self.owner.dm_daily_cap = rules.daily_cap
            changed.append(f"daily limit **{daily_cap_label(rules.daily_cap)}**")

        if changed:
            self.owner.dirty = True
            status = "🔔 Deal rules updated: " + " • ".join(changed) + "."
        elif rules.has_updates:
            status = "Everything entered already matches the current draft."
        else:
            status = "No deal-rule changes entered."
        await self.owner._refresh(interaction, status=status)


class _FlipProfitModal(discord.ui.Modal, title="Flip Override minimum profit"):
    amount = discord.ui.TextInput(
        label="Minimum estimated net profit in dollars",
        placeholder="50",
        required=True,
        max_length=12,
    )

    def __init__(self, owner: DmDealPreferencesView) -> None:
        super().__init__()
        self.owner = owner
        self.amount.default = f"{owner.flip_min_profit_cents / 100:.2f}"

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw = (
            str(self.amount.value or "")
            .replace("$", "")
            .replace(",", "")
            .strip()
        )
        try:
            dollars = float(raw)
        except ValueError:
            await interaction.response.send_message(
                "Enter a dollar amount such as `50` or `125.00`.",
                ephemeral=True,
            )
            return
        if not 10 <= dollars <= 100_000:
            await interaction.response.send_message(
                "Flip profit must be between $10 and $100,000.",
                ephemeral=True,
            )
            return
        new_value = int(round(dollars * 100))
        changed = new_value != self.owner.flip_min_profit_cents
        if changed:
            self.owner.flip_min_profit_cents = new_value
            self.owner.dirty = True
        await self.owner._refresh(
            interaction,
            status=(
                f"📈 Flip minimum set to **${new_value / 100:,.2f}**."
                if changed
                else "That Flip minimum is already selected."
            ),
        )
