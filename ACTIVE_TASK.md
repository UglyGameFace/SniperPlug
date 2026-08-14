# Active Task

## Status
**Active — SP-DEALS-DISCOVER-HARDENING-244 live acceptance gate. PR #247 was squash-merged to `main` as `3d1787e79eb2c04379c4e8f48df613c577b85fc8`. Final PR-head CI, review/thread inspection, cleanup/conflict inspection, and all three post-merge `main` workflows passed. The remaining blockers are Discloud deployment of the merged build and live `/deals` + private/public `/discover` acceptance.**

## Scope
Finish the existing `/deals` + `/discover` hardening task. Do not start a new implementation task until this live gate passes or the user explicitly FORCE SWITCHes.

## Root cause / findings
- The original post-#245 `/deals` result View acknowledged interactions inside individual callbacks rather than at the earliest View boundary.
- Mode changes spawned another controls message while every View reused the same static component custom IDs, creating unnecessary competing/stale dispatch surfaces.
- ACK failures were swallowed without task-specific component diagnostics.
- Normal `/deals` output reused dense Walmart proof/audit cards intended for deeper verification surfaces.
- `New / Drops` can contain both verified cards and private review/search leads, so presentation status must come from actual `DealFinderResult.verified_cards` membership rather than a generic card flag.
- Final `/discover` queue-isolation reinspection remains clean. The #244 regression `test_discover_queue_readback_only_allows_current_search_item_ids` rejects unrelated global queue rows and only allows exact rows whose item IDs came from that discovery search.
- **Aug 14 live `/discover` failure:** the canonical slash command defaulted `max_public_posts` to 10 with no separate public-post opt-in. `_finish_discovery_job` unconditionally sliced fresh cards into `public_cards` and called `maybe_post_public_deal_cards`. The DM status destination controlled progress/private-result delivery only; it never disabled the public lane. The public helper was behaving as designed, so weakening or bypassing its safety/config gates was not the fix.
- `select_fresh_deal_cards` was reinspected after the public opt-in change. It performs public-quality/duplicate/reservation/cache preflight reads and annotates cards, but it does not reserve or publish a deal. Keeping that reporting preflight on private discovery runs therefore does not secretly create a public post/reservation.

## Implemented follow-up
- Each `/deals` result menu receives a unique token embedded in every component custom ID.
- `DealSearchModeView.interaction_check` acknowledges components with `response.defer(thinking=False)` before button-specific work and enforces menu ownership.
- Component ACK failures and callback exceptions have task-specific logs.
- Mode switches and Fresh Scan preserve the menu token/owner and replace the existing controls message instead of stacking duplicate control panels.
- Selected mode is primary/disabled; other modes remain selectable.
- `/deals` uses compact shopping-focused embeds preserving price, seller, delivery, links, thumbnails, and verified/private status while removing internal identity/evidence noise.
- Normal finder summary and mode menu are compact; diagnostic/public proof renderers and autoscan safety gates were not weakened or replaced.
- `New / Drops` determines verified/private presentation from actual verified-result membership so review leads cannot be mislabeled as verified.
- `/discover` now has an explicit `public_posts` per-run option that defaults to `False`; `max_public_posts` is only a cap when public posting is opted in. A private run selects zero public cards and never calls the public-post helper. Opted-in runs still use the existing `maybe_post_public_deal_cards` guard unchanged.
- `/discover` start/status/final messaging states Public Posts ON/OFF explicitly, and DM-closed fallback messaging follows the selected public mode instead of implying that every discovery run publishes.
- Added a pure `select_public_discovery_cards` policy helper and regression coverage proving private-by-default selection and capped public opt-in behavior.
- Updated the existing deal-feed command coverage guard so it requires category-filtered discovery cards, explicit public-policy selection, and the guarded public-helper call instead of requiring the old unconditional slice implementation.

## Validation completed
- Prior focused #244/#245/control/presentation regressions: **15 passed**.
- Prior full Python 3.11 regression suite: **1,234 passed, 1 warning** (discord.py/audioop deprecation only).
- PR #246 checks/review/cleanup and post-merge `main` checks passed; Discloud deployment of PR #246 was confirmed by live tokenized component ACK logs.
- `/discover` current-search item-ID queue isolation remains directly regression-tested and has no competing implementation.
- PR #247 started from then-current `main`; no rebase/conflict gap was present.
- First PR #247 Python 3.11 run: dependency and compile checks passed; full suite reached **1,234 passed / 1 failed**. The sole failure was an existing static assertion that required the old `public_cards = fresh_cards[:]` implementation string. The guard was updated to validate the new explicit opt-in path rather than restoring unsafe behavior.
- Revised/final PR #247 Python 3.11 workflow: **1,235 passed, 1 warning**; `pip check` passed; `python -m compileall -q sniperplug tests *.py` passed.
- Revised/final PR #247 Python 3.12 `Python Check`: dependency check, compile, import smoke, and full test suite all passed.
- Revised/final PR #247 separate `Import smoke check`: passed.
- Final PR #247 review/thread inspection found no unresolved review threads or code-review blockers.
- Public-freshness preflight reinspection confirmed it does not create public posts/reservations; the actual publishing helper remains behind `if public_cards:` and is unreachable when `public_posts=False`.
- Cleanup/conflict inspection: PR #247 changed only `ACTIVE_TASK.md`, `sniperplug/cogs/auto_discovery.py`, `tests/test_exact_discover_coverage.py`, and `tests/test_deal_feed_controls_command_coverage.py`; no temporary runner/applicator or alternate discovery implementation was added.
- PR #247 squash merge succeeded as `3d1787e79eb2c04379c4e8f48df613c577b85fc8`.
- Post-merge `main` workflows for `3d1787e79eb2c04379c4e8f48df613c577b85fc8`: **Import smoke check passed**, **Python Check passed**, and **Python tests passed**.

## Validation pending
- CI for this documentation-only active-task record refresh.
- Discloud deployment of merged `main`.
- Live acceptance below.

## Remaining Definition of Done
1. Complete live `/deals search:phone mount` sweep: every enabled mode button responds; Fresh Scan responds; one control panel remains current; compact embeds are readable; private leads remain private.
2. Deploy merged `main` containing `3d1787e79eb2c04379c4e8f48df613c577b85fc8` to Discloud.
3. Live `/discover coverage:Quick` with **Public Posts off/default** completes privately and publishes **zero** discovery cards to the public deal lane; its private/final result contains only current-search job items.
4. Live `/discover coverage:Quick public_posts:True max_public_posts:1` proves the explicit public option still sends only eligible verified cards through the unchanged public safety/threshold/category/duplicate guard and respects the one-card cap.
5. Only then close SP-DEALS-DISCOVER-HARDENING-244.

## Suspended task
**SP-WM-MISSED-DEALS-236 remains suspended, not closed.** Resume only after this active task closes or an explicit FORCE SWITCH.

## Backlog
- **SP-TURSO-WRITE-AMPLIFICATION:** Urgently investigate Turso Cloud rows-written usage after the Aug 14 warning that the organization exceeded 75% of its monthly write quota. Do not default to a paid-plan upgrade. First use Turso organization/database usage and top-query stats to identify the actual write-heavy database and SQL statements, then trace those statements through SniperPlug's real database callers, worker cadences, lease/retry/status/heartbeat paths, migrations, guards, and compatibility layers. Eliminate needless updates of unchanged rows, write-on-read patterns, overly frequent lease/status touches, duplicate retry/fanout bookkeeping, and other write amplification; batch/coalesce safe writes and make upserts/update predicates idempotent where correctness permits. Preserve durable fanout/dedupe/exact-queue correctness and crash recovery. Add subsystem write telemetry and regression coverage, then validate rows-written rate in production before considering overages or a plan upgrade. Do not implement until #244 closes unless explicitly FORCE SWITCHed.
- **SP-SETUP-DELIVERY-UX:** Align `/setup_sniperplug_here` with the clearer `/dm_deals` setup experience. Make public delivery an explicit understandable option, show the selected public channel/status, separate public-post controls from personal DM controls, keep only essential choices mandatory, and make the resulting configuration easy to review/change without hidden defaults. Do not implement until #244 closes unless explicitly FORCE SWITCHed.
- **SP-DM-DELIVERY-STARVATION:** Investigate why personal DM alerts are producing `checked/matched/sent 2/1/0` with `DM disabled 0` and `errors 0`; current evidence shows matching deals are being lost to filter and duplicate suppression (`filter/duplicate/cap/diversity 1/1/0/0`) rather than Discord DM delivery being disabled. Inspect the real DM rule evaluation, dedupe key/lifetime, event-version semantics, per-user thresholds/categories, and whether a legitimate new price/version is being incorrectly treated as already delivered before changing behavior.
- **SP-DM-NEW-LOW-ALERT:** When the exact same item reaches a genuinely lower price, emit a distinct user-facing “New Lower Price” event instead of suppressing it as a duplicate or resending the original card. Preserve the item’s price timeline and show previous observed price → new price, dollars saved since the last alert, percentage drop since the last alert, and discount versus the highest trusted observed/reference price. Clearly distinguish official Walmart was/list price from SniperPlug-observed history and estimated market reference. Re-alert only for meaningful new lows; same-price repeats remain deduped. Prefer updating/replacing the prior active DM card when practical, or send a compact follow-up that visually emphasizes the new low and links back to the product.
- **SP-WM-REFERENCE-PRICE-RECOVERY:** Improve Walmart exact verification when the API omits current/reference/was price. Current logs show `api_price_missing`, `api_exact_search_no_match`, `page_challenge`, and long `page_circuit_open` windows, while page retries can occasionally rescue candidates. Inspect the current API → PDP/page → memory path first. Desired hierarchy: official Walmart strike/list/was price when available; same-item historical price observed by SniperPlug; exact same variant/condition Walmart offer context; conservative cross-seller/market reference only as clearly labeled estimated/reference context, never falsely presented as an official Walmart “was” price. Reject outliers and variant/condition mismatches, retain provenance/confidence, and keep public markdown proof stricter than private review/scout context.
- **SP-WM-CASH-PDP-AMOUNT:** A live Ivory Body Wash deal visibly shows **“Get $0.50 Walmart Cash”** on the exact Walmart product page, while SniperPlug's alert omits the amount. Root-cause inspection found that `walmart_pdp_cash_proof.py` can parse page wording such as `Get $0.50 Walmart Cash`, but the production Walmart Cash pipeline is API-only and explicitly disables PDP fallback, leaving the parser unwired to autoscan/exact-deal delivery. After #244 closes, inspect the exact Walmart product-page fetch/recovery execution path and safely integrate same-item PDP Cash proof for private/DM enrichment, preserving strict provenance and never letting account-dependent Walmart Cash substitute for verified markdown proof. Store amount, eligibility wording, selected option/variant, and source; render a clear `Walmart Cash: $0.50` line and optionally an `after Cash` value. Do not confuse OnePay cashback with Walmart Cash.
- Resume Walmart exact-verification/page-recovery throughput work after #244 closes.
- Investigate remaining Discord gateway lag if it persists after this command path is stable.
- Resume SniperPlug-Site Whop content-reader work later.
