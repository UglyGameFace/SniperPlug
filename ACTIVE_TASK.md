# Active Task

## Status
Active — Walmart selected-offer and delivered-price accuracy hardening is isolated on `fix/walmart-delivered-offer-price`. No production deployment has occurred.

## Scope
Bind Walmart price proof to the exact selected seller/offer and use the payable delivered price for qualification whenever mandatory shipping is exposed. Alternate-seller minimum prices must remain context only and must never replace the selected buy-box offer.

## Confirmed findings
- `SourceCandidate` and `NormalizedDeal` have seller/offer fields but no item-price, shipping-cost, delivered-price, or shipping-proof fields.
- Walmart current-price extraction accepts broad item-level fields including `minPrice`, while seller and selected-offer identity are resolved separately.
- Nested `selectedOffer` / `buyBoxOffer` seller and price data are not normalized as one atomic offer record.
- Shipping charges are not included in discount math, ranking, price memory, or public qualification.
- Marketplace offers with a seller identity can currently pass exact-offer identity without proving whether shipping is free, paid, or unknown.

## Intended changes
- Normalize one selected-offer record from `selectedOffer`, `buyBoxOffer`, or the root buy-box payload.
- Keep `minPrice` / alternate seller prices as labeled context only.
- Capture selected seller, seller ID, offer ID, fulfillment, condition, item price, shipping state, shipping cost, and delivered total from the same offer record.
- Make Walmart candidate `current_price` / `api_current_price` equal the selected offer's delivered total when shipping is known.
- Fail closed for third-party marketplace offers whose mandatory shipping cannot be verified.
- Preserve Walmart-owned offers when shipping is checkout/location dependent, while labeling that state instead of claiming free shipping.
- Carry item price, shipping, and delivered total into normalized deal metadata and card evidence.
- Keep exact item, seller, offer, variant, condition, fulfillment, duplicate, and threshold gates intact.

## Definition of done
- Targeted tests cover paid shipping, explicit free shipping, unknown marketplace shipping, alternate `minPrice`, selected-offer seller switching, and delivered-price discount math.
- Existing Walmart provider, exact-offer identity, exact-price enrichment, card rendering, queue snapshot, and public-lane tests pass.
- Full Python tests, import smoke check, and static/compile validation pass in CI.
- Review finds no unresolved correctness, compatibility, or conflicting implementation issues.
- Temporary files or redundant code are removed before merge.
- Production remains unclaimed until the merged code is deployed to the canonical Discloud app and confirmed in logs/cards.

## Current branch
`fix/walmart-delivered-offer-price`

## Deployment boundary
- Do not modify or deploy duplicate Discloud app `1785806676351`.
- Do not merge PR #200 during this task.
- Do not weaken exact verification or lower public alert thresholds.
- Do not advertise unknown shipping as `$0` or `free`.

## Backlog
- Deploy merged `main` to canonical Discloud app and verify PRs #218/#219 in production if that has not already been done.
- Rebase and finish PR #200 only after this task closes.
