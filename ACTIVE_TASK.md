# Active Task

## Task / outcome
**SP-MOVIE-TICKETS-RECOVERY — restore SniperPlug movie tickets end-to-end.**

Target outcome: `/movies` shows only recently confirmed official offers/screenings, current source layouts parse safely, healthy sources continue when one source is blocked, Gofobo local screenings persist and deliver, setup/status/latest/panel interactions give truthful actionable health instead of stale results or circular recovery instructions, and delivery/feedback safety remains intact.

## Scope
`official Atom/Fandango/Gofobo fetch → fail-closed parse → per-source state/backoff → durable drop cache → freshness gate → /movies latest/status/panel → configured channel delivery → feedback controls`

Do not switch to unrelated Walmart, watcher, gateway, setup, DM, or site work without an explicit FORCE SWITCH.

## Status
**IN PROGRESS on branch `fix/movie-tickets-end-to-end-258`.**

Previous Walmart delivery work (#256/#257) remains merged on `main`; its production acceptance is paused by the explicit task switch and stays backlog/context only.

## Root cause / findings
- Production logs show Atom returning HTTP 403 and entering a six-hour access backoff while the last verified cache is preserved.
- Movie cache rows have no serving-time freshness ceiling. A source can fail for days/weeks while old rows remain `active=1` and `/movies latest` still presents them as active.
- Current Atom public promotions layout no longer requires the old `Film Promotions` section; the parser currently requires both Film + Partner sections, so a legitimate current partner-only page is treated as structurally invalid and old Atom rows are preserved instead of safely clearing to zero public reusable codes.
- Multi-source runtime declares `local_screening` deliverable, but `MovieTicketStore.replace_active_drops()` and `list_active_drops()` hard-filter to `public_reusable`; Gofobo screenings can be parsed then silently discarded by storage.
- Registered runtime is a single inherited monitor, not duplicate pumps; the Atom and multi-source startup log lines come from one cog loading through `super()`.
- Manual/guide latest paths can swallow partial-source failures and then display old cached rows without telling the user which source is stale.
- Movie panel component fallback tells users to try another movie command/restart even when the underlying failure is upstream; those instructions cannot fix an Atom 403 and create a dead-end recovery loop.

## Planned implementation
1. Make current Atom promotions structure valid when the official promotions page is confirmed even if `Film Promotions` is absent; continue excluding partner/account-specific codes.
2. Add a bounded source-freshness policy so stale source caches are hidden from user-facing active results rather than shown indefinitely; preserve durable rows for recovery/history.
3. Keep raw per-source cached rows available internally for HTTP 304 recovery so freshness filtering cannot erase a still-current unchanged snapshot.
4. Persist/list both safe deliverable classifications: `public_reusable` and `local_screening`.
5. Surface partial-source degradation in scan outcomes/status/latest/panel instead of treating one healthy source as a fully healthy refresh.
6. Replace circular panel error guidance with truthful source/application health guidance and no fake user-side fix for upstream access blocks.
7. Add regressions for partner-only Atom pages, stale-cache hiding, 304 recovery behavior, Gofobo persistence, partial-source health, and user-facing recovery messaging.

## Validation required
- targeted movie parser/store/runtime/guide/feedback tests
- `python -m pip check`
- `python -m compileall -q sniperplug scripts tests`
- `python scripts/smoke_imports.py`
- native libsql process tests where available
- full pytest suite on the exact PR head
- final diff/review inspection

## Production acceptance required
After merge/deploy:
1. Old Atom rows are no longer shown as current once Atom confirmation is stale.
2. A valid current Atom page with no public free-ticket codes clears old Atom results safely instead of preserving them as active.
3. Atom 403 remains contained/backed off without poisoning Fandango/Gofobo health.
4. Gofobo local screening leads can persist, appear in `/movies latest`, and deliver when present.
5. `/movies status`, `/movies latest`, and guide-panel controls distinguish healthy, degraded, and stale sources without telling users to perform ineffective setup/restart rituals.
6. Fresh eligible movie alerts still dedupe and post to configured channels, and feedback controls remain functional.

Only after those live checks pass call movie tickets restored and close this task.

## Backlog — not active
- SP-WM-DEAL-DELIVERY-STALL production acceptance
- SP-GATEWAY-EVENT-LOOP-LAG
- SP-SETUP-DELIVERY-UX
- SP-DM-DELIVERY-STARVATION
- SP-DM-NEW-LOW-ALERT
- SP-WM-CASH-PDP-AMOUNT
- eBay watcher PR #200
- HP/Target standalone watcher deployment/acceptance
- SniperPlug-Site work

## Next step
Implement the movie source/storage/freshness/UX repair on this branch, add regressions, then validate the exact head before merge.