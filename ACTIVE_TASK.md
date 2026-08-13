# Active Task

## Status
**Active — SP-WM-MISSED-DEALS-236. Milestones 1–4 are merged through PR #239. The merged Milestone 4 build passed several real production gates: global catalog discovery stayed healthy, safe fresh proof was generated and promoted, page retries were rescued by rediscovery, the product-page challenge circuit opened, exact verification continued through official API/fresh proof, and exact events reached fanout. The same live run exposed two remaining runtime scheduling/write-amplification defects now addressed by PR #240. The task remains intentionally OPEN until PR #240 is merged, deployed, and live production proves the remaining gates. The Single Active Task Lock remains engaged.**

## Task
**SP-WM-MISSED-DEALS-236 — Restore high-throughput Walmart exact verification so real catalog-discovered deals are not lost to flaky detail/page recovery, use current official selected-offer proof safely, and prevent valid exact deals from being discarded by stale presentation state.**

## Scope / safety invariants
- Requested Walmart item ID must exactly match the official item being verified.
- Current payable price must come from current bounded official proof.
- Seller, selected offer, variant, condition, fulfillment, mandatory marketplace shipping, delivered price, and reference price cannot be mixed across offers.
- Third-party marketplace offers remain blocked when shipping/delivered-price truth is incomplete.
- Reference-price, availability, threshold, category, DM preference, and duplicate gates remain fail-closed.
- Fresh catalog proof is reusable only while bounded-fresh, exact-item matched, cent-for-cent matched to the queued payable price, seller/offer complete, variant-safe, and shipping-complete when required.
- Product-page structured data remains a later identity/shipping fallback only; it cannot replace official item/current-price proof or choose a cheaper seller.
- No CAPTCHA/challenge bypass, proxy rotation, browser fingerprint spoofing, seller guessing, relevance-neighbor borrowing, sibling/cross-offer reference borrowing, or alternate-retailer proof substitution.

## Changes already merged
### Milestone 1 — PR #236
- Added strict exact-ID Affiliate search fallback after incomplete/failed item-detail recovery and before product-page HTML.
- Exact search accepts only a returned `itemId`/`usItemId` equal to the requested ID.
- Added concrete retry-kind telemetry.
- Removed stale exact-card `watchlist` presentation text only after strict exact proof passes.

### Milestone 2 — PR #237
- Persisted a bounded normalized selected-offer snapshot from current Affiliate catalog discovery.
- Added strict zero-refetch promotion while the snapshot is fresh and item/price/seller/offer/variant/shipping checks pass.
- Preserved marketplace delivered-price truth and same-selected-offer reference isolation.

### Milestone 3 — PR #238
- Reduced abandoned durable catalog-claim recovery from the historical 15-minute starvation window to a 3-minute maximum claim age/lease.
- Added token-guarded stale-claim takeover, cooperative claim release, awaited worker shutdown, and bounded claim-contention telemetry.
- Preserved cursor ownership: recovery repeats the same route slice rather than skipping catalog coverage.

### Milestone 4 — PR #239
- Prioritized current bounded fresh catalog proof ahead of network recovery.
- Persisted/reused strict selected-offer proof and rearmed challenged `page_retry` rows to `pending` when a safe current catalog snapshot rediscovered them.
- Capped no-fresh page-recovery claims and added a provider-scoped challenge circuit that suppresses only Walmart HTML while official detail/exact search continue.
- Added `safe fresh proof`, `page retries rescued`, promoted/input fresh-proof counts, rejection reasons, page-circuit state, suppressed-page counts, and cooldown telemetry.
- PR #239 branch and real merge-commit CI passed before deployment.

## Milestone 4 live production result
### Gates that passed
- Global catalog producer remained healthy after deployment and continued advancing route/pass state.
- Catalog batches repeatedly produced nonzero strict `safe fresh proof`, including high-yield batches such as 59, 68, and 80 safe snapshots.
- Fresh proof actually promoted in the exact worker, including observed `2/2`, `6/6`, `1/1`, and `2/2` promoted/input batches.
- `page retries rescued **2**` appeared in production, proving rediscovery can move challenged rows back to immediately useful strict work.
- The page challenge circuit opened and suppressed Walmart HTML while official/fresh verification continued.
- Exact verification produced real markdown/no-reference/unavailable classifications instead of remaining stuck at `verified 0`.
- Exact fanout ran successfully. A supplied VTech event at 24.46% was rejected for the correct server-rule reason (`below_threshold` against 40%/50%), not for stale watchlist or identity state.

### Remaining failures exposed by the same live run
1. **Open-circuit page backlog churn.** While the page circuit was already open, existing due `page_retry` rows remained immediately claimable. The worker repeatedly leased them every 20 seconds even though HTML was deliberately unavailable, producing `page_circuit_open` failures and unnecessary Turso writes. One live cycle still consumed all 8 claims as suppressed page work (`claimed 8 • verified 0 • page_circuit_open=8`).
2. **Terminal maintenance bursts.** The 15-minute terminal-maintenance cadence was bounded in time but not row volume. Production rearmed batches of 5 and later 15 weekly identity reprobes and quarantined batches of 30/15 rows, creating avoidable fresh-lane and database-write bursts.
3. **Pressure telemetry ambiguity.** `page recovery **64**` was a saturated bounded measurement, not an exact total. The old summary omitted the lower-bound marker and could falsely imply the backlog was exactly 64.
4. One Discord gateway `Can't keep up` warning occurred in the supplied window. Nearby isolated-Turso warnings also showed multi-second remote/lock waits. Native libSQL already runs in a child process, so this single warning is not being falsely attributed to one operation; PR #240 removes the confirmed unnecessary queue/write churn first and the live follow-up will determine whether gateway lag repeats.

## Milestone 5 — PR #240 implementation
### Do not lease work that cannot use the page
- Added a bounded open-circuit deferral step before exact pressure/claim selection.
- While the page circuit has remaining cooldown, up to 64 currently due `page_retry` rows are moved just past the circuit close time instead of being leased and failed again.
- The deferral is scheduling only: no seller, offer, price, reference, shipping, threshold, category, DM, or dedupe proof is created or weakened.
- Fresh catalog rediscovery remains immediate: the existing safe-snapshot enqueue path changes a rescued row to `pending`, and the circuit deferral intentionally touches only `page_retry`.
- A newly discovered/pending row may still use official API/fresh proof while the circuit is open; only repeated already-known page-recovery churn is removed.

### Bound terminal maintenance by row volume
- Production terminal maintenance now uses a row-limited implementation in addition to its existing 15-minute cadence.
- Weekly rediscovery reprobes are capped at **4 rows per maintenance tick**.
- Terminal quarantine timestamp repairs are capped at **64 rows per maintenance tick**.
- The strict reprobe eligibility rule is unchanged: terminal status, last attempt at least seven days old, and a later rediscovery (`last_seen_at > last_attempt_at`).
- Terminal rows remain excluded from ordinary claims while waiting.

### Make bounded pressure telemetry truthful
- Saturated bounded lanes are now labeled as lower bounds.
- A capped page-recovery measurement renders as `page recovery **>=64**`; actionable total is also marked `>=` when any contributing lane is saturated.
- Unsaturated values remain ordinary exact measured numbers.

## PR #240 validation completed so far
- Regression: open-circuit page deferral is bounded and leaves `pending` rescued work untouched.
- Regression: terminal reprobe and quarantine operations obey explicit row caps.
- Regression: production exact runtime performs page-backlog deferral before pressure and claim selection.
- Regression: production runtime schema uses the row-limited terminal-maintenance implementation.
- Regression: saturated pressure telemetry is marked as a lower bound and unsaturated telemetry remains exact.
- Canonical Python 3.11 suite on current PR head: **1,205 passed**, one upstream `discord.py` `audioop` deprecation warning.
- `pip check`: **PASS — no broken requirements**.
- `python -m compileall -q sniperplug tests *.py`: **PASS**.
- Import smoke: **PASS**.
- Python Check: **PASS**.
- Python tests: **PASS**.

## Cleanup / conflict gate still required before merge
- Re-check the final changed-file set after this task-record commit.
- Confirm the branch is not behind `main` and GitHub still reports mergeable.
- Inspect PR conversation, reviews, and inline review threads for actionable findings.
- Wait for canonical CI on the final task-record head; merge only when all final-head checks are green.
- Verify the real merge commit on `main` passes push-triggered Import smoke, Python Check, and Python tests.

## Remaining Definition of Done
1. Complete PR #240 final-head cleanup/review/conflict/CI gate and merge only from a green head.
2. Verify the real PR #240 merge commit on `main` passes all canonical push workflows.
3. Deploy merged `main` to the canonical SniperPlug Discloud app using the existing update workflow.
4. Confirm global Walmart catalog batches continue completing and producing safe fresh proof where available.
5. During an open page circuit, confirm the already-known page-recovery lane is deferred instead of repeatedly producing `page_circuit_open` claim/failure batches; fresh/API verification must continue.
6. Confirm bounded pressure telemetry reports `>=64` when the recovery lane is saturated and then falls or stays deferred during cooldown rather than being rewritten every 20 seconds.
7. Confirm weekly identity reprobes never exceed 4 per 15-minute maintenance tick and terminal quarantine repairs never exceed 64 per tick.
8. Confirm strong exact markdowns continue reaching fanout and are rejected only by legitimate configured threshold/category/DM/dedupe/safety rules.
9. Confirm marketplace cards continue preserving mandatory delivered-price shipping truth and same-selected-offer reference isolation.
10. Watch for another gateway-lag warning. If it repeats, use the event-loop watchdog phase/active-worker/request-state evidence to isolate the remaining blocker instead of guessing.
11. Keep SP-WM-MISSED-DEALS-236 active until these production checks pass.

## Blocker
**Final PR #240 merge/deploy and live production validation.** The code-side fixes and current regression suite are green, but this task is not being called 100% production-complete until the final merged build proves the remaining runtime gates under real Walmart traffic.

## Backlog
- Resume the SniperPlug-Site Whop app-specific content-reader task for Hidden Files experiences such as `Make Money Here`, `Content`, and `Better Content` only after this Walmart task closes.
- Investigate repeated Discord gateway `Can't keep up` / websocket-behind warnings after this active Walmart task closes unless new evidence directly ties them to this Walmart execution path.
- Continue verifying PR #223 delivery-health accounting after this task closes.
- PR #200 remains isolated and must not be mixed into this task.
