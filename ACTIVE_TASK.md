# Active Task

## Status
In progress — implement and validate the standalone Target catalog and exact-offer watcher plus shared SniperPlug delivery integration.

## Scope
Build Target as a separate continuously running watcher that uses Target's published PDP sitemap for catalog discovery, Target RedSky JSON for exact TCIN pricing and fulfillment, the existing Turso/libSQL verified-retailer outbox for delivery, and SniperPlug's existing server thresholds, categories, channels, duplicate guards, feedback controls, active-deal cache, and opt-in DMs.

## Root cause and execution-path findings
- Target does not provide the same easy public retail API contract as Best Buy.
- Target publishes a PDP sitemap index that can provide broad catalog identity without relying on search-result completeness.
- Target's web application uses structured RedSky product and fulfillment responses keyed by exact TCIN and store/ZIP context.
- The existing verified-retailer outbox was reusable, but its fanout consumer hard-rejected every retailer except HP.
- Target availability strings require negative-first parsing because `unavailable` contains the word `available`.
- Public alerts must fail closed unless exact TCIN, seller, prices, and local fulfillment survive an independent second request.
- The standalone process must share SniperPlug's Turso/libSQL database; a separate local SQLite file cannot feed the bot.

## Changes
- Added standalone Target configuration, entrypoint, Discloud configuration, bounded HTTP client, gzip/XML sitemap parsing, RedSky JSON parsing, exact offer history, durable scheduling, and health state.
- Added optional `TARGET_WATCH_TCINS` seeding while full sitemap discovery progresses.
- Added strict TCIN URL validation, positive-price validation, seller preservation, store/ZIP context, fulfillment verification, and independent cache-busted confirmation.
- Added a protected $200+/69% price-error lane while preserving ordinary exact markdown events for server-level thresholds.
- Added Target-specific Discord cards and a Target public proof gate.
- Refactored verified-retailer fanout into explicit HP/Target dispatch instead of a hard-coded HP-only branch.
- Added Target to supported retailer normalization, canonical setup, existing enabled Walmart destination migration, `/autoscan_health`, active-deal cache, and DM fanout.
- Added deployment documentation and CI coverage.

## Validation
- New Target Python files compile successfully.
- Target parser/storage targeted tests pass locally: 6 passed.
- Full repository tests and branch CI still required after the branch files are committed.
- Final conflict, review, temporary-file, duplicate-code, and changed-file inspection still required.

## Cleanup status
In progress. New code is isolated under `sniperplug/target_watcher` and Target-specific delivery modules; the shared outbox is extended through one dispatch function rather than duplicated.

## Blockers
None currently.

## Backlog
- Deploy the separate Target Discloud app using `discloud.target-watcher.config` and the same Turso credentials as SniperPlug after merge.
- Observe live sitemap size, RedSky response stability, and effective sweep latency before changing bounded defaults.
