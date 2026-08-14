# Active Task

## Status
**Active — SP-TURSO-READ-AMPLIFICATION is now at the post-deploy quota-slope gate.** PR #251 was squash-merged as `374a5d3cbd0b050a7378c3325d5cf572c78f3366` and removed the major broad Walmart exact-queue claim/pressure scan. The deployed exact worker then claimed in roughly 0.03-0.18s while continuing to verify real work, but the clean Turso dashboard checkpoints still rose from **465,010,750 rows read at 4:39 PM** to **466,177,322 at 5:19 PM**: **+1,166,572 rows in 40 minutes (~1.75M/hour)** at 94% account quota. PR #252 has now been squash-merged to `main` as `68ec064125205f8591062ab2a8df95e1303e8172`; its PR checks and all three post-merge `main` workflows passed. The second hardening pass removes stale operational data and makes cleanup/restore reads bounded and index-seeking. Remaining acceptance is deployment plus a fresh live Turso rows-read slope measurement.

## Scope
Reduce SniperPlug's Turso rows scanned/read rate enough to remove the account-quota emergency while preserving Walmart exact-verification correctness, price intelligence, dedupe safety, user preferences, and useful learning. Remove operational data that is expired, terminal, redundant, or no longer reachable by runtime behavior. Cleanup itself must be bounded and index-seeking so it does not create another quota spike.

## Root cause / findings
- Turso's Aug 14 baseline was account-critical: **92% rows-read quota**, with SniperPlug at **455,545,327 rows read**. Later checkpoints reached **94%**.
- PR #251 fixed the repeated exact-queue selector/pressure amplification. Mature verified rows with legacy-due schedules no longer force broad status scans; live claim timings confirm that path is now fast.
- The remaining clean post-deploy slope was still high enough to justify inspecting other real readers rather than reopening the solved exact selector.
- Startup `run_storage_maintenance` was still using generic `SELECT rowid ... WHERE ... ORDER BY rowid LIMIT ?` selectors. Even though deletes were capped at 500 rows, `ORDER BY rowid` did not align with expiry/time predicates and could force avoidable scans before finding old rows.
- `/storage_maintenance_now` performed a full server-specific `storage_counts()` sweep **before and after** cleanup. Those decorative `COUNT(*)` reads were unnecessary during a quota emergency because the cleanup result already knows exact changed-row counts.
- `scan_runs` is execution telemetry, not price intelligence, yet was retained for 30 days. Startup already hit the exact 500-row cleanup cap, proving a backlog exists. Seven days is sufficient for current diagnostics.
- Legacy per-server autoscan report history was retained for 14 days even though global Walmart delivery no longer depends on it; seven days retains recent troubleshooting value.
- `guild_retailer_auto_scan_runs` had no retention at all. Its only runtime uses are today's run count and an interval gate whose maximum configured interval is seven days, so rows older than eight days are no longer decision-relevant.
- `error_events` stores potentially large traceback/context payloads and had no retention. Thirty days preserves useful troubleshooting while preventing indefinite growth.
- `guild_public_deal_posts` retained normal `posted` rows indefinitely even though the independent `alert_dedupe` guard expires normal public duplicate protection after 30 days. Posted/sending ledger rows older than 31 days are redundant.
- Active deal rows marked `cleared` were terminal but never included in terminal cache cleanup.
- Feedback target rows were set to a 10-year expiry. Startup restored at most 250 persistent feedback views but fetched up to 750 targets first; the live log showed **250 registered / 501 skipped_old**. Rows beyond the bounded restore surface are not useful after restart. The new cleanup keeps a 500-row buffer and restoration now queries only the capacity it can register.
- Global `prune_active_deal_history` and `prune_walmart_recheck_audit` each used a correlated `COUNT(*)` per candidate row to enforce per-guild caps. That is unnecessary at startup and can grow toward quadratic work. Global startup maintenance only needs a bounded age prune; per-guild caps can be enforced selectively when that guild's history is explicitly opened.
- DM receipts keep their existing 90-day dedupe semantics, but stale receipt cleanup now gets a standalone `delivered_at` cleanup index when the table exists.
- A final conflict/safety inspection found one lifecycle edge case before merge: optional tables such as retailer autoscan history or DM receipts can be created after the first startup maintenance call. Cleanup-index initialization now tracks base/optional index groups independently, so a later maintenance pass can install the missing selective index instead of returning early and scanning the newly-created table without one.
- Price observations, product identity, exact Walmart verification queue state, exact Walmart offer-price memory, feedback summaries/votes, live dedupe rows, user DM settings, and clearance seeds are explicitly outside this purge scope.

## Changes live in `main` from PR #252
- Reworked storage maintenance selectors to use bounded rowid batches ordered by the same expiry/timestamp columns used by their retention predicates.
- Added quota-safe cleanup indexes for feedback targets/events, active deal terminal rows, autoscan reports, public-post ledger rows, store-cache expiry, scan-run age, optional retailer autoscan run age, and optional DM receipt age.
- Cleanup index installation is connection/group aware: base indexes initialize once, while optional retailer-autoscan and DM-receipt indexes can still be installed later if those tables appear after startup.
- Shortened disposable operational retention to: scan runs **7d**, autoscan reports **7d**, raw feedback click events **30d**, error events **30d**, public post ledger **31d**, retailer autoscan run history **8d**. DM receipts remain **90d**.
- Added bounded cleanup for old posted/sending public-post rows, old error events, old retailer autoscan run rows, stale DM receipts, and terminal `cleared` active-cache rows.
- Capped restart-safe feedback targets to the newest **500** rows in bounded batches while preserving feedback summaries and active vote ledgers.
- Changed persistent feedback restoration to query only the remaining registration capacity instead of fetching hundreds of rows that will be discarded.
- Removed before/after `storage_counts()` sweeps from `/storage_maintenance_now`; the command now reports exact cleanup-result counts and explicitly skips quota-wasting count scans.
- Reworked global active-deal history and Walmart recheck-audit pruning to bounded timestamp seeks. Removed the global correlated COUNT-per-row cap queries. Per-guild cap enforcement remains selective and bounded on explicit history access.
- Added focused regressions for bounded operational cleanup, protected price history, feedback target bounding, Turso mapping rowid aliases, no-op cleanup write avoidance, bounded history/audit pruning, static quota-safety invariants, and optional cleanup indexes appearing after startup.

## Validation completed
- PR #251 final Python 3.12 suite: **1,246 passed, 1 warning** (discord/audioop deprecation only); dependency check, compileall, and import smoke passed, and all post-merge `main` workflows passed.
- Post-deploy exact-worker claim timing after #251 stayed fast and exact verification remained active; no recurrence of the prior 4.03s queue-claim warning appeared in the supplied window.
- PR #252 final branch was **0 commits behind** its `main` merge base and changed **13 files**, all scoped to the task record, storage cleanup/admin behavior, bounded history/audit pruning, bounded feedback restoration, and regressions.
- The first PR #252 full-suite pass exposed one stale static regression that still required the old in-memory `targets[:remaining]` slice. The production optimization intentionally moved that bound into the database query itself, so the stale test was updated narrowly rather than restoring the wasteful fetch-and-discard behavior.
- Final Python 3.12 `Python Check` passed **1,259 tests, 1 warning** (discord/audioop deprecation only).
- `pip check` passed.
- `python -m compileall -q sniperplug scripts tests` passed.
- Import smoke passed **37 modules / 22 symbols**.
- The separate `Python tests` workflow passed.
- All three final PR #252 workflows passed: Python Check, Python tests, and Import smoke check.
- PR #252 had **no inline review threads** or submitted review blockers. The only discussion item was Qodo's inactive-subscription notice, not a code finding.
- PR #252 squash-merged as `68ec064125205f8591062ab2a8df95e1303e8172`.
- All three push workflows on merged `main` passed: Python Check, Python tests, and Import smoke check.
- No temporary workflow, alternate database/runtime, unbounded wipe, `VACUUM`, monkey patch, or relaxed Walmart proof behavior was introduced.

## Validation pending / Definition of Done
- Deploy merged `main` commit `68ec064125205f8591062ab2a8df95e1303e8172` to Discloud.
- Confirm startup cleanup completes with bounded changed-row counts and without broad/slow maintenance SELECT warnings.
- Confirm persistent feedback restoration now reads/registers only the bounded capacity instead of loading hundreds of discarded targets.
- Confirm Walmart catalog discovery, exact verification, fanout, and DM/public dedupe remain active after cleanup.
- Use the first Turso dashboard reading after deployment as the new clean slope anchor, then obtain another checkpoint after a meaningful interval.
- Compare the new clean rows-read rate with the pre-#252 **~1.75M rows/hour** rate.
- The task closes only when actual quota growth is acceptably flattened; merge/CI alone are not production acceptance.

## Cleanup / safety constraints
- No `VACUUM`, unbounded emergency wipe, alternate database, stale safety-critical cache, monkey patch, or disabled Walmart verification.
- No deletion of `price_observations`, `product_identity`, `walmart_offer_price_memory`, live exact queue state, feedback summaries/votes, unexpired alert dedupe, or user delivery settings.
- Cleanup stays bounded per invocation and uses retention/index order instead of whole-table counts.
- Public proof gates, exact seller/item/offer/variant/condition/fulfillment proof, page-recovery semantics, durable leases, price history, fanout, and delivery dedupe remain intact.

## Blockers
- Direct Turso top-query/organization read metrics are not exposed through the connected tools, so final quota-rate acceptance still requires user dashboard checkpoints after deployment.

## Suspended tasks
**SP-TURSO-WRITE-AMPLIFICATION is suspended at its final dashboard rows-written slope gate.** Its exact-worker write-floor acceptance passed after PR #250; do not reopen solved scheduled-recheck write churn without evidence.

**SP-DEALS-DISCOVER-HARDENING-244 is suspended at its live acceptance gate, not closed.** PR #247 is merged and CI-green.

**SP-WM-MISSED-DEALS-236 remains suspended, not closed.**

## Backlog
- **SP-GATEWAY-EVENT-LOOP-LAG:** Investigate repeated Discord gateway `Can't keep up` warnings around 41 seconds behind after quota work closes.
- **SP-SETUP-DELIVERY-UX:** Align `/setup_sniperplug_here` with `/dm_deals`; make public delivery explicit and easy to review/change.
- **SP-DM-DELIVERY-STARVATION:** Investigate matched DM deals lost to filter/duplicate suppression rather than Discord delivery errors.
- **SP-DM-NEW-LOW-ALERT:** Send a distinct meaningful lower-price alert while same-price repeats remain deduped.
- **SP-WM-REFERENCE-PRICE-RECOVERY:** Improve exact reference-price recovery without weakening public proof.
- **SP-WM-CASH-PDP-AMOUNT:** Integrate exact same-item Walmart Cash proof for private/DM enrichment without confusing OnePay cashback or using Cash as markdown proof.
- Resume #244 live acceptance after the active quota task closes.
- Resume Walmart missed-deals/page-recovery throughput after active quota work closes.
- Resume SniperPlug-Site Whop content-reader work later.
