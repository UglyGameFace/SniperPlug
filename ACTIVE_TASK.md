# Active Task

## Status
In progress — make scheduled Walmart scans retain bounded observed-price history so verified price-drop deals can mature and post.

## Scope
Repair the authoritative autoscan collector only. Keep the strict verified public threshold, four scheduled routes, eight manual routes, bounded provider concurrency, and one scheduled guild scan at a time.

## Root cause
- The live autoscan path called `collect_verified_discount_cards(... use_price_memory=False)`.
- Logs therefore reported `price_memory_summary: not used` after every scan.
- Walmart frequently omits trustworthy reference prices, so strict verification rejected nearly every product.
- Because scheduled scans discarded observations, SniperPlug could never build its own exact-item historical baseline and later prove a real price drop.

## Changes
- Delegate the authoritative autoscan collector to the existing bounded observed-memory service.
- Preserve two pages per route, concurrency three inside the provider collector, a four-item memory recheck seed limit, and a 300-observation write cap per pass.
- Keep all public-deal quality gates unchanged.
- Add structural regressions forbidding `use_price_memory=False` on autoscan.

## Validation required
- Compile changed runtime and tests.
- Run targeted observed-memory and autoscan tests.
- Run import smoke.
- Run complete pytest suite.
- Remove temporary applicator/workflow and inspect final diff before merge.

## Cleanup status
Pending.

## Blockers
None.

## Backlog
- Surface observation baseline counts and time-to-maturity in `/autoscan_health` after this fix is deployed.
