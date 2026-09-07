# Active Task

## Task / outcome
**SP-WM-DEAL-DELIVERY-STALL — restore Walmart deal delivery end-to-end.**

Target outcome: catalog discovery must continue feeding exact verification, strict selected-offer proof must recover when Walmart uses current seller/merchant aliases, page pacing must not churn known page-retry rows, verified deals must fan out before maintenance uses the shared Turso worker, and all proof/shipping/dedupe safety must remain intact.

## Scope
Active path only:

`catalog discovery → selected-offer normalization → fresh proof → exact-detail queue → claim → Walmart API/page recovery → exact result persistence → Walmart/external fanout → bounded maintenance`

Do not switch to unrelated watcher, gateway, setup, DM, or site work without an explicit FORCE SWITCH.

## Status
**Implementation complete on draft PR #256; local pre-merge validation is green for all Termux-executable tests. GitHub-hosted CI remains infrastructure-blocked before any workflow step starts. Production acceptance still required after merge.**

Branch: `fix/walmart-deal-delivery-stall-256`
PR: #256 `Restore Walmart deal verification throughput`

## Root cause / findings
- Sep 7 production catalog discovery was healthy: pages 7-8/40 returned 98 products, 98 unique candidates, and 98 usable item IDs.
- Safe fresh proof collapsed to effectively 0 for those 98, strongly tracking missing selected seller/marketplace identity in current Walmart payload shapes.
- Exact drain batches claimed 24 rows at a time and verified 0; 47 of 48 recent claims became `page_pacing_deferred`.
- #255's one-per-60-second HTML safety guard correctly prevented request bursts, but the queue still leased many rows already known to need page recovery while the single HTML slot was unavailable, creating pointless lease/outcome writes.
- Turso's single process-isolated worker was also being held by maintenance and queue writes for 8-10 seconds.
- Production catalog bulk enqueue still called the older blind exact-queue prune even though a newer bounded cleaner existed elsewhere. Cleanup ownership was duplicated.
- Exact verification also performed cleanup before returning to the caller, and fanout schema initialization contained a retention DELETE before delivery work. Housekeeping could therefore sit directly in front of a ready alert.

## Changes in PR #256
- Expanded the existing authoritative selected-offer normalizer in `walmart_marketplace_comp.py` to recognize explicit current Walmart seller/merchant aliases, including `merchantInfo`, `merchantName`, merchant IDs, scalar seller/merchant values, marketplace aliases, explicit Walmart-first-party flags, and the repo's existing known Walmart seller IDs.
- Identity is read only from the selected offer first and exact item root second. Sibling offers are never scanned for seller identity.
- Existing `SourceCandidate` Walmart payable-price normalization remains authoritative, so third-party shipping still fails closed and delivered-price math is unchanged.
- Known `page_retry` rows are not leased while the single HTML probe slot is merely paced. Fresh/pending work still receives normal official API/exact-search verification.
- Removed the legacy blind prune call from production catalog bulk enqueue.
- Removed exact-queue retention cleanup from the verification runtime so durable verified results return immediately for fanout.
- Reduced exact-queue bounded cleanup from 500 IDs to 100 IDs per write.
- Production runner now performs maintenance only after Walmart fanout and external verified-event fanout, and skips maintenance while catalog/reconciliation fanout ownership is active.
- Removed retention DELETE behavior from fanout schema initialization and added bounded post-fanout event/decision cleanup.
- Updated stale architecture/static tests to validate the new three-way page-lane policy and new cleanup ownership instead of old source syntax.

## Safety / conflict cleanup
- Walmart HTML challenge circuit remains enabled and authoritative.
- HTML remains one paced probe; first challenge-class response still opens the circuit immediately.
- No public threshold, DM rule, dedupe rule, selected-offer/variant proof, reference-price proof, catalog depth, or price-history rule was weakened.
- Marketplace shipping remains fail-closed when shipping is unknown.
- Sibling offers cannot donate selected seller identity.
- Duplicate seller-identity shim created during implementation was removed; alias handling now lives in the existing selected-offer normalizer.
- Production cleanup ownership is centralized after fanout instead of being duplicated across catalog enqueue, exact verification, and schema initialization.

## Validation / results
### Termux Ubuntu aarch64, Python 3.12.14
Validated exact PR code locally after fixing the one genuine stale test discovered by the first run.

- Targeted affected regression set: **32 passed**, 1 unrelated `audioop` deprecation warning.
- `python -m pip check`: **passed** (`No broken requirements found`).
- `python -m compileall -q sniperplug scripts tests`: **passed**, exit 0.
- `python scripts/smoke_imports.py`: **passed**, 37 modules / 22 symbols, exit 0.
- Full Termux-compatible suite: **1,272 passed**, 1 unrelated `audioop` deprecation warning, exit 0.
- Six tests in `tests/test_libsql_process_connection.py` cannot execute on this phone because `requirements.txt` intentionally excludes native `libsql==0.1.11` on `aarch64`. Their failures were all the same pre-test worker startup import error: `ModuleNotFoundError: No module named 'libsql'`.
- The first real local full run exposed one #256-related stale static assertion in `tests/test_walmart_live_gate_240.py`; it was corrected on branch commit `9fe904721c62dffa74acf1495f1e73baf5d7cb67`, after which the affected set and all Termux-executable tests passed.

### GitHub Actions blocker
For the current #256 heads, GitHub Actions creates the jobs but assigns no runner: `runner_id=0`, empty runner name, `steps=[]`. The workflows fail before checkout or any Python command executes. This is CI infrastructure/account runner assignment failure evidence, not a test failure. Do not label those runs as code-red.

## Remaining Definition of Done
1. Obtain one x86_64/hosted run of the six native `libsql` process tests (normally supplied by GitHub Actions) and the normal full workflows once GitHub assigns a runner.
2. Final PR diff/review-thread inspection and update PR validation text to the exact final head.
3. Mark #256 ready and merge only when pre-merge evidence is sufficient and the head has not moved unexpectedly.
4. After merge/deploy, live-prove:
   - safe fresh proof is nonzero again when explicit current seller/merchant identity exists;
   - exact batches verify real rows instead of repeated 0/24 page-pacing churn;
   - known page-retry rows are not repeatedly leased while the probe slot is occupied;
   - Walmart/external fanout produces processed events/posts/DMs for eligible verified deals;
   - HTML remains paced with no multi-request challenge burst;
   - maintenance no longer blocks the hot delivery path for 8-10 seconds.
5. Only after live acceptance call Walmart deal delivery restored and close this active task.

## Blockers / risks
- GitHub-hosted Actions currently never receives a runner, so the native x86_64 libsql tests and standard hosted workflows cannot execute there yet.
- Product-page HTML parser itself was not changed without raw live page evidence; this task fixes identity normalization from official payloads, page-retry scheduling, and delivery-path backpressure without blind parser edits.
- Live Walmart payload/traffic acceptance is still required after merge.

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
Keep #256 as the only active implementation task. Recheck the final head/workflows and obtain the remaining native x86_64 validation when a runner becomes available; then merge and perform production acceptance.
