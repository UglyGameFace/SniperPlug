# Active Task

## Status
**Active — SP-WM-MISSED-DEALS-236. Live production validation after PR #235 disproves practical completion: catalog discovery is healthy, but most exact Walmart claims are still failing in page/API recovery before they can become verified alerts, and an exact 56.82% event was rejected publicly as `quality:review_or_watchlist`. The task lock is re-engaged. Do not begin another implementation task until this task satisfies its Definition of Done.**

## Task
**SP-WM-MISSED-DEALS-236 — Restore high-throughput Walmart exact verification so catalog-discovered deals are not lost to flaky detail/page recovery, preserve official seller/offer proof instead of discarding it, stop transient API recovery traffic from starving fresh discovery, and eliminate false public rejection of exact-verified cards caused by stale review/watchlist text.**

## Production evidence
- Live catalog discovery continues returning roughly 90–100 usable Walmart item IDs per two-route batch, so global discovery itself is functioning.
- Exact verification throughput is unacceptable. Production repeatedly reports entire claimed batches as transient failures: `page 8/8`, `api 8/8`, and mixed batches such as `page 5 + api 3`, often with `verified 0`.
- Catalog discovery still pauses at `fresh/retry pressure 12/12` and `13/12` while `page recovery 0`, proving ordinary API/detail retries remain in the fresh/retry lane and can still contribute to discovery starvation.
- User-supplied Walmart screenshots show real live seller identity on products SniperPlug previously labeled as missing seller identity: Bounty item `22395155` is sold and shipped by Walmart; DEWALT item `2814230491` shows seller `HVAC AND TOOLS` and fulfillment by Walmart. A Patbrela 34 QT air-fryer listing in the same report is visibly above the 40% server threshold, demonstrating missed alert opportunity rather than only below-threshold identity noise.
- New public-skip telemetry exposed an exact Walmart event at 56.82% being blocked by `quality:review_or_watchlist`; that is not a threshold failure and requires tracing the exact-card normalization/text gate.

## Root cause / findings so far
- The Walmart Affiliate catalog/search response is already normalized into `SourceCandidate` with selected seller, seller ID, offer ID, fulfillment, condition, marketplace state, item price, reference price, and shipping evidence where returned.
- The exact queue currently discards that official discovery offer proof. Its enqueue row preserves item ID, apparent prices, title, URL/image, route, and timestamps, but not seller/offer/fulfillment/condition/marketplace proof. The worker then has to rediscover identity from the detail endpoint or Walmart product HTML even when the catalog response already had it.
- `WalmartPageRecoveryProvider` always begins with the item-detail endpoint. If that payload still needs seller recovery, it immediately depends on Walmart HTML. It does not currently retry the already-working Affiliate search endpoint for an exact matching item ID before touching the page.
- Product-page recovery is strict and intentionally fail-closed, but production is frequently receiving page-level transient failures. No CAPTCHA/challenge bypass, proxy rotation, or scraping evasion will be introduced.
- Official detail/API failures are still persisted as ordinary `retry`; the fresh-work pressure policy counts that lane with genuinely new work. PR #235 separated only `page_retry`, so API recovery storms can still hit the normal 12-row catalog-pause threshold.
- Exact retry telemetry currently reports only broad buckets (`page`, `api`, etc.). The persisted error contains `retry_kind`, but the batch summary does not surface top concrete reasons such as `page_http_403`, `page_challenge`, `api_http_404`, or `api_network`.
- `public_deal_quality.is_review_or_watchlist()` scans rendered card text for generic review/watchlist markers before checking the structured exact proof.
- Exact Walmart normalization deliberately removes stale review/scout wording, but its stale-marker set does not cover generic `watchlist` wording. An otherwise exact-verified markdown can therefore remain poisoned by stale display text and be rejected on a later guild-specific quality pass.

## Scope / plan
1. Trace and preserve official catalog/search selected-offer proof through queue enqueue, claim, exact merge, snapshot, and fanout without allowing stale identity to masquerade as current proof.
2. Add an official exact-item search fallback before HTML recovery. Accept it only when the Affiliate search result returns the exact requested Walmart item ID; bind seller/offer proof only with strict selected-price/item consistency.
3. If recently observed discovery offer proof is used as a last-resort identity assist, require a bounded freshness window plus exact item and cent-for-cent exact-price agreement; otherwise fail closed.
4. Add a bounded `api_retry` recovery lane for transient official detail/API failures, separate from fresh first-pass work. It must remain inside total emergency backpressure and use bounded delays; do not simply raise or disable pressure thresholds.
5. Preserve claim priority: genuinely fresh first-pass work ahead of recovery traffic; recovery lanes must not monopolize discovery.
6. Surface top concrete `retry_kind` reasons in live exact-worker summaries so remaining HTTP/challenge/API behavior can be diagnosed without guesswork or secrets.
7. Repair the exact-verified public-card review/watchlist false negative. Only cards satisfying the strict exact Walmart proof boundary may have stale review/watchlist presentation markers scrubbed/ignored; non-exact scout/watchlist cards must remain blocked.
8. Add focused regressions for the production failure shapes, including exact Affiliate-search fallback, stale discovery proof rejection, API retry pressure isolation, and exact-card watchlist normalization.
9. Run dependency checks, compile/static validation, focused tests, full suite, cleanup/redundancy inspection, diff/conflict inspection, PR CI/review inspection, merge, and post-merge canonical validation before claiming code completion.
10. Require a later live deployment to prove production improvement; code-side success alone must not be called live-complete.

## Safety invariants
- Requested item ID must exactly equal the official item being verified.
- Current price must come from a current official exact-item response; discovery/page identity may never replace a missing exact current price.
- Seller, offer, condition, fulfillment, variant, shipping, and selected price may not be mixed across different offers.
- Any reused discovery identity must be recent, official, exact-item matched, and price-aligned; stale or ambiguous proof remains blocked.
- Marketplace mandatory shipping remains part of delivered-price truth.
- Reference-price, threshold, category, duplicate, condition, and availability gates remain fail-closed.
- No CAPTCHA/challenge bypass, proxy rotation, raw-page persistence, alternate-retailer proof substitution, or unrestricted historical rearm.

## Validation
- Pending. Production evidence has reproduced the failure and the real execution path has been inspected through catalog candidate normalization, queue enqueue, exact worker, detail/page provider, public fanout, and quality gate.

## Cleanup / conflict inspection
- Pending. Any new recovery path must integrate with the canonical bulk exact runtime rather than creating a parallel exact worker.

## Blockers
- None currently. Live logs provide the production signatures; exact per-reason retry telemetry still needs to be surfaced by this task.

## Backlog
- Resume the SniperPlug-Site Whop app-specific content-reader task for Hidden Files experiences such as `Make Money Here`, `Content`, and `Better Content` after this task closes.
- Investigate repeated Discord gateway `Can't keep up ... websocket is 41.5s behind` warnings after this active Walmart task closes unless the current root cause directly explains them.
- Continue verifying PR #223 delivery-health accounting from live post-deploy logs after this task closes.
- PR #200 remains isolated and must not be mixed into this task.
