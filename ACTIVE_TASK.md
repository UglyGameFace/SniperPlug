# Active Task

## Status
Implementation complete and merge-ready on PR #220 (`fix/walmart-delivered-offer-price`). No production deployment has occurred; production behavior remains unclaimed until the merged `main` branch is deployed to the canonical Discloud app and confirmed in live logs/cards.

## Scope
Bind Walmart current-price, reference-price, seller, offer, fulfillment, condition, and shipping proof to one selected offer. Use the payable delivered total for qualification whenever mandatory shipping is explicitly known. Alternate-seller and unit-price values remain context only and never participate in selected-offer deal math.

## Root causes confirmed
- Broad item-level `minPrice` could represent a different seller while seller/offer identity was resolved separately.
- Shipping had no first-class item-price, shipping-cost, delivered-price, or proof state in candidates/deals.
- Third-party marketplace identity could be complete while mandatory shipping remained unknown.
- A page-level `wasPrice` could be compared with a nested selected offer belonging to another seller.
- Generic price dictionaries could contain unit pricing such as `$0.12/oz` rather than the full offer price.
- Truthy display fallbacks could discard valid `$0.00` price evidence.

## Implemented changes
- Normalize one atomic offer from `selectedOffer`, `buyBoxOffer`, `primaryOffer`, `offer`, or the root Walmart buy-box payload.
- Bind selected seller name/ID, offer ID, condition, fulfillment, item price, shipping state/cost, delivered total, and same-offer reference price.
- Keep `minPrice` and `bestMarketplacePrice` as explicitly labeled other-seller/flip context only.
- Use selected-offer item price plus mandatory shipping as Walmart `current_price` / `api_current_price` and discount input.
- Fail closed for third-party marketplace offers when shipping is not returned; no alertable current price or discount survives.
- Preserve Walmart-owned item prices with `checkout_dependent` shipping rather than falsely claiming free or delivered shipping.
- Reject measurement-unit price structures while accepting currency-shaped amount structures.
- Block page-level reference prices when a nested selected offer has no same-offer reference; retain the page value only as non-discount context.
- Carry item price, shipping, delivered total, proof sources, payable-price status, seller, and offer identity into normalized deals and Walmart cards.
- Preserve valid `$0.00` item/delivered evidence with explicit `is not None` rendering.
- Avoid assigning unverified delivered totals to non-Walmart candidates.
- Preserve existing positional dataclass constructor compatibility by appending the new fields.

## Validation
- Python 3.11 full suite: **1,101 passed**, one upstream `audioop` deprecation warning.
- Python 3.12 Python Check: passed.
- Import smoke check: passed.
- `pip check`: passed.
- `compileall` across app, tests, and entry points: passed.
- Targeted regressions cover paid shipping, explicit free shipping, unknown marketplace shipping, alternate seller minimums, same-offer reference proof, cross-offer reference blocking, seller switching, exact-detail replacement, unit-price rejection, currency-unit acceptance, zero-value evidence, card rendering, and non-Walmart delivery semantics.
- Qodo review findings for non-Walmart delivered totals, unit-price parsing, and zero-value rendering are resolved and outdated.
- Final changed-file inspection contains only implementation, active-task documentation, and targeted regression files; temporary probe files were removed.

## Cleanup and conflict inspection
- No duplicate or temporary implementation remains.
- Existing exact item, seller, offer, variant, condition, fulfillment, duplicate, queue, and per-server threshold gates remain intact.
- No threshold was lowered and no identity/shipping proof was weakened.
- PR #200 remains isolated and unmerged.
- Duplicate Discloud app `1785806676351` remains outside this task and must stay offline.

## Current branch
`fix/walmart-delivered-offer-price`

## Deployment boundary
After merge, deploy only the canonical SniperPlug Discloud app and verify:
- cards show selected seller, item price, shipping, and delivered total;
- other-seller minimums are labeled context only;
- unknown marketplace shipping is blocked rather than shown as free/$0;
- discount thresholds use the delivered total and same-offer reference;
- no new errors or seller/offer mismatches appear in exact-verification logs.

## Backlog
- Deploy merged `main` to the canonical Discloud app and verify PRs #218, #219, and #220 in production.
- Rebase and finish PR #200 only after the production stability/accuracy pass closes.
