# Active Task

## Active task / outcome
**SP-WM-DM-PERSONALIZATION — specific Walmart personal DM interests and product-aware notification badges.**

Outcome: keep broad category alerts compatible while allowing each subscriber to narrow personal Walmart DMs by specific product type and optional brand, with notification titles that visually distinguish both deal strength and product identity.

Base main: `e8f4a271aa8f595d6d2e3f8eca3cf9d79f90e2df` (PR #290 merge).
Current branch: `feat/walmart-dm-personalization`.

Do not switch to unrelated work without the user's explicit FORCE SWITCH instruction.

## Previous task closure
`SP-WM-EXACT-RECOVERY-DEBT` is production-accepted.

Fresh post-merge logs proved:
- v290 telemetry is live with `page_pacing_schedule=page_pacing_slot_v290`;
- page recovery repeatedly reached `0`, including a bounded-pressure sample with actionable due `0`, page recovery `0`, scheduled rechecks `0`, and actively verifying `0`;
- page circuit remained closed with no observed challenge/403/429 regression;
- exact verification continued producing verified markdowns, including full 8/8 batches;
- public and personal DM fanout continued successfully;
- PR #289's `lookup_6001_no_match_v289` marker appeared on fresh code-6001 responses and those events were classified as `api_price_missing->api_exact_lookup_no_match` instead of generic HTTP 400.

Production acceptance evidence was recorded on PR #290 without modifying merged code.

## Status
**Implementation is in progress. Validation has not yet been run on the current feature head. Do not mark ready or merge yet.**

## Requirement / root cause
Existing personal DMs have strong exact-item/price proof, thresholds, categories, favorites, mutes, free-form required/excluded keywords, Flip Override, dedupe, and daily limits. The missing layer is product intent inside a category.

A subscriber who wants portable chargers should not receive every mobile accessory. A fragrance subscriber should be able to target real cologne/perfume and reject body spray/deodorant lookalikes. A Pokémon TCG subscriber should be able to target sealed TCG products without generic Pokémon merchandise. Category-only delivery is therefore correct but too broad for many users.

The notification surface also used the same heat emoji for many unrelated products, so Android/Discord notification previews visually blended together.

## Execution path inspected
- `/dm_deals` in `sniperplug/cogs/dm_deal_alerts.py` is the public personal-alert command and opens `DmDealPreferencesView`.
- `user_dm_deal_alert_preferences.categories_json` already stores category control tokens; no schema migration is required for product/brand tokens.
- `sniperplug/services/dm_personal_categories.py` owns broad category/favorite/mute/flip token composition.
- `sniperplug/services/dm_deal_matching.py` is the canonical production matcher imported by Walmart bulk fanout, legacy Walmart fanout, and verified retailer event fanout.
- `sniperplug/services/walmart_global_deal_fanout_bulk.py` is the active Walmart fanout path seen in production telemetry.
- Walmart exact cards are built through `deal_scanner.build_walmart_cards()`; after personal matching, bulk fanout clones the card embed into the personal DM.
- An older duplicate `match_dm_deal()` remains in `dm_deal_alerts.py`, but production fanouts import the canonical matcher from `dm_deal_matching.py`. This duplicate is part of affected-area cleanup review before completion.

## Matching design
Broad category controls remain the first personalization layer and keep existing behavior for users who do nothing new.

New structured tokens in `categories_json`:
- `interest:<specific_product_profile>`
- `brand:<normalized_brand>`

Semantics:
- multiple product interests are OR;
- multiple brands are OR;
- when both product interests and brands are configured, the product must satisfy one selected product profile **and** one selected brand;
- explicit product/brand intent is a hard user constraint and Flip Override may not bypass it;
- existing maximum price, Walmart Cash-only, required keywords, excluded keywords, exact proof, dedupe, and daily limits remain hard;
- broad category favorites/mutes/allowlists remain separate from the new specific-product layer.

## Specific product profiles
Initial intentionally narrow profiles include:
- sealed Pokémon TCG products: booster packs/bundles/boxes, ETBs, collection boxes, premium collections, tins and related sealed TCG products; generic plush, binders, sleeves, books, video games and other Pokémon merchandise are excluded;
- NeeDoh sensory toys;
- portable chargers / power banks;
- MagSafe / magnetic battery packs;
- designer cologne / perfume / EDP / EDT, excluding body spray/mist, deodorant, lotion and shower-gel lookalikes;
- LEGO sets;
- premium Hot Wheels collector lines;
- Funko Pop collectibles;
- gaming controllers;
- headphones/earbuds;
- Bluetooth speakers;
- SSDs and microSD cards;
- robot vacuums, air fryers, countertop ice makers and coffee makers;
- power tools and tool battery kits;
- diapers, strollers and car seats;
- energy drinks;
- sneakers;
- smartwatches, tablets, laptops and computer monitors.

Pokémon/resale interest does not claim an item is profitable merely because it is Pokémon. Product matching identifies the sealed TCG product; the existing strict Flip assessment remains responsible for resale/profit evidence.

## Product-aware notification badges
Personal DM titles are limited to at most two leading badges:
1. deal strength: `🚨`, `🔥`, `💸`, `💎`, `⚡`, `✅`, or `🔎`;
2. product identity, such as `🧴`, `🃏`, `🔋`, `🧸`, `🧱`, `🎮`, `🎧`, `🚗`, `👶`, or `🛒`.

The title decoration is idempotent so a card evaluated for multiple subscribers does not accumulate emojis.

## Current scope
Expected net feature scope is:
- `ACTIVE_TASK.md`
- `sniperplug/cogs/dm_deal_alerts.py`
- `sniperplug/cogs/dm_deal_preferences_view.py`
- `sniperplug/services/dm_personal_categories.py`
- `sniperplug/services/dm_product_interests.py`
- `sniperplug/services/dm_deal_matching.py`
- `sniperplug/services/dm_deal_badges.py`
- `tests/test_dm_product_interests_personalization.py`
- `tests/test_dm_product_interest_surface_static.py`

Additional directly affected files may enter scope only if validation demonstrates they are required for compatibility/correctness.

## Changes so far
- Added specific product-profile and arbitrary-brand storage/matching helpers without a DB schema change.
- Existing category/favorite/mute/flip edits preserve product and brand tokens.
- Canonical personal matcher enforces product/brand intent before Flip Override.
- `/dm_deals` typed options can add/remove product interests and brands and can display the supported product catalog.
- Personalization menu now includes a Specific products modal and Product catalog button while retaining the category tabs.
- Settings/status surfaces explain OR/OR plus cross-group AND semantics and show the saved product/brand filters.
- Added idempotent product-aware personal DM title badges with a two-badge maximum.
- Added dynamic and static regression coverage for product specificity, brand combinations, category-token compatibility, Pokémon/fragrance exclusions, NeeDoh, and notification badges.

## Compatibility
- Users with no new product/brand tokens retain existing category behavior.
- Existing preference rows need no migration.
- Public destination thresholds and public posting behavior are not intentionally changed.
- Exact Walmart proof, Product Lookup, queue scheduling, fanout dedupe, delivery receipts and daily-cap policy are not intentionally changed.
- Existing advanced free-form required/excluded keywords remain available and separate from structured product profiles.

## Validation required
Before this task can be called complete:
- inspect final diff and exact changed-file scope;
- targeted new product/brand/badge tests;
- existing DM alert, category mute/favorite, Flip Override, delivery policy, Walmart fanout and personalization tests;
- command/static surface tests;
- compileall;
- import smoke;
- pip check;
- full pytest suite;
- conflict-marker scan;
- clean worktree / exact-head verification in an executable checkout;
- review Discord component/command option limits and existing behavior compatibility.

## Cleanup / conflicts
- No DB migration or parallel personal-alert persistence layer was added.
- New structured product tokens reuse the existing categories JSON persistence intentionally.
- Production fanout continues to use `dm_deal_matching.py` as the canonical matcher.
- The older duplicate matcher in `dm_deal_alerts.py` remains under affected-area cleanup review and must not be allowed to become a conflicting second implementation.

## Blockers / risks
Validation is pending. In particular, the personalization menu file was materially extended and must be exercised by the existing menu/static tests and compile/import gates before any completion claim.

## Suspended / unrelated work
- `SP-MOVIE-TICKETS-RECOVERY` and PR #273 remain suspended and untouched.
- eBay watcher PR #200 remains unrelated and untouched.
- Setup, HP/Target, site, Minecraft, Dank Shield and other projects are outside this active lock.

## Backlog
- SP-SETUP-DELIVERY-UX
- SP-DM-DELIVERY-STARVATION
- SP-DM-NEW-LOW-ALERT
- SP-WM-CASH-PDP-AMOUNT
- HP/Target standalone watcher deployment/acceptance
- SniperPlug-Site work

## Next step
Finish affected-area cleanup, open a focused draft PR, run targeted and full exact-head validation, repair only regressions attributable to this task, then update the task record and PR status without invalidating the validated head.