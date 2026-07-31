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

## Changes
- Scheduled four-route scans now use the existing broad public-safe builder, selecting one route across multiple major categories.
- Manual eight-route scans continue using the same broad builder.
- Replaced the deleted fast-policy fallback with `AUTO_SCAN_SCHEDULED_QUERY_COUNT`.
- Removed the unused direct native runner import from `bot.py`.
- Added cross-runner static regressions for broad scheduled coverage and one runtime import/registration.

## Validation required
- Compile changed runtime and tests.
- Run targeted native/resilient autoscan tests.
- Run import smoke validation.
- Run complete pytest regression suite.
- Inspect final diff for temporary files, stale policy names, duplicate runner wiring, and conflicts.

## Cleanup status
Pending. Temporary applicator/workflow must be removed before merge.

## Blockers
None.

## Backlog
- Improve scheduled zero-post diagnostics surfaced to server owners after this execution-path repair is validated.
