# Active Task

## Task / outcome
**SP-MOVIE-TICKETS-RECOVERY — restore SniperPlug movie tickets end-to-end.**

Target outcome: the existing July 31 permanent movie panel stays useful across deployments; current free-ticket and screening sources are freshness-qualified and truthfully classified; managers can configure alerts directly from the panel; Atom degradation does not poison the whole movie surface; movie cards include trustworthy poster artwork when available; and users always get an obvious action to open/claim a result without needing to discover that a Discord embed title is clickable.

## Scope
`public movie source fetch/discovery → fail-closed parse → per-source state/backoff/fallback → durable freshness-qualified cache → poster enrichment → actionable permanent panel/latest/status → obvious per-result open/claim controls → configured direct-source alert delivery → feedback controls`

Do not switch to unrelated Walmart, watcher, gateway, setup, DM, site, Minecraft, or other work without an explicit FORCE SWITCH.

## Status
**#263 is merged and production acceptance exposed one remaining user-facing navigation defect. Follow-up branch `fix/movie-result-ctas-264` adds explicit result actions and is awaiting exact-head executable validation.**

Movie milestones:
- #260 merge: `14d15a72277a2c6e6d8f05a27fc6ae12c366efd0`
- #261 merge: `e49acaab9a2c21531407a62ec8cb3622f4d84b6c`
- #262 merge: `00335c0943c6b9fc214329c8468b35049ac9b23b`
- #263 validated head: `52dedfd4813886ce09ee877f19d08d566e233448`
- #263 merge: `85e26ffa76a8a76fe6aac32c98393e2cbd5c54ee`

Previous Walmart delivery work (#256/#257) remains merged; its production acceptance is paused by the explicit movie-task switch.

## Production finding after #263
The backend/source/artwork changes are not sufficient UX by themselves. Manual movie result cards from both:
1. `/movies latest`, and
2. the permanent July 31 panel's **Check Now** action
were sent as embeds with no attached result-action view. The embed title carried a URL, so a user who already knew Discord titles can be tapped could navigate, but there was no obvious open/claim button.

This is distinct from automatic public movie alerts: `MovieTicketFeedbackView` already gives auto-posted Atom/Fandango/Gofobo alerts an explicit source link button alongside Worked/Didn't work feedback. That working path must remain unchanged.

## Authoritative execution paths
### Manual slash result
`registered_multi_source_movies.MovieTicketsCog.latest`
→ freshness-qualified `store.list_active_drops(limit=8)`
→ existing poster-aware `_build_drop_embeds`
→ numbered result annotation
→ shared `build_movie_result_actions_view`
→ one ephemeral Discord response with cards + explicit link buttons

### Permanent panel result
`MovieCommandGuideCog.send_latest_from_panel`
→ registered movie cog scan/store/build path
→ same numbered annotation
→ same `build_movie_result_actions_view`
→ one ephemeral Discord response with cards + explicit link buttons

### Public automatic alerts
Existing `_deliver_drops` / delivery persistence
→ `MovieTicketFeedbackView`
→ Worked / Didn't work + existing official source link button

No replacement or duplicate public alert-control path is introduced.

## #264 implementation
- Added shared `sniperplug/cogs/movie_result_actions.py` rather than duplicating button logic between slash and panel surfaces.
- Up to 8 manual results receive numbered, source-specific link buttons:
  - Atom: `#N Open Atom Promo`
  - Fandango: `#N Open Offer`
  - Gofobo: `#N Check ZIP / Claim`
  - SeeItEarly: `#N Find Pass`
- Buttons use each result's exact `offer_url` when present and only the corresponding source's known public page as that source's fallback.
- Buttons are packed two per row across at most four rows, staying within Discord's five-row component limit while remaining usable on mobile.
- Each result card receives a first field such as `👇 Result #N action` that tells the user exactly which numbered button to tap and explicitly says the title does not need to be tapped.
- `/movies latest` and permanent-panel **Check Now** both use the same shared annotation + view.
- Guide copy now describes the numbered actions rather than teaching hidden title-link behavior.
- Existing automatic public alert feedback/source buttons are untouched.

## Regression coverage added
`tests/test_movie_result_actions_264.py` covers:
- Atom/Fandango/Gofobo/SeeItEarly source-specific labels
- exact result URL preservation
- numbering and two-buttons-per-row layout
- label length safety
- card annotation and explicit no-title-tap guidance
- `/movies latest` wiring to the shared view
- permanent-panel **Check Now** wiring to the same view
- preservation of the existing public `MovieTicketFeedbackView` controls

## Existing #263 behavior that must remain intact
- Direct Atom remains authoritative; only 401/403/429 may enter exact-canonical public-index fallback.
- Indexed fallback never infers/republishes reusable promo codes.
- Persisted cooldown lanes remain: under 30m no Atom/Bing request, 30m-to-6h Bing-only refresh, >=6h direct Atom reprobe.
- Fandango/Gofobo artwork remains intact.
- SeeItEarly may use only allowed TMDB poster assets from its own movie-detail path.
- SeeItEarly remains supplemental and non-auto-deliverable.
- July 31 panel migration, alert destination, exact Snowflakes, freshness, dedupe, and feedback remain intact.

## Validation / results
### #263 baseline
Exact #263 validated head `52dedfd4813886ce09ee877f19d08d566e233448` on Linux/Ubuntu Python 3.12:
- targeted movie regressions: 94 passed
- pip check clean
- compileall exit 0
- import smoke: 37 modules / 22 symbols
- native libsql: 10 passed
- full pytest: 1,328 passed
- diff check clean

### #264 current state
- Structural execution-path inspection complete.
- Current diff is limited to the shared result-action helper, the two manual result surfaces, new regressions, and this task record.
- Executable exact-head validation is still required before ready/merge.
- GitHub-hosted Actions have recently failed before checkout with `runner_id=0` / `steps=[]`; if repeated, do not represent those infrastructure results as executed code tests.

Required before ready/merge:
- targeted #264 + affected movie tests
- `python -m pip check`
- `python -m compileall -q sniperplug scripts tests`
- `python scripts/smoke_imports.py`
- native libsql process tests
- full `PYTHONHASHSEED=0 python -m pytest -q`
- `git diff origin/main...HEAD --check`
- final HEAD verification
- final PR diff/review-thread inspection

## Production acceptance after #264 deploy
1. `/movies latest` shows visible numbered action buttons under the returned result cards.
2. Permanent July 31 panel → **Check Now** shows the same visible numbered actions.
3. Every displayed card tells the user which matching button to press.
4. A user can reach the claim/source page without knowing the embed title is clickable.
5. Gofobo's action clearly communicates ZIP/claim behavior; SeeItEarly clearly communicates pass discovery.
6. Existing public auto-post feedback + official link controls remain present.
7. #263 Atom fallback, source health, posters, freshness, dedupe, Snowflake safety, and panel migration remain intact.

Only after executable validation and these live checks pass call movie tickets restored and close this task.

## Risks / boundaries
- Link buttons navigate only to already-accepted result/source URLs; they do not weaken source proof or classify an aggregator as an issuer.
- Manual result actions do not create extra network requests.
- Public auto-alert delivery behavior is deliberately unchanged.
- No CAPTCHA evasion, proxy rotation, browser impersonation, account/login scraping, gated-code guessing, or private invitation scraping.

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
Open the #264 draft PR, inspect its final scoped diff and review state, then run exact-head Python 3.12/native-libsql validation. Merge only the exact validated SHA, deploy, and verify the visible numbered controls in both `/movies latest` and July-panel **Check Now**.