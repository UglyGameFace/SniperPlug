# Active Task

## Status
**Active — SP-AUTOSCAN-COVERAGE-STALL.** User explicitly FORCE SWITCHED on Aug 14 after `/autoscan_health` showed Walmart catalog discovery advancing but exact coverage badly misleading/stalled: **20,005 queue rows, 1,074 page-recovery rows shown actionable, 0 new/retry, 0 scheduled rechecks, 0 actively verifying, 12,390 terminal identity blocks, and 6 current-rule eligible exact events without a durable public-post receipt**. HP Store and Target health both report `not_started`. Implementation work is on `fix/autoscan-coverage-stall`; do not resume quota work or other backlogs until this task closes or another exact FORCE SWITCH is received.

## Scope
Repair the actual autoscan coverage path without weakening proof or hammering Walmart HTML. The task covers: truthful claimable-vs-blocked Walmart health, safe progress while the Walmart HTML challenge circuit is open, immediate rescue of terminal/page-retry rows when strict fresh official catalog proof becomes available, catalog discovery beyond the same first two search pages, durable/historical public delivery decision visibility, and an explicit production path for HP/Target watcher activation. Preserve exact item/offer/seller/variant/condition/fulfillment/price proof, durable leases, dedupe, price history, and bounded Turso behavior.

## Root cause / findings
- Walmart global catalog discovery is alive. The supplied health screen showed **265 routes** and **98 completed full passes**.
- Catalog discovery is **not exhaustive**: each route currently requests only **pages 1 and 2 on every pass**. The Walmart Affiliate search provider supports pagination up to the first **1,000 results** (25/page), but autoscan never advances beyond page 2. Repeating 98 full passes therefore repeats the same shallow result window rather than exploring deeper route results.
- The owner-facing full queue health query counts every due `page_retry` row as actionable. It has no knowledge of the provider-scoped HTML challenge circuit.
- The production exact worker does know about the circuit and currently sets `page_recovery_claim_cap=0` while the circuit is open. That makes the 1,074 due page-recovery rows look actionable in `/autoscan_health` while none can actually be claimed from that lane.
- `_fetch_exact_candidate_off_loop` already has a safe **API-only bypass** for an open page circuit: it uses official item detail and exact Affiliate-search refresh and skips Walmart HTML. The zero page-recovery claim cap prevents the existing backlog from benefiting from that API-only rescue path.
- Strict fresh catalog snapshots are already strong enough to be promoted without a network refetch: exact item ID, payable price, selected offer, seller, variant checks, and marketplace delivered-price/shipping checks are revalidated.
- Bulk enqueue already re-arms `page_retry` when such a fresh snapshot arrives, but **terminal `incomplete_identity` / `identity_mismatch` rows are not re-armed by fresh strict proof**. They remain quarantined until a low-volume weekly rediscovery reprobe even when current official catalog proof now supplies the identity that was previously missing.
- Terminal maintenance intentionally rearms only 4 rows every 15 minutes and only after age + rediscovery. That is safe as a fallback, but it cannot be the primary rescue path for 12,390 terminal rows.
- The 6 `eligible_without_post` rows are not yet proof of six live delivery failures. The current audit reconstructs **today's** threshold/category/quality rules and checks the current public-post ledger, but fanout does not persist the historical per-guild decision for every processed event. A prior below-threshold/category/duplicate decision can therefore be misreported later as “eligible without receipt.” Historical destination decisions need durable, bounded provenance before autoscan health can call this a delivery problem confidently.
- HP and Target watcher implementations are real production services but intentionally run as **separate Discloud applications**. The main SniperPlug process only starts `main.py`; the watcher configs use `hp_watcher_main.py` and `target_watcher_main.py`. Their `not_started` health therefore means those standalone producers are not currently writing health/candidate state into the shared Turso database (or are not deployed/running against that DB). The main bot cannot make those producers run merely by showing their health.

## Planned changes
1. Let a tiny bounded page-retry lane continue during an open HTML circuit, but force it through the existing API-only bypass so no challenged HTML request is made.
2. Make owner health distinguish **due page recovery** from **claimable now** and explicitly show circuit-blocked recovery instead of presenting all due rows as immediately actionable.
3. Re-arm terminal identity rows immediately when a complete strict fresh official catalog snapshot arrives; keep weekly terminal reprobe as fallback only.
4. Add persistent catalog page-window rotation so full route passes advance beyond pages 1-2 and eventually cover the provider's bounded 1,000-result search window without multiplying per-server scans.
5. Persist bounded per-guild Walmart fanout decisions so the delivery audit can distinguish historical below-threshold/category/quality/duplicate outcomes from true missing receipts.
6. Make HP/Target `not_started` health explicitly actionable and document/deploy them as their existing standalone Discloud apps; do not silently start duplicate watcher loops inside the Discord process.

## Validation / Definition of Done
- Focused regressions prove circuit-open page retries are claimable only in API-only mode and never call Walmart HTML.
- Fresh strict snapshots re-arm both `page_retry` and terminal identity rows, while incomplete/unsafe snapshots do not.
- Catalog state persists a page-window cursor; repeated full passes advance through deeper pages and wrap safely within Walmart's 1,000-result bound.
- `/autoscan_health` reports total due, page-recovery due, page-recovery claimable now, and circuit-blocked counts truthfully.
- Historical per-guild fanout decisions are persisted/consumed by delivery health so “eligible without receipt” is reserved for an actually unexplained current-rule gap rather than a missing historical reason.
- Full test suite, dependency check, compileall, import smoke, diff/conflict cleanup, PR checks/review, merge and post-merge checks pass.
- Live Discloud acceptance: Walmart catalog still advances; exact worker makes progress while HTML circuit is open through API-only rescue; terminal count can decrease when fresh proof returns; health no longer says 1,074 rows are claimable when the circuit blocks them; and no public proof/dedupe regression appears.
- HP/Target acceptance requires their standalone Discloud processes to be running against the same Turso DB and health to change from `not_started` to a real cycle state. This cannot be proven from repository edits alone.

## Safety constraints
- Do not disable or weaken the Walmart page-challenge circuit.
- Do not use HTML while the circuit is open.
- Do not promote a terminal row from weak relevance/search text; only strict fresh selected-offer proof or the normal exact proof path may rescue it.
- Do not increase Walmart catalog/exact work without bounded concurrency/page windows.
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
