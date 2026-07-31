# Active Task

## Status
Complete — scheduled Walmart autoscan now spreads its bounded routes across major public-safe categories while preserving verified-only posting and one registered runtime.

## Scope
Trace and repair the live resilient autoscan path responsible for configured servers receiving no public posts. Keep one active runtime, four scheduled routes, eight manual routes, one provider scan at a time, and verified-only public posting.

## Findings
- The runtime loads `ResilientAutoScanRunnerCog`, which inherits the native runner.
- Scheduled scans explicitly cap work at four routes.
- The native selector spent all four scheduled routes inside one rotated category, then recorded an empty pass and waited behind the six-hour safety floor.
- The native fallback still referenced deleted `AUTO_SCAN_FAST_QUERY_COUNT`.
- `bot.py` retained an unused direct native runner import even though only the resilient runner is registered.
- The temporary write-enabled workflow outlived its guarded applicator and conflicted with the active branch history.

## Changes
- Scheduled four-route scans now use the existing broad public-safe builder, selecting one route across multiple major categories.
- Manual eight-route scans continue using the same broad builder.
- Replaced the deleted fast-policy fallback with `AUTO_SCAN_SCHEDULED_QUERY_COUNT`.
- Removed the unused direct native runner import from `bot.py`.
- Added cross-runner static regressions for broad scheduled coverage and one runtime import/registration.
- Rebuilt the work as clean PR #156 directly on current `main`.
- Removed the temporary self-modifying autoscan workflow.

## Validation
- Repository compilation passed.
- Import smoke passed for 28 modules and 12 required symbols.
- Full pytest regression suite passed: 664 tests.
- Targeted native/resilient autoscan assertions passed inside the full suite.
- PR #156 is mergeable against current `main`.

## Cleanup status
Complete. Temporary workflow scaffolding is removed, the deleted fast/deep policy name is absent from the native selector, and the runtime registration path is `bot.py` → resilient runner → native implementation with no duplicate cog registration.

## Blockers
None.

## Backlog
- Add a single `/movies` command group for official free-ticket drops, starting with Atom's first-party promotions hub and expanding to official Atom social/email/SMS/push, movie-studio, distributor, and partner sources. Include setup, latest, manual scan/test, deduplicated alerts, source labeling, restrictions, expiration, and public-vs-unique-code classification.
- Improve scheduled zero-post diagnostics surfaced to server owners after this execution-path repair is validated.
