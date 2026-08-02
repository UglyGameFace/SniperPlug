# Active Task

## Status
In progress — finish and validate the standalone HP Store watcher and its SniperPlug delivery integration.

## Scope
Build a separate continuously running HP catalog/price watcher that requires no Discord token, shares SniperPlug's Turso/libSQL database, and sends only exact-verified HP events through SniperPlug's existing server thresholds, category filters, channels, duplicate guards, feedback controls, active-deal cache, and opt-in DMs.

## Root cause and execution path findings
- HP product pages can expose a visible `$0.00` placeholder, so visible page text is not reliable price proof.
- Official HP sitemaps can discover the US Store catalog.
- HP's structured `HPServices?action=cupis` response exposes exact `productId`, `partNumber`, `price`, and `lPrice` fields.
- A separate Discloud app cannot share SniperPlug's local SQLite file; both production processes must use the same Turso/libSQL database.
- Review found that a non-MSRP historical reference was selected for one price-drop cycle but not persisted, which would demote a sustained markdown to the slow polling lane.
- Review found that a permanently failing fanout event could be released with no delay and monopolize every oldest-first claim batch.
- `/autoscan_health` originally had no visibility into the separate HP process even though health state was stored.

## Changes
- Added a standalone HP watcher entrypoint, configuration, bounded HTTP client, sitemap/product parsers, durable scheduling, exact offer history, and health state.
- Added strict identity and price gates that reject zero prices, malformed responses, missing identity, cross-product/SKU data, unsupported URLs, and untrusted references.
- Label HP `lPrice` as HP MSRP; use prior exact HP.com observations when MSRP is absent.
- Added a shared leased/idempotent verified-retailer event outbox and SniperPlug HP fanout.
- Added HP-specific Discord cards and public proof gates while reusing existing per-server delivery controls and DM receipts.
- Added HP to canonical setup and a one-time migration for existing enabled Walmart alert destinations.
- Persist the selected historical reference baseline so sustained non-MSRP markdowns remain on the fast polling lane.
- Add exponential fanout retry backoff and terminal dead-lettering so one broken destination cannot starve newer events.
- Surface HP watcher health, catalog coverage, failures, and pending fanout in `/autoscan_health`.
- Added separate Discloud deployment configuration and documentation using the same Turso credentials and no Discord token.

## Validation required
- Compile all changed Python files.
- Run import smoke checks for both SniperPlug and the standalone HP watcher.
- Run targeted HP parser, storage/history, public proof, health, event-backoff, setup migration, and configuration tests.
- Run the complete pytest suite.
- Reinspect review threads, conflicts, redundant code, temporary files, and the final branch diff before merge.

## Cleanup status
In progress. No temporary applicators or placeholder files are intended in the branch. The previously unused HP health reader is now integrated into `/autoscan_health`.

## Blockers
None currently.

## Backlog
- After merge, create the second Discloud application from `discloud.hp-watcher.config` and give it the exact same Turso/libSQL credentials as SniperPlug.
- Observe initial catalog coverage and tune only within the bounded settings if HP rate limits or schema behavior require it.
