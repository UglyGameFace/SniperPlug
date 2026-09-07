# Active Task

## Task / outcome
**SP-WM-DEAL-DELIVERY-STALL — restore Walmart deal delivery end-to-end.**

Target outcome: catalog discovery feeds exact verification, strict selected-offer/current/reference proof produces eligible Walmart deals, page recovery stays paced, verified deals fan out before maintenance, and proof/shipping/dedupe safety remains intact.

## Scope
`catalog discovery → selected-offer normalization → fresh proof → exact-detail queue → claim → Walmart official detail/exact-search/page recovery → exact result persistence → Walmart/external fanout → bounded maintenance`

Do not switch to unrelated watcher, gateway, setup, DM, or site work without an explicit FORCE SWITCH.

## Status
**PR #257 is merged to `main` as `4d7646dd0b4d120c4c994d60b3d78b6c08760c53`. Implementation and exact-head validation are complete. Active work is production acceptance of the merged Walmart delivery path.**

Merged:
- #256 `Restore Walmart deal verification throughput` → `2b057b6b02b96c44699be69baa915fe9e0d07a97`
- #257 `Recover Walmart exact prices and trusted references` → `4d7646dd0b4d120c4c994d60b3d78b6c08760c53`

Validated #257 head: `edff57a27fb2e7d342970d0bf2deed782dbbffca`

## #256 live acceptance findings
Proven improved after #256:
- catalog discovery remained healthy and safe fresh proof became nonzero again: examples **3/98**, **11/98**, **10/96**
- exact verification produced real verified rows again: examples **3/24**, **10/24**, **10/24**
- page retry/suppression collapsed to **page 0 / page suppressed 0** across repeated post-deploy batches
- exact fetch/store timings returned to sane ranges; supplied post-deploy logs did not show the old 8-10 second Walmart maintenance stalls
- runtime reported `maintenance_after_all_fanout=true`

The remaining blocker exposed by #256 was exact current/reference recovery:
- repeated exact batches failed as `api_price_missing`, including **8/8** and **24/24** batches
- verified fresh rows usually had no trusted reference; example **10 verified / 0 official was prices / 9 no reference / 0 markdowns**

## #257 root cause
- Canonical exact verification tried official Affiliate exact search as a recovery source only when seller/offer identity was missing.
- If official detail had complete identity but no numeric current price, verification returned `api_price_missing` without trying exact search, even though exact search is an exact-ID-checked official API path that can supply current price.
- A strict fresh discovery snapshot could safely prove current selected-offer price while lacking a trusted Walmart was/regular/original reference. It returned immediately as verified and became `verified_no_reference` without a same-cycle exact-search opportunity to recover a trusted reference.

## #257 merged implementation
- Keep `_fetch_exact_candidate_off_loop()` as the only canonical proof implementation.
- Add an exact-search-only provider view with HTML forcibly disabled.
- Hide the fresh discovery snapshot only during the recovery pass so it must use official exact search rather than short-circuiting on the same snapshot.
- On canonical `api_price_missing`, run one exact-ID Affiliate search recovery through the canonical verifier.
- Limit same-cycle trusted-reference enrichment to strict fresh-snapshot candidates that already have valid current-price proof, avoiding an extra exact-search call for every generic no-reference row.
- Failed reference recovery preserves the already-valid no-reference candidate and cannot route it to HTML.
- Recovery remains active while the real HTML challenge circuit is open; that circuit suppresses HTML, not healthy official API work.
- Exact item ID, seller, offer, marketplace, shipping, variant, current-price, and trusted-reference gates remain unchanged.

## #257 validation / results
Exact validated head `edff57a27fb2e7d342970d0bf2deed782dbbffca`, Termux Ubuntu/glibc aarch64, Python 3.12.14:
- `python -m pip check`: passed during environment validation
- `python -m compileall -q sniperplug scripts tests`: **passed**, exit 0
- `python scripts/smoke_imports.py`: **passed**, 37 modules / 22 symbols, exit 0
- native `libsql==0.1.11` built/installed locally on ARM
- `tests/test_libsql_process_connection.py`: **10 passed**, exit 0
- full suite: **1,286 passed**, exit 0
- one unrelated Python `audioop` deprecation warning
- final PR head matched the validated SHA exactly
- final PR state: mergeable, no inline review threads, no submitted reviews
- Qodo review bot was unavailable because its subscription was inactive; this was not a code-review finding

GitHub-hosted Actions remained infrastructure-blocked before execution on the validated head (`runner_id=0`, empty runner name, `steps=[]`). Those red workflow conclusions were not code-test failures.

## Remaining Definition of Done — production acceptance
Deploy/restart current `main`, then live-prove all of the following:
1. `api_price_missing` materially drops from the repeated 8/8 and 24/24 pattern.
2. Exact-search recovery produces verified current-price rows when official detail omits price.
3. `official was prices` / trusted references become nonzero when Walmart actually exposes a trusted native reference.
4. `markdowns` become nonzero when a verified current/reference pair crosses the configured threshold.
5. Fresh no-reference candidates remain valid if reference enrichment fails; they must not become HTML retries.
6. Page retry/suppression remains zero or correctly paced; no multi-request HTML burst returns.
7. Eligible verified markdowns reach Walmart/external fanout and then posts/DMs.
8. Exact fetch/store and Turso maintenance remain off the old 8-10 second hot-path stall pattern.

Only after these live checks pass call Walmart deal delivery restored and close this task.

## Risks / boundaries
- No catalog/relevance neighbor may donate exact price/reference; Walmart exact search must return the requested exact item ID.
- Trusted reference rules remain unchanged. Existing MSRP/list/retail/marketplace-context blocks remain in force.
- Product-page HTML parsing was not blindly changed without raw live page evidence.

## Backlog — not active
- SP-GATEWAY-EVENT-LOOP-LAG
- SP-SETUP-DELIVERY-UX
- SP-DM-DELIVERY-STARVATION
- SP-DM-NEW-LOW-ALERT
- SP-WM-CASH-PDP-AMOUNT
- HP/Target standalone watcher deployment/acceptance
- SP-TURSO-READ-AMPLIFICATION acceptance
- SP-TURSO-WRITE-AMPLIFICATION acceptance
- SP-DEALS-DISCOVER-HARDENING-244 live acceptance
- SniperPlug-Site work

## Next step
Deploy/restart the latest `main` build and inspect fresh production Walmart catalog/exact/fanout logs against the eight acceptance checks above.
