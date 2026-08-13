# Active Task

## Status
**Active — SP-WM-MISSED-DEALS-236. PRs #236–#240 are merged. PR #240 passed merge CI and was deployed, but the live production gate exposed one remaining open-circuit scheduling defect. The Single Active Task Lock remains engaged.**

## Task
**SP-WM-MISSED-DEALS-236 — Restore high-throughput Walmart exact verification so catalog-discovered deals are not lost to flaky detail/page recovery while all exact item/offer/seller/price/shipping/reference/threshold/category/DM/dedupe gates remain fail-closed.**

## Scope / safety invariants
- Requested Walmart item ID must exactly match the official item being verified.
- Current payable price must come from current bounded official proof.
- Seller, selected offer, variant, condition, fulfillment, mandatory marketplace shipping, delivered price, and reference price cannot be mixed across offers.
- Third-party marketplace offers remain blocked when shipping/delivered-price truth is incomplete.
- Reference-price, availability, threshold, category, DM preference, and duplicate gates remain fail-closed.
- Fresh catalog proof is reusable only while bounded-fresh, exact-item matched, cent-for-cent matched to the queued payable price, seller/offer complete, variant-safe, and shipping-complete when required.
- Product-page structured data remains a later identity/shipping fallback only; it cannot replace official item/current-price proof or choose a cheaper seller.
- No CAPTCHA/challenge bypass, proxy rotation, browser fingerprint spoofing, seller guessing, relevance-neighbor borrowing, sibling/cross-offer reference borrowing, or alternate-retailer proof substitution.

## Production findings after PR #240 deployment
### Gates that passed
- Global catalog discovery continues advancing normally with repeated 59–100 item batches.
- Strict `safe fresh proof` remains active, including high-yield batches such as 80, 82, 75, 72, and 67.
- Fresh proof continues to promote in the exact worker (`2/2`, `3/3`, `1/1`, etc.) with zero network fetch when promotion succeeds.
- Official API verification continues while the page circuit is open.
- Bounded pressure telemetry now correctly renders saturated recovery pressure as `>=64`.
- Weekly terminal identity reprobes are capped at exactly 4 per maintenance tick in the supplied live window.
- Terminal quarantine writes stayed below the 64-row cap.

### Remaining live failure
PR #240 deferred at most 64 due `page_retry` rows before each claim cycle. Production then showed a recovery backlog larger than that bounded sweep. After a new two-page challenge opened the 15-minute circuit, telemetry repeatedly showed:
- `page recovery **>=64**`
- `claimed **2**` / `claimed **3**`
- `retry kinds **page_circuit_open=2/3**`
- the same pattern again every ~20 seconds while the circuit remained open.

This proves the bounded deferral itself is working but is not a complete claim guard: once the first 64 rows are moved forward, the normal claim query simply reaches the next due `page_retry` rows and leases up to its hardcoded page-recovery cap of 2.

## Root cause / real execution path
Production imports `process_actionable_walmart_exact_queue_batch` from `sniperplug.services.walmart_exact_queue_bulk_runtime` in `global_auto_scan_runner.py`.

The bulk runtime performs this sequence:
1. compute page-circuit remaining time;
2. defer up to 64 due `page_retry` rows;
3. load pressure;
4. call `claim_due_rows_batched`.

`claim_due_rows_batched` always allows up to `PAGE_RECOVERY_CLAIM_CAP = 2` no-fresh `page_retry` rows. It has no circuit-state input. Therefore a backlog larger than the bounded deferral slice remains claimable during the open circuit.

The fetch path then correctly suppresses HTML, keeps official detail/exact-search recovery, and when official seller/offer proof is still incomplete returns `PAGE_RETRY_STATUS` with `retry_kind=page_circuit_open`. Persistence places those rows back into the recovery lane, creating repeated lease/failure churn.

## Current implementation plan — PR #241
- Add an explicit page-recovery claim-cap argument to `claim_due_rows_batched`, defaulting to the existing cap of 2 so all existing callers/tests retain behavior.
- When the production bulk runtime sees an open page circuit, pass a page-recovery claim cap of **0**.
- Keep fresh/pending/retry work claimable so fresh catalog proof and official API/exact-search verification continue during the cooldown.
- Keep the existing bounded 64-row deferral as database hygiene so recovery timestamps move toward the circuit close time without requiring an unbounded rewrite.
- Add a regression reproducing the live condition: more than 64 due `page_retry` rows, one bounded deferral sweep, open-circuit claim cap 0, and no stale page-recovery rows leased afterward.
- Add a production-path regression proving the bulk runtime passes the zero cap only while the circuit is open.

## Validation required
- Targeted Walmart live-gate tests.
- Full Python test suite.
- `pip check`.
- `python -m compileall -q sniperplug tests *.py`.
- Import smoke, Python Check, and Python tests workflows on the final PR head.
- Changed-file/conflict/review-thread cleanup before merge.
- Real merge-commit workflows on `main` after merge.

## Remaining live Definition of Done
1. Deploy merged PR #241.
2. Open a real Walmart page challenge circuit.
3. Confirm a large existing `page_retry` backlog is not leased while the circuit is open; isolated `page_circuit_open` may still occur once for newly pending work that successfully attempted official recovery first, but the old recovery backlog must not churn every 20 seconds.
4. Confirm fresh proof and official API verification continue during the cooldown.
5. Confirm `page recovery >=64` may remain as deferred backlog telemetry but does not translate into repeated old-page claim/failure batches.
6. Confirm weekly reprobes remain <=4 and quarantine maintenance <=64 per tick.
7. Confirm exact markdowns still reach fanout and are rejected only by legitimate configured/safety gates.
8. Keep SP-WM-MISSED-DEALS-236 active until this live gate passes.

## Blocker
**Open-circuit page-recovery claim selection still ignores circuit state once the bounded 64-row deferral slice is exhausted.**

## Backlog
- Resume the SniperPlug-Site Whop app-specific content-reader task only after this Walmart task closes.
- Investigate repeated Discord gateway `Can't keep up` warnings after this active Walmart task closes unless new evidence directly ties them to this Walmart execution path.
- Continue verifying PR #223 delivery-health accounting after this task closes.
- PR #200 remains isolated and must not be mixed into this task.
