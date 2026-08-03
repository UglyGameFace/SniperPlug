# Active Task

## Status
Complete — standalone Target catalog and exact-offer watcher implemented, hardened, and validated in PR #199.

## Scope
Build Target as a separate continuously running watcher that uses Target's published PDP sitemap for catalog discovery, Target RedSky JSON for exact TCIN pricing and fulfillment, the existing Turso/libSQL verified-retailer outbox for delivery, and SniperPlug's existing server thresholds, categories, channels, duplicate guards, feedback controls, active-deal cache, and opt-in DMs.

## Root cause and execution-path findings
- Target does not provide the same easy public retail API contract as Best Buy.
- Target publishes a PDP sitemap index that can provide broad catalog identity without relying on search-result completeness.
- Target's web application uses structured RedSky product and fulfillment responses keyed by exact TCIN and store/ZIP context.
- The existing verified-retailer outbox was reusable, but its fanout consumer hard-rejected every retailer except HP.
- Target availability strings require negative-first parsing because `unavailable` contains the word `available`.
- A fulfillment response can contain more than one store; pickup proof must never borrow availability from a different store.
- Target Plus products require an explicit marketplace seller and must not fall back to a manufacturer/vendor name or be mislabeled as sold by Target.
- Public alerts must fail closed unless exact TCIN, seller, prices, and local fulfillment survive an independent second request.
- The standalone process must share SniperPlug's Turso/libSQL database; a separate local SQLite file cannot feed the bot.

## Changes
- Added standalone Target configuration, entrypoint, Discloud configuration, bounded HTTP client, gzip/XML sitemap parsing, RedSky JSON parsing, exact offer history, durable scheduling, and health state.
- Added optional `TARGET_WATCH_TCINS` seeding while full sitemap discovery progresses.
- Added strict TCIN URL validation, positive-price validation, exact seller preservation, store/ZIP context, fulfillment verification, and independent cache-busted confirmation.
- Added fail-closed Target Plus handling: marketplace products without an exact seller identity are rejected.
- Added exact-store pickup selection: multi-store responses cannot leak another store's stock into the configured location.
- Removed the unsafe inference that a false global out-of-stock flag automatically means the item is purchasable.
- Added a protected $200+/69% price-error lane while preserving ordinary exact markdown events for server-level thresholds.
- Added Target-specific Discord cards and a Target public proof gate.
- Refactored verified-retailer fanout into explicit retailer dispatch instead of a hard-coded single-retailer branch.
- Added Target to supported retailer normalization, canonical setup, existing enabled Walmart destination migration, `/autoscan_health`, active-deal cache, and DM fanout.
- Added deployment documentation and CI coverage.

## Validation
- Python compilation passed.
- Import smoke check passed.
- Full Python Check workflow passed.
- Full repository pytest workflow passed.
- Target parser regressions cover exact TCINs, gzip sitemaps, unavailable-state parsing, Target Plus seller identity, configured-store-only pickup proof, and non-invented availability.
- Target storage/history regressions cover initial verified markdowns, duplicate suppression, later price drops, big-ticket scheduling, and unavailable-offer blocking.
- Target public proof regressions cover domain, TCIN/store offer identity, trusted references, fulfillment, and per-server thresholds.
- PR #199 is mergeable, zero commits behind `main`, with no unresolved inline review threads.
- Final changed-file inspection found no temporary, backup, or applicator files.

## Cleanup status
Complete. Target code is isolated under `sniperplug/target_watcher` and Target-specific delivery modules; shared delivery is extended through one retailer dispatch function rather than duplicated.

## Blockers
None in code.

## Backlog
- Merge PR #199.
- Deploy the separate Target Discloud app using `discloud.target-watcher.config` and the same Turso credentials as SniperPlug.
- Observe live sitemap size, RedSky response stability, and effective sweep latency before changing bounded defaults.
