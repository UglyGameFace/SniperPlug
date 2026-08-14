# Active Task

## Status
**Active — SP-DEALS-DISCOVER-HARDENING-244 live follow-up. PR #245 merged and deployed; the second live `/deals` test proved the initial latency improvement but exposed remaining component timeouts and dense result presentation. The focused follow-up implementation is now code-complete and locally/CI validated on `fix/deals-live-timeout-245`; PR/merge/deploy/live validation still remain.**

## Scope
Finish the existing `/deals` + `/discover` hardening task. Do not start the suspended Walmart missed-deals backlog until this live gate passes.

## Root cause / findings
- The original post-#245 `/deals` result View acknowledged interactions inside individual callbacks rather than at the earliest View boundary.
- Mode changes spawned another controls message while every View reused the same static component custom IDs, creating unnecessary competing/stale dispatch surfaces.
- ACK failures were swallowed without task-specific component diagnostics.
- Normal `/deals` output reused dense Walmart proof/audit cards intended for deeper verification surfaces.
- `New / Drops` can contain both verified cards and private review/search leads, so presentation status must come from actual `DealFinderResult.verified_cards` membership rather than a generic card flag.

## Implemented follow-up
- Each result menu now receives a unique token embedded in every component custom ID.
- `DealSearchModeView.interaction_check` acknowledges components with `response.defer(thinking=False)` before button-specific work and enforces menu ownership.
- Component ACK failures and callback exceptions now have task-specific logs.
- Mode switches and Fresh Scan preserve the menu token/owner and replace the existing controls message instead of stacking duplicate control panels.
- Selected mode is primary/disabled; other modes remain selectable.
- `/deals` now uses compact shopping-focused embeds preserving price, seller, delivery, links, thumbnails, and verified/private status while removing internal identity/evidence noise.
- Normal finder summary and mode menu are compact; diagnostic/public proof renderers and autoscan safety gates were not weakened or replaced.
- `New / Drops` determines verified/private presentation from actual verified-result membership so review leads cannot be mislabeled as verified.

## Validation completed
- Focused #244/#245/control/presentation regressions: **15 passed**.
- Full Python 3.11 regression suite: **1,234 passed, 1 warning** (discord.py/audioop deprecation only).
- `pip check`: passed.
- `python -m compileall -q sniperplug tests *.py`: passed.
- `git diff --check origin/main...HEAD`: passed.
- Temporary patch/validation workflows were removed after use.

## Remaining Definition of Done
1. Final branch conflict/redundancy inspection shows only intended files and no temporary workflow/duplicate implementation.
2. Open PR; PR checks pass; review/comments/threads contain no unresolved code findings.
3. Squash merge and confirm post-merge `main` checks pass.
4. Deploy merged `main` to Discloud app `1779293887444`.
5. Live `/deals search:phone mount`: initial result responsive; every enabled mode button responds; Fresh Scan responds; one control panel remains current; compact embeds are readable; private leads remain private.
6. Live `/discover` still returns only current-job item IDs as established by PR #244.
7. Only then close SP-DEALS-DISCOVER-HARDENING-244.

## Suspended task
**SP-WM-MISSED-DEALS-236 remains suspended, not closed.** Resume only after this active task closes or an explicit FORCE SWITCH.

## Backlog
- Resume Walmart exact-verification/page-recovery throughput work after #244 closes.
- Investigate remaining Discord gateway lag if it persists after this command path is stable.
- Resume SniperPlug-Site Whop content-reader work later.
