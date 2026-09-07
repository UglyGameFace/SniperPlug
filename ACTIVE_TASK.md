# Active Task

## Task / outcome
**SP-WM-DEAL-DELIVERY-STALL — restore Walmart deal delivery end-to-end.**

Target outcome: catalog discovery feeds exact verification, current Walmart selected-offer identity normalizes correctly, paced page recovery does not churn known retries, verified deals fan out before maintenance uses the shared Turso worker, and proof/shipping/dedupe safety remains intact.

## Scope
`catalog discovery → selected-offer normalization → fresh proof → exact-detail queue → claim → Walmart API/page recovery → exact result persistence → Walmart/external fanout → bounded maintenance`

Do not switch to unrelated watcher, gateway, setup, DM, or site work without an explicit FORCE SWITCH.

## Status
**PR #256 merged to `main` as `2b057b6b02b96c44699be69baa915fe9e0d07a97`. Implementation and pre-merge validation are complete. Active work is now production acceptance only.**

PR: #256 `Restore Walmart deal verification throughput`
Validated PR head: `edd9e6f31535db64f00b26b1a152fae50a042266`

## Root cause / findings
- Sep 7 production catalog discovery was healthy: 98 returned / 98 unique / 98 usable item IDs.
- Safe fresh proof collapsed to effectively 0 and exact drain batches verified 0/24 while 47/48 recent claims became `page_pacing_deferred`.
- Current Walmart payloads use seller/merchant aliases the older selected-offer normalizer did not recognize.
- Known `page_retry` rows were repeatedly leased while the single HTML probe slot was unavailable, creating useless lease/outcome writes.
- Production queue cleanup ownership was duplicated; the old blind prune still ran from catalog enqueue while newer bounded cleanup existed elsewhere.
- Exact verification and fanout schema paths could run maintenance before ready alerts reached delivery.
- Turso's single process-isolated worker showed 8-10 second maintenance/queue stalls on the hot path.

## Changes merged in #256
- Extended the existing authoritative selected-offer normalizer for current seller/merchant aliases, marketplace aliases, explicit first-party flags, scalar seller/merchant values, and existing Walmart seller IDs.
- Seller identity is read only from the selected offer first and exact item root second; sibling offers cannot donate identity.
- Existing `SourceCandidate` payable-price normalization stays authoritative; marketplace shipping remains fail-closed.
- Known `page_retry` rows are not leased while the HTML probe is merely paced; fresh/pending official API work remains claimable.
- Removed legacy blind prune ownership from production catalog enqueue.
- Removed retention cleanup from exact verification so durable results return immediately for fanout.
- Reduced exact-queue cleanup from 500 IDs to 100 IDs per bounded write.
- Moved Walmart event/decision and exact-queue maintenance after Walmart and external fanout; skip maintenance while another fanout owner is active.
- Removed retention DELETE behavior from fanout schema initialization.
- Updated stale architecture/static tests for the three-way page-lane policy and cleanup ownership.

## Validation / results
Termux Ubuntu `aarch64`, Python 3.12.14, exact validated PR head:
- affected regression set: **32 passed**
- native `libsql==0.1.11` built successfully from source on ARM
- `tests/test_libsql_process_connection.py`: **10 passed**
- `python -m pip check`: **passed**
- `python -m compileall -q sniperplug scripts tests`: **passed**, exit 0
- `python scripts/smoke_imports.py`: **passed**, 37 modules / 22 symbols, exit 0
- full suite with native libsql installed: **1,282 passed**, exit 0
- one unrelated Python `audioop` deprecation warning
- final PR diff inspected; review threads: none; submitted reviews: none

GitHub-hosted Actions remains infrastructure-blocked before execution (`runner_id=0`, empty runner name, `steps=[]`). Those workflow conclusions are not code-test failures.

## Remaining Definition of Done — production acceptance
After the merged code is deployed/restarted, live-prove all of the following:
1. `safe fresh proof` becomes nonzero when current explicit seller/merchant identity exists.
2. Exact batches verify real rows instead of repeated 0/24 page-pacing churn.
3. Known `page_retry` rows are not repeatedly leased while the HTML probe slot is occupied.
4. Walmart/external fanout produces processed events/posts/DMs for eligible verified deals.
5. HTML remains one paced probe with no multi-request challenge burst.
6. Maintenance no longer blocks the hot delivery path for 8-10 seconds.

Only after those live checks pass call Walmart deal delivery restored and close this task.

## Risks / boundaries
- Product-page HTML parsing was not blindly changed without raw live page evidence.
- Live Walmart payload/traffic acceptance is still required.
- No public threshold, DM rule, dedupe rule, reference-price rule, catalog depth, price history, challenge circuit, or shipping guard was weakened.

## Backlog — not active
- SP-GATEWAY-EVENT-LOOP-LAG
- SP-SETUP-DELIVERY-UX
- SP-DM-DELIVERY-STARVATION
- SP-DM-NEW-LOW-ALERT
- SP-WM-REFERENCE-PRICE-RECOVERY
- SP-WM-CASH-PDP-AMOUNT
- HP/Target standalone watcher deployment/acceptance
- SP-TURSO-READ-AMPLIFICATION acceptance
- SP-TURSO-WRITE-AMPLIFICATION acceptance
- SP-DEALS-DISCOVER-HARDENING-244 live acceptance
- SniperPlug-Site work

## Next step
Deploy/restart the merged `main` build and inspect fresh production Walmart autoscan/exact/fanout logs against the six acceptance checks above.
