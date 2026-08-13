# Active Task

## Status
**Active — SP-WM-MISSED-DEALS-236. PRs #236–#240 are merged and deployed. PR #240 passed multiple live gates but exposed one final open-circuit claim-selection defect. PR #241 implements the focused fix and its canonical branch validation is green. The Single Active Task Lock remains engaged until the merged build passes the live Walmart gate.**

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
- Global catalog discovery continued advancing normally with repeated 59–100 item batches.
- Strict `safe fresh proof` remained active, including high-yield batches such as 80, 82, 75, 72, and 67.
- Fresh proof continued to promote in the exact worker (`2/2`, `3/3`, `1/1`, etc.) with zero network fetch when promotion succeeded.
- Official API verification continued while the page circuit was open.
- Bounded pressure telemetry correctly rendered saturated recovery pressure as `>=64`.
- Weekly terminal identity reprobes were capped at 4 per maintenance tick in the supplied live window.
- Terminal quarantine writes stayed below the 64-row cap.

### Remaining live failure isolated
After two new page challenges reopened the 15-minute circuit, production repeatedly showed `page recovery >=64`, followed by another `claimed 2/3` with `page_circuit_open=2/3` every ~20 seconds.

PR #240 deferred only a bounded slice of 64 due `page_retry` rows before claim selection. The production claim helper independently allowed a hardcoded two-row no-fresh page-recovery lane. With more than 64 recovery rows due, the deferral moved the first slice forward and the claim query simply reached the next rows. HTML suppression then correctly returned `page_circuit_open`, persistence placed the rows back into `page_retry`, and the same churn repeated.

## Real production execution path inspected
`global_auto_scan_runner.py` imports `process_actionable_walmart_exact_queue_batch` from `walmart_exact_queue_bulk_runtime.py`.

The bulk runtime performs terminal maintenance → bounded prune/recheck repair → page-circuit inspection → bounded page deferral → bounded pressure → `claim_due_rows_batched` → fresh proof / official detail / exact-search / page fallback → strict exact classification → bulk persistence → offer memory → fanout.

The defect was specifically between bounded deferral and claim selection; no seller/offer/price/fanout proof gate needed to change.

## PR #241 implementation
- `claim_due_rows_batched` now accepts `page_recovery_claim_cap`, defaulting to the existing closed-circuit value of 2.
- The production bulk runtime passes a cap of **0** while `page_circuit_remaining > 0`.
- A backlog larger than the 64-row hygiene sweep can therefore no longer leak stale `page_retry` claims into every worker cycle.
- Fresh/pending/retry work remains claimable, so current catalog snapshots and official detail/exact-search recovery continue during page cooldown.
- The bounded 64-row deferral remains in place as database/scheduling hygiene; correctness no longer depends on rewriting the entire recovery backlog.
- Closed-circuit behavior is unchanged: ordinary page recovery remains capped at 2 per exact batch.

## Regression coverage
- Reproduces the live condition with **70 due `page_retry` rows**.
- Defers only 64, proving 6 recovery rows remain due.
- With open-circuit claim cap 0, asserts none of those remaining page rows are leased while normal pending/API work is still claimed.
- Confirms the default closed-circuit claim path still leases exactly the bounded two page-recovery rows.
- Confirms the production bulk runtime wires cap 0 only while the circuit has remaining cooldown.

## Validation completed on PR #241 implementation head
- Python 3.11 full suite: **1,208 passed**, one upstream `discord.py` `audioop` deprecation warning.
- `pip check`: **PASS — no broken requirements**.
- `python -m compileall -q sniperplug tests *.py`: **PASS**.
- Import smoke: **PASS**.
- Python Check: **PASS**.
- Python tests: **PASS**.

## Cleanup / conflict inspection
- Source diff is limited to the canonical bulk exact runtime and its shared bounded claim selector; one focused regression file was added plus this active-task record.
- `walmart_exact_queue_drain.py` patch contains only the new configurable page-recovery cap and parameter wiring; no unrelated source deletion occurred.
- `walmart_exact_queue_bulk_runtime.py` patch contains only the open-circuit cap wiring/import/doc update; no proof/classification/persistence logic changed.
- Branch is **0 commits behind `main`**.
- PR conversation contains only Qodo's inactive-subscription notice; there are no submitted reviews or inline review threads/findings.

## Remaining Definition of Done
1. Wait for canonical CI on this final task-record head and merge PR #241 only if all checks remain green and GitHub reports mergeable.
2. Verify the real PR #241 merge commit on `main` passes push-triggered Import smoke, Python Check, and Python tests.
3. Deploy merged `main` to the canonical SniperPlug Discloud app using the existing update workflow.
4. Open/observe a real Walmart page challenge circuit.
5. Confirm a large existing `page_retry` backlog no longer generates repeated old `page_circuit_open=2/3` batches every 20 seconds. An isolated `page_circuit_open` may still occur once for newly pending work after its official API attempt; that is expected and must not become recovery-lane churn.
6. Confirm fresh proof and official API verification continue during cooldown.
7. Confirm `page recovery >=64` may remain as deferred backlog telemetry but does not translate into repeated stale page claims.
8. Confirm weekly reprobes remain <=4 and quarantine maintenance <=64 per tick.
9. Confirm exact markdowns still reach fanout and are rejected only by legitimate configured/safety gates.
10. Keep SP-WM-MISSED-DEALS-236 active until this live gate passes.

## Blocker
**Final PR #241 head/merge CI plus live production validation.** The code-side root cause is implemented and the implementation-head suite is green; the task is not called production-complete until the merged deployment proves the no-churn behavior under a real open circuit.

## Backlog
- Resume the SniperPlug-Site Whop app-specific content-reader task only after this Walmart task closes.
- Investigate repeated Discord gateway `Can't keep up` warnings after this active Walmart task closes unless new evidence directly ties them to this Walmart execution path.
- Continue verifying PR #223 delivery-health accounting after this task closes.
- PR #200 remains isolated and must not be mixed into this task.
