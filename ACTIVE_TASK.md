# Active Task

## Status
Complete — full Discord deal-bot recovery and repository-wide audit passed final validation.

## Scope
The production path was traced and repaired from process startup through Discord command registration, provider discovery, searches, normalization, variant/offer verification, filtering, cache reuse, deduplication, channel routing, embeds/views, autoscan scheduling, manual scans, persistence, error handling, and deployment validation.

## Non-negotiable implementation rules
- No monkey patches.
- No startup-installed guards or runtime method replacement.
- Repair the authoritative production implementation and its real callers.
- Remove all temporary one-shot workflow scaffolding before merge.

## Resolved findings
- Wired Walmart credentials and enabled state into the registered runtime provider.
- Kept uncertain autoscan review/scout cards private while preserving them for staff review.
- Cleared stale process-global providers before authoritative startup registration.
- Normalized duplicate scan locks and added stale-lock recovery.
- Closed the retained manual review dropdown permission bypass.
- Preserved mixed-result private review leads instead of discarding them when public cards also exist.
- Added durable reserved, sending, and posted public-delivery states.
- Isolated active-cache writes per card so one malformed card cannot roll back earlier valid cards.
- Removed setup self-heal from the bot startup lifecycle.
- Upgraded CI from compile-only checks to compilation, import smoke validation, and the complete pytest suite.

## Validation
- Clean-head compilation passed.
- Import smoke validation passed for 28 critical modules and 12 required symbols.
- Full pytest suite passed after replacing the obsolete startup-self-heal expectation with a regression that forbids startup self-heal wiring.
- Active-cache isolation regression passed.
- Temporary repair workflows were deleted and normal test-only CI was restored.

## Architecture cleanup
- `sniperplug.bot` registers only `native_auto_scan_runner.AutoScanRunnerCog`.
- `auto_scan_runner.py` remains a shared base/helper module used by the native runner; it is not separately registered and does not create a second autoscan runtime.
- No monkey patch or startup-installed repair guard is part of the recovered runtime.
- One authoritative public-posting and active-cache implementation remains in `sniperplug/services/public_deal_posts.py`.

## Definition of Done
- Root causes documented against the real runtime and callers.
- Startup, command, manual scan, autoscan, provider, normalization, dedupe, cache, routing, and posting paths repaired.
- Variant, seller, fulfillment, offer, price, coupon, location, and stale-data safety preserved or improved.
- Targeted tests and complete regression suite pass.
- Compilation, import smoke validation, and CI configuration pass.
- Temporary, conflicting, and duplicate runtime paths were removed or correctly identified as shared dependencies.
- Final conflict inspection shows one registered autoscan runtime and one authoritative public-posting implementation.

## Work log
- 2026-07-30: Resumed the beginning-to-end SniperPlug Discord deal-bot recovery.
- 2026-07-30: Repaired Walmart runtime configuration and provider lifecycle.
- 2026-07-30: Enforced verified-only public autoscan posting and private review handling.
- 2026-07-30: Hardened scan locks, routing, permissions, completion checkpoints, dedupe, and public-post durability.
- 2026-07-30: Removed startup self-heal wiring per owner instruction.
- 2026-07-30: Landed per-card active-cache commit/rollback isolation.
- 2026-07-30: Removed temporary workflow scaffolding and passed final clean-head CI.

## Blockers
None.

## Backlog
SniperPlug Site / Whop importer work can resume after PR #125 is merged.
