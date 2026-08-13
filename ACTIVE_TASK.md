# Active Task

## Status
**Active — FORCE SWITCH accepted on 2026-08-13. SP-DEALS-PRODUCT-FALLBACK-243 is now the only active implementation task. Branch: `fix/deals-product-fallback-243`.**

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

## Validation required before done
1. Targeted tests for two-word product matching, single-word product matching, weak-partial rejection, missing-price rejection, and fallback priority.
2. Confirm product fallback preserves review audit counters/reasons.
3. Full Python test suite.
4. `pip check`.
5. `python -m compileall -q sniperplug tests *.py`.
6. `git diff --check` / PR conflict and cleanup inspection.
7. Merge commit CI passes.
8. Production live test: `/deals search:phone mount` must return relevant current-price product cards when no deal qualifies, clearly labeled as non-deal search matches.

## Suspended task
**SP-WM-MISSED-DEALS-236 is suspended, not closed.** PR #242 had already proven v2 fanout evaluation and stable duplicate behavior; its only remaining live gate was one newly qualifying 50%+/78+ Walmart event with no existing same-price receipt producing `DM ... sent=1`, followed by receipt dedupe on re-verification. Resume only after this `/deals` task closes or another explicit FORCE SWITCH.

## Backlog
- Resume SniperPlug-Site Whop app-specific content-reader work after the active task closes.
- Investigate repeated Discord gateway `Can't keep up` warnings after the active task closes unless directly tied to the active execution path.
- Continue broader PR #223 delivery-health accounting later.
- PR #200 remains isolated.
