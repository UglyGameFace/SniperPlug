# Active Task

## Status
**Active — SP-WM-MISSED-DEALS-236. PR #236 Milestone 1 failed the live throughput gate. PR #237 Milestone 2 merged and passed code/merge CI, but its first production validation also FAILED: after an interrupted deployment the exact worker repeatedly reported `fresh catalog proof 0` while the global catalog producer stopped completing batches, leaving the worker to hammer old page-recovery rows. Milestone 3 is now merged through PR #238 at `10385d358e2c4437a20834f1f067552f264a0695`; final cleanup-head validation, conflict/review inspection, and the real merge-commit workflows all pass. The task remains intentionally OPEN for live Discloud proof of catalog recovery, fresh-proof activation, throughput improvement, and exact-card safety. The Single Active Task Lock remains engaged.**

## Task
**SP-WM-MISSED-DEALS-236 — Restore high-throughput Walmart exact verification so real catalog-discovered deals are not lost to flaky detail/page recovery, use current official seller/offer proof safely, and prevent false public rejection of strict exact-verified cards.**

## Production evidence that reopened the task
- Global Walmart catalog discovery was healthy at roughly 80–100 usable item IDs per two-route batch while exact verification repeatedly lost whole batches as `page 8/8`, `api 8/8`, or mixed transient failures, often with `verified 0`.
- Post-PR-#236 concrete retry telemetry showed `api_exact_search_no_match` and `page_challenge` dominating failed exact batches; actionable due grew from single digits to 107.
- User screenshots showed current seller identity on items SniperPlug had previously blocked, including Bounty `22395155` sold/shipped by Walmart and DEWALT `2814230491` sold by `HVAC AND TOOLS` / fulfilled by Walmart.
- A Patbrela 34 QT air-fryer example was about 43.7% off, above the affected server's 40% threshold, demonstrating a real missed-alert opportunity.
- Fanout telemetry exposed a 56.82% exact Walmart event rejected as `quality:review_or_watchlist`.
- The Aiho 3-Piece Gray production example shows a selected marketplace offer at `$73.99` plus mandatory `$19.99` shipping. Sibling variant tiles expose unrelated strike-through prices; they cannot safely prove a reference price for the selected Gray offer.

## Milestone 2 live production failure after PR #237
- The post-deploy exact-worker field activated correctly but remained `fresh catalog proof **0**` across every supplied new-format batch.
- The exact worker repeatedly hit `claimed **8** • verified **0**`, dominated by `page_challenge=8`; actionable due climbed into the 47–56 range instead of draining.
- Before the final deployment restart, global catalog discovery was completing about once per minute. After the final process came online, the supplied continuous log window through roughly 19:36 contained no `Global Walmart catalog batch completed` line at all.
- The first new process came online around 19:27:45. Its 45-second catalog startup delay makes its first durable catalog claim likely around 19:28:30. A second process restart began around 19:28:49, directly inside that likely first catalog batch.
- The replacement exact worker stayed alive independently and therefore kept draining old pre-snapshot rows while the only producer capable of writing new fresh catalog proof was unavailable.
- Queue backpressure was not the producer blocker: the live actionable backlog was about 55 while the catalog backpressure ceiling is 450.
- A 75.01% Garnier exact event was still rejected for one guild as `quality:review_or_watchlist` in the pre-final-restart portion of the supplied logs. That remains part of the active Walmart live-validation gate; it is not being treated as a separate task.

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

## Milestone 3 merged through PR #238 — interrupted catalog-claim recovery
- The durable global catalog claim previously used a 15-minute lease. `CancelledError` re-raised without releasing an in-flight claim, and `cog_unload` canceled worker tasks without awaiting them.
- A replacement catalog worker receiving an active durable claim returned `claim=None`, slept 15 seconds, and emitted no claim-contention telemetry. This matches the post-restart silent producer gap.
- Current catalog batches use two routes / four search requests. Search requests can wait up to 45 seconds for exact-priority coordinator capacity and then spend up to 12 seconds in Walmart HTTP. A 3-minute claim bound leaves a conservative degraded-batch margin while reducing crash/restart starvation from 15 minutes to at most 3 minutes.
- The same 3-minute ceiling is used both for newly written `lease_until` values and as a maximum claim age. The age ceiling is independent of stored lease duration, so a claim created by the old 15-minute version becomes recoverable after three minutes without a schema migration.
- Claim takeover remains token-guarded in the UPDATE itself. If multiple replacement processes race to recover one stale claim, only the first token can win.
- Recovery intentionally repeats the same route slice; the durable cursor is not advanced until the winning claim completes, so restart recovery cannot silently skip catalog coverage.
- A stale/slow worker holding the obsolete token cannot advance the cursor after takeover.
- Cooperative cancellation explicitly releases an in-flight, not-yet-completed catalog claim, and cog unload now awaits canceled worker tasks so that cleanup can run before unload returns.
- Catalog startup telemetry exposes `catalog_claim_lease_s`, recovered batches append `recovered abandoned claim **yes**`, and active claim contention is logged at a bounded once-per-minute cadence instead of remaining silent.
- Failure logging distinguishes a discovery failure before cursor commit from a later fanout failure after the durable cursor has already advanced.

## Safety invariants
- Requested item ID must exactly equal the official item being verified.
- Payable current price must be backed by current bounded official proof: either the ordinary exact-item recovery response or a fresh normalized Affiliate catalog selected-offer snapshot whose item and cent-for-cent queued payable price are revalidated.
- Seller, offer, condition, fulfillment, variant, shipping, selected price, and reference price cannot be mixed across different offers.
- Marketplace mandatory shipping remains part of delivered-price truth and a third-party snapshot is ineligible when shipping truth is incomplete.
- Reference-price, threshold, category, duplicate, condition, and availability gates remain fail-closed.
- A stale or conflicting discovery snapshot never overrides a newer exact response; it falls through to ordinary recovery.
- Catalog claim recovery repeats work rather than skipping it; stale tokens cannot mutate the replacement token's cursor ownership.
- No CAPTCHA/challenge bypass, proxy rotation, raw-page persistence, seller guessing, relevance-neighbor borrowing, alternate-retailer proof substitution, unrestricted historical rearm, or proof-gate weakening.

## Validation completed
- Real execution path inspected end to end: Affiliate catalog/search → normalized selected offer → global queue enqueue → bounded snapshot persistence → exact claim → fresh-proof promotion or detail/exact-search/page fallback → exact offer identity → bulk persistence → fanout → guild public-quality gate.
- Direct callers of `claim_due_rows_batched` inspected. Production callers initialize/migrate the canonical schema first; a low-level ownership regression intentionally exercises the historical minimal schema directly.
- Fresh-proof focused regressions: **PASS**, including Aiho delivered-price/reference isolation, same-offer reference preservation, stale/price-conflict rejection, third-party unknown-shipping rejection, zero-network promotion, and pre-Milestone-2 schema migration.
- Queue runtime/drain/bulk/persistence and page/public regressions: **PASS**.
- PR #237 canonical/final suites: **1,192 passed**; dependency, compile, import smoke, Python Check, and Python tests passed; merge and real merge-commit workflows passed.
- Milestone 3 restart path inspected through `global_auto_scan_runner` → durable catalog state → request coordinator → off-loop catalog discovery. The request coordinator wait ceiling is 45 seconds, Walmart Affiliate HTTP timeout is 12 seconds, catalog concurrency is 3, and the production two-route batch creates four searches.
- PR #238 restart regressions reproduce an old persisted 15-minute lease, require the first 179 seconds to remain owned, require recovery immediately after the new 3-minute ceiling, require the replacement to repeat the exact same route slice, and prove the old token cannot advance the cursor during takeover.
- PR #238 also regresses explicit cancellation release/no-cursor-advance and the runner's awaited shutdown/cancellation-release lifecycle.
- First PR #238 canonical run: **1,196 passed**, one upstream `discord.py` `audioop` deprecation warning, in **13.47s**.
- First PR #238 dependency validation: **PASS — no broken requirements**.
- First PR #238 compile/static validation: **PASS**.
- First and final cleanup-head PR #238 workflows: **Import smoke PASS / Python Check PASS / Python tests PASS**.
- Final PR #238 diff contained exactly four intended files: `ACTIVE_TASK.md`, the canonical global autoscan runner, the canonical global catalog state service, and the restart regression file. The branch was 0 behind `main`, GitHub reported mergeable, review submissions/threads were empty, and the only PR comment was Qodo reporting its inactive subscription.
- Cleanup removed an unused regression import before the final head; the final head reran and passed all canonical workflows.
- PR #238 merged successfully as `10385d358e2c4437a20834f1f067552f264a0695`.
- Real PR #238 merge-commit workflows on `main`: **Import smoke PASS / Python Check PASS / Python tests PASS**, including dependency and compile gates.

## Remaining Definition of Done
1. Deploy current `main` to Discloud and verify startup reports `catalog_claim_lease_s=180`.
2. Verify a restarted process resumes `Global Walmart catalog batch completed` within the bounded startup/claim-recovery window rather than remaining silently starved for 15 minutes. A cooperative shutdown should release immediately; a hard kill is bounded by the three-minute claim age plus worker startup/retry cadence.
3. After new catalog batches occur, inspect `fresh catalog proof`, `api_exact_search_no_match`, `page_challenge`, whole-batch transient losses, and actionable-due trend.
4. Confirm fresh catalog proof materially reduces unnecessary detail/search/page refetching for newly discovered safe offers and actionable due trends down rather than snowballing.
5. Confirm marketplace delivered-price truth and same-selected-offer reference safety remain intact in live cards; sibling/variant reference prices must not leak across offers.
6. Confirm strong exact deals are no longer rejected merely because stale `watchlist` presentation text survived an earlier scan state.
7. Keep this task active until production demonstrates materially improved throughput without seller/variant/shipping/reference regressions. If live evidence still fails the gate, continue under this same task rather than moving on.

## Cleanup / conflicts
- Canonical provider/queue/worker path retained; no parallel catalog or exact worker introduced.
- Product-page HTML remains a strict later fallback.
- Numeric keyword search remains a fallback rather than being treated as a guaranteed item-ID lookup.
- Bulk enqueue remains bounded below SQLite's conservative parameter ceiling at 55 rows × 18 parameters = 990.
- Ordinary recent rediscovery write suppression remains in place; safe proof snapshots have their own bounded refresh cadence.
- Low-level pre-snapshot queue compatibility is preserved without adding a production-path schema query on every exact claim.
- Catalog recovery modifies only lease ownership/lifecycle; it does not alter Walmart candidate proof, thresholds, fanout eligibility, or exact verification semantics.
- No temporary implementation workflow or duplicate catalog state table was added.
- PR #238 final conflict/review/net-diff inspection passed before merge.

## Blocker
- **Live production validation of merged Milestone 3.** Code-side implementation, targeted regressions, canonical full suites, compile/dependency checks, cleanup, conflict/review inspection, merge, and real merge-commit CI are complete. The overall Walmart missed-deal task remains active until Discloud proves catalog recovery, fresh-proof activation, throughput improvement, and exact-card safety.

## Backlog
- Resume the SniperPlug-Site Whop app-specific content-reader task for Hidden Files experiences such as `Make Money Here`, `Content`, and `Better Content` after this task closes.
- Investigate repeated Discord gateway `Can't keep up ... websocket is 41.5s behind` warnings after this active Walmart task closes unless current evidence directly connects them.
- Continue verifying PR #223 delivery-health accounting after this task closes.
- PR #200 remains isolated and must not be mixed into this task.
