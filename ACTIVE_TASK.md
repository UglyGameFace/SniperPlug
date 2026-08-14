# Active Task

## Status
**Active — SP-AUTOSCAN-COVERAGE-STALL is at the merge/deploy/live-acceptance gate.** User explicitly FORCE SWITCHED on Aug 14 after `/autoscan_health` showed Walmart catalog discovery advancing but exact coverage badly misleading/stalled: **20,005 queue rows, 1,074 page-recovery rows shown actionable, 0 new/retry, 0 scheduled rechecks, 0 actively verifying, 12,390 terminal identity blocks, and 6 current-rule eligible exact events without a durable public-post receipt**. HP Store and Target health both reported `not_started`. PR #253 now contains the root-cause fixes on `fix/autoscan-coverage-stall`; final code head `20f30c795540c9eb262f11a93e49d2d2abe57368` passed all three PR workflows and the full **1,266-test** suite. The task remains open through merge, Discloud deployment, Walmart live acceptance, and standalone HP/Target watcher activation.

## Scope
Repair the actual autoscan coverage path without weakening proof or hammering Walmart HTML. The task covers truthful claimable-vs-backlog Walmart health, safe progress while the Walmart HTML challenge circuit is open, immediate rescue of terminal/page-retry rows when strict fresh official catalog proof becomes available, catalog discovery beyond the same first two search pages, durable/historical public delivery decision visibility, and an explicit production path for HP/Target watcher activation. Preserve exact item/offer/seller/variant/condition/fulfillment/price proof, durable leases, dedupe, price history, and bounded Turso behavior.

## Root cause / findings
- Walmart global catalog discovery was alive. The supplied health screen showed **265 routes** and **98 completed full passes**.
- Catalog discovery was not deepening: every route requested only **pages 1 and 2 on every pass**. Walmart Affiliate search is bounded to the first **1,000 results** and SniperPlug requests 25/page, so 40 pages are available but 98 full passes repeatedly revisited the same shallow window.
- Owner full queue health counted every due `page_retry` row as immediately actionable without knowing the provider-scoped HTML challenge circuit state.
- The exact worker did know the circuit state and set `page_recovery_claim_cap=0` while it was open. That produced the live contradiction: **1,074 due page-recovery rows** in health while the worker could claim **0** from that lane.
- `_fetch_exact_candidate_off_loop` already had a safe API-only open-circuit path using official item detail and exact Affiliate-search refresh while skipping Walmart HTML. The zero claim cap prevented the old page-retry backlog from using that safe path.
- Strict fresh catalog snapshots already require exact item ID, payable price, selected offer, seller, variant checks, and required marketplace shipping/delivered-price proof. Bulk enqueue used those snapshots to rescue `page_retry`, but did not rescue terminal `incomplete_identity` / `identity_mismatch` rows even when current official proof now supplied the missing identity.
- The fallback terminal maintenance path intentionally rearms only a tiny weekly rediscovery sample. That remains useful as a safety net but cannot be the primary recovery mechanism for **12,390** terminal rows.
- The six `eligible_without_post` rows were not proof of six missed posts. The old read-only audit rebuilt historical events using **current** threshold/category/quality rules and checked the post ledger, but fanout did not persist the per-guild reason that applied when each event originally ran. Old legitimate threshold/category/dedupe decisions could therefore be mislabeled later as unexplained missing receipts.
- HP and Target are real production watcher services but intentionally run as **separate Discloud applications**. Main SniperPlug starts `main.py`; HP starts `hp_watcher_main.py`; Target starts `target_watcher_main.py`. Their `not_started` state means those standalone producers have not reported a production cycle to the shared Turso database.

## Changes implemented in PR #253
- **Circuit-safe page recovery:** production keeps a two-row `page_retry` claim lane while the HTML challenge circuit is open. The existing fetch path switches those claims to official API/exact-search-only recovery; challenged Walmart HTML remains completely suppressed.
- **Truthful exact health:** the 1,074-style number is now labeled a **due backlog**, and health reports page-recovery mode, per-batch cap, actual claimable-this-batch count, and remaining HTML cooldown when applicable.
- **Terminal rescue from strict proof:** `incomplete_identity` and `identity_mismatch` rows are immediately rearmed to `pending` only when the existing strict fresh official catalog snapshot builder succeeds. Missing seller/offer or unsafe marketplace proof leaves the row terminal. Telemetry now reports `terminal identity rescued`.
- **Deep catalog coverage without more requests:** full catalog passes rotate through two-page windows across pages **1-40**, using the durable global `completed_passes` counter. Two pages per route remains unchanged, so network rate does not multiply. Twenty completed full passes cover the entire bounded 1,000-result search window before wrapping.
- **Historical public decision provenance:** added bounded `walmart_guild_fanout_decisions` storage with event/guild outcome, threshold, detail, and timestamp; indexed by guild/time and retained 31 days. Fanout records posted, threshold, category, quality, duplicate, disabled, retailer, and error outcomes before marking an event processed.
- **Read-only delivery audit remains read-only:** health reads historical destination decisions when they exist and never creates/mutates provenance. New events with a stored unexplained outcome can be called real gaps; pre-provenance legacy events that current rules happen to allow are labeled `legacy decision unknown` instead of falsely called missed posts.
- **Bounded provenance writes/cleanup:** destination decisions use bulk upserts, schema initialization is cached per live connection, and old provenance prunes in bounded batches on the existing low-frequency fanout cleanup cadence.
- **Actionable HP/Target health:** `not_started` now states that `SniperPlug-HP-Watcher` / `SniperPlug-Target-Watcher` must be deployed/running with `hp_watcher_main.py` / `target_watcher_main.py` against the exact same Turso DB. No duplicate watcher loop was added to the Discord bot.

## Validation completed
- Added focused regressions for: open-circuit two-row API-only page recovery; strict terminal identity rescue; fail-closed incomplete catalog proof; persistent two-page catalog window rotation and wrap; truthful 1,074-vs-2 queue health; durable fanout-decision round trip/classification; and actionable HP/Target not-started health.
- Existing page-circuit regressions still prove the shared fetcher suppresses Walmart HTML while the circuit is open and keeps official API recovery available.
- Existing low-level claim regression still proves a caller can explicitly set page recovery cap to zero without rewriting page-retry rows; production simply no longer uses zero during the HTML circuit because its API-only path is safe.
- First full PR run exposed **4 failures / 1,262 passes**. All four were traced from the actual CI log before changes: one stale queue-health wording assertion, one stale static `read-only audit` phrase, one delivery-health fake that did not model the new read-only provenance query, and one fanout-schema test that still expected only the old cursor index. No compile/import/runtime implementation failure was hidden by those tests.
- The fixes kept delivery health genuinely read-only, updated legacy-event expectations to the new non-overclaiming semantics, and updated schema regression coverage for the provenance table/index plus the existing cursor index.
- Final Python 3.12 `Python Check` on code head `20f30c795540c9eb262f11a93e49d2d2abe57368`: **1,266 passed, 1 warning** (discord/audioop deprecation only).
- `pip check` passed with **No broken requirements found**.
- `python -m compileall -q sniperplug scripts tests` passed.
- Import smoke passed **37 modules / 22 symbols**.
- Separate `Python tests` workflow passed.
- All three final PR workflows passed: Python Check, Python tests, and Import smoke check.
- PR is mergeable, **19 commits ahead / 0 behind** its `main` base at the final code-validation checkpoint. Changed files are limited to the active task record, Walmart catalog/exact/health/fanout paths, HP/Target health text, and regressions.
- PR discussion has no code-review blocker; the only comment is Qodo's inactive-subscription notice.

## Validation pending / Definition of Done
- Re-run all PR checks after this task-record-only update and squash-merge PR #253 only if they remain green.
- Verify merged `main` workflows remain green.
- Deploy merged `main` to the existing SniperPlug Discloud app.
- Live Walmart acceptance must show:
  - catalog batches continue advancing and log rotating page windows deeper than the old fixed pages 1-2;
  - if the Walmart HTML circuit is open, queue health reports the page backlog separately from the **2-row claimable API-only batch**;
  - exact cycles can claim/process bounded `page_retry` work during the HTML cooldown without generating challenged HTML requests;
  - fresh strict catalog proof can produce `terminal identity rescued > 0` when previously blocked rows are rediscovered with complete current proof;
  - public/delivery dedupe and exact proof behavior remain intact;
  - legacy pre-provenance events are no longer presented as confirmed missing posts merely because today's rules allow them.
- HP acceptance requires the standalone `SniperPlug-HP-Watcher` Discloud process to run against the exact same Turso credentials and health to change from `not_started` to a real cycle state.
- Target acceptance requires the standalone `SniperPlug-Target-Watcher` process, the same Turso credentials, and its required `TARGET_REDSKY_API_KEY`; health must change from `not_started` to a real cycle state.
- The task closes only after those live production checks; green CI/merge alone is not production acceptance.

## Safety constraints
- Do not disable or weaken the Walmart page-challenge circuit.
- Do not use Walmart HTML while the circuit is open.
- Do not promote a terminal row from weak relevance/search text; only strict fresh selected-offer proof or the normal exact proof path may rescue it.
- Do not increase Walmart catalog requests per route/pass; coverage depth rotates within the existing two-page request budget.
- Do not delete price history, offer memory, queue identity history, live dedupe, user settings, or feedback summaries to make health look better.
- Do not run duplicate HP/Target watcher owners inside the Discord bot while standalone deployment remains the architecture.

## Suspended tasks
**SP-TURSO-READ-AMPLIFICATION is suspended at its live post-#252 quota-slope gate.** Last clean anchor: **466,696,732 rows read at 6:09 PM Aug 14**. Resume only after this task closes or an exact FORCE SWITCH.

**SP-TURSO-WRITE-AMPLIFICATION is suspended at its final dashboard rows-written slope gate.**

**SP-DEALS-DISCOVER-HARDENING-244 is suspended at its live acceptance gate, not closed.**

**SP-WM-MISSED-DEALS-236 remains suspended, not closed.**

## Backlog
- **SP-GATEWAY-EVENT-LOOP-LAG**
- **SP-SETUP-DELIVERY-UX**
- **SP-DM-DELIVERY-STARVATION**
- **SP-DM-NEW-LOW-ALERT**
- **SP-WM-REFERENCE-PRICE-RECOVERY**
- **SP-WM-CASH-PDP-AMOUNT**
- Resume quota slope acceptance after this task closes.
- Resume #244 live acceptance after active quota/autoscan work closes.
- Resume SniperPlug-Site Whop content-reader work later.
