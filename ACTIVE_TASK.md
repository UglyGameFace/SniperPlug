# Active Task

## Status
**Active — SP-WM-PAGE-RETRY-FANOUT. Implementation and branch-level full validation are clean. PR #235 is open; canonical PR workflows, final review/conflict inspection, merge, and post-merge validation remain before code-side completion. Production behavior remains intentionally unclaimed until a later live deployment exercises the new retry/public-reason telemetry.**

## Task
**SP-WM-PAGE-RETRY-FANOUT — Stop Walmart page-recovery transient storms from starving fresh discovery, expose exact retry/public skip reasons, and preserve strict item/price/seller/offer/shipping/variant/reference safety.**

## Scope
1. Trace the real exact-worker execution path from queue claim through `CachedWalmartProvider` → `WalmartPageRecoveryProvider` → official API/page fallback → exact candidate build → failure persistence → fresh-work backpressure.
2. Distinguish official API failures, product-page HTTP/challenge/network failures, price-change retries, outer hard timeouts, and candidate-build failures instead of collapsing all of them into generic `retry`.
3. Prevent product-page recovery retry traffic from being treated as genuinely fresh first-pass work by the catalog-pause policy, while retaining bounded retries and emergency total backpressure.
4. Align exact-worker hard timeout/lease budgets with the bounded provider operations they enclose.
5. Expose bounded transient-failure buckets in live exact-worker diagnostics.
6. Expose the exact public `not_alertable` reason for verified Walmart events without weakening category, threshold, proof, duplicate, seller/offer, condition, variant, fulfillment, shipping, or reference-price gates.
7. Make the page-recovery migration rearm count trustworthy when Turso/SQLite cursors do not expose a usable `rowcount`.
8. Complete focused/full regressions, dependency/static/compile validation, cleanup, conflict/review inspection, merge, and post-merge canonical validation.

## Root cause / findings
- PR #234 is live and its primary seller-identity objective is confirmed: post-deploy exact batches consistently report `identity unavailable / safely blocked 0`, versus the previous 16–24 seller blocks per batch.
- The next production bottleneck was visible immediately: many 8-item batches produced 6–8 `transient failures`, which could raise fresh/retry pressure to `14/12` and later `38/12`, pausing new Walmart catalog discovery.
- Product-page recovery HTTP/challenge/network/price-change failures were converted to ordinary queue status `retry`. The fresh-work pressure policy counts ordinary `retry` exactly like brand-new discoveries, so recoverable page failures could directly shut off new catalog intake.
- The former outer exact-worker hard timeout was 8 seconds while the official Walmart provider allows a 12-second HTTP timeout and product-page recovery independently allows up to 8 seconds. That outer budget could race legitimate bounded provider work; live wall-clock data did not prove it was the dominant failure class, so reason telemetry was added instead of guessing.
- Generic queue persistence duplicated retry-delay logic between compatibility and bulk paths, risking policy drift.
- The page-recovery one-time rearm counter treated unsupported/negative cursor `rowcount` as zero rather than falling back to `SELECT changes()`.
- Live fanout at 14:46 confirmed personal DM delivery end to end for KC Masterpiece at `$3.98`, 67.3% discount (`DM checked/matched/sent 1/1/1`). The same event returned generic public `not_alertable=1` for a 40% guild. Existing public code collapsed category suppression, public-quality rejection, and `should_alert` into one counter, so the production log alone did not prove a public-post bug.
- Current category logic can intentionally suppress a muted category unless its extreme-discount override applies; therefore no category rule was weakened without evidence. The fix is exact skip-reason telemetry.

## Changes delivered on PR #235 branch
- `WalmartProviderError` now carries a bounded `retry_kind` so official API errors distinguish HTTP code, timeout, network, non-JSON, payload-shape, and generic detail failures.
- `WalmartPageRecoveryTransientError` now carries a bounded reason code for page HTTP status, timeout, network, challenge, redirect, oversized response, and selected-offer price mismatch.
- Added queue status `page_retry` with bounded delays: 10 minutes for price-change mismatch, 30 minutes for ordinary page-recovery transients, and 60 minutes for 403/429/challenge pressure.
- Centralized retry persistence through one `_failure_storage_policy()` used by both compatibility and production bulk persistence.
- Added a separate bounded page-recovery pressure lane. Page retries still count toward total emergency pressure but no longer trip the normal 12-row fresh-discovery pause or 48-row fresh drain thresholds by themselves.
- Claim ordering remains fresh first, then ordinary retry/failed work, then page recovery, then scheduled rechecks. Runtime claim SQL and the expression-index SQL now use the same literal ordering contract.
- Versioned/rebuilt the claim-order expression index and explicitly drops the obsolete legacy index to avoid duplicate write overhead.
- Raised the exact hard-safety timeout to 50 seconds and queue lease to 240 seconds so the outer worker does not prematurely race bounded provider operations; provider-level HTTP/page timeouts remain the normal control points.
- Exact-worker summary now breaks transient failures into page/API/outer-timeout/price-change/candidate-build/other buckets, while persisted errors retain a compact `retry_kind`.
- Queue health/pressure diagnostics now report due/delayed page-recovery retries separately.
- Page-recovery migration row counting now falls back to `SELECT changes()` when cursor `rowcount` is unavailable.
- Public posting now records `not_alertable_reasons` such as category suppression/error, specific public-quality rejection, scout rejection, or `should_alert=false`.
- `public_deal_rejection_reason()` is the canonical diagnostic mirror of the existing boolean quality gate; `has_verified_api_threshold_discount()` delegates to it, preserving the previous gate semantics.
- No CAPTCHA/challenge bypass, proxy rotation, raw-page persistence, alternate-retailer proof substitution, unrestricted historical rearm, or weakened item/price/seller/offer/shipping/variant/reference checks was introduced.

## Validation
- Initial focused implementation workflow: diff check **PASS**, compile **PASS**, dependency install **PASS**, focused Walmart retry/backpressure/fanout tests **PASS**.
- First canonical PR run: import smoke **PASS**. Full suites found four stale architecture assertions that modeled the pre-`page_retry` tuple/SQL parameter/index statement/claim ordinal contracts; runtime behavior was inspected before those tests were updated.
- Second branch full run reduced the suite to one static claim-order parity failure. The implementation was cleaned up so runtime claim SQL and expression-index SQL use the same literal `page_retry` ordering instead of weakening the test.
- Final branch Python 3.11.15 full suite: **1,181 passed**, one upstream `discord.py` `audioop` deprecation warning.
- `pip check`: **PASS — no broken requirements**.
- `git diff --check`: **PASS**.
- Compile validation: **PASS** for runtime/test trees. The temporary helper workflow printed a harmless `Can't list '*.py'` for a quoted optional glob after successfully compiling the actual `sniperplug` and `tests` directories; canonical repository workflows use their established compile commands and remain the merge gate.
- Temporary applicator/test-repair workflow and scripts self-deleted before the final implementation branch commit and are absent from the net PR diff.
- Canonical PR CI on the user-authored final task-record commit is pending.

## Cleanup / conflict / review status
- Production runner path was verified to use `walmart_exact_queue_bulk_runtime`; the compatibility runtime was kept policy-compatible rather than patched independently.
- Retry delay/storage logic is centralized instead of duplicated across bulk and compatibility persistence.
- Runtime claim ordering and runtime-schema expression-index ordering are now literal/static parity matches.
- Branch is based directly on current `main` for this task and was 0 commits behind at the last compare; final mergeability/conflict check is still required immediately before merge.
- PR #235 currently has no human reviews or inline review threads; final PR discussion/review inspection will be repeated before merge.

## Blockers
- Canonical PR workflows must pass on the final user-authored head before merge.

## Backlog
- Resume the SniperPlug-Site Whop app-specific content-reader task for Hidden Files experiences such as `Make Money Here`, `Content`, and `Better Content` after this task closes.
- Investigate repeated Discord gateway `Can't keep up ... websocket is 41.5s behind` warnings after this active Walmart delivery task closes unless live retry telemetry proves a direct connection.
- Continue verifying PR #223 delivery-health accounting from live post-deploy logs after this task closes.
- PR #200 remains isolated and must not be mixed into this task.
