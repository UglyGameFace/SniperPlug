# Active Task

## Status
In progress — restore useful scheduled Walmart autoscan coverage without loosening verified-deal safety.

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

## Validation required
- Compile changed runtime and tests.
- Run targeted native/resilient autoscan tests.
- Run import smoke validation.
- Run complete pytest regression suite.
- Inspect final diff for temporary files, stale policy names, duplicate runner wiring, and conflicts.

## Cleanup status
Temporary applicator/workflow scaffolding is removed. Final cleanup and conflict inspection remain pending until clean-head CI passes.

## Blockers
None.

## Backlog
- Add a single `/movies` command group for official free-ticket drops, starting with Atom's first-party promotions hub and expanding to official Atom social/email/SMS/push, movie-studio, distributor, and partner sources. Include setup, latest, manual scan/test, deduplicated alerts, source labeling, restrictions, expiration, and public-vs-unique-code classification.
- Improve scheduled zero-post diagnostics surfaced to server owners after this execution-path repair is validated.
