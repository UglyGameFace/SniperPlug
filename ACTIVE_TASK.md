# Active Task

## Active task / outcome
**SP-WM-DM-PERSONALIZATION — production UX repair after merged PR #291.**

Outcome: keep the specific Walmart product/brand matching added by PR #291, but replace the confusing wall-of-text configuration flow with a guided, compact Discord UI where products and categories are clearly separated, obvious product choices require no secret aliases, and stale broad category state cannot silently block an explicitly selected Walmart product.

Base main: `bfb9ccf54bf68f2d5c440d50fa13cd0c7df3e715` (PR #291 merge).
Branch: `fix/walmart-dm-ux-292`.
Intended PR: #292, draft until exact-head executable validation passes.

Do not switch to unrelated work without the user's explicit FORCE SWITCH instruction.

## Why PR #291 is not production-accepted
PR #291 passed executable validation and merged, but screenshot-based production acceptance exposed a real UX failure:
- the personal DM menu became a very large instructional embed with category controls, product controls, rules, Flip Override and safety documentation all competing on one screen;
- the Product catalog exposed internal storage keys such as `pokemon_tcg_sealed` and `portable_chargers`;
- Specific products used a free-text modal that rejected ordinary user input such as `iphones`, `airpods`, and `tools` when it did not exactly map to a predeclared alias;
- broad category mutes/allowlists were still evaluated after specific Walmart product intent, so old category state could unexpectedly veto an item the subscriber had deliberately selected.

The active task therefore remains SP-WM-DM-PERSONALIZATION until this repair is validated and production-accepted.

## Root cause / execution path
- `/dm_deals` in `sniperplug/cogs/dm_deal_alerts.py` opens `DmDealPreferencesView` and remains the command owner.
- `sniperplug/cogs/dm_deal_preferences_view.py` had become a monolithic dashboard/category editor/rules editor/product editor/help page.
- `sniperplug/services/dm_product_interests.py` maps human product intent to structured `interest:*` tokens and previously lacked obvious iPhone/AirPods/tool aliases.
- `sniperplug/services/dm_deal_matching.py` is the canonical live matcher used by Walmart bulk, Walmart legacy and verified-retailer fanout.
- The canonical matcher previously applied specific Walmart product/brand matching and then separately applied category mute/allowlist checks, allowing old broad category state to cross into the newer explicit product layer.
- A pre-existing stale `match_dm_deal()` symbol still exists in `sniperplug/services/dm_deal_alerts.py`, but no live fanout imports it; static coverage locks all live fanouts to `dm_deal_matching.py`.

## Repair design
### Guided UI
The private editor now has three screens instead of one giant embed:
1. **Dashboard** — short summary plus Products & brands, Categories, Deal rules, Mode, Flip Override, Save and Close.
2. **Products & brands** — real Discord multi-select product profiles with pagination, optional arbitrary brand entry, clear controls and return to Dashboard.
3. **Categories** — broad Favorites, Muted and Only-these category modes with category multi-select, pagination/search and return to Dashboard.

The old Product catalog button and free-text `_ProductInterestModal` are removed from the primary menu. Typed `/dm_deals` product/category options remain only as a compatibility/backup surface.

### Product usability
- Added dedicated `iphones` and `airpods` profiles with accessory exclusions.
- `tools` / `tool` now resolve naturally to the existing Power tools profile.
- Existing product profiles and strict lookalike exclusions remain intact.
- Product profiles are paged at Discord's 25-option Select limit.
- Product/brand storage capacity is raised above the current catalog size so selecting across multiple pages is not silently truncated.

### Category/product precedence
For Walmart only:
- when no specific product/brand filters exist, category mutes and allowlists keep their existing behavior;
- when specific Walmart product and/or brand filters exist, those explicit choices become the inclusion layer and broad category mutes/allowlists do not veto a matching chosen product;
- exact proof, maximum price, Walmart Cash-only, required/excluded keywords, minimum markdown/score/savings, dedupe and daily limits remain hard;
- specific product/brand matching remains hard and Flip Override cannot bypass it;
- favorites remain a Smart-mode priority hint;
- HP/Target keep their existing behavior.

## Current branch scope
Expected repair files:
- `ACTIVE_TASK.md`
- `sniperplug/cogs/dm_deal_preferences_view.py`
- `sniperplug/services/dm_product_interests.py`
- `sniperplug/services/dm_deal_matching.py`
- `tests/test_dm_product_interests_personalization.py`
- `tests/test_dm_product_interest_surface_static.py`
- `tests/test_dm_deal_rules_menu.py`
- `tests/test_dm_personalization_command_static.py`
- `tests/test_dm_personal_category_mutes.py`

No database migration, new fanout, retry shim or public Walmart posting change is intended.

## Validation required before ready/merge
- new product alias/profile tests, including iPhones/AirPods/tools and accessory exclusions;
- product pagination/select limits;
- explicit product intent vs stale category allowlist/mute regression;
- no-specific-product category fallback regression;
- menu save/close UX tests;
- existing category/favorite/mute/Flip tests;
- existing DM rule tests;
- canonical fanout/matcher static checks;
- Walmart/Target/HP compatibility tests where applicable;
- compileall;
- import smoke;
- dependency check;
- full pytest suite using the known Termux Ubuntu Python 3.12 workflow, with native libsql tests run separately when the clean aarch64 venv intentionally lacks `libsql`;
- `git diff origin/main...HEAD --check`;
- exact changed-file scope;
- conflict-marker scan;
- clean worktree;
- final exact SHA recheck.

## Hosted CI blocker
GitHub-hosted workflow jobs have recently failed before allocating a runner (`steps=[]`, `runner_id=0`). Those jobs are not code evidence. Use exact-head Termux validation if that condition persists.

## Suspended / backlog
- `SP-MOVIE-TICKETS-RECOVERY` / PR #273 remains suspended.
- eBay watcher PR #200 remains unrelated.
- Backlog: SP-SETUP-DELIVERY-UX, SP-DM-DELIVERY-STARVATION, SP-DM-NEW-LOW-ALERT, SP-WM-CASH-PDP-AMOUNT, HP/Target standalone watcher deployment/acceptance, SniperPlug-Site.

## Next step
Open draft PR #292 from this repair branch, inspect exact scope, then run the targeted and full exact-head executable validation before marking it ready or merging.