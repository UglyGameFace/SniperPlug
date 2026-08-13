# Active Task

## Status
**Active — SP-WM-MISSED-DEALS-236. PRs #236–#241 are merged; the latest live run proved Discord DM delivery and the user's saved DM policy are healthy, but exposed a durable Walmart fanout-event identity defect that can suppress a newly qualifying same-price deal before personal/public filters ever see it. Branch `fix/walmart-fanout-event-refresh-242` contains the focused fix. The Single Active Task Lock remains engaged until merged production proves exact verification → fanout evaluation → legitimate filter/dedupe → delivery end to end.**

## Task
**SP-WM-MISSED-DEALS-236 — Restore high-throughput Walmart exact verification and complete reliable fanout so catalog-discovered deals are not lost to flaky recovery or stale durable event dedupe while all exact item/offer/seller/price/shipping/reference/threshold/category/DM/dedupe gates remain fail-closed.**

## Scope / safety invariants
- Requested Walmart item ID must exactly match the official item being verified.
- Current payable price must come from current bounded official proof.
- Seller, selected offer, variant, condition, fulfillment, mandatory marketplace shipping, delivered price, and reference price cannot be mixed across offers.
- Third-party marketplace offers remain blocked when shipping/delivered-price truth is incomplete.
- Reference-price, availability, threshold, category, DM preference, and duplicate gates remain fail-closed.
- Fresh catalog proof is reusable only while bounded-fresh, exact-item matched, cent-for-cent matched to the queued payable price, seller/offer complete, variant-safe, and shipping-complete when required.
- Product-page structured data remains a later identity/shipping fallback only; it cannot replace official item/current-price proof or choose a cheaper seller.
- A new fanout event version may cause filters to be re-evaluated, but it must **not** bypass existing public-post dedupe or personal-DM delivery receipts.
- No CAPTCHA/challenge bypass, proxy rotation, browser fingerprint spoofing, seller guessing, relevance-neighbor borrowing, sibling/cross-offer reference borrowing, alternate-retailer proof substitution, threshold weakening, or duplicate bypass.

## Production gates already passed through PR #241
- Global catalog discovery keeps advancing with large usable-item batches.
- Strict `safe fresh proof` remains active and can promote exact rows without another network fetch.
- Official API verification continues while the Walmart product-page challenge circuit is open.
- Seller/offer identity blocks fell to zero in the supplied post-fix windows.
- Open-circuit page-recovery leasing is bounded/suppressed while fresh/API work remains eligible.
- Bounded pressure telemetry distinguishes saturated `>=64` recovery pressure.
- Weekly terminal identity reprobes are capped at 4 per maintenance tick and quarantine maintenance at 64.
- Exact worker continues producing verified markdowns under live traffic.

## New live DM/fanout evidence
The user reported no personal deal DM after the 9:33 PM verified Walmart alert and then ran the canonical diagnostics:
- `/dm_deals action:Show my settings` shows alerts **enabled**, Smart mode, **50%** minimum markdown, **78/250** minimum score, all categories allowed, no required/excluded words, no muted categories, Walmart Cash-only off, and daily limit **unlimited**.
- Durable delivery health shows only **1 delivered today (UTC)**, so a finite daily cap is not suppressing alerts.
- `/dm_deals action:Send a test DM` delivered successfully, proving Discord can currently reach the user.
- Process-local delivery audit says **no exact deal events have been evaluated for the personal filter since this bot process started**.
- At the same time, live exact-worker logs show repeated successful `verified` rows and `markdowns`, so the gap is after exact verification and before per-user matching.

This rules out closed DMs, the daily cap, disabled alerts, and a blanket user-category/keyword exclusion as the cause of the zero per-user evaluation count.

## Root cause — durable event identity was too coarse
The canonical bulk fanout cursor correctly sees newly reverified `verified_markdown` snapshots by `(verified_at, item_id)`, but event insertion previously used `card_deal_key(card)` as the durable event-table primary key.

For a card with a current price, `card_deal_key` is intentionally a delivery/post duplicate key centered on selected offer/item identity plus the **current price**. It does not encode the trusted reference price, resulting markdown percentage, Sniper score, category/search text, Walmart Cash proof, or other fields that personal/public eligibility can depend on.

That is correct for **delivery dedupe**, but incorrect for **event evaluation identity**.

Concrete failure shape:
1. Exact offer verifies at `$10` with a trusted reference that yields 33% off. The durable fanout event is processed; a subscriber requiring 50% is correctly rejected.
2. The same exact offer later verifies again at the same `$10` current price, but current trusted proof now yields 50% off (or its score/category/Cash/search proof changes enough to become eligible).
3. The old durable key is still the same `...:price:10.00` key.
4. `ON CONFLICT(deal_key) DO NOTHING` discards the newly qualifying proof **before** public or personal filters run.
5. Per-user telemetry therefore remains untouched even though the exact worker has a fresh verified markdown.

This is a real missed-deal path and directly explains why exact verification can be healthy while personal fanout sees no new event to evaluate.

## PR #242 implementation — separate event versioning from delivery dedupe
`sniperplug/services/walmart_global_deal_fanout_bulk.py` now uses two deliberately different identities:

### Fanout event-version key
A compact v2 SHA-256 fingerprint is appended to the existing stable deal identity. The fingerprint covers alert-eligibility state including:
- current and trusted reference price,
- exact discount,
- Sniper score,
- normalized personal-category classification,
- strict Walmart Cash amount proof,
- normalized card/search text used by keyword/category matching,
- public `should_alert` state,
- selected seller/variant/condition/fulfillment/stock/add-to-cart presentation when present.

A materially changed proof state therefore creates a new durable event version and reaches public/personal evaluation. An identical re-verification produces the same v2 key and is still deduped before fanout work.

### Stable delivery key
Personal DM receipt checks and writes continue using the original `card_deal_key` current-price identity. Existing receipts remain compatible. Therefore:
- a user who already received the same exact offer/current price does **not** receive it again just because proof metadata changed;
- a user who previously rejected that offer can receive it later if a newly verified proof state now legitimately satisfies their filter;
- public posting still runs through the existing canonical public dedupe/reservation gates.

The first v2 deployment also gives current reverified exact markdowns a v2 event namespace, so they can be evaluated against current subscribers instead of colliding with old v1 processed rows.

## Observability changes
- Fanout summary now distinguishes `new event versions` from `deduped versions`.
- The exact worker emits periodic fanout summary telemetry when exact cards were seen even if every version was deduped, removing the previous silent-success ambiguity.
- `/dm_deals` empty process-audit wording now correctly says no **new exact fanout event versions** reached the user, rather than implying exact verification itself did not occur.

## Regression coverage added
- Same current price + changed trusted reference/discount => **new event version**, same delivery key.
- Identical alert proof across re-verification => stable v2 event key.
- Same current price + score crossing => new event version, same delivery key.
- Integrated durable-ingest reproduction: a 33% event is inserted, the same-price offer later becomes 50% and inserts a second event version, then an identical third verification inserts **zero** new versions.
- Static guard proves both personal DM receipt read and receipt write use the stable delivery key rather than the v2 event key.
- Existing fanout cursor regressions remain green.

## Validation completed on implementation branch
- Focused fanout suites: **11 passed**.
- Full Python 3.11.15 suite: **1,213 passed** with one upstream `discord.py` `audioop` deprecation warning.
- `pip check`: **PASS — no broken requirements**.
- `python -m compileall -q sniperplug tests *.py`: **PASS**.
- `git diff --check`: **PASS**.

## Cleanup / conflict requirements
Before merge:
- remove the temporary branch-only applicator script/workflow used to make and validate the focused change;
- final diff must contain only this task record, the canonical fanout/runtime/telemetry changes, and the focused regression file;
- branch must remain based on current `main` with no unrelated feature work;
- inspect canonical PR checks, changed files, reviews, comments, and inline threads before merge.

## Remaining Definition of Done
1. Remove branch-only applicator files and inspect the final diff against `main`.
2. Open PR #242 and require canonical Import smoke, Python Check, and Python tests to pass on the final head.
3. Inspect PR reviews/comments/threads and resolve any real finding; merge only a clean, green head.
4. Verify the real merge commit on `main` passes push-triggered Import smoke, Python Check, and Python tests.
5. Deploy merged `main` to canonical SniperPlug app `1779293887444` using the existing-app commit workflow.
6. In live logs, confirm `new event versions` and/or `deduped versions` telemetry appears after exact markdown verification rather than disappearing silently.
7. Confirm `/dm_deals` process audit increments `checked` when a new v2 event reaches the subscriber and reports the actual rejection reason when it does not match.
8. Confirm a newly qualifying 50%+/78+ exact event reaches personal DM `matched/sent` when all saved hard filters pass, while an already-delivered same-price offer remains receipt-deduped.
9. Confirm public thresholds/dedupe and all exact item/offer/seller/shipping/reference gates remain unchanged.
10. Continue watching the PR #241 page-circuit live gate; no stale page-retry churn may reappear.
11. Keep SP-WM-MISSED-DEALS-236 active until these production gates pass.

## Blocker
**Final PR #242 cleanup/canonical CI plus live merged production proof.** Code-side reproduction and tests are green; production completion remains intentionally unclaimed until the merged deployment proves the corrected event-version → filter → delivery path under real Walmart traffic.

## Backlog
- Resume the SniperPlug-Site Whop app-specific content-reader task only after this Walmart task closes.
- Investigate repeated Discord gateway `Can't keep up` warnings after this active Walmart task closes unless new evidence directly ties them to this Walmart execution path.
- Continue broader PR #223 delivery-health accounting only after this Walmart task closes.
- PR #200 remains isolated and must not be mixed into this task.
