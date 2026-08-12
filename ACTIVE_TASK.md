# Active Task

## Status
**Complete code-side — SP-WM-PAGE-RETRY-FANOUT satisfied its Definition of Done. PR #235 merged into `main` at `14a9a518bb9d7db5246f962f2a0f82d6daec4953`, all three canonical PR workflows passed, and all three canonical workflows on the merge commit passed. The implementation task lock is released after validation of this task-record-only close commit. Production behavior remains intentionally unclaimed until merged `main` is deployed and live Walmart traffic exercises the new retry/public-skip telemetry.**

## Task
**SP-WM-PAGE-RETRY-FANOUT — Stop Walmart page-recovery transient storms from starving fresh discovery, expose exact retry/public skip reasons, and preserve strict item/price/seller/offer/shipping/variant/reference safety.**

## Root cause / findings
- PR #234's seller-identity recovery was confirmed live: post-deploy exact batches consistently reported `identity unavailable / safely blocked 0`, versus the previous 16–24 seller blocks per batch.
- The next production bottleneck was repeated 6–8/8 transient exact failures. Product-page HTTP/challenge/network/price-change failures were stored as ordinary `retry`, and the fresh-work pressure policy counted those retries exactly like brand-new discoveries. Recovery hiccups could therefore raise fresh/retry pressure above the 12-row catalog-pause threshold and stop new Walmart discovery.
- The former outer exact-worker hard timeout was 8 seconds while the official Walmart provider allows a 12-second HTTP timeout and product-page recovery independently allows up to 8 seconds. That outer budget could race legitimate bounded provider work. Live timing did not prove it was the dominant failure class, so typed telemetry was added rather than guessing.
- Retry-delay/storage policy existed in both compatibility and production bulk persistence, creating drift risk.
- The page-recovery one-time rearm counter treated unsupported/negative cursor `rowcount` as zero instead of falling back to `SELECT changes()`.
- Live fanout proved personal DM delivery end to end for KC Masterpiece at `$3.98`, 67.3% discount (`DM checked/matched/sent 1/1/1`). The same event returned generic public `not_alertable=1` for a 40% guild, but the old public path collapsed category suppression, quality rejection, and `should_alert=false` into one counter. That log alone did not prove a public-post logic bug, so no safety/category gate was weakened without evidence.

## Changes delivered
- `WalmartProviderError` now carries bounded `retry_kind` classifications for API HTTP status, timeout, network, non-JSON, payload shape, and generic detail failures.
- `WalmartPageRecoveryTransientError` now carries bounded reason codes for page HTTP status, timeout, network, challenge, redirect, oversized response, and selected-offer price mismatch.
- Added queue status `page_retry` with bounded delays: 10 minutes for price-change mismatch, 30 minutes for ordinary page-recovery transients, and 60 minutes for 403/429/challenge pressure.
- Centralized retry persistence through one `_failure_storage_policy()` shared by compatibility and production bulk persistence.
- Added a separate bounded page-recovery pressure lane. Page retries still count toward total emergency pressure but no longer trip the normal 12-row fresh-discovery pause or 48-row fresh-drain thresholds by themselves.
- Claim ordering is fresh work first, ordinary retry/failed work second, page recovery third, and scheduled rechecks last. Runtime claim SQL and the expression-index SQL use the same literal ordering contract.
- Versioned/rebuilt the claim-order expression index and drops the obsolete legacy index to avoid duplicate write overhead.
- Raised the exact hard-safety timeout to 50 seconds and queue lease to 240 seconds so the outer worker does not prematurely race bounded provider operations; provider-level HTTP/page timeouts remain the normal control points.
- Exact-worker summaries now break transient failures into page/API/outer-timeout/price-change/candidate-build/other buckets, and persisted errors retain compact `retry_kind` information.
- Queue health/pressure diagnostics now report due and delayed page-recovery retries separately.
- Page-recovery migration row counting now falls back to `SELECT changes()` when cursor `rowcount` is unavailable.
- Public posting now records `not_alertable_reasons` for category suppression/error, specific public-quality rejection, scout rejection, or `should_alert=false`.
- `public_deal_rejection_reason()` is the canonical diagnostic mirror of the existing boolean quality gate, and `has_verified_api_threshold_discount()` delegates to it so the old gate semantics are preserved.
- No CAPTCHA/challenge bypass, proxy rotation, raw-page persistence, alternate-retailer proof substitution, unrestricted historical rearm, or weakened exact item/price/seller/offer/shipping/variant/condition/fulfillment/reference/category/threshold/dedupe rule was introduced.

## Validation
- Initial focused implementation workflow: **PASS** for `git diff --check`, compile, dependency install, and focused Walmart retry/backpressure/fanout regressions.
- First canonical PR run exposed four stale architecture assertions that modeled the pre-`page_retry` health tuple, SQL parameter count, index statement count, and claim ordinal. The runtime path was inspected before updating those contracts.
- The next full branch run exposed one static claim-order parity mismatch; the implementation itself was cleaned up so runtime claim SQL and expression-index SQL use the same literal `page_retry` ordering instead of weakening the test.
- Final branch Python 3.11.15 suite: **1,181 passed**, with one upstream `discord.py` `audioop` deprecation warning.
- `pip check`: **PASS — no broken requirements**.
- `git diff --check`: **PASS**.
- Compile/import validation: **PASS** through the canonical workflows.
- Final PR head `ed31b6c26e2a460d7230f2b461e54b9122a49cfa` passed all three canonical workflows: **Import smoke check PASS**, **Python tests PASS**, **Python Check PASS**.
- Merge commit `14a9a518bb9d7db5246f962f2a0f82d6daec4953` independently passed all three canonical `main` workflows: **Import smoke check PASS**, **Python tests PASS** including `pip check`, compileall, and pytest, and **Python Check PASS** including compile, import smoke, and the full suite.

## Cleanup / conflict / review inspection
- Temporary applicator/test-repair scripts and workflows self-deleted before the final implementation branch commit and are absent from the net PR diff.
- Production runner path was verified to use `walmart_exact_queue_bulk_runtime`; the compatibility runtime was kept policy-compatible instead of becoming a competing implementation.
- Retry delay/storage logic is centralized rather than duplicated across bulk and compatibility persistence.
- Runtime claim ordering and runtime-schema expression-index ordering are literal/static parity matches.
- Before merge, PR #235 was mergeable and the branch was **0 commits behind `main`**.
- PR #235 had no human reviews and no inline review threads. The only PR conversation comment was an automated Qodo billing notice saying review was paused because its subscription was inactive; it contained no code finding.
- PR #235 merged cleanly into `main` as `14a9a518bb9d7db5246f962f2a0f82d6daec4953`.

## Production validation boundary
- Code-side completion does **not** claim the live bot already has the new behavior.
- After the merged `main` is deployed, live exact-worker lines should expose transient buckets such as `page`, `api`, `timeout`, `price-change`, `candidate`, and `other` instead of one opaque failure count.
- Queue-pressure diagnostics should show page-recovery pressure separately; page-recovery retries alone should no longer trigger the normal fresh-discovery pause.
- Future public Walmart skips should show `not_alertable_reasons=[...]`, allowing a category suppression, quality gate, or `should_alert=false` decision to be diagnosed without guessing.

## Backlog
- Resume the SniperPlug-Site Whop app-specific content-reader task for Hidden Files experiences such as `Make Money Here`, `Content`, and `Better Content` after this task closes.
- Investigate repeated Discord gateway `Can't keep up ... websocket is 41.5s behind` warnings after this task closes unless live retry telemetry proves a direct connection.
- Continue verifying PR #223 delivery-health accounting from live post-deploy logs after this task closes.
- PR #200 remains isolated and must not be mixed into this task.
