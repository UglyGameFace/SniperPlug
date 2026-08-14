# Active Task

## Status
**Active — SP-TURSO-READ-AMPLIFICATION. Turso's Aug 14 dashboard shows the account at 92% of its rows-read quota, with SniperPlug alone at 455,545,327 rows read. Production also logged a 4.03s isolated database `SELECT item_id, title, product_url...` from the Walmart exact queue claim path. PR #251 now has a CI-green bounded/indexed replacement for the high-frequency claim and pressure reads. The code/repository validation gate has passed; the task remains open through merge, Discloud deployment, and live Turso rows-read-rate acceptance.**

## Scope
Reduce SniperPlug's Turso rows scanned/read rate enough to remove the account-quota emergency while preserving the existing Walmart exact-verification architecture and safety semantics. Fix the actual high-frequency read access paths rather than masking them with stale caches, disabling verification, or creating a parallel queue/runtime.

## Root cause / findings
- Turso's dashboard evidence is account-critical: rows read are at **92%**, and SniperPlug alone shows **455,545,327 rows read**, **6,273,176 rows written**, and **2.38 GB synced**.
- Production logged a slow isolated database operation for `SELECT item_id, title, product_url...` with **remote_s=4.03**. That projection maps to the Walmart exact queue claim selector.
- The exact worker normally runs about every **20 seconds**. Before this branch, `claim_due_rows_batched` used one all-status SELECT combining due time, retention, lease state, fresh-snapshot priority, page-recovery handling, verified status-age floors, status ordering, priority ordering, and a final small LIMIT.
- A mature-queue SQLite reproduction with thousands of recently verified low-signal rows showed the old claim planner choosing the `last_seen_at` access path and a temporary ORDER BY rather than seeking directly by the verified-age floor. A small result LIMIT therefore did not guarantee a small scan.
- PR #250 intentionally stopped rewriting legacy early verified schedules in order to remove needless Turso writes. A mature verified row can therefore have an already-due legacy `next_attempt_at` while its newer `verified_at` correctly keeps it below the real 24h/72h/7d safety cadence. The old claim SELECT can repeatedly encounter and reject large numbers of those rows.
- `load_walmart_exact_queue_pressure` is another multiplier. It is used by the exact worker before claims, after real work, and by catalog/backpressure paths. Its old verified recheck branch combined due schedule and verified-age predicates in a way that could repeatedly walk the same legacy-due-but-too-young rows.
- Full owner-facing `load_walmart_exact_queue_health` performs broader aggregates, but background runtime explicitly avoids it (`background_full_queue_scans=false`). It is not the primary continuous read source identified here.
- Global Walmart fanout cursor reads were already hardened separately with bounded cursor/index behavior and focused regression coverage. This task does not duplicate that work without evidence.
- The read fix preserves: durable per-row leases; fresh-snapshot priority; page-recovery claim cap; terminal identity exclusion; exact seller/item/offer/variant/condition/fulfillment proof; immediate material catalog wakeups; 24h markdown, 72h reference/under-threshold, and 7d low-signal safety sweeps; missing-`verified_at` recovery; price history; fanout; and delivery dedupe.

## Changes in PR #251
- Added `idx_walmart_exact_detail_queue_status_verified_due` over `(status, verified_at, next_attempt_at, last_seen_at, lease_until, priority_score DESC)` during the existing one-time exact runtime schema initialization.
- Reworked production exact claims from one broad all-status SELECT into a bounded candidate pool of index-seekable lanes: fresh proof, pending, retry, failed, capped page recovery, each verified status/age band, missing verified timestamps, and expired verifying work.
- Preserved the existing final global ordering: fresh proof first, then the established status order, then priority and recency. The durable guarded lease UPDATE/RETURNING path remains unchanged.
- Non-fresh lanes exclude currently fresh snapshot rows so a row cannot enter the candidate pool twice.
- Direct compatibility callers that have not initialized runtime indexes retain the historical current-schema selector; older pre-snapshot/pre-`verified_at` callers retain the legacy selector after the current-schema fallback reports missing optional columns.
- Reworked bounded queue pressure so fresh/retry, page recovery, and active leases seek the existing status/due index, while scheduled verified rechecks are split by 24h/72h/7d age and seek the new status/verified index. Missing verified timestamps remain recoverable.
- Added EXPLAIN-query-plan regressions using a mature queue fixture containing hundreds of legacy-due-but-too-young low-signal rows. Tests require the selective indexes and forbid a direct full queue scan in the optimized production path.
- Updated existing pressure, recheck-floor, claim, and runtime-schema regressions to exercise the indexed runtime schema while preserving previous behavior assertions.

## Validation completed
- Root-cause reproduction completed before edits: the old mature-queue claim plan used the `last_seen_at` index plus temporary ordering instead of a verified-age seek.
- Synthetic optimized claim-plan inspection shows the new verified lanes search by `status=? AND verified_at<?` using the new status/verified index.
- Synthetic optimized pressure-plan inspection follows the same age-seek approach rather than repeatedly post-filtering recent verified rows.
- Focused regression coverage includes a 256-row legacy-due recent low-signal fixture where only the genuinely old recheck and fresh pending work are eligible.
- The first PR CI pass exposed exactly two compatibility regressions: a minimal legacy schema fell through only one fallback layer, and the runtime-schema test still expected five index/drop statements instead of six. Both were fixed narrowly without changing the optimized production semantics.
- Final Python 3.12 `Python Check` passed **1,246 tests, 1 warning** (discord/audioop deprecation only). `pip check`, `python -m compileall -q sniperplug scripts tests`, and the 37-module/22-symbol import smoke check passed.
- The separate `Python tests` workflow also passed dependency check, compilation, and the full pytest suite.
- PR #251 has **9 changed files**, all directly scoped to the task record, exact claim/pressure/schema paths, and their regressions. No temporary workflow, alternate runtime, cache layer, or unrelated product behavior is in the diff.
- PR #251 is mergeable with no review threads or submitted reviews. The only PR comment is Qodo's inactive-subscription notice, not a code finding.

## Validation pending / Definition of Done
- Merge PR #251 only after the final docs-only head checks remain green.
- Deploy the merged build to Discloud.
- Live acceptance must show the exact worker still claims/processes valid work correctly and the previous slow broad claim SELECT disappears or materially drops in latency/read cost.
- Confirm bounded queue pressure remains behaviorally correct and no proof/dedupe regression appears.
- Recheck Turso cumulative rows-read usage after a meaningful live interval and compare the post-deploy slope with the pre-fix rate. The task is not complete until actual quota burn is demonstrably reduced.

## Cleanup / safety constraints
- No stale safety-critical cache, alternate database, parallel queue, startup exception swallow, monkey patch, or relaxed Walmart proof gate.
- No exact-verification quality reduction or catalog-discovery shutdown was used to save reads.
- Page-recovery cap, status priority, material-change wake path, status-aware safety cadence, durable leases, offer memory, price history, fanout, and dedupe remain intact.
- The fix uses bounded indexed reads rather than timestamp-repair writes, so it does not undo the write-amplification work merely to make reads easier.

## Blockers
- Direct Turso top-query/organization read metrics are not exposed through the connected tools, so final quota-rate acceptance still needs the user's Turso dashboard after deployment.
- PR #251 must be merged and deployed before live read-rate acceptance can start.

## Suspended tasks
**SP-TURSO-WRITE-AMPLIFICATION is suspended at its final dashboard rows-written slope gate.** Its exact-worker write-floor acceptance passed after PR #250; do not reopen solved scheduled-recheck write churn without evidence.

**SP-DEALS-DISCOVER-HARDENING-244 is suspended at its live acceptance gate, not closed.** PR #247 is merged and CI-green.

**SP-WM-MISSED-DEALS-236 remains suspended, not closed.**

## Backlog
- **SP-GATEWAY-EVENT-LOOP-LAG:** Investigate repeated Discord gateway `Can't keep up` warnings around 41 seconds behind. Trace event-loop blocking/slow DB or network work before editing; keep separate from quota work.
- **SP-SETUP-DELIVERY-UX:** Align `/setup_sniperplug_here` with `/dm_deals`; make public delivery explicit and easy to review/change.
- **SP-DM-DELIVERY-STARVATION:** Investigate matched DM deals lost to filter/duplicate suppression rather than Discord delivery errors.
- **SP-DM-NEW-LOW-ALERT:** Send a distinct meaningful lower-price alert while same-price repeats remain deduped.
- **SP-WM-REFERENCE-PRICE-RECOVERY:** Improve exact reference-price recovery without weakening public proof; current logs still show `api_price_missing`, `api_exact_search_no_match`, page challenges, and open-circuit recovery periods.
- **SP-WM-CASH-PDP-AMOUNT:** Integrate exact same-item Walmart Cash proof for private/DM enrichment without confusing OnePay cashback or using Cash as markdown proof.
- Resume #244 live acceptance after the active quota task closes.
- Resume Walmart missed-deals/page-recovery throughput after active quota work closes.
- Resume SniperPlug-Site Whop content-reader work later.
