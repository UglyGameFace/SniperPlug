# Active Task

## Status
Active — full Discord deal-bot recovery and repository-wide audit.

## Scope
Trace and repair the production execution path from process startup through Discord command registration, provider discovery, searches, normalization, variant/offer verification, filtering, cache reuse, deduplication, channel routing, embeds/views, autoscan scheduling, manual scans, persistence, error handling, and deployment configuration.

Do not start unrelated SniperPlug Site, Whop importer, Dank Shield, or cosmetic work until this task satisfies its Definition of Done. New findings remain part of this audit unless explicitly forced to another task by the owner.

## Findings
- Startup loaded Walmart credentials into `Settings` but discarded them by registering `WalmartProvider(configured=False)`.
- The registered native autoscan inherits shared scheduling and helper behavior from the legacy runner.
- Native autoscan publicly posted uncertain review/scout cards despite staff-facing text saying those cards remain private.
- Tests explicitly required the conflicting public scout fallback.
- Turso/libsql lacks a multi-write transaction abstraction for atomic workflows.
- CI compiled Python but did not run pytest.
- Startup maintenance and self-heal retry behavior require repair.
- Broad exception handling and unpinned dependencies require review.

## Changes
- Wired the loaded Walmart consumer ID, key version, private key, publisher ID, and enabled flag into the actual registered runtime provider.
- Added Walmart runtime configuration regression tests.
- Changed the registered native autoscan to verified-only public posting.
- Kept uncertain review/scout cards private and available through the staff review UI.
- Updated conflicting scout fallback tests to enforce the private-only contract.
- Updated the main Python CI workflow to compile, smoke-import, and run the complete pytest suite.
- Opened draft PR #125 for the active recovery branch.

## Validation
- Static regression coverage added for Walmart runtime wiring and private-only autoscan review behavior.
- GitHub Actions is configured to run the full suite on pull requests; current branch checks are pending workflow pickup.
- Full regression status is not yet green and the task is not complete.

## Cleanup status
- Removed native autoscan constants and branches used only for automatic public scout posting.
- Legacy shared runner remains referenced by the registered native cog and cannot be deleted until all callers/helpers are mapped.

## Definition of Done
- Root causes documented against the real runtime and callers.
- Startup, command, manual scan, autoscan, provider, normalization, dedupe, cache, routing, and posting paths repaired.
- Variant, seller, fulfillment, offer, price, coupon, location, and stale-data safety preserved or improved.
- Targeted tests and regression suite pass.
- Compilation/static validation and CI configuration pass.
- Redundant, obsolete, duplicate, temporary, conflicting, and partially implemented paths are removed or integrated after reference inspection.
- Deployment/environment documentation matches runtime behavior.
- Final conflict inspection shows one authoritative implementation per responsibility.

## Work log
- 2026-07-30: Owner explicitly resumed SniperPlug Discord deal-bot work and requested a beginning-to-end repair.
- 2026-07-30: Created branch `audit/full-discord-deal-bot-recovery` from `main`.
- 2026-07-30: Confirmed startup registers `native_auto_scan_runner.AutoScanRunnerCog` while a separate legacy `auto_scan_runner.py` remains.
- 2026-07-30: Repaired Walmart runtime provider configuration.
- 2026-07-30: Removed automatic public posting of uncertain autoscan review/scout leads.
- 2026-07-30: Made the full pytest suite mandatory in CI.

## Blockers
- GitHub Actions had not attached workflow runs to the newest commits at the time of this update.

## Backlog
- SniperPlug Site / Whop importer work remains paused while this task is active.
