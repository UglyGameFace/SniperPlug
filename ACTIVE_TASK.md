# Active Task

## Status
**Active — SP-TURSO-WRITE-AMPLIFICATION is at its final Turso-dashboard acceptance gate. PR #248, #249, and #250 are merged and deployed. Aug 14 post-#250 production telemetry proves the blanket daily scheduled-recheck write floor is gone: bounded pressure repeatedly reports `new/retry 0`, `scheduled rechecks 0`, and exact cycles often claim 0 rows with `store 0.00s`, while page-recovery backlog remains parked behind the open page circuit. Exact-worker live acceptance has passed. The Turso dashboard screenshot supplied at 13:10 EDT shows SniperPlug at **6,273,176 rows written**, but does not expose a rows-written time slope, so the original write-rate acceptance cannot be quantified from that screen alone. The same screenshot exposes a new account-critical blocker: the banner is for **rows READ**, at 92% used, with SniperPlug itself at **455,545,327 rows read**. Do not silently switch implementation tasks; SP-TURSO-READ-AMPLIFICATION is the next critical task if the user FORCE SWITCHes.**

## Scope
Reduce SniperPlug's real Turso rows-written rate without weakening exact Walmart proof, delivery dedupe, durable leases, crash recovery, material-change detection, or price history. Fix storage-maintenance startup correctness and remove needless catalog/exact-worker writes using the existing queue/runtime rather than adding a parallel persistence or scheduling layer.

## Root cause / findings
- Prior Turso work remains authoritative: PR #222 suppresses recent identical catalog UPSERTs, PR #226 bounds startup cleanup, PR #204 bulk-persists exact outcomes, PR #207 bulk-fanouts events, and the exact worker uses durable per-row leases.
- The startup crash came from bare `rowid` result-name assumptions under process-isolated libSQL. PR #248 fixed it with an explicit rowid alias and production now reaches `Storage maintenance completed` and `SniperPlug online`.
- PR #248 also removed stale proof timestamp churn, lengthened the discovery heartbeat, removed open-circuit page-row reschedule writes, and added exact-write telemetry. Production catalog passes overwhelmingly remain unchanged with many stale proof refreshes skipped; `page_circuit_reschedule_rows=0` remains live-proven.
- PR #249 replaced the old 4/12/24-hour discount heartbeat with a 24-hour verified fallback, made material catalog current-price/reference/selected-offer proof changes wake verified rows immediately, and enforced the verified-age floor in claim/pressure/health without repair writes.
- The first short #249 sample looked like a finite backlog, but the longer 11:56-12:16 production sample showed a sustained blanket-24h anniversary floor: even with `new/retry=0`, roughly 18-25 scheduled rechecks remained continuously claimable and batches repeatedly wrote `lease_rows=8 outcome_rows=8 offer_memory_rows=7-8`.
- Bulk exact persistence was reinspected and correctly advances `verified_at` and `next_attempt_at`; this was not an immediate same-row reclaim bug. The mature queue was large enough that different low-signal rows crossed a blanket 24-hour anniversary continuously.
- The live outcome mix identified the waste: many saturated batches contained `official was prices 0`, `markdowns 0`, and `no reference 7-8`. `verified_no_reference` and `not_buyable` were consuming daily lease/outcome/offer-memory writes despite being low-signal unchanged states.
- The global catalog rotates 265 routes two at a time (roughly a 2.2-hour pass when not backpressured). Material catalog price/reference/selected-offer changes already rearm all verified states to `pending` immediately, so unchanged low-signal rows do not need a daily exact heartbeat for responsiveness.
- The Aug 14 Turso dashboard screenshot shows that the current critical banner is **not rows written**. It says **Quota Critical — 92% used** for rows read. SniperPlug shows **455,545,327 rows read**, **6,273,176 rows written**, and **2.38 GB bytes synced**. The screen itself warns that hitting any free-tier limit blocks the entire account, so read amplification is now the immediate account-availability risk even though the write churn targeted by this task is live-fixed.

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

## Changes live from PR #250
- `verified_markdown`: retain a **24-hour** exact safety sweep.
- `verified_under_threshold` and `verified_reference`: use a **72-hour** exact safety sweep.
- `verified_no_reference` and `not_buyable`: use a **7-day** exact safety sweep.
- The material catalog wake path remains immediate for current-price, trusted-reference, or selected-offer proof changes.
- The claim selector applies status-specific verified-age floors to legacy 24-hour rows with zero schedule-repair writes.
- Bounded queue pressure and owner queue health use the same status groups/cutoffs.
- Verified rows missing `verified_at` remain recoverable.
- No durable lease, exact seller/item/offer/variant/condition/fulfillment proof, price history, fanout/dedupe, or offer-memory safety gate was removed.

## Validation completed
- PR #248/#249/#250 pre/post-merge Python 3.11/3.12 tests, compilation, dependency checks, and import smoke passed.
- PR #250 Python 3.11 workflow passed **1,244 tests, 1 warning** (discord/audioop deprecation only); `pip check` and `python -m compileall -q sniperplug tests *.py` passed.
- PR #250 changed-file cleanup was limited to the existing drain/pressure/health policy paths, task record, and focused regressions. No review threads or submitted review blockers existed.
- PR #250 was squash-merged as `0d49e9d4e29bb123b47280ad8cf76f540a447746`; post-merge `main` checks passed.
- PR #250 is now live on Discloud.
- Live status-aware cadence acceptance passed. Repeated bounded-pressure samples at 13:35, 13:40, 13:45, 13:50, 13:55, 14:00, and 14:06 report **`new/retry 0` and `scheduled rechecks 0`** while `page recovery >=64` remains bounded behind the open page circuit.
- Exact-worker idle cycles now occur normally: multiple samples show **`claimed 0`**, `fetch 0.00s`, and **`store 0.00s`** instead of the pre-#250 continuous 8-row write floor.
- Non-idle cycles are now tied to real material catalog wakeups, page/API retry work, or small exact verification batches rather than a permanent scheduled anniversary lane. Catalog logs continue to show `verified changes rearmed` when material changes occur.
- `page_circuit_reschedule_rows=0` remains true throughout the supplied post-#250 sample.
- A real DM delivery succeeded during the sample: fanout reported `DM checked/matched/sent 1/1/1`, proving the DM transport path is operational even though separate DM filter/dedupe starvation work remains backlogged.
- Turso dashboard evidence at 13:10 EDT: SniperPlug cumulative **rows written = 6,273,176**. The dashboard view supplied does not include a write-over-time graph, so no pre/post write slope can be calculated from it.
- The same dashboard evidence shows the account-critical metric is **rows read = 92% used**, with SniperPlug cumulative **rows read = 455,545,327**.

## Validation pending / Definition of Done
- If a Turso view with a rows-written time series is available, compare the post-#250 slope with the pre-#250 rate. The runtime write telemetry already demonstrates the solved write floor, but the original dashboard-slope criterion is not measurable from the supplied database-list screen.
- Do not conflate the new 92% rows-read warning with write-amplification acceptance. It is a separate quota dimension and should become **SP-TURSO-READ-AMPLIFICATION** only via the task-lock FORCE SWITCH.
- If the user chooses to close the write task based on the direct runtime evidence, record that explicitly before starting the read task. Otherwise keep this task open at the dashboard-slope gate.

## Cleanup status
- No startup exception swallow, monkey patch, alternate database, duplicate queue runtime, or relaxed proof gate was added.
- Page-circuit reschedule writer and write-based legacy cadence migration remain deleted.
- The existing exact claim selector remains the single lease/cadence gate.
- Price history and exact offer memory remain intact.

## Blockers
- The supplied Turso database-list screen exposes cumulative rows-read/rows-written totals but no time series, so it cannot prove a rows-written slope by itself.
- Direct Turso organization/top-query metrics are not exposed through the connected tools.
- New critical availability risk discovered: Turso rows-read quota is at **92%** and the UI warns that hitting any free-tier limit blocks the entire account.

## Suspended task
**SP-DEALS-DISCOVER-HARDENING-244 is suspended at its live acceptance gate, not closed.** PR #247 is merged and CI-green. Resume only after this Turso task closes or another explicit FORCE SWITCH.

**SP-WM-MISSED-DEALS-236 remains suspended, not closed.**

## Backlog
- **SP-TURSO-READ-AMPLIFICATION — CRITICAL:** Turso dashboard is at **92% rows-read quota**. SniperPlug alone shows **455,545,327 rows read**. Root-cause audit should identify the highest-frequency/highest-row SELECT paths, repeated full/large queue scans, health/pressure polling, startup/restoration scans, fanout/history reads, and any query missing a selective index or bounded predicate. Preserve correctness and existing write optimizations; do not simply cache stale safety-critical data. Requires exact FORCE SWITCH before implementation.
- **SP-GATEWAY-EVENT-LOOP-LAG:** Investigate repeated Discord gateway `Can't keep up` warnings around 41 seconds behind seen during the Aug 14 post-#250 sample. Trace event-loop blocking/slow DB or network work before editing; keep separate from the Turso cadence fix.
- **SP-SETUP-DELIVERY-UX:** Align `/setup_sniperplug_here` with `/dm_deals`; make public delivery explicit and easy to review/change.
- **SP-DM-DELIVERY-STARVATION:** Investigate matched DM deals lost to filter/duplicate suppression rather than Discord delivery errors.
- **SP-DM-NEW-LOW-ALERT:** Send a distinct meaningful lower-price alert while same-price repeats remain deduped.
- **SP-WM-REFERENCE-PRICE-RECOVERY:** Improve exact reference-price recovery without weakening public proof; current logs still show `api_price_missing`, `api_exact_search_no_match`, page challenges, and open-circuit recovery periods.
- **SP-WM-CASH-PDP-AMOUNT:** Integrate exact same-item Walmart Cash proof for private/DM enrichment without confusing OnePay cashback or using Cash as markdown proof.
- Resume #244 live acceptance after the active quota task closes.
- Resume Walmart missed-deals/page-recovery throughput work after the active quota task closes.
- Resume SniperPlug-Site Whop content-reader work later.
