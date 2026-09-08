# Active Task

## Task / outcome
**SP-MOVIE-TICKETS-RECOVERY — restore SniperPlug movie tickets end-to-end.**

Target outcome: the existing July 31 permanent movie panel remains useful across deployments; movie offers are current, clearly actionable, poster-aware, optionally local to a user's ZIP/radius, and resilient when one source fails. Atom degradation must not poison the rest of the movie surface.

## Scope
`public source discovery → fail-closed parse → per-source health/backoff/fallback → durable freshness-qualified cache → poster enrichment → actionable panel/latest/status → obvious result actions → optional per-user ZIP/radius local view → configured alert delivery → feedback controls`

Do not switch to unrelated Walmart, watcher, gateway, setup, DM, site, Minecraft, or other work without an explicit FORCE SWITCH.

## Status
**#265 is merged and validated. Draft #266 (`fix/atom-fallback-v2-266`) contains Atom indexed-fallback v2 and is awaiting exact-head executable validation.**

Movie milestones:
- #260 merge: `14d15a72277a2c6e6d8f05a27fc6ae12c366efd0`
- #261 merge: `e49acaab9a2c21531407a62ec8cb3622f4d84b6c`
- #262 merge: `00335c0943c6b9fc214329c8468b35049ac9b23b`
- #263 merge: `85e26ffa76a8a76fe6aac32c98393e2cbd5c54ee`
- #264 merge: `6e745529a13d881a9e651d2cc6dfd221cb7ef4e1`
- #265 validated head: `69ab983b82032e826a90f9dcb741477bf907685f`
- #265 merge: `d4d22c704c4a38c7a094f2d09f553583c6a6f3d7`

## Root cause / findings
Production still gets HTTP 403 from direct Atom. #263's indexed recovery also fails because it asks Bing to confirm one exact URL using `url:www.atomtickets.com/promotions` and rejects legitimate official detail pages such as `/promotions/<slug>`.

Current research confirms:
- Microsoft's `url:` operator checks one exact URL/domain index identity.
- Microsoft's `site:` operator searches a site or directory tree.
- Atom's canonical promotions hub is publicly live.
- Official Atom `/promotions/<slug>` pages are publicly indexed.

The fallback therefore needs a directory-level index query while preserving a strict Atom-only URL allowlist and zero-code fail-closed behavior.

## #266 implementation
- Direct Atom remains authoritative and is still tried first whenever its six-hour reprobe is due.
- Indexed fallback still activates only after explicit Atom access-block responses: 401, 403, or 429.
- Bing query changed from exact `url:` lookup to `site:www.atomtickets.com/promotions ...` directory search.
- Canonical `/promotions` is preferred when present.
- Otherwise exactly one-level official `/promotions/<slug>` URLs are allowed.
- HTTPS plus exact `atomtickets.com` / `www.atomtickets.com` host requirement remains fail-closed.
- HTTP, off-domain, nested promotion paths, encoded-path tricks, unrelated Atom paths, and unsupported Atom subdomains are rejected.
- Query strings and fragments are stripped from accepted indexed URLs.
- Search result title/description must still identify Atom plus a promotion/offer/ticket context.
- Indexed snippets never produce reusable promo codes. Synthetic fallback HTML still represents a zero-public-code partner shell so stale Atom codes are cleared rather than inferred.
- Existing persisted 30-minute index refresh and six-hour direct Atom reprobe timers remain unchanged.
- Source health now says an **official Atom promotions URL** was index-confirmed instead of falsely claiming the canonical hub was confirmed.

## Regression coverage
New `tests/test_atom_indexed_fallback_v2_266.py` covers:
- canonical hub and one-level detail normalization;
- rejection of HTTP, off-domain, wrong-path, nested, encoded, and unsupported-subdomain URLs;
- detail-page confirmation when the canonical hub is absent;
- canonical preference when both hub and detail pages are present;
- identity fail-closed behavior;
- directory `site:` query and rejection of the former exact `url:` query;
- direct Atom 403 recovery through an indexed official detail page;
- zero reusable codes from indexed fallback.

Existing `tests/test_atom_indexed_fallback_263.py` must continue proving:
- marker compatibility;
- 30-minute index cache window;
- six-hour direct Atom reprobe;
- direct recovery automatically removes fallback state;
- non-access HTTP failures do not use Bing;
- source health uses the actual index-confirmation timestamp.

## Validation / results
### #265
Exact head `69ab983b82032e826a90f9dcb741477bf907685f` on Ubuntu/PRoot Python 3.12 + native libsql:
- targeted movie tests: passed
- pip check clean
- compileall exit 0
- import smoke: 37 modules / 22 symbols
- native libsql: 10 passed
- full pytest: **1,353 passed, 1 warning**
- diff check and worktree clean

### #266
Structural diff is limited to Atom fallback/status/tests plus this active-task record. Exact-head executable validation is still required before ready/merge.

## Production acceptance
After #266 deploy:
1. Direct Atom may still report HTTP-blocked, but indexed fallback must succeed when Bing returns an allowlisted official Atom promotions URL.
2. Status should read `Indexed fallback current` and `official Atom promotions URL confirmed via public index`, not `Stale/unavailable` solely because the hub URL was omitted from Bing results.
3. No stale public Atom codes may reappear from index snippets.
4. A successful future direct Atom reprobe must immediately restore direct authority and normal direct health.
5. Fandango, Gofobo, SeeItEarly, posters, ZIP/local controls, title-labeled result buttons, alert delivery, freshness, dedupe, and feedback must remain intact.

Do not call the movie-ticket task restored until #265 live locality acceptance and #266 Atom recovery both pass production checks.

## Risks / boundaries
- Indexed fallback proves only that Bing currently exposes an allowlisted official Atom promotions URL; it does not prove a reusable code or remaining promotion inventory.
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
Freeze the final #266 head after this task-record commit, run exact-head Python 3.12/native-libsql targeted and full validation, inspect final PR/review state, then merge only that tested SHA and deploy for live Atom acceptance.
