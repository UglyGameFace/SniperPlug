# Active Task Record

## Scope
Finish the Walmart exact-detail terminal identity queue follow-up from merged PR #193 before starting the queued HP Store monitor.

## Findings
- Terminal maintenance used `COUNT(*)` plus `UPDATE` with `julianday()` predicates every worker batch.
- The predicates were not covered by terminal-status timestamp indexes.
- Weekly rearm-only cycles were not logged immediately.

## Changes
- Replaced pre-count scans with one indexed `UPDATE` per maintenance action and affected-row accounting.
- Added terminal rearm and quarantine indexes through the runtime compatibility path.
- Added immediate logging for rearm-only cycles.
- Added regression coverage for weekly rediscovery rearm, one-time behavior, and index creation.

## Validation
- Branch diff inspected against `main`; three intended files changed before this record.
- GitHub Actions validation pending on the follow-up pull request.

## Cleanup
- Removed the obsolete `_count_rows` helper and duplicate read-before-write scans.

## Blockers
- None currently.

## Backlog
- #195: Build a standalone HP Store monitoring bot/service that meshes with SniperPlug and independently discovers catalog-wide price/clearance changes.
