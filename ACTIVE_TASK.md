# Active Task

## Status
Active — full Discord deal-bot recovery and repository-wide audit.

## Scope
Trace and repair the production execution path from process startup through Discord command registration, provider discovery, searches, normalization, variant/offer verification, filtering, cache reuse, deduplication, channel routing, embeds/views, autoscan scheduling, manual scans, persistence, error handling, and deployment configuration.

Do not start unrelated SniperPlug Site, Whop importer, Dank Shield, or cosmetic work until this task satisfies its Definition of Done. New findings remain part of this audit unless explicitly forced to another task by the owner.

## Preserved findings
- Walmart runtime registration and environment configuration disagree.
- The active native autoscan inherits legacy runtime code.
- Public scout fallback behavior conflicts with the stated private-only policy.
- Turso/libsql lacks a multi-write transaction abstraction for atomic workflows.
- CI does not run the complete pytest suite.
- Startup maintenance and self-heal retry behavior require repair.
- Broad exception handling and unpinned dependencies require review.

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
- 2026-07-30: Began execution-path audit; confirmed startup registers `native_auto_scan_runner.AutoScanRunnerCog` while a separate legacy `auto_scan_runner.py` remains in the repository.

## Backlog
- SniperPlug Site / Whop importer work remains paused while this task is active.
