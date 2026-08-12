# Active Task

## Status
**Active — SP-WM-LIVE-BUYBOX-PROOF. Live recovery screenshots show exact Walmart items still quarantined as `missing seller identity` even though the exact product pages visibly expose the selected seller and shipping terms. Do not claim completion until the real page-recovery execution path, tests, regressions, cleanup, and conflict checks all pass.**

## Task
**SP-WM-LIVE-BUYBOX-PROOF — Make exact Walmart product-page recovery consume the selected buy-box seller/shipping evidence that Walmart actually exposes for live marketplace items, without weakening item/price/variant/seller/offer safety.**

## Scope
1. Trace the real recovery execution path from terminal exact queue row / manual recheck through `CachedWalmartProvider` → `WalmartPageRecoveryProvider` → exact candidate merge → `exact_offer_identity()` → shipping/public quality gates.
2. Reproduce the live failure shape represented by Walmart items `17549068871` (RedSwing tent stove, seller Merry Li, free shipping) and `2368146027` (Seizeen 4-burner grill, seller Seizeen, paid shipping shown on page).
3. Determine why page recovery cannot bind seller evidence that is present on the exact Walmart page; inspect script capture limits, item-node matching, seller aliases, nesting depth, selected-offer association, and page/API price alignment.
4. Recover only exact selected-buy-box seller/offer/shipping evidence that is bound to the requested Walmart item and price. Never borrow a seller or shipping term from another offer/variant.
5. Preserve fail-closed behavior for item mismatch, ambiguous seller/offer, price mismatch, unknown mandatory shipping, condition/fulfillment/variant conflicts, reference-price proof, availability, threshold, category, and dedupe.
6. Improve recovery observability so a future terminal row exposes the page-recovery failure reason instead of collapsing everything to generic `missing seller identity`.
7. Run focused live-shape regressions, the full Python suite, compile/import/dependency checks, cleanup, diff/conflict inspection, and PR review inspection before calling the code task complete.

## Root cause / findings
- Confirmed execution path: the recovery panel's **Recheck exact offer** only re-arms the queue row; the next exact worker calls the registered cached Walmart provider, whose `fetch_product_detail_payload()` delegates to `WalmartPageRecoveryProvider` before the candidate is rebuilt and passed to `exact_offer_identity()`.
- `exact_offer_identity()` requires exact price proof, exact item ID, selected offer ID, and seller identity. A recovered seller can still be rejected later when third-party shipping is unknown.
- Current page recovery captures only JSON-like `<script>` blocks and discards an entire script once it exceeds the 2 MB per-script cap. Walmart's richer page state is therefore capable of being dropped while smaller JSON-LD survives.
- Current structured traversal starts only from dict nodes that themselves contain the exact item ID and searches seller-bearing descendants only to depth 5 / 400 scanned nodes. Live page state can expose buy-box seller data outside those narrow assumptions.
- Current seller alias support is narrower than the already-existing canonical selected-offer normalizer: page recovery does not fully consume `sellerInfo` shapes used elsewhere in SniperPlug.
- Current recovery proof contains seller/offer/marketplace only. It intentionally does not propagate selected-offer shipping evidence, so a marketplace item with seller recovered can still be blocked as `shipping unknown` even when the exact page says `Free shipping` or shows a mandatory shipping fee.
- Current `_exact_identity_failure_reason()` does not surface `pageIdentityRecoveryStatus` / recovery detail, so live failures collapse back to generic `missing seller identity` and hide which page-recovery step actually failed.
- Live evidence: Walmart item `17549068871` visibly shows seller `Merry Li`, current price around `$56.99`, a `$99.99` reference price, and free shipping; item `2368146027` visibly shows seller `Seizeen`, current price `$149.99`, a `$289.99` reference price, and shipping starting at `$49.99`. Both are still represented in recovery as exact identity blocked for missing seller identity.

## Changes
- Pending implementation after root-cause inspection.

## Validation
- Pending.

## Cleanup and conflict inspection
- Pending.

## Backlog
- Resume the SniperPlug-Site Whop app-specific content-reader task for Hidden Files experiences such as `Make Money Here`, `Content`, and `Better Content` after this task closes.
- Investigate repeated Discord gateway `Can't keep up ... websocket is 41.5s behind` warnings during Walmart drain-mode exact verification after this task closes.
- Continue verifying PR #223 delivery-health accounting from live post-deploy logs.
- PR #200 remains isolated and must not be mixed into this task.
