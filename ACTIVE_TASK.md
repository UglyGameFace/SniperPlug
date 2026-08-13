# Active Task

## Status
**Active — SP-WM-MISSED-DEALS-236. PR #242 is merged and deployed. Live production now proves exact verification → v2 fanout evaluation → legitimate public/personal filter/dedupe behavior, and PR #241's open-circuit page-recovery suppression is holding under a saturated recovery backlog. One end-to-end production gate remains before closure: a newly qualifying 50%+/78+ Walmart event with no existing same-price delivery receipt must reach the user's DM (`matched/sent`), and the user-specific `/dm_deals` process audit should reflect that evaluation/outcome.**

## Task
**SP-WM-MISSED-DEALS-236 — Restore high-throughput Walmart exact verification and complete reliable fanout so catalog-discovered deals are not lost to flaky recovery or stale durable event dedupe while all exact item/offer/seller/price/shipping/reference/threshold/category/DM/dedupe gates remain fail-closed.**

## Safety invariants
- Requested Walmart item ID must exactly match the official item being verified.
- Current payable price must come from current bounded official proof.
- Seller, selected offer, variant, condition, fulfillment, mandatory marketplace shipping, delivered price, and reference price cannot be mixed across offers.
- Third-party marketplace offers remain blocked when shipping/delivered-price truth is incomplete.
- Reference-price, availability, threshold, category, DM preference, and duplicate gates remain fail-closed.
- Fresh catalog proof is reusable only while bounded-fresh, exact-item matched, cent-for-cent matched to the queued payable price, seller/offer complete, variant-safe, and shipping-complete when required.
- Product-page structured data remains a later identity/shipping fallback only; it cannot replace official item/current-price proof or choose a cheaper seller.
- A new fanout event version may cause filters to be re-evaluated, but it must **not** bypass existing public-post dedupe or personal-DM delivery receipts.
- No CAPTCHA/challenge bypass, proxy rotation, browser fingerprint spoofing, seller guessing, relevance-neighbor borrowing, sibling/cross-offer reference borrowing, alternate-retailer proof substitution, threshold weakening, or duplicate bypass.

## Root causes fixed
### PR #241 — open-circuit page-recovery leakage
The bounded page-retry deferral sweep alone was insufficient when more than 64 page-recovery rows were due. The claim helper could still lease a small page-recovery lane from rows beyond the sweep. The production bulk runtime now sets `page_recovery_claim_cap=0` while the product-page challenge circuit has remaining cooldown, while leaving fresh/pending/API work claimable.

### PR #242 — fanout event identity too coarse
Durable fanout insertion previously used the stable delivery/post duplicate key, so a same-price offer whose trusted proof later changed enough to become eligible could collide before current filters ran. PR #242 separates:
- **v2 fanout event-version identity** — fingerprints alert-eligibility state so materially changed exact proof is re-evaluated;
- **stable delivery identity** — remains the original exact offer/current-price key so already-delivered same-price offers do not spam the user.

## Validation already passed
- Focused fanout regressions: **11 passed**.
- Full Python 3.11 suite before merge: **1,213 passed** with one upstream `discord.py` `audioop` deprecation warning.
- `pip check`: PASS.
- `python -m compileall -q sniperplug tests *.py`: PASS.
- `git diff --check`: PASS.
- PR #242 merged cleanly and the real `main` merge commit passed Import smoke, Python Check, and Python tests.

## New live production evidence — 2026-08-13
### v2 event path is working
Production emitted multiple v2 summaries, including:
- `loaded 4 • exact cards 3 • new event versions 2 • deduped versions 1 • processed 2`
- `DM checked/matched/sent 2/2/0 • DM skips ... duplicate 2`

This proves newly versioned exact proof reaches personal evaluation. Both qualifying events matched the saved personal filter and were then correctly stopped by the stable same-price delivery receipt. This is the intended PR #242 separation: **re-evaluate changed proof, but do not redeliver an already-received same-price offer.**

A later 50% Garanimals event likewise produced:
- `new event versions 1 • processed 1`
- `DM checked/matched/sent 1/1/0`
- `DM skips ... duplicate 1`

A later 18.63% RoC event produced:
- `new event versions 1 • processed 1`
- `DM checked/matched/sent 1/0/0`
- `DM skips filter ... 1`

This proves a new v2 event is also rejected by the current personal filter when it legitimately does not qualify.

### Public gates remain intact
Live decisions show both canonical public behaviors still firing after v2 eventing:
- recent-delivery duplicate suppression (`recent_duplicate=1`);
- quality/watchlist suppression (`quality:review_or_watchlist`);
- threshold suppression (`quality:below_threshold:18.63<50` and `<40`).

No public or DM errors appeared in the supplied fanout summaries.

### Exact verification remains productive while the page circuit is open
Across the supplied window, the exact worker repeatedly verified official prices and produced markdowns while `page circuit open`, including 5/5 markdown and 3/3 markdown batches. Seller/offer/item/proof identity blocks remained zero in the shown batches.

### PR #241 page-recovery suppression is holding
The strongest live proof is the saturated open-circuit snapshots:
- `actionable due >=64 (new/retry 0 • page recovery >=64 • scheduled rechecks 0) • claimed 0`

This occurred while the page circuit was open, demonstrating that known page-recovery backlog is **not** leaking through the claim selector. The current runtime explicitly sets `page_recovery_claim_cap=0` during circuit cooldown.

Some separately claimed fresh/pending/reprobe rows still end with `page_circuit_open` when their official/API recovery cannot complete and they would otherwise fall through to the product-page fallback. Those are counted as `page suppressed`; they are not evidence that the known `page_retry` lane is being leased during cooldown.

When cooldown expired, production legitimately attempted the page path again, received two fresh `page_challenge` results, and reopened the 15-minute circuit (`retry in 899/900s`). Subsequent saturated recovery snapshots again showed `claimed 0` for the page-recovery-only backlog. This is the intended circuit behavior.

## Remaining Definition of Done
1. Capture `/dm_deals` process audit after these v2 events and confirm the user's `checked` count/reason matches the live fanout decision path.
2. Observe one **newly qualifying 50%+/78+ event with no existing stable delivery receipt** produce `DM checked/matched/sent .../.../1` and arrive in the user's DM.
3. Confirm the first subsequent re-verification of that same exact offer/current price is receipt-deduped rather than redelivered.
4. Keep watching the page circuit through another cooldown boundary only for regression evidence; current PR #241 live gate is passing.
5. After the fresh-send gate passes, update this record, run final conflict/cleanup inspection, and close SP-WM-MISSED-DEALS-236.

## Blocker
**One fresh qualifying personal delivery.** The code path is evaluating current v2 events correctly, duplicate receipts are working, filter rejection is working, public gates remain intact, and page-recovery suppression is holding. The supplied live window did not contain a qualifying event that both matched the user's policy and lacked an existing same-price receipt, so `DM sent=1` has not yet been proven in production.

## Backlog
- RideSniper remains separate and must not be mixed into this active SniperPlug task unless the user explicitly force-switches.
- Resume the SniperPlug-Site Whop app-specific content-reader task only after this Walmart task closes.
- Investigate repeated Discord gateway `Can't keep up` warnings after this active Walmart task closes unless new evidence directly ties them to this Walmart execution path.
- Continue broader PR #223 delivery-health accounting only after this Walmart task closes.
- PR #200 remains isolated and must not be mixed into this task.
