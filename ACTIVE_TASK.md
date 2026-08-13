# Active Task

## Status
**Active — SP-DEALS-PRODUCT-FALLBACK-243. PR #243 merged to `main` at `e4e3012628ff36f6208f6ce5a6f097835670fc36`. Branch CI and the real merge-commit Import smoke, Python Check, and Python tests all pass. The only remaining gate is production deployment plus `/deals search:phone mount` live behavior.**

## Task
**SP-DEALS-PRODUCT-FALLBACK-243 — Make `/deals search:<product>` return useful current-price Walmart product matches when no verified/review/scout deal survives, without weakening any public/DM deal-proof gate.**

## Root cause
The live slash-command path is not the legacy `DealScannerCog._send_walmart_scan` result renderer by itself. `VerifiedDealScannerCog` inherits `UnifiedDealScannerCog`, so `/deals` routes through the unified finder:

`/deals` → `DealScannerCog.deals` → `UnifiedDealScannerCog._send_walmart_scan` → `_send_unified_deal_search` → `find_walmart_deals_for_query`.

The unified finder can scan many Walmart results successfully but only returns cards from these lanes:
- verified markdown/value cards;
- review/flip/value cards;
- low-price scout cards.

`build_review_candidate_cards` explicitly counts a direct query hit with no deal/value signal as `search match only` and then discards it. Therefore a normal search such as `phone mount` can have real Walmart products and current prices in the aggregate while the Discord result surface still ends with “Nothing useful found yet.”

## Implementation
- Added a final **private direct-product fallback** inside `deal_finder_engine.py` after verified, review, and scout lanes run.
- The fallback activates **only when verified cards and all private review/scout cards are empty**.
- Multi-word searches require a strong direct token/phrase match; weak partial matches such as `phone` without `mount` are rejected.
- Single-word shopping searches such as `monitor`, `lego`, or `tide` are supported privately when the title contains the exact meaningful token.
- A fallback product must still have a positive payable current price and product URL.
- Fallback cards reuse the existing truthful review renderer and are explicitly marked `Search match — not deal proof`.
- Fallback cards are forced `should_alert=False`, `manual_share_allowed=False`, and `deal_lane=product_search_match` so they cannot become public posts or personal DM deal alerts.
- Existing verified/review/scout results always win; the fallback never replaces a real deal/value lead.
- Review audit reason/source counts are preserved through merge/rank so the fallback does not erase diagnostics.

## Safety invariants
- No threshold reduction for public posts or DMs.
- No untrusted Walmart reference price is converted into `% off` proof.
- No product-search relevance score is treated as deal proof.
- No third-party offer with a blocked/missing payable current price can enter the fallback.
- Product fallback stays private and cannot auto-post, auto-DM, or become manually shareable through the deal-review flow.
- Existing seller/offer/variant/shipping/current-price/reference/dedupe/category gates are unchanged.

## Validation completed
- Targeted regressions added for two-word `phone mount`, single-word searches, weak partial-match rejection, missing-price rejection, fallback priority, audit preservation, and private wiring.
- Full Python 3.11 suite on PR #243: **1,219 passed**, one upstream `discord.py` `audioop` deprecation warning.
- `pip check`: **PASS** — no broken requirements.
- `python -m compileall -q sniperplug tests *.py`: **PASS**.
- PR branch Import smoke check: **PASS**.
- PR branch Python Check: **PASS**.
- PR branch Python tests: **PASS**.
- PR compare was limited to `ACTIVE_TASK.md`, `sniperplug/services/deal_finder_engine.py`, and `tests/test_deals_product_fallback.py`; no unrelated implementation files changed.
- PR #243 merged cleanly with no inline review threads.
- Real `main` merge commit `e4e3012628ff36f6208f6ce5a6f097835670fc36`: **Import smoke PASS • Python Check PASS • Python tests PASS**.

## Remaining Definition of Done
1. Deploy merged `main` to the existing Discloud app.
2. Production live test: `/deals search:phone mount` must return relevant current-price product cards when no verified/review/scout deal qualifies, clearly labeled as non-deal search matches.
3. Confirm those fallback cards do not public-post or trigger personal DM deal alerts.
4. Then close SP-DEALS-PRODUCT-FALLBACK-243 and resume the suspended Walmart DM live-proof task only if desired.

## Suspended task
**SP-WM-MISSED-DEALS-236 is suspended, not closed.** PR #242 had already proven v2 fanout evaluation and stable duplicate behavior; its only remaining live gate was one newly qualifying 50%+/78+ Walmart event with no existing same-price receipt producing `DM ... sent=1`, followed by receipt dedupe on re-verification. Resume only after this `/deals` task closes or another explicit FORCE SWITCH.

## Backlog
- Resume SniperPlug-Site Whop app-specific content-reader work after the active task closes.
- Investigate repeated Discord gateway `Can't keep up` warnings after the active task closes unless directly tied to the active execution path.
- Continue broader PR #223 delivery-health accounting later.
- PR #200 remains isolated.
