# Active Task

## Status
**Active — SP-TURSO-WRITE-AMPLIFICATION. PR #248 and PR #249 are merged/deployed and fixed the startup `rowid` crash, catalog rediscovery write storm, page-circuit reschedule writer, and old 4/12-hour verified heartbeat. Longer Aug 14 production telemetry disproved the earlier finite-backlog conclusion: a sustained 24-hour anniversary flow still keeps roughly 18-25 scheduled rechecks claimable and the exact worker saturated. A status-aware fallback cadence is now implemented on `fix/turso-status-aware-recheck-cadence`; CI/live acceptance are still required.**

## Scope
Reduce SniperPlug's real Turso rows-written rate without weakening exact Walmart proof, delivery dedupe, durable leases, crash recovery, material-change detection, or price history. Fix storage-maintenance startup correctness and remove needless catalog/exact-worker writes using the existing queue/runtime rather than adding a parallel persistence or scheduling layer.

## Root cause / findings
- Prior Turso work remains authoritative: PR #222 suppresses recent identical catalog UPSERTs, PR #226 bounds startup cleanup, PR #204 bulk-persists exact outcomes, PR #207 bulk-fanouts events, and the exact worker uses durable per-row leases.
- The startup crash came from bare `rowid` result-name assumptions under process-isolated libSQL. PR #248 fixed it with an explicit rowid alias and production now reaches `Storage maintenance completed` and `SniperPlug online`.
- PR #248 also removed stale proof timestamp churn, lengthened the discovery heartbeat, removed open-circuit page-row reschedule writes, and added exact-write telemetry. Production catalog passes now overwhelmingly remain unchanged (examples `3/96`, `0/99`, `3/92`, `3/97`, `7/93`) with many stale proof refreshes skipped; `page_circuit_reschedule_rows=0` remains live-proven.
- PR #249 replaced the old 4/12/24-hour discount heartbeat with a 24-hour verified fallback, made material catalog current-price/reference/selected-offer proof changes wake verified rows immediately, and enforced the verified-age floor in claim/pressure/health without repair writes.
- The first short #249 sample looked like a finite backlog because scheduled rechecks fell `84 -> 64 -> 34 -> 13`. The longer 11:56-12:16 production sample disproves that interpretation: with `new/retry=0`, bounded pressure repeatedly stabilizes around `page recovery >=64` plus **18-25 scheduled rechecks**, while the worker still emits `lease_rows=8 outcome_rows=8 offer_memory_rows=7-8` about every 20 seconds.
- This is not an immediate same-row reclaim bug. Bulk exact persistence stamps a new `verified_at` and future `next_attempt_at`; the sustained floor is enough different mature rows crossing their 24-hour anniversary continuously.
- The live outcome mix identifies the waste: many saturated batches contain `official was prices 0`, `markdowns 0`, and `no reference 7-8`. `verified_no_reference` means exact current price exists but there is no trusted reference price; `not_buyable` is unavailable/out-of-stock. These low-signal states cannot produce a normal verified markdown on that pass but were still consuming daily lease/outcome/offer-memory writes.
- The global catalog rotates 265 routes two at a time (roughly a 2.2-hour pass when not backpressured). Material catalog price/reference/selected-offer changes already rearm **all** verified states to `pending` immediately, so unchanged low-signal rows do not need a daily exact heartbeat for responsiveness.

## Changes live from PR #248
- Stable `rowid AS __sniperplug_rowid` maintenance extraction.
- No-op storage maintenance skips final operational commit.
- Catalog discovery heartbeat 6h -> 24h.
- Unusable timestamp-only fresh-proof refreshes are suppressed.
- Open-circuit page-retry reschedule writer deleted; claim cap is the gate.
- Catalog/exact Turso write telemetry added.

## Changes live from PR #249
- All verified states moved from 4/12/24h to a 24h safety fallback.
- Material catalog price/reference/selected-offer proof changes rearm verified rows to `pending` immediately.
- Cosmetic rediscovery does not wake verified rows.
- Claim, bounded pressure, and owner health ignore legacy early verified schedules without repair writes.
- Obsolete write-based recheck schedule migration deleted.

## Current status-aware cadence change
- `verified_markdown`: retain a **24-hour** exact safety sweep.
- `verified_under_threshold` and `verified_reference`: use a **72-hour** exact safety sweep.
- `verified_no_reference` and `not_buyable`: use a **7-day** exact safety sweep.
- The existing material catalog wake path is unchanged: a current-price, trusted-reference, or selected-offer proof change still reclassifies any verified state to `pending` immediately, bypassing the fallback age.
- The claim selector applies the same status-specific verified-age floor to legacy 24-hour rows with **zero schedule-repair writes**, so the production reduction is immediate after deployment instead of waiting a week for existing timestamps to roll over.
- Bounded queue pressure and owner queue health use the same status groups/cutoffs so diagnostics cannot disagree with what the worker can actually claim.
- Verified rows missing `verified_at` remain recoverable.
- No durable lease, exact seller/item/offer/variant/condition/fulfillment proof, price history, fanout/dedupe, or offer-memory safety gate was removed.

## Validation completed
- PR #248 and PR #249 pre/post-merge Python 3.11/3.12 tests, compilation, dependency checks, and import smoke passed.
- Live startup, catalog write suppression, and zero page-circuit-reschedule proof passed.
- Longer production telemetry was inspected before this third-stage change and is now treated as authoritative over the earlier finite-backlog hypothesis.
- Exact bulk persistence was reinspected: successful candidates update `verified_at` and `next_attempt_at` correctly, ruling out immediate same-row reclaim as the sustained cause.
- Exact classifier was reinspected: no-reference and unavailable states are low-signal relative to proven markdowns, supporting a longer fallback without relabeling or weakening proof.
- Existing catalog verified-change rearm path remains unchanged and covers all verified statuses.
- New regressions cover 24h/72h/7d fallback delays, status-specific claim floors, pressure/health alignment, missing timestamp recovery, and immediate material catalog wakeup of a weekly no-reference row.

## Validation pending / Definition of Done
- Run focused and full CI on the status-aware branch: Python 3.11/3.12, `pip check`, compileall, import smoke, and full pytest.
- Inspect changed-file references/conflicts and PR review threads; do not merge with a failing or stale guard.
- Merge/deploy only after CI is green.
- Live bounded pressure should show the daily `scheduled rechecks` floor falling materially below the current ~18-25 once the status-aware claim floor is active.
- With `new/retry=0` and page circuit open, exact telemetry should no longer remain saturated at `8/8/7-8` every ~20 seconds solely because no-reference/unavailable rows hit a daily anniversary.
- Confirm material catalog changes still report `verified changes rearmed` and are processed promptly.
- Check the Turso dashboard rows-written slope after deployment. The task is not complete until the monthly-rate risk is demonstrably reduced.

## Cleanup status
- No startup exception swallow, monkey patch, alternate database, duplicate queue runtime, or relaxed proof gate was added.
- Page-circuit reschedule writer and write-based legacy cadence migration remain deleted.
- The existing exact claim selector remains the single lease/cadence gate; the new change refines that policy by status rather than adding another scheduler.
- Price history and exact offer memory remain intact; offer-memory coalescing is not being changed unless post-deploy telemetry proves it is still necessary.

## Blockers
- Direct Turso organization/top-query metrics are not exposed through the connected tools. Final quota-rate acceptance requires the user's Turso usage view after deployment.

## Suspended task
**SP-DEALS-DISCOVER-HARDENING-244 is suspended at its live acceptance gate, not closed.** PR #247 is merged and CI-green. Resume only after this Turso task closes or another explicit FORCE SWITCH.

**SP-WM-MISSED-DEALS-236 remains suspended, not closed.**

## Backlog
- **SP-SETUP-DELIVERY-UX:** Align `/setup_sniperplug_here` with `/dm_deals`; make public delivery explicit and easy to review/change.
- **SP-DM-DELIVERY-STARVATION:** Investigate matched DM deals lost to filter/duplicate suppression rather than Discord delivery errors.
- **SP-DM-NEW-LOW-ALERT:** Send a distinct meaningful lower-price alert while same-price repeats remain deduped.
- **SP-WM-REFERENCE-PRICE-RECOVERY:** Improve exact reference-price recovery without weakening public proof.
- **SP-WM-CASH-PDP-AMOUNT:** Integrate exact same-item Walmart Cash proof for private/DM enrichment without confusing OnePay cashback or using Cash as markdown proof.
- Resume #244 live acceptance after this task closes.
- Resume Walmart missed-deals/page-recovery throughput work after the active task closes.
- Resume SniperPlug-Site Whop content-reader work later.
