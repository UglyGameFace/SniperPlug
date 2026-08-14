# Active Task

## Status
**Active — SP-TURSO-WRITE-AMPLIFICATION. PR #248 is implemented and code-validation green on `fix/turso-write-amplification`; production completion is not claimed. The remaining Definition of Done is merge/deploy plus live startup and Turso write-rate validation.**

## Scope
Reduce SniperPlug's real Turso rows-written rate without weakening exact Walmart proof, delivery dedupe, durable leases, crash recovery, or price history. Fix the deterministic storage-maintenance startup crash as part of the same storage path. Inspect existing implementations before changing them; do not stack a new persistence layer over the established queue/fanout runtimes.

## Root cause / findings
- The connected tooling does not expose the user's Turso organization usage/top-query dashboard, so no top-query metric is being invented. The supplied production logs and real callers are the current source of truth; new write telemetry is being added for post-deploy validation.
- Prior Turso work is present and must not be duplicated: PR #222 already suppresses recent identical catalog row UPSERTs, PR #226 already bounds startup cleanup, PR #204 already bulk-persists exact outcomes, PR #207 already bulk-fanouts events, and the exact worker already uses bounded leases/pressure reads.
- The startup crash is deterministic in `storage_maintenance._bounded_rowids()`: it selected bare `rowid` and then assumed either `row["rowid"]` or `row[0]`. `guild_auto_scan_report_history` has `id INTEGER PRIMARY KEY`, so libSQL can expose the selected rowid under primary-key metadata; the process-isolated worker normalizes it to a mapping and both old lookups fail with the supplied `KeyError: 'rowid'` / `KeyError: 0`.
- The dominant current catalog-write pattern is different from the old #222 bug. Production examples on pass 91 show `safe fresh proof 81` with `persisted/unchanged 87/7`, `safe fresh proof 76` with `83/17`, and `safe fresh proof 62` with `81/19`. The queue refreshed an otherwise identical safe discovery snapshot after ten minutes even when a verified row's next exact recheck was four, twelve, or twenty-four hours away. That refreshed proof expires after fifteen minutes, so the timestamp-only write often cannot be consumed before it is stale again.
- `build_fresh_discovery_offer_snapshot()` was reinspected after implementation: the serialized proof payload contains normalized offer/product fields but no capture timestamp. Snapshot freshness is carried separately by `discovery_snapshot_at`, so identical-offer JSON equality is stable and the new timestamp-refresh suppression can actually fire.
- The global catalog rotates 265 routes two at a time. Mature products are therefore rediscovered repeatedly; a six-hour `last_seen_at` heartbeat also rewrote unchanged rows several times per day despite the queue's fourteen-day retention boundary.
- During an open Walmart HTML challenge circuit, the bulk runtime both set `page_recovery_claim_cap=0` **and** rewrote up to 64 due `page_retry` rows per worker cycle to move their `next_attempt_at`. The claim cap already prevents those rows from being leased. Recovery pressure is bounded for scheduling diagnostics, so the reschedule writer was redundant write amplification rather than a correctness requirement.

## Changes implemented in PR #248
- Storage maintenance now selects `rowid AS __sniperplug_rowid` and uses a mapping/positional-safe extractor. This directly covers the process-isolated Turso row shape while preserving local aiosqlite behavior.
- Storage maintenance now skips its final operational commit when every bounded cleanup selector found zero rows; the existing per-subsystem prune functions retain their own ownership.
- Added a regression that emulates process-isolated mapping rows against a table with `id INTEGER PRIMARY KEY AUTOINCREMENT` and proves bounded deletion works through the explicit rowid alias.
- Walmart catalog queue discovery heartbeat is coalesced from six hours to twenty-four hours, still far inside fourteen-day retention.
- Identical stale safe proof snapshots are refreshed only when the row can consume them within the existing fifteen-minute proof-validity horizon. New/changed proof still persists immediately. `pending`/`retry`/`failed` rearming remains immediate, and any safe snapshot still rescues `page_retry` to `pending` immediately.
- Catalog queue summaries now report `stale proof refresh skipped` alongside persisted/unchanged rows and write statements so the live reduction is measurable.
- Removed the open-circuit page-retry reschedule call. The existing claim cap remains the authoritative gate and leaves blocked page rows untouched until the circuit closes or a safe catalog snapshot rescues one.
- Removed the now-unreferenced `walmart_page_circuit_queue.py` writer instead of leaving a dead compatibility shim.
- Added exact-worker write telemetry for requested lease rows, outcome rows, offer-memory rows, persistence statements, and explicit zero page-circuit reschedule rows.
- Updated live-gate regressions to prove a page backlog larger than the old 64-row sweep stays unclaimed **without changing its `next_attempt_at` values** while fresh pending/API work remains claimable.
- Added catalog regressions proving far-future verified rows skip timestamp-only proof refreshes, due-soon verified rows still refresh, page retries are still rescued, changed data is never suppressed, and the daily retention heartbeat still persists.

## Validation completed
- Root execution path, callers, existing write-suppression implementation, exact lease path, pressure policy, page-circuit behavior, storage-maintenance tests, fresh-proof serialization, and relevant historical Turso PRs were inspected before/during implementation.
- First Python 3.12 PR run compiled and passed import/dependency checks; its full suite reached **1,237 passed / 1 failed**. The sole failure was an old static assertion requiring the crash-prone literal `SELECT rowid FROM {table}`. The guard was updated to require the explicit aliased rowid path rather than restoring unsafe behavior.
- Final PR-head Python 3.11 workflow: **1,238 passed, 1 warning**; `pip check` passed; `python -m compileall -q sniperplug tests *.py` passed.
- Final PR-head Python 3.12 `Python Check`: dependency install/check, compilation, import smoke, and full test suite all passed.
- Final separate import-smoke workflow passed.
- PR #248 is mergeable against current `main`; no review threads or submitted review blockers exist.
- Final changed-file inspection contains only the active-task record, the three intended storage/Walmart runtime files, deletion of the obsolete page-circuit writer, and the five directly related regression files. No alternate queue/runtime/database implementation or temporary applicator was added.
- Deleted `walmart_page_circuit_queue.py` has no remaining PR-head runtime import; both Python versions compile and import successfully with it removed.

## Validation pending / Definition of Done
- Final documentation-only PR-head CI after this active-task record refresh.
- Merge PR #248 and validate post-merge `main` workflows.
- Deploy merged `main` to Discloud.
- Live startup proof that storage maintenance completes and the old `KeyError: 'rowid'` / `KeyError: 0` crash is gone.
- Live catalog logs showing meaningful `stale proof refresh skipped`, much higher unchanged counts on mature rediscovery passes, and materially fewer persisted rows/write statements than the supplied pre-change pass-91 logs.
- Live exact telemetry confirming `page_circuit_reschedule_rows=0` and measuring lease/outcome/offer-memory write demand.
- Turso dashboard rows-written slope after deployment. The task remains active until the rate is validated rather than inferred from code alone; if exact offer-memory/lease writes remain material, continue optimizing them under this same active task.

## Cleanup status
- No startup try/except, monkey patch, alternate database, duplicate queue implementation, or relaxed proof gate was added.
- The obsolete page-circuit queue rescheduler was deleted after caller/reference inspection.
- Price observations, exact offer-price memory, durable fanout/dedupe state, and exact queue history are not deleted to save quota.

## Blockers
- Direct Turso organization/top-query metrics are not available through the connected tools in this session. Production write telemetry plus the user's Turso usage view will be required for final live rate validation.
- SniperPlug must be deployed after merge before the startup-crash and quota-rate Definition of Done can be closed.

## Suspended task
**SP-DEALS-DISCOVER-HARDENING-244 is suspended at its live acceptance gate, not closed.** PR #247 is already merged and CI-green. Remaining live `/deals` button and private/public `/discover` acceptance resumes only after this active Turso task closes or another explicit FORCE SWITCH.

**SP-WM-MISSED-DEALS-236 remains suspended, not closed.**

## Backlog
- **SP-SETUP-DELIVERY-UX:** Align `/setup_sniperplug_here` with the clearer `/dm_deals` setup experience; make public delivery explicit and easy to review/change.
- **SP-DM-DELIVERY-STARVATION:** Investigate matched DM deals lost to filter/duplicate suppression rather than Discord delivery errors.
- **SP-DM-NEW-LOW-ALERT:** Send a distinct meaningful lower-price alert for the same item while same-price repeats remain deduped.
- **SP-WM-REFERENCE-PRICE-RECOVERY:** Improve exact reference-price recovery with official PDP/history and conservative clearly labeled estimates without weakening public proof.
- **SP-WM-CASH-PDP-AMOUNT:** Integrate exact same-item PDP Walmart Cash amount proof for private/DM enrichment without confusing OnePay cashback or using Cash as markdown proof.
- Resume #244 live acceptance after this task closes.
- Resume Walmart missed-deals/page-recovery throughput work after the active task closes.
- Investigate remaining Discord gateway lag only if it persists after the Turso write path is stable.
- Resume SniperPlug-Site Whop content-reader work later.
