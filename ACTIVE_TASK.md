# Active Task

## Status
**Active — SP-WM-MISSED-DEALS-236. Live production validation after PR #235 disproves practical completion: catalog discovery is healthy, but most exact Walmart claims are still failing in page/API recovery before they can become verified alerts, and an exact 56.82% event was rejected publicly as `quality:review_or_watchlist`. Milestone 1 implementation is focused-test clean; full regression/PR validation and remaining recovery-policy decisions are still required. The task lock remains engaged.**

## Task
**SP-WM-MISSED-DEALS-236 — Restore high-throughput Walmart exact verification so catalog-discovered deals are not lost to flaky detail/page recovery, preserve/use official seller/offer proof safely, stop recovery traffic from starving useful discovery, and eliminate false public rejection of exact-verified cards caused by stale review/watchlist text.**

## Production evidence
- Live catalog discovery continues returning roughly 90–100 usable Walmart item IDs per two-route batch, so global discovery itself is functioning.
- Exact verification throughput is unacceptable. Production repeatedly reports entire claimed batches as transient failures: `page 8/8`, `api 8/8`, and mixed batches such as `page 5 + api 3`, often with `verified 0`.
- Catalog discovery still pauses at `fresh/retry pressure 12/12` and `13/12` while `page recovery 0`. Some of that pressure is genuinely fresh work dumped by a successful catalog batch; ordinary API retries also remain in the same lane after their delay. Raising the threshold would hide the throughput defect rather than solve it.
- User-supplied Walmart screenshots show real live seller identity on products SniperPlug previously labeled as missing seller identity: Bounty item `22395155` is sold and shipped by Walmart; DEWALT item `2814230491` shows seller `HVAC AND TOOLS` and fulfillment by Walmart. A Patbrela 34 QT air-fryer listing in the same report is visibly above the 40% server threshold, demonstrating missed alert opportunity rather than only below-threshold identity noise.
- New public-skip telemetry exposed an exact Walmart event at 56.82% being blocked by `quality:review_or_watchlist`; that is not a threshold failure.

## Root cause / findings
- The Walmart Affiliate catalog/search response is already normalized into `SourceCandidate` with selected seller, seller ID, offer ID, fulfillment, condition, marketplace state, item price, reference price, and shipping evidence where returned.
- The exact queue currently does not retain all of that discovery-time selected-offer proof. The worker therefore has to refresh identity through an exact official response before an event can be treated as verified. Reusing stale discovery identity blindly would be unsafe, so the first remediation refreshes the same exact item ID through the working Affiliate search route instead of trusting old proof.
- `WalmartPageRecoveryProvider` previously went from an incomplete detail response directly to Walmart product HTML. Production frequently receives page-level transient failures, making that HTML dependency a major loss point.
- The Affiliate search route is demonstrably healthy enough to keep global catalog discovery moving while item-detail/page verification is failing. A strict exact-ID search is therefore a safer official recovery source than page HTML, provided relevance neighbors are rejected.
- Official detail/API failures remain ordinary `retry`. A separate `api_retry` lane is still under evaluation; it must not be added merely to move counters around if exact-search recovery removes most of the failure load.
- Exact retry telemetry previously reported only broad buckets (`page`, `api`, etc.). Persisted errors carry concrete `retry_kind`, so the batch summary can surface those without logging secrets or page bodies.
- `public_deal_quality.is_review_or_watchlist()` scans rendered card text for generic review/watchlist markers.
- `deal_scanner.liveness_block()` literally renders `Good for watchlist` for 30–50% candidates. Exact-card normalization removes stale review/scout wording after strict proof promotion, but its marker list omitted generic `watchlist`. A card can therefore be rebuilt/promoted as exact and still carry stale presentation text that later poisons the guild-specific public gate.

## Milestone 1 changes on `fix/walmart-missed-deal-throughput`
- Added `WalmartProvider.fetch_exact_search_item_payload()` / `_exact_search_item_payload()` using the existing Affiliate search API with `responseGroup=full`.
- Exact search accepts a result only when `itemId`/`usItemId` exactly equals the requested Walmart item ID. It never trusts the first relevance result.
- When both item-detail URL forms fail, exact verification now attempts that official exact-ID Affiliate search before returning a transient API failure.
- When detail succeeds but seller/offer identity still requires recovery, `WalmartPageRecoveryProvider` now tries the exact-ID Affiliate search before requesting Walmart HTML. If the exact search supplies complete official identity, the product page is not fetched.
- Exact-search candidates carry an internal `officialExactSource=affiliate_search_exact_item` diagnostic marker/signal; this does not weaken current-price, seller, marketplace-shipping, reference, condition, or variant gates.
- Exact-worker summaries now aggregate concrete bounded retry kinds such as `page_http_403`, `page_challenge`, `api_http_429`, `api_network`, etc., in addition to the broad page/API/timeout buckets.
- Strict exact Walmart normalization now treats stale `watchlist` presentation wording as removable only after the existing exact item, exact price, trusted reference, offer identity, URL/item, and threshold proof boundary has passed. Non-exact watchlist/scout cards remain blocked.
- Added focused regressions for Bounty-shaped missing-seller recovery through exact Affiliate search without a page fetch, exact-ID relevance-neighbor rejection, stale watchlist cleanup on strict exact cards, preservation of non-exact watchlist blocking, and retry-kind parsing.
- No CAPTCHA/challenge bypass, proxy rotation, raw-page persistence, alternate-retailer proof substitution, threshold weakening, seller/offer guessing, or relevance-neighbor identity borrowing was introduced.

## Remaining scope / decisions
1. Run canonical full regression/compile/dependency validation for Milestone 1 and inspect failures before any further implementation.
2. Inspect whether exact-search recovery materially reduces the need to persist discovery-time seller/offer proof. If discovery proof is retained as a last-resort assist, it must have a bounded freshness window plus exact item and cent-for-cent current-price agreement; otherwise it remains diagnostic-only.
3. Decide from evidence whether a bounded `api_retry` lane is still needed. It must remain inside total emergency backpressure and may not simply raise/disable fresh thresholds.
4. Preserve claim priority: genuinely fresh first-pass work ahead of recovery traffic; recovery lanes must not monopolize discovery.
5. After code validation, use the new concrete retry kinds in live production to distinguish Walmart 403/challenge pressure from API endpoint/network failures.
6. Run cleanup/redundancy inspection, diff/conflict inspection, PR CI/review inspection, merge, and post-merge canonical validation before code-side completion.
7. Require a later live deployment to prove production improvement; code-side success alone must not be called live-complete.

## Safety invariants
- Requested item ID must exactly equal the official item being verified.
- Current price must come from a current official exact-item response; stale discovery/page identity may never replace a missing exact current price.
- Seller, offer, condition, fulfillment, variant, shipping, and selected price may not be mixed across different offers.
- Any reused discovery identity must be recent, official, exact-item matched, and price-aligned; stale or ambiguous proof remains blocked.
- Marketplace mandatory shipping remains part of delivered-price truth.
- Reference-price, threshold, category, duplicate, condition, and availability gates remain fail-closed.
- No CAPTCHA/challenge bypass, proxy rotation, raw-page persistence, alternate-retailer proof substitution, or unrestricted historical rearm.

## Validation
- Real execution path inspected through Affiliate catalog/search normalization → global catalog enqueue → exact claim → item-detail provider → page recovery → exact candidate/offer identity → bulk persistence → fanout → guild-specific public quality gate.
- Milestone 1 applicator: dependency installation **PASS**.
- `git diff --check`: **PASS** in the applicator workflow.
- Compile validation for `sniperplug` and `tests`: **PASS**.
- Focused Walmart recovery/public-lane/runtime/pressure/schema/architecture/diagnostic test set: **PASS**.
- Temporary applicator and workflow self-deleted from the implementation commit; they are not part of the net source change.
- Canonical full-suite/PR validation: **pending**.

## Cleanup / conflict inspection
- Milestone 1 integrates with the existing `WalmartProvider` → `WalmartPageRecoveryProvider` → canonical bulk exact worker path. No parallel exact worker was introduced.
- Page HTML remains only a fallback after current official exact API paths cannot provide the required identity.
- Full net-diff/redundancy/conflict inspection is pending after canonical CI.

## Blockers
- None currently. Full canonical regression results are the next gate.

## Backlog
- Resume the SniperPlug-Site Whop app-specific content-reader task for Hidden Files experiences such as `Make Money Here`, `Content`, and `Better Content` after this task closes.
- Investigate repeated Discord gateway `Can't keep up ... websocket is 41.5s behind` warnings after this active Walmart task closes unless the current root cause directly explains them.
- Continue verifying PR #223 delivery-health accounting from live post-deploy logs after this task closes.
- PR #200 remains isolated and must not be mixed into this task.
