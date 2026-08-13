# Active Task

## Status
**Active — SP-WM-MISSED-DEALS-236. Milestones 1–3 are merged. Milestone 3 fixed interrupted catalog-claim recovery and passed its live producer gate: production restarted with `catalog_claim_lease_s=180` and resumed `Global Walmart catalog batch completed` events. The same live run exposed the remaining throughput failure: exact verification repeatedly produced `claimed 8 • verified 0 • fresh catalog proof 0 • page_challenge=8` while new catalog batches continued discovering roughly 97–100 items. Milestone 4 is implemented on PR #239 and canonical branch CI is green. The task remains intentionally OPEN until the merged build is deployed and live production proves fresh-proof activation, bounded page challenges, throughput recovery, and exact-card safety. The Single Active Task Lock remains engaged.**

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

## Production evidence / root causes
### Original missed-deal failure
- Global Walmart catalog discovery remained healthy while exact verification repeatedly lost whole batches to API/page transients.
- Seller/offer identity omissions and challenged product pages caused potentially valid catalog leads to stop before public/DM fanout.
- Exact fanout also exposed stale `watchlist` presentation text rejecting otherwise strict exact cards.

### Milestone 2 live failure
- Fresh-proof support existed but production still reported `fresh catalog proof 0` while page/API retries dominated.
- A restart interrupted the only durable catalog producer, leaving the exact worker draining old pre-snapshot rows.

### Milestone 3 live result after PR #238
- **Passed producer recovery:** startup reported `catalog_claim_lease_s=180`, and global catalog batches resumed normally after deployment.
- **Failed overall throughput:** the exact worker then repeatedly returned `claimed 8 • verified 0 • page_challenge=8` with `fresh catalog proof 0` while catalog discovery continued writing new item batches.
- Inspection of the real production path found four remaining causes:
  1. The bounded claim query ranked work by status/priority but did not put current `discovery_snapshot_at` proof ahead of old high-priority page-recovery rows.
  2. Catalog rediscovery with a new safe snapshot moved a `page_retry` row's due time forward but did **not** change its status out of the page-recovery lane.
  3. The one-hour retry delay applied per item. With many distinct challenged rows, the worker could still send eight different Walmart page requests every cycle.
  4. `fresh catalog proof 0` did not distinguish "no snapshot was claimed" from a specific strict snapshot rejection.

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

## Milestone 4 — PR #239 implementation
### Fresh proof is now first-class claim work
- The exact claim query ranks current bounded discovery snapshots ahead of network recovery even when an old page-retry row has a higher historical priority score.
- No-fresh-snapshot `page_retry` work is capped at **2 rows per exact batch**, matching the existing product-page concurrency boundary instead of allowing an 8/8 or 24/24 page storm.
- The capped page lane remains ahead of scheduled verified-item rechecks so recovery still makes bounded forward progress without starving known-good rechecks.
- Added/updated claim indexes for the fresh-proof lane and new bounded status order.

### Safe rediscovery actually rescues page retries
- When a catalog pass builds a complete strict selected-offer snapshot for an existing `page_retry` row, the queue now changes that row back to `pending` and makes it immediately due.
- Catalog telemetry now reports `safe fresh proof **N**` and `page retries rescued **N**` so production can prove whether discovery is generating reusable proof.

### Page challenge circuit breaker
- Two `page_challenge`, page HTTP 403, or page HTTP 429 responses within two minutes open a provider-scoped **15-minute product-page cooldown**.
- While open, the exact worker continues official Walmart item-detail and strict exact-item Affiliate-search recovery; only product-page HTML is suppressed.
- A complete official API seller/offer response can still verify while the circuit is open.
- If official proof remains incomplete, the row stays retryable/page-recovery work; no seller or offer is guessed.
- The circuit is process-local and resets on process restart. It creates no proof and changes no alert eligibility rule.

### Live diagnostics are no longer ambiguous
- Exact-worker telemetry now reports fresh proof as `promoted/input` rather than only a promoted count.
- Strict snapshot rejection reasons include stale timestamp, invalid JSON/version, item mismatch, price mismatch, missing offer, missing seller, incomplete marketplace shipping, delivered-price mismatch, and residual strict-guard rejection.
- Worker telemetry reports page-circuit open/closed state, suppressed page calls, and remaining cooldown.

## Validation completed on PR #239
- Real execution path inspected end to end: Affiliate catalog selected offer → bulk queue enqueue/snapshot → bounded claim → fresh promotion or official detail/exact-search/page fallback → exact identity → bulk persistence → global offer memory → fanout.
- Provider wiring inspected: production registers `WalmartPageRecoveryProvider` inside `CachedWalmartProvider`; the exact worker can therefore suppress only page HTML while retaining the official API paths.
- Regression added: a low-priority fresh-snapshot row must beat higher-priority stale page-recovery rows.
- Regression added: no-fresh page-recovery work is bounded to two claims per batch and cannot consume the whole batch.
- Regression added: a safe catalog snapshot re-arms an existing `page_retry` row to `pending`, makes it due, and reports the rescue.
- Regression added: two challenge-class failures open the page circuit; the next incomplete item does not make another page call; official detail/exact-search still run; a complete official API response still verifies while the circuit is open.
- Runtime-schema regression updated to require the fresh-proof index and bounded recovery-before-recheck order.
- First PR #239 full run exposed one stale static assertion for the historical status order; implementation was not reverted. The claim index/static contract was updated to the intentional bounded ordering and rerun.
- Final PR #239 Python 3.11 canonical suite: **1,199 passed**, one upstream `discord.py` `audioop` deprecation warning.
- `pip check`: **PASS — no broken requirements**.
- `python -m compileall -q sniperplug tests *.py`: **PASS**.
- Import smoke: **PASS**.
- Python Check: **PASS**.
- Python tests: **PASS**.

## Cleanup / conflict inspection
- PR #239 changes only the canonical exact worker, bounded claim/snapshot persistence/schema support, and Walmart regressions; no parallel exact worker or alternate proof pipeline was added.
- Branch is currently **0 commits behind `main`** and GitHub reports the PR mergeable.
- Changed-file set is limited to:
  - `sniperplug/services/walmart_exact_queue_bulk_runtime.py`
  - `sniperplug/services/walmart_exact_queue_drain.py`
  - `sniperplug/services/walmart_exact_queue_runtime.py`
  - `sniperplug/services/walmart_exact_runtime_schema.py`
  - `sniperplug/services/walmart_exact_verification_queue_bulk.py`
  - `tests/test_walmart_exact_runtime_schema.py`
  - `tests/test_walmart_throughput_final.py`
- The only PR conversation comment observed is Qodo's inactive-subscription notice; it contains no code finding.

## Remaining Definition of Done
1. Finish final PR #239 diff/review/conflict cleanup and merge only from a green canonical head.
2. Verify the real merge commit on `main` passes Import smoke, Python Check, and Python tests.
3. Deploy current merged `main` to the canonical SniperPlug Discloud app using the existing update workflow (do not substitute a new-app upload command).
4. Confirm global catalog batches continue completing after deployment.
5. Confirm catalog summaries expose nonzero `safe fresh proof` whenever strict selected-offer proof is actually present, and `page retries rescued` when a challenged row is safely rediscovered.
6. Confirm exact-worker summaries show fresh proof inputs/promotions and no longer lose whole batches to `page_challenge=8`; during a challenge wave the page circuit must open after at most two challenge-class requests and subsequent page calls must be suppressed during cooldown.
7. Confirm actionable due trends down/stays bounded instead of snowballing.
8. Confirm live marketplace cards preserve mandatory delivered-price shipping truth and same-selected-offer reference isolation.
9. Confirm strong exact deals are not rejected solely by stale watchlist presentation text.
10. Keep SP-WM-MISSED-DEALS-236 active until those production checks pass. If any live gate fails, continue under this same task rather than switching topics.

## Blocker
**Live production validation of the merged Milestone 4 build.** Code implementation and canonical branch CI are green. The overall task is not being called 100% production-complete until the merged build is deployed and the live telemetry above proves the fix under real Walmart traffic.

## Backlog
- Resume the SniperPlug-Site Whop app-specific content-reader task for Hidden Files experiences such as `Make Money Here`, `Content`, and `Better Content` only after this Walmart task closes.
- Investigate repeated Discord gateway `Can't keep up` / websocket-behind warnings after this active Walmart task closes unless new evidence directly ties them to this Walmart execution path.
- Continue verifying PR #223 delivery-health accounting after this task closes.
- PR #200 remains isolated and must not be mixed into this task.
