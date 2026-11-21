from __future__ import annotations

from dataclasses import replace
from typing import Any

import discord

from sniperplug.services.dm_deal_alerts import (
    DmDealAlertPreference,
    UNLIMITED_MAX_ALERTS_PER_DAY,
    daily_cap_label,
    save_dm_deal_alert_preference,
)
from sniperplug.services.dm_deal_routes import (
    personal_route_labels,
    split_dm_route_preferences,
)
from sniperplug.services.dm_personal_categories import (
    DEFAULT_FLIP_MIN_PROFIT_CENTS,
    all_personal_categories,
    category_label,
    compose_category_preferences,
    explicit_flip_min_profit_cents,
    personal_category_pages,
    split_category_preferences,
)
from sniperplug.services.dm_product_interests import (
    brand_label,
    normalize_brands,
    product_interest_label,
    product_interest_pages,
    split_product_preferences,
    update_product_preferences,
)
from sniperplug.services.routing import PRICE_GLITCH_ROUTE


SCREEN_HOME = "home"
SCREEN_WHAT = "what"
SCREEN_PRODUCTS = "products"
SCREEN_CATEGORIES = "categories"
SCREEN_QUALITY = "quality"
SCREEN_COUNT = "count"

DISCOUNT_PRESETS: tuple[tuple[str, int], ...] = (
    ("Any discount", 0),
    ("30%+", 30),
    ("50%+", 50),
    ("70%+", 70),
)
MAX_PRICE_PRESETS: tuple[tuple[str, int | None], ...] = (
    ("No limit", None),
    ("$50", 5_000),
    ("$100", 10_000),
    ("$250", 25_000),
    ("$500", 50_000),
)
DAILY_CAP_PRESETS: tuple[tuple[str, int], ...] = (
    ("5 a day", 5),
    ("10 a day", 10),
    ("25 a day", 25),
    ("No limit", UNLIMITED_MAX_ALERTS_PER_DAY),
)
PROFIT_FLOOR_PRESETS: tuple[tuple[str, int], ...] = (
    ("Worth $0+", 0),
    ("Worth $25+", 2_500),
    ("Worth $50+", 5_000),
    ("Worth $100+", 10_000),
)


class DmDealPreferencesView(discord.ui.View):
    """One simple editor for one personal-DM subscriber.

    Three questions, one screen each: what DMs you, how good it has to be,
    and how many a day. Everything is a draft until Save changes is pressed.
    """

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
        self._sync_from_preference(self.preference)

        self.screen = SCREEN_HOME
        self.search_query = ""
        self.page_index = 0
        self.product_page_index = 0
        self.saved = False
        self.dirty = False
        self._rebuild_items()

    def _sync_from_preference(self, preference: DmDealAlertPreference) -> None:
        normalized = preference.normalized()
        self.enabled = bool(normalized.enabled)
        self.selected_categories = set(
            split_category_preferences(normalized.categories)
        )
        product_interests, product_brands = split_product_preferences(
            normalized.categories
        )
        self.product_interests = set(product_interests)
        self.product_brands = set(product_brands)
        self.selected_routes = set(
            split_dm_route_preferences(normalized.categories)
        )
        self.flip_floor_cents = explicit_flip_min_profit_cents(
            normalized.categories
        )
        self.dm_min_discount = int(normalized.min_discount)
        self.dm_max_price_cents = normalized.max_price_cents
        self.dm_daily_cap = int(normalized.max_alerts_per_day)

    def _draft_categories(self) -> tuple[str, ...]:
        categories = compose_category_preferences(
            self.selected_categories,
            flip_enabled=self.flip_floor_cents > 0,
            flip_min_profit_cents=(
                self.flip_floor_cents
                if self.flip_floor_cents > 0
                else DEFAULT_FLIP_MIN_PROFIT_CENTS
            ),
            routes=tuple(
                route
                for route, _ in personal_route_labels()
                if route in self.selected_routes
            ),
        )
        return update_product_preferences(
            categories,
            replace_interests=self.product_interests,
            replace_brands=self.product_brands,
        )

    def _draft_preference(self) -> DmDealAlertPreference:
        return replace(
            self.preference,
            enabled=self.enabled,
            min_discount=self.dm_min_discount,
            max_price_cents=self.dm_max_price_cents,
            max_alerts_per_day=self.dm_daily_cap,
            categories=self._draft_categories(),
        ).normalized()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) == self.user_id:
            return True
        await interaction.response.send_message(
            "This is someone else's private SniperPlug preference menu.",
            ephemeral=True,
        )
        return False

    # -- embeds -----------------------------------------------------------

    def build_embed(self, *, status: str = "") -> discord.Embed:
        if self.screen == SCREEN_WHAT:
            embed = self._build_what_embed()
        elif self.screen == SCREEN_PRODUCTS:
            embed = self._build_products_embed()
        elif self.screen == SCREEN_CATEGORIES:
            embed = self._build_categories_embed()
        elif self.screen == SCREEN_QUALITY:
            embed = self._build_quality_embed()
        elif self.screen == SCREEN_COUNT:
            embed = self._build_count_embed()
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

    def _build_home_embed(self) -> discord.Embed:
        return discord.Embed(
            title="🎯 My alerts",
            description=self._draft_preference().summary_sentence(),
            color=discord.Color.blurple(),
        )

    def _route_preview(self) -> str:
        labels = dict(personal_route_labels())
        return (
            ", ".join(
                labels[route] for route in labels if route in self.selected_routes
            )
            if self.selected_routes
            else "None picked"
        )

    def _build_what_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="🔔 What DMs you",
            description=(
                "Pick any mix. A deal DMs you when it matches **anything** "
                "you picked — a kind of deal, a specific product, a brand, "
                "or a category. Nothing you pick blocks the others."
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="Kinds of deals",
            value=f"**{self._route_preview()}**",
            inline=False,
        )
        embed.add_field(
            name="Products & brands",
            value=(
                f"Products: **{len(self.product_interests)}** • "
                f"Brands: **{len(self.product_brands)}**"
            ),
            inline=False,
        )
        embed.add_field(
            name="Categories",
            value=(
                f"**{len(self.selected_categories)}** picked"
                if self.selected_categories
                else "None picked"
            ),
            inline=False,
        )
        if PRICE_GLITCH_ROUTE in self.selected_routes:
            embed.add_field(
                name="Price mistakes",
                value=(
                    "Any price mistake, no minimum worth."
                    if self.flip_floor_cents <= 0
                    else (
                        "Only mistakes worth "
                        f"**${self.flip_floor_cents / 100:,.0f}+** when a "
                        "price mistake is the only reason for the DM."
                    )
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
        embed = discord.Embed(
            title="🗂️ Pick categories",
            description=(
                "Categories are one way to get a DM: a deal in a category "
                "you picked can DM you. Leave this empty if kinds of deals "
                "or specific products already cover what you want.\n\n"
                f"Page **{page_number}/{page_total}** • Search **{search_text}**\n"
                f"All {len(categories)} live categories remain available."
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="Selected categories",
            value=self._selection_preview(),
            inline=False,
        )
        return embed

    def _build_quality_embed(self) -> discord.Embed:
        max_price_text = (
            f"${self.dm_max_price_cents / 100:,.2f}"
            if self.dm_max_price_cents is not None
            else "No limit"
        )
        return discord.Embed(
            title="🎚️ How good it has to be",
            description=(
                f"Smallest discount: **{self.dm_min_discount}%** • "
                f"Highest price: **{max_price_text}**\n\n"
                "Tap a preset to change it. Every DM still has to prove its "
                "current and was prices first."
            ),
            color=discord.Color.blurple(),
        )

    def _build_count_embed(self) -> discord.Embed:
        return discord.Embed(
            title="🔢 How many a day",
            description=(
                f"Daily limit: **{daily_cap_label(self.dm_daily_cap)}**\n\n"
                "Tap a preset to change it. The limit counts DMs per day "
                "and resets at midnight UTC."
            ),
            color=discord.Color.blurple(),
        )

    # -- previews -----------------------------------------------------------

    def _pages(self):
        return personal_category_pages(self.search_query)

    def _product_pages(self):
        return product_interest_pages()

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
            self.selected_categories,
            key=lambda key: category_label(key).lower(),
        )
        if not selected:
            return "None — no category picks"
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

    # -- items --------------------------------------------------------------

    def _rebuild_items(self) -> None:
        self.clear_items()
        if self.screen == SCREEN_WHAT:
            self._build_what_items()
        elif self.screen == SCREEN_PRODUCTS:
            self._build_product_items()
        elif self.screen == SCREEN_CATEGORIES:
            self._build_category_items()
        elif self.screen == SCREEN_QUALITY:
            self._build_quality_items()
        elif self.screen == SCREEN_COUNT:
            self._build_count_items()
        else:
            self._build_home_items()

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

    def _add_back(self, *, row: int, target: str) -> None:
        back = discord.ui.Button(
            label="Back",
            emoji="⬅️",
            style=discord.ButtonStyle.secondary,
            row=row,
        )
        back.callback = self._show_screen_callback(target)
        self.add_item(back)

    def _build_home_items(self) -> None:
        what = discord.ui.Button(
            label="What DMs you",
            emoji="🔔",
            style=discord.ButtonStyle.primary,
            row=0,
        )
        what.callback = self._show_screen_callback(SCREEN_WHAT)
        self.add_item(what)

        quality = discord.ui.Button(
            label="How good it has to be",
            emoji="🎚️",
            style=discord.ButtonStyle.primary,
            row=0,
        )
        quality.callback = self._show_screen_callback(SCREEN_QUALITY)
        self.add_item(quality)

        count = discord.ui.Button(
            label="How many a day",
            emoji="🔢",
            style=discord.ButtonStyle.primary,
            row=0,
        )
        count.callback = self._show_screen_callback(SCREEN_COUNT)
        self.add_item(count)

        toggle = discord.ui.Button(
            label=f"Alerts: {'ON' if self.enabled else 'OFF'}",
            emoji="🔕" if not self.enabled else "🔔",
            style=(
                discord.ButtonStyle.success
                if self.enabled
                else discord.ButtonStyle.secondary
            ),
            row=1,
        )
        toggle.callback = self._toggle_enabled
        self.add_item(toggle)

        self._add_save_close(row=2)

    def _build_what_items(self) -> None:
        labels = personal_route_labels()
        for index, (route, label) in enumerate(labels):
            button = discord.ui.Button(
                label=label,
                emoji="✅" if route in self.selected_routes else None,
                style=(
                    discord.ButtonStyle.success
                    if route in self.selected_routes
                    else discord.ButtonStyle.secondary
                ),
                row=0 if index < 3 else 1,
            )
            button.callback = self._route_toggle_callback(route, label)
            self.add_item(button)

        row = 2
        if PRICE_GLITCH_ROUTE in self.selected_routes:
            for label, cents in PROFIT_FLOOR_PRESETS:
                button = discord.ui.Button(
                    label=label,
                    style=(
                        discord.ButtonStyle.success
                        if self.flip_floor_cents == cents
                        else discord.ButtonStyle.secondary
                    ),
                    row=row,
                )
                button.callback = self._profit_floor_callback(cents, label)
                self.add_item(button)
            row += 1

        products = discord.ui.Button(
            label=f"Products & brands ({len(self.product_interests)})",
            emoji="🎯",
            style=discord.ButtonStyle.primary,
            row=row,
        )
        products.callback = self._show_screen_callback(SCREEN_PRODUCTS)
        self.add_item(products)

        categories = discord.ui.Button(
            label=f"Categories ({len(self.selected_categories)})",
            emoji="🗂️",
            style=discord.ButtonStyle.primary,
            row=row,
        )
        categories.callback = self._show_screen_callback(SCREEN_CATEGORIES)
        self.add_item(categories)

        self._add_back(row=row + 1, target=SCREEN_HOME)
        self._add_save_close(row=row + 1)

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

        self._add_back(row=3, target=SCREEN_WHAT)
        self._add_save_close(row=3)

    def _build_category_items(self) -> None:
        self.add_item(_CategoryPageSelect(self))

        pages = self._pages()
        last_index = max(0, len(pages) - 1)

        previous = discord.ui.Button(
            label="Back",
            emoji="◀️",
            style=discord.ButtonStyle.secondary,
            row=1,
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
            row=1,
            disabled=True,
        )
        self.add_item(page)

        next_button = discord.ui.Button(
            label="Next",
            emoji="▶️",
            style=discord.ButtonStyle.secondary,
            row=1,
            disabled=(not pages or self.page_index >= last_index),
        )
        next_button.callback = self._next_page
        self.add_item(next_button)

        search = discord.ui.Button(
            label="Search",
            emoji="🔎",
            style=discord.ButtonStyle.secondary,
            row=2,
        )
        search.callback = self._open_search
        self.add_item(search)

        clear_search = discord.ui.Button(
            label="All categories",
            emoji="🧹",
            style=discord.ButtonStyle.secondary,
            row=2,
            disabled=not bool(self.search_query),
        )
        clear_search.callback = self._clear_search
        self.add_item(clear_search)

        clear = discord.ui.Button(
            label="Clear categories",
            emoji="🗑️",
            style=discord.ButtonStyle.secondary,
            row=2,
            disabled=not bool(self.selected_categories),
        )
        clear.callback = self._clear_categories
        self.add_item(clear)

        self._add_back(row=3, target=SCREEN_WHAT)
        self._add_save_close(row=3)

    def _build_quality_items(self) -> None:
        for label, value in DISCOUNT_PRESETS:
            button = discord.ui.Button(
                label=label,
                style=(
                    discord.ButtonStyle.success
                    if self.dm_min_discount == value
                    else discord.ButtonStyle.secondary
                ),
                row=0,
            )
            button.callback = self._discount_callback(value, label)
            self.add_item(button)

        for label, value in MAX_PRICE_PRESETS:
            button = discord.ui.Button(
                label=label,
                style=(
                    discord.ButtonStyle.success
                    if self.dm_max_price_cents == value
                    else discord.ButtonStyle.secondary
                ),
                row=1,
            )
            button.callback = self._max_price_callback(value, label)
            self.add_item(button)

        self._add_back(row=2, target=SCREEN_HOME)
        self._add_save_close(row=2)

    def _build_count_items(self) -> None:
        for label, value in DAILY_CAP_PRESETS:
            button = discord.ui.Button(
                label=label,
                style=(
                    discord.ButtonStyle.success
                    if self.dm_daily_cap == value
                    else discord.ButtonStyle.secondary
                ),
                row=0,
            )
            button.callback = self._daily_cap_callback(value, label)
            self.add_item(button)

        self._add_back(row=1, target=SCREEN_HOME)
        self._add_save_close(row=1)

    # -- callbacks ------------------------------------------------------------

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

    def _show_screen_callback(self, target: str):
        async def _callback(interaction: discord.Interaction) -> None:
            self.screen = target
            await self._refresh(interaction)

        return _callback

    def _route_toggle_callback(self, route: str, label: str):
        async def _callback(interaction: discord.Interaction) -> None:
            if route in self.selected_routes:
                self.selected_routes.discard(route)
                status = f"{label} off."
            else:
                self.selected_routes.add(route)
                status = f"{label} on."
            self.dirty = True
            await self._refresh(interaction, status=status)

        return _callback

    def _profit_floor_callback(self, cents: int, label: str):
        async def _callback(interaction: discord.Interaction) -> None:
            changed = self.flip_floor_cents != cents
            self.flip_floor_cents = cents
            if changed:
                self.dirty = True
            await self._refresh(
                interaction,
                status=(
                    f"Price mistakes: {label.lower()}."
                    if changed
                    else "That minimum is already set."
                ),
            )

        return _callback

    def _discount_callback(self, value: int, label: str):
        async def _callback(interaction: discord.Interaction) -> None:
            changed = self.dm_min_discount != value
            self.dm_min_discount = value
            if changed:
                self.dirty = True
            await self._refresh(
                interaction,
                status=(
                    f"Smallest discount: {label}."
                    if changed
                    else "That discount is already set."
                ),
            )

        return _callback

    def _max_price_callback(self, value: int | None, label: str):
        async def _callback(interaction: discord.Interaction) -> None:
            changed = self.dm_max_price_cents != value
            self.dm_max_price_cents = value
            if changed:
                self.dirty = True
            await self._refresh(
                interaction,
                status=(
                    f"Highest price: {label}."
                    if changed
                    else "That price limit is already set."
                ),
            )

        return _callback

    def _daily_cap_callback(self, value: int, label: str):
        async def _callback(interaction: discord.Interaction) -> None:
            changed = self.dm_daily_cap != value
            self.dm_daily_cap = value
            if changed:
                self.dirty = True
            await self._refresh(
                interaction,
                status=(
                    f"Daily limit: {label}."
                    if changed
                    else "That daily limit is already set."
                ),
            )

        return _callback

    async def _toggle_enabled(self, interaction: discord.Interaction) -> None:
        self.enabled = not self.enabled
        self.dirty = True
        await self._refresh(
            interaction,
            status=(
                "🔔 Alerts on. Save changes to apply."
                if self.enabled
                else "🔕 Alerts off. Save changes to apply."
            ),
        )

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

    async def _open_brands(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(_BrandFilterModal(self))

    async def _clear_search(self, interaction: discord.Interaction) -> None:
        self.search_query = ""
        self.page_index = 0
        await self._refresh(interaction)

    async def _clear_categories(self, interaction: discord.Interaction) -> None:
        changed = bool(self.selected_categories)
        if changed:
            self.selected_categories.clear()
            self.dirty = True
        await self._refresh(
            interaction,
            status=(
                "🧹 Cleared category picks."
                if changed
                else "No categories were picked."
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

    async def _save(self, interaction: discord.Interaction) -> None:
        updated = replace(
            self._draft_preference(),
            failure_count=0,
            last_error="",
        )
        saved = await save_dm_deal_alert_preference(self.bot.db, updated)
        self.preference = saved
        self._sync_from_preference(saved)
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


class _CategoryPageSelect(discord.ui.Select):
    def __init__(self, owner: DmDealPreferencesView) -> None:
        self.owner = owner
        page = owner._current_page()
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
                row=0,
            )
            return

        options = [
            discord.SelectOption(
                label=category.label[:100],
                value=category.key,
                description=(
                    f"Deals from {category.min_discount_percent:g}% off"
                )[:100],
                default=category.key in owner.selected_categories,
            )
            for category in page
        ]
        super().__init__(
            placeholder="Select categories",
            min_values=0,
            max_values=len(options),
            options=options,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        page_keys = {category.key for category in self.owner._current_page()}
        selected = {value for value in self.values if value != "__none__"}
        before = set(self.owner.selected_categories)
        self.owner.selected_categories.difference_update(page_keys)
        self.owner.selected_categories.update(selected)
        changed = before != self.owner.selected_categories
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
