# Active Task

## Status
**Active — SP-WM-MISSED-DEALS-236. Live production validation after PR #235 disproved practical completion: global discovery works, but exact Walmart verification still loses too many candidates to page/API recovery, and a 56.82% exact event was falsely rejected as `quality:review_or_watchlist`. PR #236 Milestone 1 now has a clean implementation/full-suite pass plus cleanup; final canonical CI/review/conflict inspection, merge, post-merge validation, and later live production proof remain. The task lock stays engaged.**

## Task
**SP-WM-MISSED-DEALS-236 — Restore high-throughput Walmart exact verification, use current official seller/offer proof safely, prevent flaky recovery from causing missed alerts, and eliminate false public rejection of exact-verified cards from stale review/watchlist text.**

## Production evidence
- Global catalog discovery remains healthy at roughly 90–100 usable Walmart item IDs per two-route batch.
- Exact verification repeatedly produced full transient batches such as `page 8/8`, `api 8/8`, and mixed `page 5 + api 3`, frequently with `verified 0`.
- Discovery still occasionally paused at `fresh/retry pressure 12/12` or `13/12`. Some of that is genuine first-pass work after successful catalog batches, so simply raising the threshold or moving counters into another lane would not solve the throughput defect.
- User screenshots show live seller proof for previously blocked items: Bounty `22395155` is sold/shipped by Walmart; DEWALT `2814230491` is sold by `HVAC AND TOOLS` and fulfilled by Walmart. The Patbrela 34 QT air-fryer example is about 43.7% off, above the affected server's 40% threshold, demonstrating a real missed-alert opportunity.
- Public skip telemetry exposed a 56.82% exact Walmart event rejected as `quality:review_or_watchlist`.

## Root cause / findings
- Walmart Affiliate search/catalog responses already contain useful selected-offer seller/fulfillment/condition/price proof for many products.
- The exact worker cannot safely treat an old discovery snapshot as current proof, so Milestone 1 refreshes the requested item through the same current Affiliate search API when detail is unavailable/incomplete instead of blindly reusing stale identity.
- `WalmartPageRecoveryProvider` previously moved directly from incomplete item-detail seller identity to Walmart HTML. Live logs show that page dependency failing heavily.
- A strict exact-ID Affiliate search is a safer official recovery path than HTML as long as relevance neighbors are rejected; the implementation requires exact `itemId`/`usItemId` equality.
- The renderer can emit `Good for watchlist` for mid-tier cards. Exact-card normalization removed stale review/scout wording but omitted generic `watchlist`, so a later strict public gate could reject an otherwise exact-verified deal solely because old presentation text survived.
- Retry records already preserve a concrete `retry_kind`, but the live summary formerly exposed only broad `page`/`api` buckets.
- Cleanup inspection found one redundant path: when both detail URLs had already fallen back to exact Affiliate search and that result still needed page identity, the wrapper would call the exact Affiliate search a second time before HTML. That duplicate request has been removed and regression-covered.

## PR #236 Milestone 1 changes
- Added `WalmartProvider.fetch_exact_search_item_payload()` / `_exact_search_item_payload()` using the existing Affiliate search API with full response data.
- Exact search accepts only an exact requested Walmart item-ID match; relevance neighbors cannot donate seller/offer identity.
- If both official item-detail URL forms fail, exact verification tries current exact-ID Affiliate search before returning a transient API failure.
- If item detail succeeds but seller/offer identity remains incomplete, `WalmartPageRecoveryProvider` tries exact-ID Affiliate search before Walmart HTML.
- If the base detail path already used exact Affiliate search, the page wrapper detects `sniperplugOfficialExactSource=affiliate_search_exact_item` and does not call the same search twice.
- Exact-search candidates carry bounded diagnostic proof-source metadata/signals without weakening price, seller, marketplace-shipping, condition, variant, reference, threshold, category, or dedupe gates.
- Exact-worker summaries now aggregate top concrete retry kinds such as `page_http_403`, `page_challenge`, `api_http_429`, and `api_network` alongside broad buckets.
- Strict exact Walmart normalization now removes stale `watchlist` presentation wording only after the existing exact item/price/offer/reference/URL/threshold proof boundary passes. Non-exact scout/watchlist cards remain blocked.
- Added regressions for Bounty-shaped official exact-search seller recovery, relevance-neighbor rejection, duplicate exact-search prevention, exact-card watchlist cleanup, non-exact watchlist blocking, and retry-kind parsing.
- No CAPTCHA/challenge bypass, proxy rotation, raw-page persistence, seller guessing, alternate-retailer proof, threshold weakening, or relevance-neighbor borrowing was introduced.

## Remaining decisions / scope
1. Finish canonical CI on the final cleanup/task-record head, then inspect net diff, mergeability, conflicts, PR comments/reviews, and stale/temporary files.
2. Merge only if those gates are clean, then validate the real merge commit through canonical `main` workflows.
3. Keep the overall task active after merge until live deployment proves materially better verified-throughput and shows concrete retry reasons.
4. Use post-deploy telemetry to decide whether retaining bounded fresh discovery-offer proof or introducing a distinct `api_retry` recovery lane is actually necessary. Do not add either merely to move counters around.
5. If discovery proof is ever reused, require bounded freshness plus exact item and cent-for-cent current-price agreement; stale/ambiguous proof remains blocked.

## Safety invariants
- Requested item ID must exactly equal the current official item being verified.
- Current price must come from a current official exact-item response; stale discovery/page identity cannot replace missing current-price proof.
- Seller, offer, condition, fulfillment, variant, shipping, and selected price cannot be mixed across different offers.
- Marketplace mandatory shipping remains part of delivered-price truth.
- Reference-price, threshold, category, duplicate, condition, and availability gates remain fail-closed.
- No CAPTCHA/challenge bypass, proxy rotation, raw-page persistence, alternate-retailer proof substitution, or unrestricted historical rearm.

## Validation
- Real execution path inspected end to end: Affiliate catalog/search normalization → global enqueue → exact claim → item detail → official exact-search/page recovery → exact offer identity → bulk persistence → fanout → guild public-quality gate.
- Milestone applicator: dependency installation **PASS**, `git diff --check` **PASS**, compile **PASS**, focused recovery/public/runtime/pressure/schema/architecture diagnostics **PASS**.
- First canonical PR head: **Import smoke PASS**, **Python tests PASS**, **Python Check PASS**.
- First canonical full suite: **1,185 passed**, one upstream `discord.py` `audioop` deprecation warning; `pip check` reported no broken requirements; compileall passed.
- Cleanup applicator for duplicate exact-search request: **PASS** for install, diff check, compile, focused exact-recovery/public/runtime tests, and commit.
- Cleanup implementation commit: `065dd2d41ceb5d0fbb13d2b48129a991da2b71e3`.
- Final canonical PR CI after this task-record commit: **pending**.

## Cleanup / conflict inspection
- Both temporary applicator scripts/workflows self-delete before their implementation commits; they are not intended to remain in the net PR diff.
- Milestone 1 integrates into the canonical `WalmartProvider` → `WalmartPageRecoveryProvider` → bulk exact worker path; no competing exact worker was created.
- Page HTML is now later in the recovery chain and remains strict/fail-closed.
- Duplicate exact Affiliate search before page fallback was removed rather than tolerated.
- Final net-diff/redundancy/conflict/review inspection is pending final canonical CI.

## Blockers
- None currently. Final canonical PR validation/inspection is the next gate.

## Backlog
- Resume the SniperPlug-Site Whop app-specific content-reader task for Hidden Files experiences such as `Make Money Here`, `Content`, and `Better Content` after this task closes.
- Investigate repeated Discord gateway `Can't keep up ... websocket is 41.5s behind` warnings after this active Walmart task closes unless the current root cause directly explains them.
- Continue verifying PR #223 delivery-health accounting after this task closes.
- PR #200 remains isolated and must not be mixed into this task.
