# Active Task

## Status
Validation in progress — scheduled Walmart coverage is repaired without loosening verified-deal safety.

## Scope
Trace and repair the live resilient autoscan path responsible for configured servers receiving no public posts. Keep one active runtime, four scheduled routes, eight manual routes, one provider scan at a time, and verified-only public posting.

## Findings
- The bot registers `ResilientAutoScanRunnerCog`, which inherits the native Walmart runner.
- Scheduled scans were capped at four routes but spent all four inside one rotated category.
- An empty narrow-category pass then waited behind the six-hour safety floor, sharply reducing the chance of discovering a verified deal.
- The native fallback referenced removed `AUTO_SCAN_FAST_QUERY_COUNT`, which could raise `AttributeError` on the scheduled/default path.
- `bot.py` retained an unused direct native-runner import even though only the resilient runner was registered.
- Four tests preserved those redundant and obsolete implementation details instead of validating the real inheritance/registration path.

## Changes
- Scheduled four-route scans now use the existing broad public-safe builder across major Walmart categories.
- Manual eight-route scans use the same broad builder with the larger bounded route count.
- Replaced the removed fast-policy fallback with `AUTO_SCAN_SCHEDULED_QUERY_COUNT`.
- Removed the unused direct native-runner import from `bot.py`.
- Replaced stale marker tests with execution-path assertions: one resilient registration, native inheritance, broad selection, and verified-only public posting.

## Validation
- Guarded source replacements passed exact-match checks.
- Changed runtime and tests compile successfully.
- Focused native/resilient autoscan tests passed.
- Import smoke passed for 28 critical modules and 12 required symbols.
- Complete pytest regression suite is running on the cleaned final branch head.

## Cleanup status
Complete. The temporary applicator is deleted and the temporary workflow is absent from the branch. No monkey patch, startup guard, duplicate runner registration, or temporary runtime code remains.

## Blockers
None.

## Backlog
- Improve scheduled zero-post diagnostics surfaced to server owners after this execution-path repair is merged and deployed.
