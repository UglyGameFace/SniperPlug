# Active Task

## Status
**Active — SP-WM-MISSED-DEALS-236. PR #236 Milestone 1 passed code validation but FAILED the live throughput gate: production still produced repeated 8/8 transient exact-verification losses, `api_exact_search_no_match` / `page_challenge` dominated, and actionable due climbed to 107. Milestone 2 is implementing bounded fresh official catalog selected-offer proof reuse inside the same canonical exact worker. The Single Active Task Lock remains engaged.**

## Task
**SP-WM-MISSED-DEALS-236 — Restore high-throughput Walmart exact verification so real catalog-discovered deals are not lost to flaky detail/page recovery, use current official seller/offer proof safely, and prevent false public rejection of strict exact-verified cards.**

## Production evidence that reopened the task
- Global Walmart catalog discovery was healthy at roughly 90–100 usable item IDs per two-route batch while exact verification repeatedly lost whole batches as `page 8/8`, `api 8/8`, or mixed transient failures, often with `verified 0`.
- User screenshots showed current seller identity on items SniperPlug had previously blocked, including Bounty `22395155` sold/shipped by Walmart and DEWALT `2814230491` sold by `HVAC AND TOOLS` / fulfilled by Walmart.
- A Patbrela 34 QT air-fryer example was about 43.7% off, above the affected server's 40% threshold, demonstrating a real missed-alert opportunity.
- Fanout telemetry exposed a 56.82% exact Walmart event rejected as `quality:review_or_watchlist`.

## Root causes addressed by PR #236
- Incomplete Walmart item-detail responses went directly to flaky Walmart product HTML even though the Affiliate search route was healthy enough to keep global discovery moving.
- Detail endpoint failures did not have a strict current exact-ID Affiliate search fallback before becoming transient failures.
- The renderer can emit `Good for watchlist`; strict exact-card normalization previously failed to remove generic stale `watchlist` wording, so a later public quality pass could reject an otherwise exact-verified deal.
- Retry persistence already carried concrete `retry_kind`, but live summaries exposed only broad page/API buckets.
- Cleanup found and removed a duplicate exact-Affiliate request that could occur before HTML fallback.

## Changes now on `main`
- Added a current exact-ID Affiliate search recovery path using the existing Walmart Affiliate search API.
- Exact recovery accepts only a result whose `itemId`/`usItemId` exactly equals the requested Walmart item; relevance neighbors cannot supply identity.
- Both detail-endpoint failure and incomplete seller/offer identity now try current official exact-ID Affiliate search before Walmart HTML.
- If the detail path already used exact Affiliate search, the page wrapper does not issue the same exact search twice.
- Exact-search candidates carry bounded proof-source diagnostics without weakening current-price, seller/offer, shipping, condition, variant, reference, threshold, category, or duplicate gates.
- Exact-worker summaries now include top concrete retry kinds such as `page_http_403`, `page_challenge`, `api_http_429`, and `api_network` in addition to broad buckets.
- Strict exact Walmart normalization removes stale `watchlist` presentation wording only after the existing exact item/price/offer/reference/URL/threshold proof boundary passes. Non-exact scout/watchlist cards remain blocked.

## Milestone 2 — fresh official catalog selected-offer proof
- Live production proved the numeric Affiliate keyword-search fallback is not a reliable exact-item lookup: catalog discovery had just returned the item, but later exact searches frequently returned `api_exact_search_no_match`.
- The catalog `SourceCandidate` already normalizes selected item ID, selected offer, seller, variant, condition, fulfillment, item price, marketplace shipping, delivered total, and same-offer reference proof. The queue currently discards most of that structure.
- Milestone 2 persists only a bounded normalized selected-offer snapshot (never the raw Walmart payload) and permits a zero-network fast path only while that snapshot is fresh, exact-item matched, cent-for-cent matched to the queued payable price, seller/offer complete, and marketplace shipping complete.
- Sibling/cross-offer references remain context only. The Aiho 3-Piece Gray production example is `$73.99 + $19.99 shipping = $93.98 delivered`; sibling variant strike-through prices must not become its reference price.
- Page/API recovery remains the fallback whenever the fresh snapshot is absent, stale, price-conflicting, variant-ambiguous, or otherwise incomplete. No challenge bypass is introduced.

## Safety invariants
- Requested item ID must exactly equal the current official item being verified.
- Current price must come from a current official exact-item response; stale discovery/page identity cannot replace missing current-price proof.
- Seller, offer, condition, fulfillment, variant, shipping, and selected price cannot be mixed across different offers.
- Marketplace mandatory shipping remains part of delivered-price truth.
- Reference-price, threshold, category, duplicate, condition, and availability gates remain fail-closed.
- No CAPTCHA/challenge bypass, proxy rotation, raw-page persistence, seller guessing, relevance-neighbor borrowing, alternate-retailer proof substitution, or unrestricted historical rearm.

## Validation completed
- Real execution path inspected end to end: Affiliate catalog/search → queue enqueue → exact claim → detail → official exact-search/page recovery → offer identity → bulk persistence → fanout → guild public-quality gate.
- Focused recovery/public/runtime/pressure/schema/architecture tests: **PASS**.
- Dependency validation: **PASS — no broken requirements**.
- Compile/static validation: **PASS**.
- Final PR #236 canonical suite: **1,186 passed**, one upstream `discord.py` `audioop` deprecation warning.
- Final PR workflows: **Import smoke PASS / Python tests PASS / Python Check PASS**.
- Cleanup/regression for duplicate exact search: **PASS**.
- Net PR diff contained only 9 intended files; temporary applicator/workflow files were absent.
- Branch was 0 commits behind `main`, mergeable, with no review submissions, review threads, or PR comments at final inspection.
- PR #236 merged successfully as `9ca3a32b456eb38fff09396e3aac64649dd9b7dd`.
- Real merge-commit `main` workflows: **Import smoke PASS / Python tests PASS / Python Check PASS**.

## Remaining Definition of Done
1. Deploy current `main` and inspect live exact-worker batches with the new concrete retry-kind telemetry.
2. Confirm exact verification no longer routinely loses whole 8-item batches and that official exact-search recovery materially reduces page dependency.
3. Confirm strong exact deals are no longer rejected merely because stale `watchlist` presentation text survived an earlier scan state.
4. Use live evidence—not guesses—to decide whether a bounded fresh discovery-offer proof assist or distinct `api_retry` lane is still necessary. Do not add either merely to move counters.
5. If additional implementation is required, continue under this same active task and repeat focused/full/static/cleanup/conflict/PR/post-merge validation before closing.

## Cleanup / conflicts
- Canonical provider/worker path retained; no parallel exact worker introduced.
- Product-page HTML remains a strict later fallback.
- Duplicate exact search removed.
- Temporary implementation helpers removed from the net source tree.
- PR merge/conflict/review inspection passed.

## Blocker
- **Live production validation of the merged behavior.** Code-side Milestone 1 is validated, but the overall Walmart missed-deal task remains active until production proves the throughput improvement.

## Backlog
- Resume the SniperPlug-Site Whop app-specific content-reader task for Hidden Files experiences such as `Make Money Here`, `Content`, and `Better Content` after this task closes.
- Investigate repeated Discord gateway `Can't keep up ... websocket is 41.5s behind` warnings after this active Walmart task closes unless current evidence directly connects them.
- Continue verifying PR #223 delivery-health accounting after this task closes.
- PR #200 remains isolated and must not be mixed into this task.
