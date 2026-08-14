# Active Task

## Status
**Active — SP-TURSO-READ-AMPLIFICATION is at the live post-deploy quota-slope gate.** PR #251 was squash-merged as `374a5d3cbd0b050a7378c3325d5cf572c78f3366` and removed the major broad Walmart exact-queue claim/pressure scan. PR #252 was squash-merged as `68ec064125205f8591062ab2a8df95e1303e8172` and is now deployed on Discloud. The first post-restart Turso dashboard anchor is **466,696,732 rows read at 6:09 PM on Aug 14**, still at 94% account quota. Startup cleanup completed without a broad/slow maintenance warning and removed exactly **1,000 stale operational rows** in bounded batches: **500 old `scan_runs` + 500 old `guild_retailer_auto_scan_runs`**. The task remains open until a clean 30-60 minute post-#252 rows-read slope proves the production rate is acceptably flattened.

For context only, the prior checkpoint was **466,177,322 rows read at 5:19 PM**. The move to the 6:09 PM anchor was **+519,410 rows in 50 minutes (~623k/hour)**, about 64% below the earlier ~1.75M/hour emergency slope. Most of that 50-minute interval occurred before the #252 deployment, so it is encouraging evidence for the earlier hardening but is **not** valid acceptance evidence for #252.

## Scope
Reduce SniperPlug's Turso rows scanned/read rate enough to remove the account-quota emergency while preserving Walmart exact-verification correctness, price intelligence, dedupe safety, user preferences, and useful learning. Remove operational data that is expired, terminal, redundant, or no longer reachable by runtime behavior. Cleanup itself must be bounded and index-seeking so it does not create another quota spike.

## Root cause / findings
- Turso's Aug 14 baseline was account-critical: **92% rows-read quota**, with SniperPlug at **455,545,327 rows read**. Later checkpoints reached **94%**.
- PR #251 fixed the repeated exact-queue selector/pressure amplification. Mature verified rows with legacy-due schedules no longer force broad status scans; live claim timings remain fast.
- Before #252, the clean post-#251 dashboard slope from 4:39 PM to 5:19 PM was **+1,166,572 rows in 40 minutes (~1.75M/hour)**, still too expensive.
- Startup `run_storage_maintenance` used generic bounded selectors whose `ORDER BY rowid` did not align with expiry/time predicates, allowing avoidable reads before finding old rows.
- `/storage_maintenance_now` performed decorative before/after `COUNT(*)` sweeps despite the cleanup result already knowing exact changed-row counts.
- `scan_runs` is execution telemetry, not price intelligence, and had a 30-day retention. It is now 7 days.
- Legacy per-server autoscan report history is now 7 days; global Walmart delivery does not depend on it.
- `guild_retailer_auto_scan_runs` had no retention despite runtime decisions only needing recent interval/today data. It is now 8 days.
- `error_events` could grow indefinitely with traceback/context payloads. It is now 30 days.
- Old `guild_public_deal_posts` rows were retained beyond the independent 30-day public duplicate guard. Posted/sending ledger retention is now 31 days.
- Terminal `cleared` active-deal rows were not included in cleanup and now are.
- Persistent feedback targets had a 10-year expiry. Runtime can restore only 250 controls, yet previously fetched hundreds more. Cleanup now keeps a bounded 500-row buffer and restoration reads only the remaining registration capacity.
- Global active-deal-history and Walmart-recheck-audit pruning used correlated COUNT-per-row cap checks. Global startup pruning is now a bounded timestamp seek; per-guild caps remain selective on explicit history access.
- DM receipts keep their existing 90-day dedupe semantics, with a selective `delivered_at` cleanup index when the table exists.
- Optional tables can appear after initial startup maintenance. Cleanup-index initialization now tracks base/optional index groups independently so later maintenance can install those indexes rather than skipping them.
- The first live #252 startup hit the configured 500-row cap in both `scan_runs` and retailer autoscan history. That proves a real stale backlog exists and also confirms the cleanup stayed bounded rather than turning deployment into an unbounded purge.
- `run_storage_maintenance` is currently invoked at startup or explicitly through `/storage_maintenance_now`; no new automatic high-frequency maintenance loop was introduced during the quota emergency.
- Price observations, product identity, exact Walmart queue state, exact Walmart offer-price memory, feedback summaries/votes, live dedupe rows, user DM settings, and clearance seeds remain outside purge scope.

## Changes live in `main` from PR #252
- Reworked storage maintenance selectors to use bounded rowid batches ordered by the same expiry/timestamp columns used by their retention predicates.
- Added quota-safe cleanup indexes for feedback targets/events, active-deal terminal rows, autoscan reports, public-post ledger rows, store-cache expiry, scan-run age, optional retailer-autoscan age, and optional DM-receipt age.
- Cleanup index installation is connection/group aware: base indexes initialize once, while optional retailer-autoscan and DM-receipt indexes can still be installed later if those tables appear after startup.
- Shortened disposable operational retention to: scan runs **7d**, autoscan reports **7d**, raw feedback click events **30d**, error events **30d**, public post ledger **31d**, retailer autoscan run history **8d**. DM receipts remain **90d**.
- Added bounded cleanup for old posted/sending public-post rows, old error events, old retailer autoscan run rows, stale DM receipts, and terminal `cleared` active-cache rows.
- Capped restart-safe feedback targets to the newest **500** rows in bounded batches while preserving feedback summaries and active vote ledgers.
- Changed persistent feedback restoration to query only the remaining registration capacity instead of fetching hundreds of rows that will be discarded.
- Removed before/after `storage_counts()` sweeps from `/storage_maintenance_now`.
- Reworked global active-deal-history and Walmart-recheck-audit pruning to bounded timestamp seeks and removed global correlated COUNT-per-row cap queries.
- Added regressions for bounded cleanup, protected price history, feedback target bounding, Turso mapping rowid aliases, no-op cleanup write avoidance, bounded history/audit pruning, static quota-safety invariants, and optional cleanup indexes appearing after startup.

## Validation completed
- PR #251 final Python 3.12 suite: **1,246 passed, 1 warning**; dependency check, compileall, import smoke, and post-merge workflows passed.
- PR #252 final Python 3.12 `Python Check`: **1,259 passed, 1 warning** (discord/audioop deprecation only).
- `pip check`, `python -m compileall -q sniperplug scripts tests`, and import smoke (**37 modules / 22 symbols**) passed.
- Separate `Python tests` workflow passed.
- All three final PR #252 workflows and all three post-merge `main` workflows passed.
- PR #252 had no inline review blockers; Qodo only reported its inactive subscription.
- PR #252 squash-merged as `68ec064125205f8591062ab2a8df95e1303e8172` and was deployed to Discloud on Aug 14.
- Live startup after deployment connected to Turso successfully, initialized schema/services, and completed storage maintenance in roughly three seconds with no broad/slow maintenance SELECT warning in the supplied log.
- Live startup cleanup changed **1,000 rows**: `old_scan_runs=500`, `old_retailer_autoscan_runs=500`, all other cleanup categories zero in that invocation.
- Persistent feedback restoration reported bounded behavior: **registered=250, cap=250** rather than loading a large discard set.
- Walmart catalog discovery continued after deployment; multiple batches completed normally.
- Walmart exact verification remained active. Supplied post-deploy claim timings were approximately **0.08s, 0.07s, and 0.03s**, with a real exact verification succeeding at 6:10 PM. The separate Walmart page circuit was open because of `page_challenge`/page-recovery throttling, not because storage cleanup disabled verification.
- A durable catalog claim left across restart was recovered once and normal catalog batches then continued.
- First clean Turso post-restart anchor recorded: **466,696,732 rows read at 6:09 PM**.
- No temporary workflow, alternate database/runtime, unbounded wipe, `VACUUM`, monkey patch, or relaxed Walmart proof behavior was introduced.

## Validation pending / Definition of Done
- Obtain a Turso rows-read checkpoint about **30 minutes after the 6:09 PM anchor** and another around **60 minutes after the anchor** if needed.
- Compute the clean post-#252 hourly rows-read rate from **466,696,732** and compare it with the pre-#252 **~1.75M rows/hour** emergency rate.
- Confirm the percentage/absolute quota movement is acceptably flattened under normal catalog and exact-verification activity.
- If the post-#252 rate is still materially high, use the new live interval as evidence to identify the next concrete reader; do not reopen the already-fast exact claim selector without matching evidence.
- The two cleanup categories that hit the 500-row batch cap indicate more stale backlog may remain. Do not turn that into an unbounded purge; if production slope remains healthy, drain additional stale batches only through the existing bounded maintenance path or a separately reviewed low-frequency continuation design.
- The task closes only when actual quota growth is acceptably flattened; deployment/CI/startup success alone are not production acceptance.

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
- **SP-AUTOSCAN-COVERAGE-STALL:** Aug 14 owner health shows Walmart catalog discovery still advancing (**265 routes, 98 full passes**), but exact verification has **1,074 due page-recovery rows with 0 new/retry, 0 scheduled rechecks, and 0 actively verifying**, plus **12,390 terminal identity-blocked rows**, **315 delayed transient retries**, and **6 current-rule eligible exact events without a durable public-post receipt**. Runtime deliberately sets page-recovery claim cap to zero while the Walmart page-challenge circuit is open, while owner queue health currently counts those page-retry rows as actionable without circuit awareness. HP and Target health also report `not_started`; their implementations/configs are standalone watcher apps rather than tasks started by the main SniperPlug bot. After the quota task unlocks, audit actual-claimable-vs-circuit-blocked health reporting, safe official-API/catalog rescue of page-retry/terminal rows, the six delivery receipts/historical decisions, route coverage semantics, and whether the HP/Target standalone apps are deployed/running. Do not weaken the Walmart anti-challenge circuit just to drain the queue.
- **SP-GATEWAY-EVENT-LOOP-LAG:** Investigate repeated Discord gateway `Can't keep up` warnings after quota work closes.
- **SP-SETUP-DELIVERY-UX:** Align `/setup_sniperplug_here` with `/dm_deals`; make public delivery explicit and easy to review/change.
- **SP-DM-DELIVERY-STARVATION:** Investigate matched DM deals lost to filter/duplicate suppression rather than Discord delivery errors.
- **SP-DM-NEW-LOW-ALERT:** Send a distinct meaningful lower-price alert while same-price repeats remain deduped.
- **SP-WM-REFERENCE-PRICE-RECOVERY:** Improve exact reference-price recovery without weakening public proof.
- **SP-WM-CASH-PDP-AMOUNT:** Integrate exact same-item Walmart Cash proof for private/DM enrichment without confusing OnePay cashback or using Cash as markdown proof.
- Resume #244 live acceptance after the active quota task closes.
- Resume Walmart missed-deals/page-recovery throughput after active quota work closes.
- Resume SniperPlug-Site Whop content-reader work later.
