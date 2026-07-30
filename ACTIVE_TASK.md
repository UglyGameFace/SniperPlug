# Active Task

## Status
Active — full Discord deal-bot recovery and repository-wide audit.

## Scope
Trace and repair the production execution path from process startup through Discord command registration, provider discovery, searches, normalization, variant/offer verification, filtering, cache reuse, deduplication, channel routing, embeds/views, autoscan scheduling, manual scans, persistence, error handling, and deployment configuration.

Do not start unrelated SniperPlug Site, Whop importer, Dank Shield, or cosmetic work until this task satisfies its Definition of Done. New findings remain part of this audit unless explicitly forced to another task by the owner.

## Non-negotiable implementation rules
- No monkey patches.
- No startup-installed guards or runtime method replacement.
- Repair the authoritative production implementation and its real callers.
- Remove all temporary one-shot workflow scaffolding before merge.

## Findings
- Startup loaded Walmart credentials into `Settings` but discarded them by registering an unconfigured provider.
- The registered native autoscan inherits shared scheduling and helper behavior from the legacy runner.
- Native autoscan publicly posted uncertain review/scout cards despite staff-facing text saying those cards remain private.
- Tests explicitly required the conflicting public scout fallback.
- The process-global provider registry retained stale providers and rejected clean re-registration.
- Scan lock normalization allowed whitespace variants of the same query to run separately.
- The retained manual review dropdown bypassed the Manage Server permission check enforced by the newer Post buttons.
- Native autoscan only captured private review leads when no verified public card existed, so useful review leads could be discarded on mixed-result passes.
- Active-cache writes used one batch transaction, allowing one malformed card to roll back earlier valid card writes.
- Turso/libsql lacks a multi-write transaction abstraction for atomic workflows.
- CI compiled Python but did not run pytest.
- Broad exception handling and unpinned dependencies require review.

## Changes
- Wired loaded Walmart credentials and enabled state into the actual registered runtime provider.
- Added Walmart runtime configuration regression tests.
- Changed the registered native autoscan to verified-only public posting.
- Kept uncertain review/scout cards private and available through the staff review UI.
- Updated conflicting scout fallback tests to enforce the private-only contract.
- Updated the main Python CI workflow to compile, smoke-import, and run the complete pytest suite.
- Made provider startup restart-safe by clearing stale process-global providers before fresh wiring.
- Added normalized provider lookups and controlled replacement support.
- Hardened duplicate scan locks with whitespace/case normalization and stale-lock recovery.
- Removed setup self-heal from the bot startup lifecycle.
- Enforced Manage Server permission through one fail-closed guard for both modern buttons and the retained compatibility dropdown used to publish private review leads.
- Added regression coverage for the manual review permission boundary.
- Opened draft PR #125 for the active recovery branch.

## Validation
- Regression coverage exists for Walmart runtime wiring, private-only autoscan review behavior, provider registry lifecycle, scan-lock normalization/stale recovery, manual review posting permissions, public-post durability, and autoscan completion.
- GitHub Actions is configured to run the full suite on pull requests.
- Final clean-head CI is still required before merge.

## Cleanup status
- Removed native autoscan constants and branches used only for automatic public scout posting.
- Removed startup setup-self-heal wiring.
- Temporary active-cache patch workflows must self-delete before merge.
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
- 2026-07-30: Repaired provider registry lifecycle, duplicate scan normalization, and stale lock recovery.
- 2026-07-30: Closed the compatibility-dropdown permission bypass in manual private-review posting.
- 2026-07-30: Removed setup self-heal from bot startup per owner instruction.

## Blockers
- Per-card active-cache isolation must land and pass final clean-head CI.

## Backlog
- SniperPlug Site / Whop importer work remains paused while this task is active.
