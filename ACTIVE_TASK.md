# Active Task

## Status
**Active — SP-AUTOSCAN-COVERAGE-STALL.** Walmart catalog depth, fresh-proof exact verification, terminal rescue, page-retry rescue, truthful queue health, and historical fanout provenance are now live-proven after PRs #253/#254. The remaining Walmart safety issue is a product-page HTML burst when a 15-minute challenge cooldown expires: a 24/4 drain batch can start several HTML fallbacks before the old two-challenge circuit trips. Branch `fix/walmart-page-request-pacing-255` isolates HTML behind a single paced probe while keeping fresh catalog proof and official API/exact-search verification moving at normal exact-worker throughput. HP and Target remain separate standalone watcher acceptance items and are not being deployed until the owner is ready.

## Scope
Repair autoscan coverage without weakening exact proof or hammering Walmart HTML. Scope includes truthful backlog-vs-claimable health, deep catalog coverage, strict fresh-proof rescue, bounded page recovery, durable delivery-decision provenance, safe product-page request pacing, and eventual production activation of separate HP/Target watcher apps. Preserve exact item/offer/seller/variant/condition/fulfillment/current-price/reference proof, durable leases, dedupe, price history, and bounded Turso behavior.

## Root cause / findings
- Original live health showed **20,005 exact rows, 1,074 due page-recovery rows, 0 actively verifying, 12,390 terminal identity blocks, and 6 current-rule eligible events without durable post receipts**.
- PR #253 fixed the original coverage stall: catalog depth rotates through pages 1-40 inside the existing two-page-per-route request budget; page recovery remains claimable in a two-row API-only lane while HTML is blocked; strict fresh catalog proof can rescue page/terminal rows; fanout decisions are persisted.
- PR #254 exposed the selected page window in production logs so deep coverage can be verified directly.
- New live evidence shows the remaining challenge pattern is specifically **HTML fallback burstiness**, not the rotating catalog itself. The exact worker can enter **24/4 drain mode**. When the HTML circuit expires, several claims can already be inside `fetch_product_detail_payload()` before the old circuit sees two challenge responses.
- Concrete production examples: **13:44:06** logged `page_challenge=5` plus `page_circuit_open=19`; **13:59:20** again logged `page_challenge=5` plus `page_circuit_open=18`. Five real challenged page requests in one drain batch is the behavior being removed.
- The fixed-rate exact worker also yields only one second after an overlong cycle, so a 30-37 second page-heavy batch can otherwise be followed almost immediately by another cycle.
- Fresh catalog snapshots and official API/exact-search continue to verify real products while HTML is blocked, including many 8/8 and 24/24 batches. Therefore the safe solution is to isolate/rate-limit HTML only, not slow the full deal pipeline.
- The pacing helper deliberately reuses the existing canonical exact verifier instead of creating a second seller/offer proof implementation. An isolated synthetic open-circuit view obtains the verifier's fresh/API-only result first; only a true need for HTML may advance to the paced real-provider pass.

## Changes already live — PR #253
- Keep a **2-row** `page_retry` lane claimable while the HTML circuit is open; those claims use official API/exact-search only.
- Queue health labels the full page-recovery number as backlog and reports recovery mode, cap, claimable-this-batch, and HTML cooldown.
- Strict fresh official catalog snapshots may immediately rearm `incomplete_identity` / `identity_mismatch`; weak proof stays terminal.
- Full catalog passes rotate through two-page windows across pages **1-40** with no request-count increase per route.
- Persist bounded per-guild Walmart fanout decisions so delivery health can distinguish real unexplained gaps from historical threshold/category/quality/dedupe outcomes.
- HP/Target `not_started` health names the standalone entrypoint and same-Turso requirement.

## Changes already live — PR #254
- Forward the catalog discovery note that contains the selected **page window / 40** and durable completed-pass count. This was observability-only.

## Live acceptance now passed
- **Deep catalog coverage:** production has explicitly logged deep windows including **39-40/40** with real returned products. Later pass 104 correctly rotated to **7-8/40**.
- **Fresh proof during HTML cooldown:** repeated 8/8 and 24/24 exact batches verify with fetch time around 0.00-0.01s while the page circuit is open.
- **Official API deals during HTML cooldown:** production continues to recover official was prices and markdowns while page HTML is suppressed.
- **Terminal rescue:** multiple catalog batches have logged `terminal identity rescued > 0`; terminal blocked count fell materially even while the queue grew.
- **Page-retry rescue:** at **14:12:33 Aug 15**, catalog telemetry logged `safe fresh proof 50 • page retries rescued 1 • terminal identity rescued 1 • verified changes rearmed 1`.
- **Truthful health:** the latest owner health separated **16,518 page-recovery backlog** from **2 claimable this batch**, identified API-only mode and the current HTML cooldown, and reported **11,515 terminal** / **9,162 verified** rows.
- **Delivery provenance:** latest health had historical destination decisions for **80/80** events; old events are no longer mislabeled as confirmed missed posts merely because current rules differ.

## In-progress #255 page pacing change
Branch: `fix/walmart-page-request-pacing-255`

Implementation intent:
- Keep `_fetch_exact_candidate_off_loop()` as the canonical proof implementation.
- First run each claim through an isolated API-only view of that verifier. Valid strict fresh catalog proof or complete official item-detail/exact-search proof returns immediately.
- Only a claim whose canonical API-only result says HTML is still required may advance to the real provider path.
- Product-page HTML becomes a **single process/provider probe at most once per 60 seconds** when the real circuit is closed.
- The probe slot is reserved synchronously in the provider's existing circuit state so concurrent 8/2 or 24/4 claims cannot race multiple HTML fallbacks through.
- If no probe slot is available, the row remains fail-closed as `page_retry` with `retry_kind=page_pacing_deferred`; fresh/API work for other rows continues.
- The **first** challenge/403/429 from a paced real-provider pass immediately opens the existing **15-minute** provider circuit. It no longer waits for two challenge responses while other requests are in flight.
- While the circuit is open, HTML remains fully suppressed and the existing two-row page-recovery lane gets official API/exact-search attempts only.
- No proof gate, public threshold, DM rule, dedupe rule, catalog depth, or queue ownership rule is weakened.

## #255 validation gate
Before merge:
1. Focused regression must prove four concurrent API-incomplete claims issue **exactly one** HTML fallback and the others return `page_pacing_deferred`.
2. Prove complete official API identity still verifies while the HTML probe lane is throttled.
3. Prove strict fresh catalog proof bypasses API and HTML entirely.
4. Prove the first challenge response opens the circuit immediately and a following claim does not issue another HTML request.
5. Prove the 60-second reservation window cannot be double-reserved.
6. Run the full test suite, dependency check, compile/static validation, import smoke, changed-file/diff inspection, and review-thread cleanup through CI.
7. Do **not** call the issue production-fixed from code/CI alone; live acceptance after merge must show no multi-challenge HTML burst while fresh/API deal verification continues.

## Remaining Definition of Done
1. Finish #255 validation/merge and later live-prove that a cooldown expiry cannot produce a multi-request HTML burst; a challenge may still occur because Walmart controls its site, but SniperPlug must send only the paced probe and immediately hold further HTML.
2. Confirm fresh/API exact verification and real deal fanout continue while HTML is paced/held.
3. Start `SniperPlug-HP-Watcher` against the shared Turso DB and confirm health changes from `not_started` to a real cycle state **when the owner is ready**.
4. Start `SniperPlug-Target-Watcher` against the shared Turso DB with `TARGET_REDSKY_API_KEY` and confirm a real cycle state **when the owner is ready**. Target also needs a saved exact local store for this server.
5. Only then close SP-AUTOSCAN-COVERAGE-STALL unless the owner explicitly defers/narrows those watcher acceptance items.

## Watcher state / pacing constraint
- HP local standalone folder has already been prepared and its Turso URL/token presence was confirmed, but **nothing has been uploaded/deployed**.
- Target's local `TARGET_REDSKY_API_KEY` is currently missing and this server has no Target location configured.
- Do not initiate watcher deployment until the owner explicitly says they are ready.

## Safety constraints
- Do not disable or weaken the Walmart challenge circuit.
- Do not use Walmart HTML while the circuit is open.
- Do not promote page/terminal rows from weak relevance text; only strict fresh selected-offer proof or the normal exact proof path may rescue them.
- Do not increase catalog requests per route/pass; depth must rotate within the existing two-page budget.
- Do not slow fresh catalog proof or complete official API exact verification merely to protect the HTML fallback.
- Do not delete price history, offer memory, queue identity history, dedupe, user settings, or feedback summaries to improve counters.
- Do not run duplicate HP/Target watcher owners inside the Discord bot.

## Separate backlog signal
- Discord gateway has still reported roughly **41.3s** behind in live samples. Keep this as **SP-GATEWAY-EVENT-LOOP-LAG** and do not switch implementation tasks without an explicit FORCE SWITCH unless evidence proves the active autoscan path is directly responsible.

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
- Resume SniperPlug-Site work later.
