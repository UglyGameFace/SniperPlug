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
3. Displays explain curated category `demand_level` as **scan priority**, maintaining persisted IDs and compatibility with subscribers.
4. Without matched recent sold-comps, the 65%-of-MSRP resale projection remains private diagnostic information but is **not qualified** for Flip Override. It no longer bypasses explicit category mutes/allowlists or Smart thresholds. An existing confirmed sold-comps path is preserved, but its feed/provenance is not verified as connected.
5. Add regression tests for valid/untrusted metadata and unconfirmed flip/mute behavior.

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
