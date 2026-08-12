# Active Task

## Status
**Active — SP-WM-MISSED-DEALS-236. PR #236 Milestone 1 passed code validation but FAILED the live throughput gate: production still produced repeated 8/8 transient exact-verification losses, `api_exact_search_no_match` / `page_challenge` dominated, and actionable due climbed to 107. Milestone 2 is now merged through PR #237 at `7ea621694598e4cfd68943774111faad130ef314`; focused/full/static/cleanup/conflict validation and the real merge-commit workflows pass. The task remains intentionally OPEN for live production throughput proof. The Single Active Task Lock remains engaged.**

## Task
**SP-WM-MISSED-DEALS-236 — Restore high-throughput Walmart exact verification so real catalog-discovered deals are not lost to flaky detail/page recovery, use current official seller/offer proof safely, and prevent false public rejection of strict exact-verified cards.**

## Production evidence that reopened the task
- Global Walmart catalog discovery was healthy at roughly 80–100 usable item IDs per two-route batch while exact verification repeatedly lost whole batches as `page 8/8`, `api 8/8`, or mixed transient failures, often with `verified 0`.
- Post-PR-#236 concrete retry telemetry showed `api_exact_search_no_match` and `page_challenge` dominating failed exact batches; actionable due grew from single digits to 107.
- User screenshots showed current seller identity on items SniperPlug had previously blocked, including Bounty `22395155` sold/shipped by Walmart and DEWALT `2814230491` sold by `HVAC AND TOOLS` / fulfilled by Walmart.
- A Patbrela 34 QT air-fryer example was about 43.7% off, above the affected server's 40% threshold, demonstrating a real missed-alert opportunity.
- Fanout telemetry exposed a 56.82% exact Walmart event rejected as `quality:review_or_watchlist`.
- The Aiho 3-Piece Gray production example shows a selected marketplace offer at `$73.99` plus mandatory `$19.99` shipping. Sibling variant tiles expose unrelated strike-through prices; they cannot safely prove a reference price for the selected Gray offer.

## Root causes addressed by PR #236
- Incomplete Walmart item-detail responses went directly to flaky Walmart product HTML even though the Affiliate search route was healthy enough to keep global discovery moving.
- Detail endpoint failures did not have a strict current exact-ID Affiliate search fallback before becoming transient failures.
- The renderer can emit `Good for watchlist`; strict exact-card normalization previously failed to remove generic stale `watchlist` wording, so a later public quality pass could reject an otherwise exact-verified deal.
- Retry persistence already carried concrete `retry_kind`, but live summaries exposed only broad page/API buckets.
- Cleanup found and removed a duplicate exact-Affiliate request that could occur before HTML fallback.

## Changes on `main` from PR #236
- Added a current exact-ID Affiliate search recovery path using the existing Walmart Affiliate search API.
- Exact recovery accepts only a result whose `itemId`/`usItemId` exactly equals the requested Walmart item; relevance neighbors cannot supply identity.
- Both detail-endpoint failure and incomplete seller/offer identity try current official exact-ID Affiliate search before Walmart HTML.
- If the detail path already used exact Affiliate search, the page wrapper does not issue the same exact search twice.
- Exact-search candidates carry bounded proof-source diagnostics without weakening current-price, seller/offer, shipping, condition, variant, reference, threshold, category, or duplicate gates.
- Exact-worker summaries include concrete retry kinds such as `page_http_403`, `page_challenge`, `api_http_429`, `api_exact_search_no_match`, and `api_network` in addition to broad buckets.
- Strict exact Walmart normalization removes stale `watchlist` presentation wording only after the existing exact item/price/offer/reference/URL/threshold proof boundary passes. Non-exact scout/watchlist cards remain blocked.

## Milestone 2 merged through PR #237 — fresh official catalog selected-offer proof
- Live production proved numeric item IDs cannot rely on the Affiliate keyword-search fallback as an exact lookup: catalog discovery had just returned an item, but later keyword searches frequently returned `api_exact_search_no_match`.
- The catalog `SourceCandidate` already normalizes the selected item ID, selected offer, seller, variant, condition, fulfillment, item price, marketplace shipping, delivered total, and same-offer reference proof. The previous queue representation discarded most of that structure before exact verification.
- The exact queue now persists only a bounded normalized selected-offer snapshot (never the raw Walmart payload or product page).
- The canonical worker may use the snapshot without another Walmart fetch only while it is within the bounded freshness window, exact-item matched, cent-for-cent matched to the queued payable price, seller/offer complete, variant-safe, and marketplace shipping complete.
- For third-party marketplace offers, delivered-price truth remains mandatory. The Aiho 3-Piece Gray regression requires `$73.99 + $19.99 = $93.98` and refuses sibling `$139.99`/`$169.99` context as a selected-offer reference.
- Same-selected-offer trusted Walmart reference proof survives promotion; cross-offer/sibling context is stripped back to no-reference rather than used to manufacture a discount.
- `page_retry` rows can be rearmed when a new safe fresh catalog snapshot arrives, allowing healthy catalog discovery to rescue work that previously depended on a challenged product page.
- Missing, stale, price-conflicting, shipping-incomplete, identity-incomplete, or variant-ambiguous snapshots fall through to the existing detail → exact-search → page recovery chain.
- Exact-worker telemetry reports `fresh catalog proof **N**` so live production can prove whether this path actually reduces refetch failures.
- No parallel exact worker, CAPTCHA/challenge bypass, proxy rotation, raw-page persistence, seller guessing, threshold weakening, category weakening, duplicate weakening, relevance-neighbor borrowing, or alternate-retailer proof substitution was added.

## Safety invariants
- Requested item ID must exactly equal the official item being verified.
- Payable current price must be backed by current bounded official proof: either the ordinary exact-item recovery response or a fresh normalized Affiliate catalog selected-offer snapshot whose item and cent-for-cent queued payable price are revalidated.
- Seller, offer, condition, fulfillment, variant, shipping, selected price, and reference price cannot be mixed across different offers.
- Marketplace mandatory shipping remains part of delivered-price truth and a third-party snapshot is ineligible when shipping truth is incomplete.
- Reference-price, threshold, category, duplicate, condition, and availability gates remain fail-closed.
- A stale or conflicting discovery snapshot never overrides a newer exact response; it falls through to ordinary recovery.
- No CAPTCHA/challenge bypass, proxy rotation, raw-page persistence, seller guessing, relevance-neighbor borrowing, alternate-retailer proof substitution, or unrestricted historical rearm.

## Validation completed
- Real execution path inspected end to end: Affiliate catalog/search → normalized selected offer → global queue enqueue → bounded snapshot persistence → exact claim → fresh-proof promotion or detail/exact-search/page fallback → exact offer identity → bulk persistence → fanout → guild public-quality gate.
- Direct callers of `claim_due_rows_batched` inspected. Production callers initialize/migrate the canonical schema first; a low-level ownership regression intentionally exercises the historical minimal schema directly.
- Fresh-proof focused regressions: **PASS**, including Aiho delivered-price/reference isolation, same-offer reference preservation, stale/price-conflict rejection, third-party unknown-shipping rejection, zero-network promotion, and pre-Milestone-2 schema migration.
- Queue runtime/drain/bulk/persistence and page/public regressions: **PASS**.
- Dependency validation: **PASS — no broken requirements**.
- Compile/static validation: **PASS**.
- First full PR #237 diagnostic identified one backward-compatibility defect rather than a performance hang: the low-level drain SELECT required new snapshot columns on a deliberately minimal old-schema test table. The failed test left its `aiosqlite` worker open, causing pytest to appear hung after the assertion failure.
- Compatibility repair: current production schema keeps the one-query fast SELECT; only the specific missing-snapshot-column error retries once with `''` / `NULL` aliases. Other database errors still propagate. Legacy claims therefore remain fail-closed with no fabricated fresh proof.
- Legacy ownership regression asserts empty snapshot fields and closes its SQLite connection in `finally`.
- Canonical PR suite after compatibility repair: **1,192 passed**, one upstream `discord.py` `audioop` deprecation warning.
- Final cleanup-head canonical suite: **1,192 passed**, one upstream warning, in **13.40s**.
- Final cleanup-head workflows: **Import smoke PASS / Python tests PASS / Python Check PASS**.
- Final PR net diff contained exactly 11 intended task/test files. Temporary applicator and diagnostic workflows were absent.
- Final PR inspection: `main` unchanged from the PR base, branch 0 behind, GitHub mergeable, no review submissions, no review threads, and no code-review comments. The only PR comment was Qodo reporting its subscription inactive.
- PR #237 merged successfully as `7ea621694598e4cfd68943774111faad130ef314`.
- Real merge-commit `main` workflows: **Import smoke PASS / Python tests PASS / Python Check PASS**.

## Remaining Definition of Done
1. Deploy current `main` to Discloud.
2. Inspect live exact-worker summaries for `fresh catalog proof`, `api_exact_search_no_match`, `page_challenge`, whole-batch transient losses, and actionable-due trend.
3. Confirm fresh catalog proof materially reduces unnecessary detail/search/page refetching for newly discovered safe offers.
4. Confirm actionable due trends down rather than continuing to snowball and that repeated `8 claimed → 0 verified` streaks materially decrease.
5. Confirm marketplace delivered-price truth and same-selected-offer reference safety remain intact in live cards; sibling/variant reference prices must not leak across offers.
6. Confirm strong exact deals are no longer rejected merely because stale `watchlist` presentation text survived an earlier scan state.
7. Keep this task active until production demonstrates materially improved throughput without seller/variant/shipping/reference regressions. If live evidence still fails the gate, continue under this same task rather than moving on.

## Cleanup / conflicts
- Canonical provider/queue/worker path retained; no parallel exact worker introduced.
- Product-page HTML remains a strict later fallback.
- Numeric keyword search remains a fallback rather than being treated as a guaranteed item-ID lookup.
- Bulk enqueue remains bounded below SQLite's conservative parameter ceiling at 55 rows × 18 parameters = 990.
- Ordinary recent rediscovery write suppression remains in place; safe proof snapshots have their own bounded refresh cadence.
- Low-level pre-snapshot queue compatibility is preserved without adding a production-path schema query on every exact claim.
- Temporary implementation and diagnostic helpers are absent from `main`.
- PR conflict/review/net-diff inspection passed before merge.

## Blocker
- **Live production validation of Milestone 2.** Code-side validation, cleanup, merge, and real merge-commit CI are complete, but the overall Walmart missed-deal task remains active until production proves the throughput improvement.

## Backlog
- Resume the SniperPlug-Site Whop app-specific content-reader task for Hidden Files experiences such as `Make Money Here`, `Content`, and `Better Content` after this task closes.
- Investigate repeated Discord gateway `Can't keep up ... websocket is 41.5s behind` warnings after this active Walmart task closes unless current evidence directly connects them.
- Continue verifying PR #223 delivery-health accounting after this task closes.
- PR #200 remains isolated and must not be mixed into this task.
