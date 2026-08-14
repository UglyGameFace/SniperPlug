# Active Task

## Status
**Active — SP-AUTOSCAN-COVERAGE-STALL is at live production acceptance.** PR #253 was squash-merged to `main` as `ff12b0e57021489b9348424188dcfc81ed57f770`; final PR validation passed **1,266 tests**, dependency/compile/import checks, post-merge `main` checks were green, and GitHub reported `discloud/commit: success` for app `1779293887444`. Fresh production logs now prove the exact worker is making bounded progress while the Walmart HTML challenge circuit is open. The task remains open because deep catalog page-window telemetry is filtered out of the live log, terminal/page-retry rescue has not yet produced a successful live rescue, `/autoscan_health` has not yet been re-captured after deployment, and the HP/Target standalone watcher processes are still pending.

## Scope
Repair autoscan coverage without weakening proof or hammering Walmart HTML. Scope includes truthful backlog-vs-claimable Walmart health, bounded API-only progress during HTML challenge cooldown, strict fresh-proof rescue of page/terminal identity rows, catalog coverage beyond pages 1-2, durable per-guild delivery-decision provenance, and production activation of the separate HP/Target watcher apps. Preserve exact item/offer/seller/variant/condition/fulfillment/price proof, durable leases, dedupe, price history, and bounded Turso behavior.

## Root cause / findings
- Original live health showed **20,005 exact rows, 1,074 page-recovery rows shown actionable, 0 new/retry, 0 scheduled rechecks, 0 actively verifying, 12,390 terminal identity blocks, and 6 current-rule eligible events without a durable post receipt**.
- Catalog discovery had repeated pages 1-2 on every full pass. The merged code now derives a two-page window from durable `completed_passes` and rotates across pages 1-40 without increasing requests per route.
- The exact worker previously set the page-recovery claim cap to zero while the HTML challenge circuit was open even though the shared fetcher already had an API-only exact-detail/exact-search path.
- Strict fresh catalog proof could rescue page retries but terminal `incomplete_identity` / `identity_mismatch` rows were not immediately rearmed when complete current official proof returned.
- Old delivery health recomputed historical events with current rules because per-guild historical fanout decisions were not persisted.
- HP and Target are intentionally separate Discloud producer apps (`hp_watcher_main.py`, `target_watcher_main.py`) writing to the same Turso DB; the main Discord process cannot make a `not_started` watcher run.

## Changes live from PR #253
- Keep a **2-row** `page_retry` lane claimable while the page circuit is open; those claims use official API/exact-search only and never call Walmart HTML.
- Full queue health labels page recovery as a due backlog and reports recovery mode, per-batch cap, claimable-this-batch count, and HTML cooldown.
- Strict fresh official catalog snapshots may immediately rearm `incomplete_identity` / `identity_mismatch`; weak/incomplete proof remains terminal.
- Full catalog passes rotate through two-page windows across pages **1-40**; request volume per route remains two pages.
- Added bounded 31-day `walmart_guild_fanout_decisions` provenance so current delivery health can distinguish real unexplained gaps from historical threshold/category/quality/dedupe decisions.
- HP/Target `not_started` health now points to the exact standalone entrypoint and shared-Turso requirement.

## Fresh live evidence from Aug 14, post-deploy
- The merged runtime is active: startup reports the global architecture flags used by #253, including `terminal_identity_quarantine=true`, `bulk_exact_persistence=true`, `catalog_discovery_only=true`, `bounded_claim_steps=true`, `fixed_rate_exact_worker=true`, and `background_full_queue_scans=false`.
- Catalog routes continue advancing live: examples move through **247-248**, **249-250**, **251-252**, **253-254**, **255-256** of 265 on pass 99 while `completed full passes` remains 98 until the route pool wraps.
- **Open-circuit page-recovery lane is working as designed.** When the old page backlog is the only bounded due lane, live batches repeatedly claim exactly **2** rows: 19:31:25, 19:37:13, 19:37:34, and 19:41:38 all show `claimed 2`, `page circuit open`, `page suppressed 2`, and `actionable due 64`. This is the live counterpart of the two-row API-only cap.
- Larger `claimed 8` / `claimed 24` open-circuit batches do **not** prove the page-retry cap was bypassed. The live pressure lines show fresh/new/retry work was also due (for example new/retry 39 with page recovery >=64). Those non-page rows are allowed one official API/exact-search attempt; if exact seller/offer proof remains incomplete they become `page_retry` and later fall under the 2-row cap.
- **Coverage no longer stops when HTML is blocked.** With the circuit open, strict fresh catalog snapshots continue verifying without page I/O: live examples include **24/24**, **8/8**, **8/8**, **8/8**, **24/24**, **24/24**, and later **24/24** fresh-proof verification batches, commonly with fetch time near 0.00-0.01s.
- The circuit remains fail-closed: open-circuit rows that cannot prove seller/offer through official API/exact-search return `page_circuit_open`; challenged HTML is reported as suppressed rather than used for proof.
- Page-recovery success is **not yet proven**: catalog notes still show `page retries rescued 0` and the two-row API-only recovery attempts in this sample returned `page_circuit_open` rather than a verified rescue.
- Terminal rescue is **not yet proven live**: catalog notes show `terminal identity rescued 0` in this sample. That is not a failure by itself; it means none of the rediscovered rows in these specific route batches matched a previously terminal row with complete current strict proof.
- A separate runtime concern appeared once: Discord gateway reported **41.3s behind** at 19:37:11. Keep this recorded as `SP-GATEWAY-EVENT-LOOP-LAG`; do not switch implementation tasks unless explicitly requested or this is proven to be caused by the active autoscan path.
- Two application restarts occurred during the merge/docs-deploy window. Avoid further docs-only commits to `main` while collecting live acceptance so observation is not reset unnecessarily.

## Live observability defect found
`walmart_catalog_discovery_only.py` already emits a note such as `Walmart catalog page window: pages 37-38/40 ...`, but `AutoScanRunnerCog._log_global_result_notes()` only forwards notes containing `exact-detail queue` or `catalog discovery-only pass`. Therefore the actual page-window note is silently filtered from production logs. This prevents direct live proof of the deeper page window even though the rotation code and durable state are present. If a follow-up code change is made, fix this logging filter and add a regression before merging; do not change catalog request volume or page-selection behavior.

## Validation already completed
- Focused regressions cover: open-circuit two-row API-only recovery; strict terminal rescue/fail-closed behavior; catalog page-window rotation/wrap; truthful backlog-vs-cap health; durable fanout-decision round-trip/classification; and actionable HP/Target startup health.
- Final PR Python 3.12 run: **1,266 passed, 1 warning** (discord/audioop deprecation only).
- `pip check`, compileall, and import smoke (**37 modules / 22 symbols**) passed.
- PR #253 had no code-review blocker; Qodo only reported inactive subscription.
- Post-merge main checks passed and Discloud commit status succeeded.

## Remaining Definition of Done
1. Fix/verify live page-window observability, then capture a catalog batch that explicitly shows a page window deeper than 1-2. At `completed_passes=98`, the deterministic current window is expected to be pages **37-38**; do not claim that as live-observed until the runtime log exposes it.
2. Re-run `/autoscan_health` after deployment and capture the full queue line. It must show the true page-recovery backlog separately from claimable-this-batch **2**, current `api_only` mode while the circuit is open, and the terminal identity count.
3. Observe at least one successful `page retries rescued > 0` or equivalent verified API-only page-recovery result when Walmart exposes sufficient official identity again. Do not weaken proof merely to force this counter.
4. Observe `terminal identity rescued > 0` when a previously terminal item is rediscovered with complete strict fresh official proof. Do not fabricate/rearm rows just to satisfy acceptance.
5. Confirm delivery health no longer labels pre-provenance legacy events as confirmed missed posts; new unexplained gaps must have persisted historical decision provenance.
6. Start `SniperPlug-HP-Watcher` against the same Turso DB and confirm health changes from `not_started` to a real cycle state.
7. Start `SniperPlug-Target-Watcher` against the same Turso DB with its required `TARGET_REDSKY_API_KEY` and confirm health changes from `not_started` to a real cycle state.
8. Only then close SP-AUTOSCAN-COVERAGE-STALL.

## Safety constraints
- Do not disable or weaken the Walmart page-challenge circuit.
- Do not use Walmart HTML while the circuit is open.
- Do not promote terminal rows from weak relevance/search text; only strict fresh selected-offer proof or the normal exact proof path may rescue them.
- Do not increase Walmart catalog requests per route/pass; coverage depth must rotate within the existing two-page request budget.
- Do not delete price history, offer memory, queue identity history, dedupe, user settings, or feedback summaries to improve health counters.
- Do not run duplicate HP/Target watcher owners inside the Discord bot.

## Suspended tasks
- **SP-TURSO-READ-AMPLIFICATION** — suspended at post-#252 live quota-slope gate; last clean anchor **466,696,732 rows read at 6:09 PM Aug 14**.
- **SP-TURSO-WRITE-AMPLIFICATION** — suspended at final rows-written slope gate.
- **SP-DEALS-DISCOVER-HARDENING-244** — suspended at live acceptance, not closed.
- **SP-WM-MISSED-DEALS-236** — suspended, not closed.

## Backlog
- **SP-GATEWAY-EVENT-LOOP-LAG**
- **SP-SETUP-DELIVERY-UX**
- **SP-DM-DELIVERY-STARVATION**
- **SP-DM-NEW-LOW-ALERT**
- **SP-WM-REFERENCE-PRICE-RECOVERY**
- **SP-WM-CASH-PDP-AMOUNT**
- Resume quota slope acceptance after this task closes.
- Resume #244 live acceptance after active autoscan work closes.
- Resume SniperPlug-Site Whop content-reader work later.
