# Active Task

## Status
**Active — SP-TURSO-WRITE-AMPLIFICATION. PR #248 and PR #249 are merged. The startup `rowid` crash and catalog rediscovery write storm are live-validated fixed, and the scheduled-recheck write reduction is now on `main` as `1ef9d05dce3dc24b92f4b6c69bb317c5881139a1` with post-merge workflows green. Discloud deployment plus live exact-write/Turso-rate acceptance remain before this task can close.**

## Scope
Reduce SniperPlug's real Turso rows-written rate without weakening exact Walmart proof, delivery dedupe, durable leases, crash recovery, or price history. Fix the storage-maintenance startup crash and remove needless catalog/exact-worker writes. Do not stack a second persistence layer or relax item/seller/offer/variant/reference proof.

## Root cause / findings
- Prior Turso work remains authoritative: PR #222 suppresses recent identical catalog UPSERTs, PR #226 bounds startup cleanup, PR #204 bulk-persists exact outcomes, PR #207 bulk-fanouts events, and the exact worker uses durable per-row leases.
- The startup crash came from selecting bare `rowid` then assuming `row["rowid"]` or `row[0]`; process-isolated libSQL can normalize an INTEGER PRIMARY KEY rowid under different mapping metadata.
- The first live catalog-write issue was stale proof timestamp churn plus an overly frequent discovery heartbeat. Identical fifteen-minute proof was being rewritten even when the next exact recheck was hours away.
- Live Aug 14 deployment proved that fix: storage maintenance completed, SniperPlug reached Discord online, and mature catalog passes fell to examples such as `persisted/unchanged 3/96`, `0/99`, `3/92`, `3/97`, and `7/93`, with dozens of `stale proof refresh skipped` rows.
- Live exact telemetry also proved `page_circuit_reschedule_rows=0` on every sampled batch, so the obsolete open-circuit page-row rewrite loop is gone.
- The remaining write floor exposed by that deployment was scheduled exact rechecks. Production repeatedly emitted `lease_rows=8 outcome_rows=8 offer_memory_rows=7-8` roughly every 20 seconds after fresh work drained. Bounded health still showed scheduled rechecks around 164-167 while eight rows per cycle were being completed, so this was sustained replenishment rather than a one-time startup backlog.
- The scheduling cause was the old exact cadence: verified markdowns at 50%+ were rechecked every four hours, 30%+ every twelve hours, and other verified states every twenty-four hours. With thousands of mature verified rows, that creates a permanent Turso lease/outcome/offer-memory write floor.
- The global catalog rotates 265 routes two at a time on a one-minute cadence, so a full catalog pass is roughly 2.2 hours when not backpressured. The safer low-write model is event-driven: a material catalog price/reference/selected-offer change wakes a verified row immediately, while unchanged verified rows use a low-frequency exact safety sweep.
- Reinspection of the former `walmart_recheck_schedule_rearm` migration found it was the wrong mechanism for the new policy: it spent writes moving due timestamps and marked a connection complete, so future legacy 4/12-hour rows could still leak one early exact cycle. The final implementation enforces the verified-age floor at claim time without writing anything.
- Queue pressure and owner health use the same verified-age rule as the claim selector so suppressed legacy timestamps cannot create false scheduled pressure and pause catalog discovery.

## Changes live from PR #248
- Stable aliased maintenance rowid extraction (`rowid AS __sniperplug_rowid`) with mapping/positional-safe reads.
- No-op operational maintenance skips its final commit.
- Catalog discovery heartbeat coalesced from six hours to twenty-four hours.
- Identical stale fresh-proof timestamp refresh is suppressed when the row cannot consume it before proof expiry.
- Open-circuit page-retry reschedule writer removed; zero page-recovery claim cap is the single gate.
- Catalog and exact-worker Turso write telemetry added.
- No price history, exact offer memory, durable fanout/dedupe state, or exact queue history was deleted.

## Changes merged in PR #249
- Verified exact rows use a **24-hour fallback safety recheck** instead of 4/12/24-hour discount-based heartbeats.
- Material catalog current-price, trusted-reference, or selected-offer proof changes rearm an existing verified row to `pending` immediately and pull `next_attempt_at` forward.
- Cosmetic title/URL/source metadata changes still persist when needed but do not wake a verified row by themselves.
- The claim selector enforces the 24-hour floor from `verified_at`; an old 4/12-hour `next_attempt_at` is ignored until truly due, with **zero schedule-repair writes**. Verified rows missing `verified_at` remain recoverable instead of being silently stranded.
- Bounded queue pressure and owner-facing queue health use the same verified-age floor so suppressed legacy timestamps are not misreported as actionable scheduled rechecks or allowed to trigger false catalog backpressure.
- The old `walmart_recheck_schedule_rearm.py` writer and its dedicated migration test were removed after caller inspection; there is no second cadence mechanism left.
- Catalog summary telemetry reports `verified changes rearmed` so the event-driven wake path is visible in production.
- Regressions cover price-change wakeup, same-price selected-offer proof wakeup, cosmetic non-wakeup, daily fallback scheduling, zero-write claim suppression of a legacy early recheck, pressure/health alignment, and recovery of malformed verified rows without a timestamp.
- Existing durable per-row leases, strict exact proof, retry/page recovery, and fanout semantics remain intact.

## Validation completed
- Root execution path, claim ordering, durable lease update, bulk outcome persistence, offer-memory write path, legacy schedule repair, fresh-work pressure policy, catalog enqueue/upsert behavior, and existing cadence tests were inspected before the second-stage change.
- PR #248 pre-merge and post-merge Python 3.11/3.12 workflows passed, including full tests, compilation, dependency checks, and import smoke.
- Live startup proof passed: Turso connected through the process-isolated driver, storage maintenance completed with 509 bounded changes, slash commands synced, and SniperPlug reached online without the old `KeyError` crash.
- Live catalog write-suppression proof passed with mature passes overwhelmingly unchanged and stale proof refreshes skipped.
- Live page-circuit write proof passed with `page_circuit_reschedule_rows=0`.
- First PR #249 Python 3.11 run compiled, passed dependency checks, and reached **1,241 passed / 1 failed**. The sole failure was the old migration regression expecting a five-hour-old high markdown to be truly due. That exposed the stale migration assumption and led to the cleaner zero-write claim-floor implementation.
- The next PR #249 run reached **1,240 passed / 3 failed**; all three failures were static SQL parameter-layout assertions that had not accounted for the new verified-age cutoff. No runtime behavior regression failed. Those guards were updated to assert the cutoff itself rather than merely shifting indexes.
- Final PR #249 Python 3.11 workflow: **1,243 passed, 1 warning** (discord/audioop deprecation only); `pip check` passed and `python -m compileall -q sniperplug tests *.py` passed.
- Final PR #249 Python 3.12 `Python Check` passed, including dependency install/check, compilation, import smoke, and full test suite.
- Final separate import-smoke workflow passed.
- Final changed-file/reference inspection confirmed the deleted `walmart_recheck_schedule_rearm.py` import/call is removed from the exact runtime and its dedicated migration test is deleted. The claim selector is the single cadence-enforcement path.
- PR #249 had no review threads or submitted review blockers and was mergeable on its final head.
- PR #249 was squash-merged to `main` as `1ef9d05dce3dc24b92f4b6c69bb317c5881139a1`.
- Post-merge `main` validation passed: Python tests, Python Check, compilation/dependency validation, and import smoke all succeeded.

## Validation pending / Definition of Done
- Validate this documentation-only active-task record commit on `main`.
- Deploy current merged `main` to Discloud.
- Confirm startup still reaches `Storage maintenance completed` and `SniperPlug online`; there must be no regression of the old rowid crash.
- Confirm there is no `Walmart legacy recheck schedule repair deferred=...` activity; the write-based repair path is deleted.
- Live queue pressure should no longer report legacy 4/12-hour timestamps as actionable scheduled rechecks before `verified_at + 24h`.
- Live exact telemetry must show a materially lower sustained lease/outcome/offer-memory row rate than the pre-change ~8-row-every-20-second saturation once fresh work is zero.
- Catalog logs should show `verified changes rearmed` only when material catalog price/reference/selected-offer proof changes require exact revalidation; ordinary unchanged rediscovery should remain mostly write-free.
- Turso dashboard rows-written slope must be checked after deployment. The task is not complete until the monthly-rate risk is demonstrably reduced.

## Cleanup status
- No startup exception swallow, monkey patch, alternate database, duplicate queue runtime, or relaxed proof gate was added.
- Obsolete page-circuit reschedule code remains deleted.
- Obsolete write-based legacy recheck schedule repair is deleted; the existing claim path is the single cadence enforcement point.
- Pressure and health use the same verified-age rule as the claim selector; there is no conflicting actionable-recheck definition left in the worker path.
- The implementation changes the existing shared recheck policy, claim selector, pressure/health accounting, and catalog UPSERT; it does not add a parallel scheduler.

## Blockers
- Direct Turso organization/top-query metrics are not exposed through the connected tools. Final quota-rate acceptance requires the user's Turso usage view after the new deployment.

## Suspended task
**SP-DEALS-DISCOVER-HARDENING-244 is suspended at its live acceptance gate, not closed.** PR #247 is already merged and CI-green. Resume only after this Turso task closes or another explicit FORCE SWITCH.

**SP-WM-MISSED-DEALS-236 remains suspended, not closed.**

## Backlog
- **SP-SETUP-DELIVERY-UX:** Align `/setup_sniperplug_here` with the clearer `/dm_deals` setup experience; make public delivery explicit and easy to review/change.
- **SP-DM-DELIVERY-STARVATION:** Investigate matched DM deals lost to filter/duplicate suppression rather than Discord delivery errors.
- **SP-DM-NEW-LOW-ALERT:** Send a distinct meaningful lower-price alert for the same item while same-price repeats remain deduped.
- **SP-WM-REFERENCE-PRICE-RECOVERY:** Improve exact reference-price recovery without weakening public proof.
- **SP-WM-CASH-PDP-AMOUNT:** Integrate exact same-item Walmart Cash proof for private/DM enrichment without confusing OnePay cashback or using Cash as markdown proof.
- Resume #244 live acceptance after this task closes.
- Resume Walmart missed-deals/page-recovery throughput work after the active task closes.
- Resume SniperPlug-Site Whop content-reader work later.
