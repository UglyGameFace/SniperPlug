# Active Task

## Status
**Active — SP-TURSO-WRITE-AMPLIFICATION. PR #248 is merged and live. The startup `rowid` crash and catalog rediscovery write storm are live-validated fixed. Production telemetry exposed scheduled exact rechecks as the remaining sustained Turso write source, so the same active task continues on `fix/turso-write-amplification-recheck-cadence`.**

## Scope
Reduce SniperPlug's real Turso rows-written rate without weakening exact Walmart proof, delivery dedupe, durable leases, crash recovery, or price history. Fix the storage-maintenance startup crash and remove needless catalog/exact-worker writes. Do not stack a second persistence layer or relax item/seller/offer/variant/reference proof.

## Root cause / findings
- Prior Turso work remains authoritative: PR #222 suppresses recent identical catalog UPSERTs, PR #226 bounds startup cleanup, PR #204 bulk-persists exact outcomes, PR #207 bulk-fanouts events, and the exact worker uses durable per-row leases.
- The startup crash came from selecting bare `rowid` then assuming `row["rowid"]` or `row[0]`; process-isolated libSQL can normalize an INTEGER PRIMARY KEY rowid under different mapping metadata.
- The first live catalog-write issue was stale proof timestamp churn plus an overly frequent discovery heartbeat. Identical fifteen-minute proof was being rewritten even when the next exact recheck was hours away.
- Live Aug 14 deployment proved that fix: storage maintenance completed, SniperPlug reached Discord online, and mature catalog passes fell to examples such as `persisted/unchanged 3/96`, `0/99`, `3/92`, `3/97`, and `7/93`, with dozens of `stale proof refresh skipped` rows.
- Live exact telemetry also proved `page_circuit_reschedule_rows=0` on every sampled batch, so the obsolete open-circuit page-row rewrite loop is gone.
- The remaining write floor is scheduled exact rechecks. Production repeatedly emitted `lease_rows=8 outcome_rows=8 offer_memory_rows=7-8` roughly every 20 seconds after fresh work drained. Bounded health still showed scheduled rechecks around 164-167 while eight rows per cycle were being completed, so this was sustained replenishment rather than a one-time startup backlog.
- The real scheduling cause is the old exact cadence: verified markdowns at 50%+ were rechecked every four hours, 30%+ every twelve hours, and other verified states every twenty-four hours. With thousands of mature verified rows, that creates a permanent Turso lease/outcome/offer-memory write floor.
- The global catalog rotates 265 routes two at a time on a one-minute cadence, so a full catalog pass is roughly 2.2 hours when not backpressured. The safe way to lower scheduled writes is therefore event-driven: a material catalog price/reference/selected-offer change must wake a verified row immediately, while unchanged verified rows use a low-frequency exact safety sweep.

## Changes already live from PR #248
- Stable aliased maintenance rowid extraction (`rowid AS __sniperplug_rowid`) with mapping/positional-safe reads.
- No-op operational maintenance skips its final commit.
- Catalog discovery heartbeat coalesced from six hours to twenty-four hours.
- Identical stale fresh-proof timestamp refresh is suppressed when the row cannot consume it before proof expiry.
- Open-circuit page-retry reschedule writer removed; zero page-recovery claim cap is the single gate.
- Catalog and exact-worker Turso write telemetry added.
- No price history, exact offer memory, durable fanout/dedupe state, or exact queue history was deleted.

## Current branch changes
- Verified exact rows now use a **24-hour fallback safety recheck** instead of 4/12/24-hour discount-based heartbeats. The compatibility helper name remains shared by bulk and legacy persistence, so there is one scheduling policy.
- Material catalog current-price, trusted-reference, or selected-offer proof changes now rearm an existing verified row to `pending` immediately and pull `next_attempt_at` forward.
- Cosmetic title/URL/source metadata changes still persist when needed but do not wake a verified row by themselves.
- Catalog summary telemetry now reports `verified changes rearmed` so the event-driven wake path is visible in production.
- Added regressions for price-change wakeup, same-price selected-offer proof wakeup, cosmetic non-wakeup, and the daily verified fallback cadence.
- Existing durable per-row leases, strict exact proof, retry/page recovery, and fanout semantics remain intact.

## Validation completed
- Root execution path, claim ordering, durable lease update, bulk outcome persistence, offer-memory write path, legacy schedule repair, fresh-work pressure policy, catalog enqueue/upsert behavior, and existing cadence tests were inspected before the second-stage change.
- PR #248 pre-merge and post-merge Python 3.11/3.12 workflows passed, including full tests, compilation, dependency checks, and import smoke.
- Live startup proof passed: Turso connected through the process-isolated driver, storage maintenance completed with 509 bounded changes, slash commands synced, and SniperPlug reached online without the old `KeyError` crash.
- Live catalog write-suppression proof passed with mature passes overwhelmingly unchanged and stale proof refreshes skipped.
- Live page-circuit write proof passed with `page_circuit_reschedule_rows=0`.

## Validation pending / Definition of Done
- Run focused and full CI for the current recheck-cadence branch.
- Inspect final diff/references for duplicate or conflicting recheck policy.
- Merge and deploy the second-stage change.
- Live logs must show `verified changes rearmed` when catalog proof materially changes and no wakeup from ordinary unchanged rediscovery.
- Scheduled-recheck pressure must fall materially after legacy 4/12-hour rows are repaired into the daily fallback.
- Exact write telemetry must show a materially lower sustained lease/outcome/offer-memory row rate than the pre-change ~8-row-every-20-second saturation.
- Turso dashboard rows-written slope must be checked after deployment. The task is not complete until the monthly-rate risk is demonstrably reduced.

## Cleanup status
- No startup exception swallow, monkey patch, alternate database, duplicate queue runtime, or relaxed proof gate was added.
- Obsolete page-circuit reschedule code remains deleted.
- The current branch changes the existing shared recheck policy and existing catalog UPSERT; it does not add a parallel scheduler.

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
