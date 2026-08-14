# Active Task

## Status
**Active — SP-TURSO-READ-AMPLIFICATION.** PR #251 was already squash-merged as `374a5d3cbd0b050a7378c3325d5cf572c78f3366` and removed the major broad Walmart exact-queue claim/pressure scan. The deployed exact worker now claims in roughly 0.03-0.18s in the supplied live sample while continuing to verify real work. However, the clean Turso dashboard checkpoints still showed SniperPlug rising from **465,010,750 rows read at 4:39 PM** to **466,177,322 at 5:19 PM**: **+1,166,572 rows in 40 minutes (~1.75M/hour)**. That is still too expensive at 94% account quota, so the active task remains open. A second evidence-based hardening pass is now in progress on branch `fix/turso-quota-safe-storage-cleanup`, targeting stale operational history and cleanup/restore paths that were still capable of broad or unnecessary reads.

## Scope
Reduce SniperPlug's Turso rows scanned/read rate enough to remove the account-quota emergency while preserving Walmart exact-verification correctness, price intelligence, dedupe safety, user preferences, and useful learning. Remove operational data that is expired, terminal, redundant, or no longer reachable by runtime behavior. Cleanup itself must be bounded and index-seeking so it does not create another quota spike.

## Root cause / findings
- Turso's Aug 14 baseline was account-critical: **92% rows-read quota**, with SniperPlug at **455,545,327 rows read**. Later checkpoints reached **94%**.
- PR #251 fixed the repeated exact-queue selector/pressure amplification. Mature verified rows with legacy-due schedules no longer force broad status scans; live claim timings confirm that path is now fast.
- The remaining clean post-deploy slope is still high enough to justify inspecting other real readers rather than reopening the solved exact selector.
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
- Price observations, product identity, exact Walmart verification queue state, exact Walmart offer-price memory, feedback summaries/votes, live dedupe rows, user DM settings, and clearance seeds are explicitly outside this purge scope.

## Changes on `fix/turso-quota-safe-storage-cleanup`
- Reworked storage maintenance selectors to use bounded rowid batches ordered by the same expiry/timestamp columns used by their retention predicates.
- Added quota-safe cleanup indexes for feedback targets/events, active deal terminal rows, autoscan reports, public-post ledger rows, store-cache expiry, scan-run age, optional retailer autoscan run age, and optional DM receipt age.
- Shortened disposable operational retention to: scan runs **7d**, autoscan reports **7d**, raw feedback click events **30d**, error events **30d**, public post ledger **31d**, retailer autoscan run history **8d**. DM receipts remain **90d**.
- Added bounded cleanup for old posted/sending public-post rows, old error events, old retailer autoscan run rows, stale DM receipts, and terminal `cleared` active-cache rows.
- Capped restart-safe feedback targets to the newest **500** rows in bounded batches while preserving feedback summaries and active vote ledgers.
- Changed persistent feedback restoration to query only the remaining registration capacity instead of fetching hundreds of rows that will be discarded.
- Removed before/after `storage_counts()` sweeps from `/storage_maintenance_now`; the command now reports exact cleanup-result counts and explicitly skips quota-wasting count scans.
- Reworked global active-deal history and Walmart recheck-audit pruning to bounded timestamp seeks. Removed the global correlated COUNT-per-row cap queries. Per-guild cap enforcement remains selective and bounded on explicit history access.
- Added focused regressions for bounded operational cleanup, protected price history, feedback target bounding, Turso mapping rowid aliases, no-op cleanup write avoidance, bounded history/audit pruning, and static quota-safety invariants.

## Validation completed before this pass
- PR #251 final Python 3.12 suite: **1,246 passed, 1 warning** (discord/audioop deprecation only).
- `pip check`, `python -m compileall -q sniperplug scripts tests`, and import smoke passed for PR #251.
- All post-merge `main` workflows for #251 passed.
- Post-deploy exact-worker claim timing stayed fast and exact verification remained active; no recurrence of the prior 4.03s queue-claim warning appeared in the supplied window.

## Validation pending / Definition of Done for cleanup pass
- Open a scoped PR from `fix/turso-quota-safe-storage-cleanup` and inspect every changed file for unrelated behavior.
- Run targeted cleanup/history tests plus the full Python suite, dependency check, compileall, and import smoke through CI.
- Verify no tests reveal hidden callers that still depend on the removed long-retention operational rows.
- Verify PR review threads/checks are clean and no temporary workflows or alternate runtime were introduced.
- After merge/deploy, confirm startup cleanup runs without broad/slow maintenance SELECT warnings and still preserves price/history intelligence required by deal detection.
- Obtain a new clean Turso dashboard rows-read slope after deployment. The task closes only when actual quota growth is acceptably flattened; edits/CI alone are not production acceptance.

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
