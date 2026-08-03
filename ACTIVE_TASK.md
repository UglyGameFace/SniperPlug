# Active Task

## Status
Complete — HP big-ticket price-error fast lane implemented, validated, and ready for deployment.

## Scope
Keep the standalone HP watcher architecture, but make its urgent alert path focus exclusively on expensive products with extreme, independently verified markdowns. Default policy: HP reference/current value of at least **$200** and a verified discount of at least **69%**.

## Root cause and execution-path findings
- The first HP watcher prioritized existing markdowns and used one general due queue.
- Enough ordinary sale products could delay a full-price expensive item that suddenly dropped 69%+.
- A server-level discount threshold alone could still allow cheap accessories with large percentages.
- Expensive products need protected polling capacity before the drop occurs, using their current/MSRP/prior exact value to classify them.

## Changes
- Added `HPPriceErrorWatcherService` on top of the proven sitemap, identity, structured-price, history, and outbox implementation.
- Reserve up to 75% of each offer batch for known due big-ticket products; unused capacity immediately fills from unclassified and background products.
- Default watcher cycle is 10 seconds with an 80-offer structured batch.
- Known big-ticket products are rescheduled every 45 seconds.
- Cheap products are returned to the slower background schedule even when they have a large percentage markdown.
- Only $200+ reference/value products at 69%+ off can publish HP events.
- Alert-worthy observations still require the independent cache-busted exact-product confirmation.
- Added candidate proof attributes identifying the price-error lane and policy floors.
- Added configurable environment values:
  - `HP_BIG_TICKET_MIN_REFERENCE_PRICE=200`
  - `HP_PRICE_ERROR_MIN_DISCOUNT_PERCENT=69`
  - `HP_BIG_TICKET_OFFER_INTERVAL_SECONDS=45`
- Restored the canonical `/autoscan_health` exact-verification queue label required by the established command-surface regression.

## Validation
- Python compilation passed.
- SniperPlug and standalone watcher import smoke checks passed.
- Targeted big-ticket qualification, protected-claim, scheduling, history, and configuration tests passed.
- Complete pytest suite passed.
- Pull request is mergeable with zero commits behind `main`.
- Review-thread inspection found no unresolved findings.
- Final changed-file inspection found no temporary, backup, or applicator files.

## Cleanup status
Complete.

## Blockers
None.

## Backlog
- Deploy/redeploy the separate Discloud HP watcher using the same Turso credentials as SniperPlug.
- Measure live catalog size and effective big-ticket sweep latency from `/autoscan_health` before changing the bounded defaults.
