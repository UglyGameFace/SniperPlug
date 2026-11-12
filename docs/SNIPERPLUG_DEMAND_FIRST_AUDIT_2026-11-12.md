# SniperPlug demand-first audit | 2026-11-12

Source of truth: `UglyGameFace/SniperPlug` main `f3b05193`; paired `UglyGameFace/SniperPlug-Site`. Distinguish repository proof from Discloud/retailer evidence.

## Executive finding

SniperPlug has a durable Walmart global catalog, exact-detail queue, seller/offer/variant and stock checks, verified markdown and explicit opt-in extreme-price lanes, per-guild fanout, expiry/recheck, HP and Target watchers, and extensive tests. Latest main Actions are green. **This proves neither current Discloud deployment nor the ability to find store-local $1 open-box bargains.** More importantly, a price anomaly does not establish demand.

In `services/opportunity_watchlist.py`, numeric `demand_level` (e.g. mobile accessories 98/100) is a *hand-written scan priority*, not recent purchases. In `services/deal_search_modes.py`, “category demand” is brand/title keyword matching; some labels had implied sales. `services/anomaly_score.py` called curated priorities “high-demand categories.” `services/dm_flip_opportunities.py` could derive a speculative resale value from **65% of retail reference**, less costs, and override explicit muted category preferences even without one sold comparable. These are real ranking/trust defects, not proof of malice by the source.

## Production paths and code redundancy

- Production bot: `main.py → sniperplug.bot → global_auto_scan_runner.AutoScanRunnerCog`. This cog inherits **resilient** and **legacy** scanner base classes. Their old per-guild scheduler is not running, but the classes still contain manual `/autoscan_now` and live helpers. Deleting them without moving callers is unsafe.
- Walmart: `walmart_catalog_discovery_only` scans → durable exact queue → `walmart_exact_queue_bulk_runtime` → approved public markdown or private/opt-in extreme lead → per-guild fanout/recovery → expiry. `walmart_global_deal_fanout` is shared helper code, **not** an active duplicate sender. The separate extreme lane is intentional.
- `walmart_metadata_install.py` monkeypatches a review renderer at import. Consolidate into an explicit wiring path after dependency/behavior tests; not safe to remove blind.
- The Nov 5 prior audit lists some modules no longer in the current main tree; `open_box_autoscan_routes.py` remains referenced by preservation tests. There is no benefit deleting orphan names that are already absent.
- Current Walmart steady-state: **4 Affiliate searches/min** (2 broad page-one routes + 2 price-ascending sniper queries). About 265 routes implies broad first-page refresh ~133 minutes absent source pressure. Sniper rotation improves hot-device responsiveness but does NOT solve retailer API blindness to local/ZIP-specific offers.
- The paired **SniperPlug-Site** repository has a Cloudflare Pages affiliate/deals board and a separate private Whop importer. Its README still describes exact deal publishing as a future integrated pipeline. Do not mistake the site for a working bot-to-public-feed bridge.
- Two old draft PRs: [#200 eBay watcher](https://github.com/UglyGameFace/SniperPlug/pull/200) and [#273 movie city locality](https://github.com/UglyGameFace/SniperPlug/pull/273), both unmerged and stale against current main. #200 builds active listing/price tracking, **not** a verified recent eBay sold history provider. Preserve both until rebased/tested or explicitly superseded. [#295 Walmart miss investigation](https://github.com/UglyGameFace/SniperPlug/issues/295) remains open pending exact offer-level production evidence.

## Implemented in this PR

1. `product_demand_evidence.py` reads already-integrated **structured Walmart item rating/review counts** and rejects embed text, malformed numbers, weak ratings and other retailers. Review counts are cumulative *engagement proxies*, never recent sale volumes.
2. Best Picks and exact-detail queue scheduling add a bounded evidence bonus; tied markdown cards can use it. Speculative flip/marketplace-comp **words in an embed** no longer outrank substantive private review candidates. Seller identity, exact item, reference price, public gate, offer checks and the existing API request budget stay unchanged.
3. Product/brand and hot-word heuristics now require whole-phrase matches, preventing generic text such as “product” from counting as a “Pro” model or “algae” as the LG brand.
4. Displays explain curated category `demand_level` as **scan priority**, maintaining persisted IDs and compatibility with subscribers.
5. Without matched recent sold-comps, the 65%-of-MSRP resale projection remains private diagnostic information but is **not qualified** for Flip Override. It no longer bypasses explicit category mutes/allowlists or Smart thresholds. An existing confirmed sold-comps path is preserved, but its feed/provenance is not verified as connected.
6. Add regression tests for valid/untrusted metadata and unconfirmed flip/mute behavior.

## Demand-first system required for real sought-after prices

A credible alert needs **three separate proofs**:

1. **Exact buyable offer**: canonical retailer item/GTIN/SKU, chosen variant, condition, seller, fulfillment, delivered price, stock, proof timestamp; fail closed on missing/mismatched identity.
2. **Actual item interest**: recent *sold listings* with exact product and condition and observation window (strongest), official bestseller rank or most-watched ranking (medium proxy), retailer verified review/rating count (weak proxy). Brand, taxonomy, “viral,” and MSRP are *not* recent sales proof.
3. **Economic value**: same-offer verified price history or matching market comparables, exact taxes/shipping/fees, expected return and fraud costs, with a freshness time limit. MSRP-only comparisons are contextual; never percentage “savings” proof.

Access candidates:
- **Already accessible in existing code:** Walmart Affiliate API's `customerRating`, `numReviews`, exact prices, and `bestseller` search sort (the provider supports this parameter). The new reviewer uses metadata without extra calls; live metadata completeness must be measured.
- **eBay Buy Marketing API:** `BEST_SELLING` product results, `most_watched_items` rankings; production Buy API approval/eligibility is needed. Match ePID, GTIN and precise model/variant before linking a Walmart item.
- **eBay Marketplace Insights:** recent sold history is **Limited Release**. Do not repurpose eBay Browse asking prices as completed sales.
- **Keepa:** paid Amazon history and sales-rank signals, useful with careful GTIN/ASIN mapping; rank is not exact volume.
- **Walmart Marketplace seller insights:** restricted seller/approved provider data; ordinary Affiliate keys do not grant this access.

The next end-to-end integration should collect authorized bestseller/watch/sold evidence with `source_id`, `retrieved_at`, `window`, `matching_model`, `variant`, `sample_count`, `confidence`; write durable demand snapshots and a strict matcher; then rotate demand-qualified SKUs into the same four-request Walmart discovery budget while retaining broad coverage and $1-device sniper fairness. Do **not** add a pretend API integration without actual approved credentials.

## Acceptance and metrics

Measure `first_seen → exact queued → offer verified → posted → expired`, search/API rate and failures, candidate precision@10 on 100+ real labeled offers, recall of known high-demand exact models, duplicate/post false-positive rate, out-of-stock rate, seller/condition mismatches and p50/p95 delivery latency. Specifically trace $1 tablet/S24 FE by exact Walmart item ID, offer/seller, ZIP if local, API visibility at time, queue state and Discord receipt. A screenshot is not enough to identify which stage failed.

Validation boundaries: green exact-head Python tests, import smoke and static checks; actual Discloud SHA/DB mode; live Walmart catalog/lookup source response and quota; end-to-end Discord receipts. A merged PR is not evidence of deployment. No production result metrics have been fabricated.

Cleanup rule: consolidate the **active** call graph before deleting base classes or monkeypatched helpers; retire only import-unreachable files after test/reference checks. Preserve #200/#273 branches and #295 until evidence reconciles them.


## 2026-11-12 continuation: official-source probe, exact comps and read-only miss trace

### Bestseller canary (source input, not verified sales)
- Every 12th completed global catalog batch at offset 7, use the **existing second broad request** as `sort=bestseller` instead of relevance, with the same original route/query, capped 25 results and no additional Affiliate calls. Both price-ascending sniper slots and the durable broad-route cursor remain unchanged.
- Set `SNIPERPLUG_BESTSELLER_CANARY_ENABLED=false` if the retailer does not honor this sort or the trial harms coverage. It defaults to true.
- Provider marks exact result position, query and timestamp as `walmartBestsellerSortRequested`, `walmartBestsellerSearchPosition`, `walmartBestsellerSearchQuery`, `walmartBestsellerObservedAt`; stale/invalid metadata is ignored. A top-five position contributes at most **4 auxiliary points**, and only if the same item already has credible retailer rating and review metadata.
- Observed order after *requesting* bestseller sort is **not independently verified retail sold volume**. This canary is about finding stronger item candidates, not confirming resale liquidity. Track source success, model identity and real-world posting outcomes before expanding cadence.
- The collector reports the number of canary searches and returned items. No new worker, token, persisted raw product-page data, or separate sender.

### Exact cross-retailer sold-comps contract
- A `yes` boolean for eBay identity/condition is insufficient. Even when those compatibility flags exist, Flip Override now requires valid matching GTIN-8/12/13/14 including check digits, matching normalized condition, and agreement on any supplied pack/model/variant.
- It additionally requires an explicit fresh `ebay_marketplace_insights` completed-sales observation (not eBay Browse active listing prices), a recent sample window, and the existing completed-sale count / net-return / ROI gates.
- **No approved Marketplace Insights connector is currently linked to this repo**; this gate stays closed instead of fabricating purchases. A source-name field is an *adapter contract*, not cryptographic proof. A future adapter must authenticate the official provider and produce actual completed-sale receipts.

### Missed-item evidence and alert latency
- New read-only `/sniperplug deal_trace item_id:<number>` for Manage Server permission. It inspects the exact queue row, a bounded sample of the most recently inserted exact events, and the invoking guild's public-post receipt, including measured discovery→verification→post latency where timestamps exist.
- It never calls the retailer, changes DB schemas, restarts worker cycles, reserves posts, or sends alerts. Errors are reduced to categories to avoid leaking raw database/HTTP exception text.
- A missing queue row or the absence of a match among the most recent 200 events is explicitly **inconclusive**: source visibility, expiry, thresholds, opt-out, and historical delivery can remain unknown. The existing detailed `walmart_delivery_health`/recovery system is still authoritative for stored per-guild threshold and dedupe decisions.
- The live missed-$1-item issue is NOT considered closed until the original item ID/offer/ZIP, scan budget, exact lookup and delivery receipt can be reconstructed in the deployed environment.

### Unverified final gate
Exact-head Python suite + import smoke + CI check, no regressions against the four-call constraint, current Discloud commit and bot menu sync, Walmart Affiliate API bestseller behavior, actual item-price/stock/offer data, guild receipts and labeled precision@10/recall. This document is a repository audit, not a live retailer or Discloud acceptance report.

- Query efficiency: exact queue uses the item-ID primary key, fanout samples newest inserted events by SQLite rowid (not a full-table timestamp sort), and guild receipt lookup uses the composite (guild_id, deal_key) key; reads remain bounded to the admin's selected item.


## 2026-11-12 buyer-intent ranking and production constraints

- The ranked Best Picks/Popular Brands pipeline now categorizes **physical target hardware** separately from accessories, mounts, cases, game discs, gift cards and game codes. The bounded target-fit score influences ordering and exact queue priority; it **never** skips identity, buyable offer, available stock, trusted discount or public subscriber policy.
- Curated targets currently recognize console systems, genuine controllers, GPUs, gaming monitors, smart TVs, laptops, popular phones/tablets and VR headsets from the item title alone. A title with a bare brand or expensive product-family reference is insufficient. Unsupported/ambiguous items keep an unknown designation rather than a fabricated popularity ranking.
- Added golden positive and negative product-title tests and controller/console-bundle distinctions, including accessory names containing expensive models. The existing extreme-price emergency verification bonus remains dominant for actual devices; accessories do not inherit the emergency bonus from the protected brand alone.
- Removed rating/review **quality** boosts based on regexing Discord embed prose. Only official structured Walmart item rating/review metadata can earn interest-ranking points. Brand, category and hot-word lookups use the product-title text rather than promotional descriptions. Product interest can still be a retailer review proxy, *not current sales*.
- The existing 265 broad routes, exactly four catalog requests per batch, two low-price sniper slots and seller/offer gates remain unchanged. New ranked target families expand without crawling additional retail pages or consuming more API quota.

### External demand and completed-sales authority

eBay's current Buy Marketing API includes best-selling product and most-watched-item endpoints. These are legitimate buyer-interest indicators but **production Buy API access is restricted**, and credentials/scopes for this project were not established. The distinct eBay Marketplace Insights sold-record endpoint remains limited-release (up to 90 days and category approval), so a sold-comps feed cannot be labeled live. The old draft eBay watcher #200 is based on active listing offers, not reliable completed-sale prices. Keep that work preserved and unmerged until rights, exact product matching, rate budgets and failure paths are established.

### Deployment blockers that code cannot safely bypass

`docs/DISCLOUD_DEPLOY.md` states that the canonical production SniperPlug stores its database on **local SQLite inside the Discloud application filesystem**. A GitHub merge is **not** a verified Discloud deployment. A blind redeploy without an external Discloud backup could destroy guild preferences, exact queues and posting history. Back up first, verify a working pre-existing database, then deploy and inspect the live runtime. No Discloud access, local DB snapshot, real Walmart Affiliate sample or Discord event receipts were accessible for this audit. The merged SHA and live deployed SHA must be compared explicitly.

- Legacy cleanup: the unregistered `BestBuyProvider` skeleton returned zero candidates even when configured. Deleted the dead adapter, unused Best Buy settings field and misleading environment variable; retained nonfunctional future-source planning previews explicitly labeled as such. No other runtime provider was removed.
