# Active Task

## Status
**Active — SP-WM-LIVE-BUYBOX-PROOF implementation is code-side validated on branch `fix/walmart-page-live-buybox-proof`, but PR review/CI/merge and post-merge validation are still pending. Production behavior remains unclaimed until merged code is deployed and live Walmart traffic exercises the path.**

## Task
**SP-WM-LIVE-BUYBOX-PROOF — Make exact Walmart product-page recovery consume the selected buy-box seller/shipping evidence that Walmart actually exposes for live marketplace items, without weakening item/price/variant/seller/offer safety.**

## Scope
1. Trace the real recovery execution path from terminal exact queue row / manual recheck through `CachedWalmartProvider` → `WalmartPageRecoveryProvider` → exact candidate merge → `exact_offer_identity()` → shipping/public quality gates.
2. Reproduce the live failure shape represented by Walmart items `17549068871` (RedSwing tent stove, seller Merry Li, free shipping) and `2368146027` (Seizeen 4-burner grill, seller Seizeen, paid shipping shown on page).
3. Determine why page recovery cannot bind seller evidence that is present on the exact Walmart page; inspect script capture limits, item-node matching, seller aliases, nesting depth, selected-offer association, and page/API price alignment.
4. Recover only exact selected-buy-box seller/offer/shipping evidence that is bound to the requested Walmart item and price. Never borrow a seller or shipping term from another offer/variant.
5. Preserve fail-closed behavior for item mismatch, ambiguous seller/offer, price mismatch, unknown mandatory shipping, condition/fulfillment/variant conflicts, reference-price proof, availability, threshold, category, and dedupe.
6. Improve recovery observability so a future terminal row exposes the page-recovery failure reason instead of collapsing everything to generic `missing seller identity`.
7. Run focused live-shape regressions, the full Python suite, compile/import/dependency checks, cleanup, diff/conflict inspection, PR CI/review inspection, merge, and post-merge validation before calling the code task complete.

## Root cause / findings
- Confirmed execution path: the recovery panel's **Recheck exact offer** only re-arms the queue row; the next exact worker calls the registered cached Walmart provider, whose `fetch_product_detail_payload()` delegates to `WalmartPageRecoveryProvider` before the candidate is rebuilt and passed to `exact_offer_identity()`.
- `exact_offer_identity()` requires exact price proof, exact item ID, selected offer ID, and seller identity. A recovered marketplace seller still must have known shipping before its delivered price can become alertable.
- The first page fallback could discard Walmart's rich `__NEXT_DATA__`/structured page state when a single script exceeded its 2 MB cap. Live Walmart page state can be larger than that even when it contains the selected buy-box evidence we need.
- The fallback's explicit buy-box aliases and bounded traversal were too narrow for live page-state shapes. It recognized `selectedOffer`/`buyBoxOffer` but not common nested `buyBox`, `buyBoxWinner`, `offerInfo`, or several item-ID aliases.
- There were two seller parsers in the page-recovery file. The actual nested path used by `_raw_seller_identity()` did not recognize `sellerInfo.sellerDisplayName`, `merchantInfo.merchantName`, or `merchantInfo.merchantId`; that directly reproduced the user's screenshots: shipping/price could be visible while seller identity remained `None`.
- `WalmartProvider._selected_offer_proof()` was a second, shallower provider-side seller parser instead of consuming the existing canonical `selected_offer_delivery_attributes()` normalization. That duplicate path could disagree with the marketplace/delivered-price layer.
- Page recovery previously stopped once any seller identity existed, even if a third-party marketplace offer still had `shipping unknown`. That meant seller recovery alone could still quarantine a real free-shipping deal.
- Page recovery previously returned seller/offer/marketplace proof only; it did not propagate same-buy-box shipping evidence. That is material here: the RedSwing stove visibly has free shipping, while the Seizeen grill has roughly $49.99 mandatory shipping, so item-price markdown alone would overstate the grill's delivered discount.
- `_exact_identity_failure_reason()` did not include `pageIdentityRecoveryStatus` or the page recovery detail, hiding the real fallback failure behind generic `missing seller identity`.

## Changes delivered on branch
- Expanded bounded product-page capture while keeping resource ceilings: 8 MB wire, 6 MB per structured script, 7 MB total structured data, 32 scripts, 75,000 JSON nodes, and concurrency fixed at two requests.
- Added exact item aliases and explicit live buy-box aliases (`buyBox`, `buybox`, `buyBoxWinner`, `offerInfo`) plus a still-bounded deeper seller-bearing descendant scan.
- Unified live seller alias handling so both nested seller readers recognize `sellerInfo`, `sellerDisplayName`, `merchantInfo`, `merchantName`, and `merchantId`.
- Integrated `WalmartProvider._selected_offer_proof()` with the existing canonical `selected_offer_delivery_attributes()` normalization instead of maintaining an independent seller/fulfillment parser.
- Page recovery now also runs for an already-known third-party marketplace seller when selected-offer shipping is still unknown.
- Added same-offer shipping extraction that only accepts explicitly shipping-labelled fields from the selected offer subtree, including zero/free shipping and mandatory paid shipping. It never scans unrelated page prices as shipping.
- Recovered shipping is merged only into the same price-aligned exact payload/selected-offer node and cannot overwrite conflicting official values.
- Official exact item price remains authoritative. Page seller/shipping proof still requires exact item match and cent-for-cent selected-offer price alignment; price disagreement is retryable rather than bound to a seller.
- Added seller-conflict and marketplace-conflict checks so an existing official identity cannot silently be replaced by disagreeing page evidence.
- Page recovery result now carries shipping status/cost and diagnostic detail into candidate attributes.
- Exact terminal failure text now appends page-recovery status/detail, so a future block says which recovery step failed instead of only `missing seller identity`.
- Bumped the one-time bounded recent terminal rearm key to v2 so up to 250 recent/high-priority seller/offer identity rows from the last 72 hours can exercise the corrected fallback after deployment without reopening the historical queue.

## Validation completed so far
- Live-shape focused regression for item `17549068871`: deep `sellerInfo.sellerDisplayName = Merry Li`, exact item price `$56.99`, reference `$99.99`, and free shipping are recovered together; the public current price remains `$56.99`.
- Live-shape focused regression for item `2368146027`: `merchantInfo.merchantName = Seizeen`, item price `$149.99`, and `$49.99` shipping produce a delivered price of `$199.98`; the resulting delivered markdown versus `$289.99` is about 31%, preventing a false 40%+ deal.
- Added regression proving a known marketplace seller with unknown shipping still invokes page recovery.
- Added regression proving a structured page-state script larger than the former 2 MB ceiling is retained within the new bounds.
- Added regression proving page-recovery failure status/detail survives into the terminal identity error.
- Focused Walmart suites: **40 passed**.
- `pip check`: **PASS — no broken requirements**.
- `python -m compileall -q sniperplug tests *.py`: **PASS**.
- `git diff --check`: **PASS** after EOF cleanup.
- Full Python 3.11.15 suite: **1,178 passed**, one upstream `discord.py` `audioop` deprecation warning.
- Implementation commit: `1e9b305b1d3ab1231809c6f826c250f849a39f35`.

## Cleanup and conflict inspection
- Temporary branch-only applicator workflow/scripts deleted themselves before the implementation commit and are absent from the net branch diff.
- Branch comparison at implementation inspection: **0 commits behind `main`**.
- Net runtime/test scope is limited to `sniperplug/providers/walmart.py`, `sniperplug/providers/walmart_page_recovery.py`, `sniperplug/services/walmart_exact_queue_runtime.py`, `tests/test_walmart_page_identity_recovery.py`, and this task record.
- The duplicate provider-side selected-offer seller parser was integrated with the existing canonical selected-offer normalization rather than left as a competing implementation.
- No relaxed exact-item, exact-price, seller/offer, marketplace, shipping, condition, fulfillment, variant, reference-price, availability, threshold, category, or dedupe gate was introduced.
- No raw-page persistence, CAPTCHA/challenge bypass, proxy rotation, alternate retailer source, or unrestricted historical queue rearm was introduced.
- PR creation, PR CI/review inspection, merge conflict inspection, and post-merge canonical workflow validation are still pending.

## Backlog
- Resume the SniperPlug-Site Whop app-specific content-reader task for Hidden Files experiences such as `Make Money Here`, `Content`, and `Better Content` after this task closes.
- Investigate repeated Discord gateway `Can't keep up ... websocket is 41.5s behind` warnings during Walmart drain-mode exact verification after this task closes.
- Continue verifying PR #223 delivery-health accounting from live post-deploy logs.
- PR #200 remains isolated and must not be mixed into this task.
