from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from sniperplug.cogs.home_depot_search import home_depot_link_block, money, price_ending, trim_title
from sniperplug.models.candidate import SourceCandidate
from sniperplug.providers.base import ProviderScanRequest, ProviderStatus
from sniperplug.providers.registry import provider_registry
from sniperplug.services.home_depot_product_lookup import HomeDepotProductDetail, fetch_home_depot_product_detail
from sniperplug.services.home_depot_store_finder import HomeDepotStoreChoice, find_home_depot_stores
from sniperplug.services.penny_score import score_penny_candidate
from sniperplug.services.quota_guard import serpapi_quota_guard
from sniperplug.services.scan_locks import ScanLockKey, scan_operation_locks


ZIP_RE = re.compile(r"^\d{5}$")
SKU_RE = re.compile(r"^[A-Za-z0-9-]{4,24}$")
STORE_ID_RE = re.compile(r"^\d{3,6}$")
ID_TOKEN_RE = re.compile(r"[A-Za-z0-9-]{4,}")


@dataclass(frozen=True)
class HomeDepotLocalScan:
    sku: str
    zip_code: str
    query: str
    candidates: tuple[SourceCandidate, ...]
    warnings: tuple[str, ...]
    quota_text: str
    requested_store_id: str | None = None
    returned_count: int = 0
    returned_candidates: tuple[SourceCandidate, ...] = ()
    match_mode: str = "blocked"
    match_note: str = ""
    detail: HomeDepotProductDetail | None = None
    detail_lookup_used: bool = False

    @property
    def best_candidate(self) -> SourceCandidate | None:
        return self.candidates[0] if self.candidates else None


class HomeDepotStoreSelect(discord.ui.Select):
    def __init__(self, owner_id: int, sku: str, zip_code: str, stores: tuple[HomeDepotStoreChoice, ...]):
        self.owner_id = owner_id
        self.sku = sku
        self.zip_code = zip_code
        self.stores_by_id = {store.store_id: store for store in stores}
        options = [
            discord.SelectOption(
                label=store.short_label[:100],
                value=store.store_id,
                description=f"Use store #{store.store_id} for this stock check"[:100],
            )
            for store in stores[:25]
        ]
        super().__init__(placeholder="Pick the Home Depot store to check…", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This store picker belongs to the user who started the scan.", ephemeral=True)
            return
        store_id = self.values[0]
        store = self.stores_by_id.get(store_id)
        db = getattr(interaction.client, "db", None)
        await interaction.response.defer(ephemeral=True)
        lock_key = ScanLockKey(guild_id=interaction.guild_id, user_id=interaction.user.id, action="hd_stock_store_select", query=self.sku, preset=store_id)
        if not await scan_operation_locks.acquire(lock_key):
            await interaction.followup.send("That Home Depot store check is already running. I blocked the duplicate tap so it does not spend another SerpApi credit.", ephemeral=True)
            return
        try:
            scan = await run_home_depot_local_scan(interaction.user.id, self.sku, self.zip_code, store_id=store_id, db=db)
            embed = build_hd_stock_embed(scan)
            if store:
                embed.add_field(name="Selected store", value=f"{store.short_label}\n{store.url}", inline=False)
            await interaction.edit_original_response(embed=embed, view=None)
        finally:
            await scan_operation_locks.release(lock_key)


class HomeDepotStoreSelectView(discord.ui.View):
    def __init__(self, owner_id: int, sku: str, zip_code: str, stores: tuple[HomeDepotStoreChoice, ...]):
        super().__init__(timeout=300)
        self.add_item(HomeDepotStoreSelect(owner_id, sku, zip_code, stores))


class HomeDepotLocalCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="hd_stock", description="Check a Home Depot SKU near a ZIP with local store selection.")
    @app_commands.describe(
        sku="Home Depot SKU / Internet #, like 334851114.",
        zip_code="5-digit ZIP code to find nearby stores.",
        store_id="Optional Home Depot store ID. Leave blank to pick from nearby stores.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def hd_stock(self, interaction: discord.Interaction, sku: str, zip_code: str, store_id: str | None = None) -> None:
        await interaction.response.defer(ephemeral=True)
        cleaned_sku = _clean_sku(sku)
        cleaned_zip = _clean_zip(zip_code)
        cleaned_store_id = _clean_store_id(store_id)
        error = _validation_error(cleaned_sku, cleaned_zip, cleaned_store_id)
        if error:
            await interaction.followup.send(error, ephemeral=True)
            return

        if not cleaned_store_id:
            stores = await find_home_depot_stores(cleaned_zip, max_results=8)
            if stores:
                embed = discord.Embed(
                    title="🏚️ Pick a Home Depot store",
                    description=(
                        f"SKU/search: `{cleaned_sku}`\n"
                        f"ZIP: `{cleaned_zip}`\n\n"
                        "Choose the actual store below so SniperPlug can run a store-specific stock check.\n\n"
                        "**Important:** ZIP-only Home Depot results are blocked because they can return the wrong store."
                    ),
                    color=discord.Color.orange(),
                )
                embed.add_field(name="Nearby stores found", value="\n".join(f"• **{store.short_label}**" for store in stores[:8]), inline=False)
                embed.set_footer(text="Pick a store first. SniperPlug will not use ZIP-only local stock proof.")
                await interaction.followup.send(embed=embed, view=HomeDepotStoreSelectView(interaction.user.id, cleaned_sku, cleaned_zip, stores), ephemeral=True)
                return

            store_search_url = f"https://www.homedepot.com/l/search/{cleaned_zip}"
            embed = discord.Embed(
                title="🏚️ Home Depot store selection required",
                description=(
                    f"SKU/search: `{cleaned_sku}`\n"
                    f"ZIP: `{cleaned_zip}`\n\n"
                    "SniperPlug could not automatically find nearby Home Depot stores for this ZIP.\n\n"
                    "**The ZIP-only scan was blocked on purpose** so it does not show wrong-location stock like Bangor again."
                ),
                color=discord.Color.dark_orange(),
            )
            embed.add_field(
                name="Next step",
                value=(
                    f"Open the Home Depot store finder:\n{store_search_url}\n\n"
                    "Pick your actual store, copy the store number from the store page, then run:\n"
                    f"`/hd_stock sku:{cleaned_sku} zip_code:{cleaned_zip} store_id:STORE_NUMBER`"
                ),
                inline=False,
            )
            embed.set_footer(text="No ZIP-only stock card was posted. Store-specific proof is required.")
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        lock_key = ScanLockKey(guild_id=interaction.guild_id, user_id=interaction.user.id, action="hd_stock", query=cleaned_sku, preset=cleaned_store_id)
        if not await scan_operation_locks.acquire(lock_key):
            await interaction.followup.send("That Home Depot stock check is already running. I blocked the duplicate tap so it does not spend another SerpApi credit.", ephemeral=True)
            return
        try:
            scan = await run_home_depot_local_scan(interaction.user.id, cleaned_sku, cleaned_zip, store_id=cleaned_store_id, db=self.bot.db)
            await interaction.followup.send(embed=build_hd_stock_embed(scan), ephemeral=True)
        finally:
            await scan_operation_locks.release(lock_key)

    @app_commands.command(name="hd_penny_zip", description="Start a ZIP-anchored Home Depot penny/clearance scan using a safe default search.")
    @app_commands.describe(zip_code="5-digit ZIP code to anchor the penny/clearance scan.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def hd_penny_zip(self, interaction: discord.Interaction, zip_code: str) -> None:
        await interaction.response.defer(ephemeral=True)
        cleaned_zip = _clean_zip(zip_code)
        if not ZIP_RE.match(cleaned_zip):
            await interaction.followup.send("Please enter a valid 5-digit ZIP code.", ephemeral=True)
            return
        provider = provider_registry.get("home_depot_serpapi")
        if provider is None:
            await interaction.followup.send("Home Depot SerpApi provider is not registered yet.", ephemeral=True)
            return
        health = await provider.healthcheck()
        if health.status != ProviderStatus.READY:
            await interaction.followup.send(health.message, ephemeral=True)
            return
        lock_key = ScanLockKey(guild_id=interaction.guild_id, user_id=interaction.user.id, action="hd_penny_zip", query="clearance", preset=cleaned_zip)
        if not await scan_operation_locks.acquire(lock_key):
            await interaction.followup.send("That Home Depot ZIP scan is already running. I blocked the duplicate tap so it does not spend another SerpApi credit.", ephemeral=True)
            return
        try:
            quota = serpapi_quota_guard.check(interaction.user.id, cost=1)
            if not quota.allowed:
                await interaction.followup.send(f"SerpApi scan blocked: {quota.reason}", ephemeral=True)
                return
            result = await provider.scan(
                ProviderScanRequest(
                    source_key="home_depot_serpapi",
                    query="clearance",
                    max_results=24,
                    metadata={"zip_code": cleaned_zip, "requested_by": str(interaction.user.id), "scan_type": "hd_penny_zip", "db": self.bot.db},
                )
            )
            quota_cost = 0 if result.metadata.get("cache_hit") else 1
            quota_after = serpapi_quota_guard.record(interaction.user.id, cost=quota_cost)
            candidates = tuple(sorted(result.candidates, key=_penny_sort_key, reverse=True))[:8]
            await interaction.followup.send(embed=build_hd_penny_zip_embed(cleaned_zip, candidates, result.warnings, quota_after.daily_used, quota_after.daily_limit), ephemeral=True)
        finally:
            await scan_operation_locks.release(lock_key)


async def run_home_depot_local_scan(user_id: int, sku: str, zip_code: str, *, store_id: str | None = None, db: Any = None) -> HomeDepotLocalScan:
    provider = provider_registry.get("home_depot_serpapi")
    if provider is None:
        return HomeDepotLocalScan(sku, zip_code, sku, (), ("Home Depot SerpApi provider is not registered yet.",), "SerpApi unavailable", requested_store_id=store_id)
    health = await provider.healthcheck()
    if health.status != ProviderStatus.READY:
        return HomeDepotLocalScan(sku, zip_code, sku, (), (health.message,), "SerpApi unavailable", requested_store_id=store_id)

    quota = serpapi_quota_guard.check(user_id, cost=2)
    cache_only = not quota.allowed
    metadata = {"zip_code": zip_code, "requested_by": str(user_id), "scan_type": "hd_stock", "db": db}
    if store_id:
        metadata["store_id"] = store_id
    if cache_only:
        metadata["cache_only"] = True

    result = await provider.scan(ProviderScanRequest(source_key="home_depot_serpapi", query=sku, max_results=24, metadata=metadata))
    candidates = _exact_sku_candidates(result.candidates, sku)
    match_mode = "exact_search_match"
    match_note = "Search proof: requested value matched returned product ID/SKU/UPC/URL token."
    if not candidates:
        candidates = _single_result_candidates(result.candidates, sku)
        if candidates:
            match_mode = "single_search_result"
            match_note = "Search fallback: Home Depot search returned exactly one product for the requested value. Detail lookup still required."
        else:
            if cache_only:
                warnings = tuple(result.warnings) + (f"Live SerpApi scan was blocked by quota guard: {quota.reason}",)
                return HomeDepotLocalScan(sku, zip_code, sku, (), warnings, "SerpApi cache miss • live scan blocked", requested_store_id=store_id, returned_count=len(result.candidates), returned_candidates=tuple(result.candidates), match_mode="blocked", match_note="Blocked: quota guard stopped the live scan and no cached result was available.")
            search_cost = 0 if result.metadata.get("cache_hit") else 1
            quota_after = serpapi_quota_guard.record(user_id, cost=search_cost)
            return HomeDepotLocalScan(sku, zip_code, sku, (), result.warnings, f"SerpApi used: {quota_after.daily_used}/{quota_after.daily_limit} today", requested_store_id=store_id, returned_count=len(result.candidates), returned_candidates=tuple(result.candidates), match_mode="blocked", match_note="Blocked: no exact ID proof and no single-result fallback.")

    candidate = candidates[0]
    detail: HomeDepotProductDetail | None = None
    detail_lookup_used = False
    product_id = candidate.product_id or candidate.sku
    if product_id and not cache_only:
        detail_lookup_used = True
        detail = await fetch_home_depot_product_detail(product_id, zip_code=zip_code, store_id=store_id, db=db)
        candidate = _merge_detail_candidate(candidate, detail)
        match_mode, match_note = _detail_match_mode(sku, candidates[0], detail, prior_mode=match_mode, store_id=store_id)
    elif cache_only:
        attrs = dict(candidate.variant_attributes or {})
        attrs["observed_price_reference_status"] = "Live SerpApi quota was blocked, so SniperPlug used cached search proof without refreshing Product API detail."
        candidate.variant_attributes = attrs
        match_note += " Cached result was used because the live quota guard would have blocked this scan."

    if db is not None:
        candidate = await _apply_price_history_reference(candidate, db=db, store_id=store_id, zip_code=zip_code)
    candidates = (candidate,)

    warnings = list(result.warnings) + list(detail.warnings if detail else ())
    applied = candidate.variant_attributes or {}
    if applied.get("observed_price_reference_status"):
        warnings.append(applied["observed_price_reference_status"])
    if detail and detail.fulfillment_store and not store_id:
        warnings.append(f"ZIP-only Product API returned provider-selected store `{detail.fulfillment_store}`. This is not trusted store-specific proof until a store_id is provided.")

    if cache_only:
        quota_text = f"SerpApi cache used • live scan blocked: {quota.reason}"
    else:
        search_cost = 0 if result.metadata.get("cache_hit") else 1
        detail_cost = 0
        if detail_lookup_used:
            detail_cost = 0 if detail and any("cache hit" in warning.lower() for warning in detail.warnings) else 1
        quota_after = serpapi_quota_guard.record(user_id, cost=search_cost + detail_cost)
        quota_text = f"SerpApi used: {quota_after.daily_used}/{quota_after.daily_limit} today"
        if search_cost + detail_cost == 0:
            quota_text += " • served fully from cache"

    return HomeDepotLocalScan(sku, zip_code, sku, candidates, tuple(warnings), quota_text, requested_store_id=store_id, returned_count=len(result.candidates), returned_candidates=tuple(result.candidates), match_mode=match_mode, match_note=match_note, detail=detail, detail_lookup_used=detail_lookup_used)


def build_hd_stock_embed(scan: HomeDepotLocalScan) -> discord.Embed:
    candidate = scan.best_candidate
    store_text = scan.requested_store_id or "ZIP-only / no store_id"
    embed = discord.Embed(title=f"🏚️ Home Depot Stock Check • SKU {scan.sku}", description=f"ZIP: `{scan.zip_code}` • Store: `{store_text}` • Mode: `{scan.match_mode}`", color=_stock_color(scan))
    if not candidate:
        embed.add_field(name="No usable stock result returned", value=f"Home Depot search returned `{scan.returned_count}` product result(s), but none were safe enough to use.\nSniperPlug blocked the card because multiple/no results would be too easy to misread.", inline=False)
        closest = scan.returned_candidates[0] if scan.returned_candidates else None
        if closest:
            thumbnail = _safe_image_url(closest.image_url)
            if thumbnail:
                embed.set_thumbnail(url=thumbnail)
            embed.add_field(name="Closest returned result", value=_trim_field(_candidate_summary(closest, scan.sku, scan)), inline=False)
            embed.add_field(name="Review link", value=home_depot_link_block(closest), inline=False)
        if scan.warnings:
            embed.add_field(name="Provider notes", value=_trim_field("\n".join(f"• {w}" for w in scan.warnings[:5])), inline=False)
        embed.add_field(name="Proof status", value=_trim_field(scan.match_note), inline=False)
        embed.set_footer(text=f"{scan.quota_text} • Blocked because usable proof was missing.")
        return embed

    thumbnail = _safe_image_url(candidate.image_url)
    if thumbnail:
        embed.set_thumbnail(url=thumbnail)
    safe_url = _safe_url(candidate.product_url)
    if safe_url:
        embed.url = safe_url

    product_id = candidate.sku or candidate.product_id or scan.sku or "n/a"
    product_lines = [
        f"**{trim_title(candidate.title, 90)}**",
        f"SKU / Internet #: `{product_id}`",
    ]
    if candidate.product_url:
        product_lines.append(candidate.product_url)
    embed.add_field(name="Product", value=_trim_field("\n".join(product_lines)), inline=False)

    price_lines = [f"Now: **{money(candidate.current_price)}**"]
    if candidate.typical_price:
        price_lines.append(f"Was: **{money(candidate.typical_price)}**")
    embed.add_field(name="Price", value=_trim_field("\n".join(price_lines)), inline=True)

    embed.add_field(name="Stock / fulfillment", value=_trim_field(_local_stock_block(candidate, scan)), inline=True)
    embed.add_field(
        name="SniperPlug read",
        value=_trim_field(f"Public alert: **No** — Home Depot local proof stays private until a user verifies store/price in person.\n**{_confidence_label(scan)}**\n{scan.match_note}"),
        inline=False,
    )

    embed.add_field(name="Product proof", value=_trim_field(_product_proof_block(scan, candidate)), inline=False)
    embed.add_field(name="Price proof", value=_trim_field(_local_price_block(candidate)), inline=True)
    embed.add_field(name="Location / availability", value=_trim_field(_local_stock_block(candidate, scan)), inline=True)
    embed.add_field(name="Links", value=_trim_field(home_depot_link_block(candidate)), inline=False)
    embed.add_field(name="Proof status", value=_trim_field(f"**{_confidence_label(scan)}**\n{scan.match_note}"), inline=False)
    if scan.warnings:
        embed.add_field(name="Provider notes", value=_trim_field("\n".join(f"• {w}" for w in scan.warnings[:5])), inline=False)
    embed.set_footer(text=f"{scan.quota_text} • Product API detail lookup: {'yes' if scan.detail_lookup_used else 'no'} • ZIP-only stock proof is blocked unless a real store_id is used. Call/check before driving.")
    return embed


def build_hd_penny_zip_embed(zip_code: str, candidates: tuple[SourceCandidate, ...], warnings: tuple[str, ...], used: int, limit: int) -> discord.Embed:
    embed = discord.Embed(title=f"🟡 Home Depot Penny / Clearance ZIP Scan • {zip_code}", description="V1 scans a targeted Home Depot clearance query with ZIP context and ranks returned candidates by penny/clearance signals. This is **not** a locked ZIP penny database yet.", color=discord.Color.gold())
    if not candidates:
        embed.add_field(name="No candidates", value="No Home Depot products came back for the ZIP scan. Try `/home_depot_penny_hunt` with a tighter query like `faucet`, `vanity`, `ryobi`, or `milwaukee`.", inline=False)
    else:
        lines = [f"**{idx}. {trim_title(c.title, 55)}**\nPrice: **{money(c.current_price)}** • Score: `{score_penny_candidate(c, has_store_id=True).score}/100` • SKU: `{c.sku or c.product_id or 'n/a'}`" for idx, c in enumerate(candidates[:6], start=1)]
        embed.add_field(name="Top candidates", value=_trim_field("\n\n".join(lines)), inline=False)
    if warnings:
        embed.add_field(name="Provider notes", value=_trim_field("\n".join(f"• {w}" for w in warnings[:5])), inline=False)
    embed.set_footer(text=f"SerpApi used: {used}/{limit} today • Verify in store before posting.")
    return embed


async def _apply_price_history_reference(candidate: SourceCandidate, *, db: Any, store_id: str | None, zip_code: str | None) -> SourceCandidate:
    product_key = _price_history_key(candidate)
    if not product_key or candidate.current_price is None or candidate.current_price <= 0:
        return candidate
    attrs = dict(candidate.variant_attributes or {})
    try:
        reference = await db.get_price_reference(retailer="Home Depot", product_key=product_key, store_id=store_id, current_price=candidate.current_price)
        if reference and reference.get("ready") and reference.get("reference_price"):
            observed_ref = float(reference["reference_price"])
            api_ref = candidate.typical_price or 0
            if observed_ref > candidate.current_price and observed_ref >= api_ref:
                candidate.typical_price = observed_ref
                attrs["reference_price_source"] = str(reference.get("source") or "SniperPlug observed price history")
                attrs["observed_reference_high"] = str(reference.get("highest_price", observed_ref))
                attrs["observed_reference_median"] = str(reference.get("median_price", ""))
                attrs["observed_reference_low"] = str(reference.get("lowest_price", ""))
                attrs["observed_reference_samples"] = str(reference.get("observation_count", ""))
        elif reference:
            attrs["observed_price_reference_status"] = _learning_status(reference)
        candidate.variant_attributes = attrs
        await db.record_price_observation(
            retailer="Home Depot",
            product_key=product_key,
            product_id=candidate.product_id,
            sku=candidate.sku,
            upc=candidate.upc,
            store_id=store_id,
            zip_code=zip_code,
            title=candidate.title,
            product_url=candidate.product_url,
            current_price=float(candidate.current_price),
            reference_price=candidate.typical_price,
            reference_source=attrs.get("reference_price_source"),
            source_key=candidate.source_key,
        )
    except Exception as exc:
        attrs["observed_price_reference_status"] = f"Price history skipped: {exc}"
        candidate.variant_attributes = attrs
    return candidate


def _learning_status(reference: dict[str, Any]) -> str:
    if not reference.get("ready") and reference.get("observation_count") is not None:
        needed = reference.get("needed", 3)
        count = reference.get("observation_count", 0)
        reason = reference.get("reason")
        if reason:
            return f"SniperPlug price history has {count}/{needed}+ samples, but {reason}"
        return f"SniperPlug price history learning mode: {count}/{needed} samples collected."
    return "SniperPlug price history learning mode: collecting baseline."


def _price_history_key(candidate: SourceCandidate) -> str | None:
    value = candidate.product_id or candidate.upc or candidate.sku
    normalized = _normalize_id(value)
    return normalized or None


def _merge_detail_candidate(candidate: SourceCandidate, detail: HomeDepotProductDetail | None) -> SourceCandidate:
    if detail is None:
        return candidate
    attrs = dict(candidate.variant_attributes or {})
    for key, value in {"internet_number": detail.product_id, "store_sku_number": detail.store_sku_number, "upc": detail.upc, "model_number": detail.model_number, "brand": detail.brand, "rating": detail.rating, "reviews": detail.reviews, "fulfillment_store": detail.fulfillment_store}.items():
        if value:
            attrs[key] = str(value)
    if getattr(detail, "reference_price_source", None):
        attrs["reference_price_source"] = str(detail.reference_price_source)
    if detail.fulfillment_quantity is not None:
        attrs["fulfillment_quantity"] = str(detail.fulfillment_quantity)
    if detail.fulfillment_options:
        attrs["fulfillment_options"] = " | ".join(option.label() for option in detail.fulfillment_options[:4])
        for option in detail.fulfillment_options:
            key = re.sub(r"[^a-z0-9]+", "_", option.type.lower()).strip("_") or "option"
            attrs[f"fulfillment_{key}"] = option.label()
            if option.quantity is not None:
                attrs[f"fulfillment_{key}_quantity"] = str(option.quantity)
    return SourceCandidate(source_key=candidate.source_key, retailer=candidate.retailer, title=detail.title or candidate.title, product_url=detail.link or candidate.product_url, current_price=detail.price if detail.price is not None else candidate.current_price, typical_price=detail.original_price if detail.original_price is not None else candidate.typical_price, image_url=_safe_image_url(detail.image_url) or _safe_image_url(candidate.image_url), product_id=detail.product_id or candidate.product_id, product_id_type=candidate.product_id_type, sku=detail.store_sku_number or candidate.sku, upc=detail.upc or candidate.upc, model=detail.model_number or candidate.model, variant_attributes=attrs, stock_status=_detail_stock_status(detail) or candidate.stock_status, can_add_to_cart=candidate.can_add_to_cart, signals=["Home Depot Product API detail lookup used"] + list(candidate.signals))


def _detail_match_mode(sku: str, candidate: SourceCandidate, detail: HomeDepotProductDetail | None, *, prior_mode: str, store_id: str | None) -> tuple[str, str]:
    if detail is None:
        return prior_mode, "Detail lookup was not available; using search-result proof only."
    normalized = _normalize_id(sku)
    ids = {_normalize_id(v) for v in (detail.product_id, detail.store_sku_number, detail.upc, detail.model_number, candidate.product_id, candidate.sku) if v}
    id_matched = bool(normalized and normalized in ids)
    if id_matched and _has_local_stock_detail(detail) and store_id:
        return "product_api_store_match", "Product API proof: requested value matched returned ID/SKU/UPC/model and store_id-specific fulfillment data was returned."
    if id_matched and _has_local_stock_detail(detail) and not store_id:
        return "product_api_zip_context", "Product API returned fulfillment data, but no store_id was supplied. Treat the returned store as provider-selected, not ZIP-verified."
    if id_matched:
        return "product_api_id_match", "Product API proof: requested value matched returned ID/SKU/UPC/model, but local quantity/store proof was limited."
    if prior_mode == "single_search_result":
        return ("single_result_with_product_api_store", "Single search result plus Product API detail lookup with requested store_id. Verify before public posting.") if store_id else ("single_result_with_product_api_zip", "Single search result plus Product API detail lookup, but no store_id was supplied. ZIP-only local store proof is not trusted.")
    return prior_mode, "Search proof remained stronger than Product API ID proof."


def _has_local_stock_detail(detail: HomeDepotProductDetail | None) -> bool:
    return bool(detail and (detail.fulfillment_store or detail.fulfillment_quantity is not None or detail.fulfillment_options))


def _stock_color(scan: HomeDepotLocalScan) -> discord.Color:
    if scan.match_mode == "product_api_store_match":
        return discord.Color.green()
    if scan.match_mode in {"product_api_id_match", "exact_search_match", "single_result_with_product_api_store"}:
        return discord.Color.orange()
    return discord.Color.dark_orange()


def _confidence_label(scan: HomeDepotLocalScan) -> str:
    return {
        "product_api_store_match": "Strong store-specific staff-review proof",
        "product_api_zip_context": "Product confirmed; store location not trusted without store_id",
        "product_api_id_match": "Product confirmed; local stock limited",
        "single_result_with_product_api_store": "Single-result match with requested store_id",
        "single_result_with_product_api_zip": "Single-result match; ZIP-only location is not trusted",
        "exact_search_match": "Search-result product match",
    }.get(scan.match_mode, "Not enough proof")


def _product_proof_block(scan: HomeDepotLocalScan, candidate: SourceCandidate) -> str:
    attrs = candidate.variant_attributes or {}
    lines = [f"**{trim_title(candidate.title, 120)}**", f"Requested SKU/search: `{scan.sku}`", f"Requested ZIP: `{scan.zip_code}`", f"Requested Store ID: `{scan.requested_store_id or 'not supplied'}`", f"Internet #: `{candidate.product_id or attrs.get('internet_number') or 'n/a'}`", f"Store SKU: `{attrs.get('store_sku_number') or candidate.sku or 'n/a'}`"]
    if candidate.model:
        lines.append(f"Model: `{candidate.model}`")
    if candidate.upc:
        lines.append(f"UPC: `{candidate.upc}`")
    if attrs.get("brand"):
        lines.append(f"Brand: **{attrs['brand']}**")
    return "\n".join(lines)


def _candidate_summary(candidate: SourceCandidate, requested: str, scan: HomeDepotLocalScan) -> str:
    return f"**{trim_title(candidate.title, 90)}**\nRequested: `{requested}` • ZIP: `{scan.zip_code}` • Store ID: `{scan.requested_store_id or 'not supplied'}`\nReturned ID/SKU: `{candidate.sku or candidate.product_id or 'n/a'}`\nPrice: **{money(candidate.current_price)}**\nStock / fulfillment:\n{_local_stock_block(candidate, scan)}"


def _local_price_block(candidate: SourceCandidate) -> str:
    ending = price_ending(candidate.current_price)
    ending_text = f"\nEnding: **.{ending}**" if ending else ""
    attrs = candidate.variant_attributes or {}
    source = attrs.get("reference_price_source")
    source_text = f"\nReference source: `{source}`" if source else ""
    if candidate.typical_price and candidate.current_price and candidate.typical_price > candidate.current_price:
        savings = candidate.typical_price - candidate.current_price
        pct = savings / candidate.typical_price * 100
        return f"Now: **{money(candidate.current_price)}**\nWas/MSRP: **{money(candidate.typical_price)}**\nSave: **{money(savings)} ({pct:.0f}%)**{source_text}{ending_text}"
    return f"Now: **{money(candidate.current_price)}**\nWas/MSRP: **Not returned by Home Depot/SerpApi**\nDiscount proof: **Blocked — no trusted reference price**{ending_text}"


def _local_stock_block(candidate: SourceCandidate, scan: HomeDepotLocalScan) -> str:
    attrs = candidate.variant_attributes or {}
    lines: list[str] = []
    provider_store = attrs.get("fulfillment_store")
    if provider_store:
        if scan.requested_store_id:
            lines.append(f"Requested store_id: {scan.requested_store_id}")
            lines.append(f"Provider returned store/location: {provider_store}")
        else:
            lines.append(f"Provider selected store/location: {provider_store} (ZIP-only; not trusted as local proof)")
    if candidate.stock_status:
        lines.append(candidate.stock_status)
    for key in ("fulfillment_quantity", "fulfillment_options", "fulfillment_pickup", "fulfillment_delivery", "fulfillment_shipping", "store_stock", "store_stock_status", "pickup", "delivery", "general_stock", "general_stock_status", "add_to_cart", "buy_online_pay_in_store", "check_nearby_stores"):
        value = attrs.get(key)
        if value:
            lines.append(f"{key.replace('_', ' ').title()}: {value}")
    if candidate.can_add_to_cart is True:
        lines.append("Add to cart: yes")
    elif candidate.can_add_to_cart is False:
        lines.append("Add to cart: not confirmed")
    if not scan.requested_store_id:
        lines.append("⚠️ Add store_id for real store-specific proof; delivery_zip alone can return the wrong store.")
    return "\n".join(lines[:9]) if lines else "Local stock not returned by provider"


def _detail_stock_status(detail: HomeDepotProductDetail | None) -> str | None:
    if detail is None:
        return None
    if detail.pickup_quantity is not None and detail.fulfillment_store:
        return f"Pickup quantity: {detail.pickup_quantity} at store {detail.fulfillment_store}"
    if detail.pickup_quantity is not None:
        return f"Pickup quantity: {detail.pickup_quantity}"
    if detail.fulfillment_quantity is not None and detail.fulfillment_store:
        return f"Local fulfillment quantity: {detail.fulfillment_quantity} at store {detail.fulfillment_store}"
    return None


def _exact_sku_candidates(candidates: tuple[SourceCandidate, ...], sku: str) -> tuple[SourceCandidate, ...]:
    normalized = _normalize_id(sku)
    exact = [candidate for candidate in candidates if _is_exact_sku_match(candidate, normalized)]
    return tuple(sorted(exact, key=lambda c: score_penny_candidate(c, has_store_id=True).score, reverse=True))


def _single_result_candidates(candidates: tuple[SourceCandidate, ...], sku: str) -> tuple[SourceCandidate, ...]:
    return tuple(candidates) if sku.isdigit() and len(candidates) == 1 else ()


def _is_exact_sku_match(candidate: SourceCandidate, normalized_sku: str) -> bool:
    return bool(normalized_sku and normalized_sku in _candidate_match_ids(candidate))


def _candidate_match_ids(candidate: SourceCandidate) -> set[str]:
    ids = {_normalize_id(v) for v in (candidate.sku, candidate.product_id, candidate.upc, candidate.selected_offer_id, candidate.model) if v}
    attrs = candidate.variant_attributes or {}
    for key in ("internet_number", "store_sku_number", "upc", "model_number"):
        if attrs.get(key):
            ids.add(_normalize_id(attrs[key]))
    for token in ID_TOKEN_RE.findall(candidate.product_url or ""):
        normalized = _normalize_id(token)
        if normalized:
            ids.add(normalized)
    return ids


def _safe_url(value: str | None) -> str | None:
    if not value:
        return None
    url = str(value).strip()
    if not url:
        return None
    if url.startswith("//"):
        url = f"https:{url}"
    elif url.startswith("/"):
        url = f"https://www.homedepot.com{url}"
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or any(ch.isspace() for ch in url):
        return None
    return url


def _safe_image_url(value: str | None) -> str | None:
    return _safe_url(value)


def _trim_field(value: str, limit: int = 1024) -> str:
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _penny_sort_key(candidate: SourceCandidate) -> tuple[int, int]:
    return score_penny_candidate(candidate, has_store_id=True).score, 100 - int(candidate.current_price or 99)


def _clean_sku(value: str) -> str:
    return "".join(value.strip().split())


def _clean_zip(value: str) -> str:
    return value.strip()


def _clean_store_id(value: str | None) -> str | None:
    cleaned = (value or "").strip()
    return cleaned or None


def _validation_error(sku: str, zip_code: str, store_id: str | None = None) -> str | None:
    if not ZIP_RE.match(zip_code):
        return "Please enter a valid 5-digit ZIP code."
    if store_id and not STORE_ID_RE.match(store_id):
        return "Please enter a valid Home Depot store_id using 3-6 digits, or leave it blank."
    if not SKU_RE.match(sku):
        return "Please enter a valid Home Depot SKU / Internet # using 4-24 letters or numbers."
    return None


def _normalize_id(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())
