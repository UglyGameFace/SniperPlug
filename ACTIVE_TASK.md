# Active Task

## Status
**Active — SP-DEALS-DISCOVER-HARDENING-244. The implementation is complete on `fix/deals-discover-hardening-244` and an exact branch validation run passes `pip check`, compileall, and the full **1,224-test** suite. The final compare is clean and behind `main` by 0. GitHub's PR-create connector action is currently being blocked by the platform before the request reaches GitHub, so PR creation/normal PR workflows, merge, deployment, and live proof remain.**

## Task
**SP-DEALS-DISCOVER-HARDENING-244 — Finish `/deals` result delivery/relevance and isolate `/discover` result scope without weakening exact Walmart proof, thresholds, or public/DM safety.**

## Production proof / root causes
### `/deals`
The live `/deals search:phone mount` run successfully searched **3,809 returned products across 200 result pages**, produced **10 review/raw/flip/scout leads**, and visibly found a **7-in-1 Universal Car Phone...** result. The search/relevance side was therefore working.

The later command crashed with:
`TypeError: expected view parameter to be of type View or LayoutView, not NoneType`.

The live unified `/deals` renderer uses `sniperplug/services/embed_delivery.py`. Its batched follow-up helpers explicitly passed `view=None` when there was no view or on an intermediate batch. The local `send_private_card_batches` helper in `unified_deal_scanner.py` had the same secondary flaw.

PR #243 also only injected direct current-price product matches when **all** verified/review/scout lanes were empty. Therefore a strong direct `phone mount` match could be hidden whenever weaker review/scout cards existed. Even after merging direct matches, the final mode UI re-ranks to five cards, so the private ranking layer must keep those strong direct matches inside the visible five-card pool.

### `/discover`
`collect_verified_discount_cards_with_observed_memory` uses a global exact-detail verification queue. Its queue readback previously accepted any recent verified snapshot not already in the current foreground set. It did **not** require that queue row's Walmart item ID to have been discovered by the current `/discover` job/chunk.

That allowed a verified global row from another route/job to become a result card for the wrong discovery run. Global queue/fanout can remain global, but a command result must only surface queue snapshots whose item IDs were found by that command's current search routes.

## Implementation
- Added `send_followup_with_optional_view(...)` in `embed_delivery.py`; it **omits** the `view` argument entirely when there is no concrete Discord view instead of passing `None`.
- Routed both shared embed/card batching helpers through that safe sender.
- Routed `unified_deal_scanner.send_private_card_batches` through the same safe sender, including intermediate batches.
- `/deals` now builds strong direct product matches even when review/scout leads also exist, dedupes them into the private result lane, and preserves all audit counters/reasons.
- Direct product matches remain `should_alert=False`, `manual_share_allowed=False`, and `deal_lane=product_search_match`; relevance is never converted into deal proof.
- Private review ranking gives `product_search_match` cards a relevance bonus and caps the visible private pool to five whenever direct matches exist, so the later five-card mode re-rank cannot bury every direct match behind weaker review/scout cards.
- Added `product_match_count` to the unified finder result for explicit internal accounting.
- Added `select_current_search_queue_candidates(...)`; exact-queue readback now requires the item ID to be in the current scan's `search_item_ids` and not already in the foreground exact set.
- `/discover` and other callers of this shared collector can still benefit from background exact verification of **their own** overflow item IDs, while unrelated global queue rows stay out of their command results.
- Preserved the established `true overflow verified added` diagnostic phrase while adding `(current-search item IDs only)` so older static regressions and operational wording remain compatible.

## Safety invariants
- No public or DM markdown threshold was lowered.
- No product-search relevance score becomes discount/reference proof.
- Direct product matches stay private, non-alertable, and non-shareable through the review publication flow.
- Existing current-price, seller/offer, variant, shipping, reference, buyability, category, dedupe, and public-post gates are unchanged.
- The global Walmart exact-detail queue remains global for verification/fanout; only command-result readback is scoped to the item IDs discovered by that command.
- No anti-bot bypass, proxying, seller guessing, or raw-page persistence was added.

## Implementation commits
- `ad996632472191d84b9bacd523505a35349151c5` — omit null views from shared deal follow-up sends.
- `ce6c3f084b7d8efdf0ebeb9eadeb66b1845651ad` — prioritize direct product matches in private review ranking.
- `4dc897f37f6d3f084f564be5176fa7e5bff8f6ca` — keep direct product matches alongside review/scout leads.
- `26ec1292497cab5a056d1cfa07c7b45ae90daed4` — scope exact queue readback to current discovery results.
- `2568ec19b8e446e614800da42e72edcb11878912` — harden the local multi-batch private `/deals` sender against null views.
- `6ebf8cc44cf400c5785aef93199bdab129555846` — keep direct matches inside the visible private five-card pool.
- `46e12ce3ca438fd5a5d6c5692deabb5978ce93c5` — targeted `/deals` + `/discover` hardening regressions.
- `f5d7811f827ae054141ab1e18467d87f0c8bb8ce` — preserve established queue diagnostic compatibility after full-suite feedback.

## Targeted regressions added
- shared multi-batch embed delivery must never pass explicit `view=None`;
- local private `/deals` multi-batch delivery must omit null views on intermediate batches and attach the real view only to the final batch;
- `phone mount` direct current-price matches coexist with review/scout leads and remain private/non-alertable;
- direct-match ranking keeps a strong product match in the final five-card Best Picks input/output even against eight high-scoring generic review leads;
- `/discover` queue readback accepts current-search overflow, rejects unrelated global rows, rejects foreground duplicates, and rejects missing identities.

## Validation completed
- PR #243 baseline before this hardening: **1,219 passed**, `pip check` PASS, compileall PASS, PR CI PASS, merge-commit CI PASS.
- Temporary branch-only validation used the same Python 3.11 install / `pip check` / compileall / full pytest commands as the normal `python-tests.yml` workflow because the PR-create connector action was blocked before GitHub could create the PR.
- First full branch validation: **1,222 passed / 2 failed**. Both failures were existing static tests requiring the diagnostic text `true overflow verified added`; there was no functional test failure. The wording was restored while retaining the new current-search item-ID filter.
- Final branch validation run `31751565838`: **PASS**.
- `pip check`: **PASS — No broken requirements found**.
- `python -m compileall -q sniperplug tests *.py`: **PASS**.
- Full pytest: **1,224 passed**, one upstream `discord.py` `audioop` deprecation warning.
- The temporary branch-only validation workflow was deleted after the green run; it is not present in the final compare.
- Final compare against `main`: **behind 0**, implementation/task/test diff limited to:
  - `ACTIVE_TASK.md`
  - `sniperplug/cogs/unified_deal_scanner.py`
  - `sniperplug/services/autoscan_observed_price_memory.py`
  - `sniperplug/services/deal_finder_engine.py`
  - `sniperplug/services/deal_ranking.py`
  - `sniperplug/services/embed_delivery.py`
  - `tests/test_deals_discover_hardening_244.py`
- No unrelated implementation files are in the final diff.

## Current blocker
The GitHub connector's `create_pull_request` action has been attempted multiple times with both detailed and generic PR metadata. Each attempt was blocked by the platform with `couldn't determine the safety status of the request` **before a PR was created**. No PR exists for this branch yet. This is a tool/platform blocker, not a repository/test failure.

## Remaining Definition of Done
1. Create the PR from `fix/deals-discover-hardening-244` into `main`; normal PR workflows should rerun the same checks.
2. Inspect PR mergeability, comments/reviews, and inline threads; fix any real finding.
3. Merge only after PR checks are green, then verify the real `main` push workflows.
4. Deploy merged `main` to existing Discloud app `1779293887444`.
5. Live `/deals search:phone mount`: no `view=None` TypeError; relevant current-price product match(es) remain visible when no verified deal qualifies; direct matches remain private and cannot public-post/DM-alert.
6. Live `/discover`: completed result cards must correspond only to item IDs discovered by that job's own routes/chunks; unrelated global exact-queue snapshots must not leak into the result set.
7. Only then mark SP-DEALS-DISCOVER-HARDENING-244 complete.

## Suspended task
**SP-WM-MISSED-DEALS-236 remains suspended, not closed.** Resume only after this active task closes or another explicit FORCE SWITCH.

## Backlog
- Resume SniperPlug-Site Whop app-specific content-reader work after the active task closes.
- Investigate repeated Discord gateway `Can't keep up` warnings after the active task closes unless directly tied to this execution path.
- Continue broader PR #223 delivery-health accounting later.
- PR #200 remains isolated.
