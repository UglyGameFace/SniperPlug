# Active Task

## Task / outcome
**SP-WM-DEAL-DELIVERY-STALL — restore Walmart deal delivery end-to-end.**

Target outcome: catalog discovery feeds exact verification, strict selected-offer/current/reference proof produces eligible Walmart deals, page recovery stays paced, verified deals fan out before maintenance, and proof/shipping/dedupe safety remains intact.

## Scope
`catalog discovery → selected-offer normalization → fresh proof → exact-detail queue → claim → Walmart official detail/exact-search/page recovery → exact result persistence → Walmart/external fanout → bounded maintenance`

Do not switch to unrelated watcher, gateway, setup, DM, or site work without an explicit FORCE SWITCH.

## Status
**PR #256 is merged and its original production choke is live-improved. Production acceptance exposed a second blocker on the same Walmart delivery path: exact current/reference recovery. Draft PR #257 is the active implementation follow-up.**

Merged: #256 `Restore Walmart deal verification throughput` → `2b057b6b02b96c44699be69baa915fe9e0d07a97`
Active: #257 `Recover Walmart exact prices and trusted references`
Branch: `fix/walmart-exact-price-recovery-257`

## #256 validation
Exact validated #256 head `edd9e6f31535db64f00b26b1a152fae50a042266` on Termux Ubuntu aarch64 / Python 3.12.14:
- affected regression set: **32 passed**
- native `libsql==0.1.11` built successfully from source on ARM
- native libsql process tests: **10 passed**
- `pip check`: passed
- compileall: passed, exit 0
- import smoke: **37 modules / 22 symbols**, exit 0
- full suite: **1,282 passed**, exit 0
- one unrelated Python `audioop` deprecation warning

GitHub-hosted Actions remains infrastructure-blocked before execution (`runner_id=0`, empty runner name, `steps=[]`).

## Live acceptance after #256
What is now proven improved:
- catalog discovery remains healthy and safe fresh proof is nonzero again: examples **3/98**, **11/98**, **10/96**
- exact verification produces real verified rows again: examples **3/24**, **10/24**, **10/24**
- after the new deployment, page retry/suppression collapsed to **page 0 / page suppressed 0** across repeated batches
- exact fetch/store timings returned to sane ranges; supplied post-deploy logs do not show the old 8-10 second Walmart maintenance stalls
- runtime explicitly reports `maintenance_after_all_fanout=true`

What still blocks end-to-end deal delivery:
- repeated exact batches now fail as `api_price_missing`, including **8/8** and **24/24** batches
- verified fresh rows usually have no trusted reference; example **10 verified / 0 official was prices / 9 no reference / 0 markdowns**
- therefore the original page-pacing/maintenance choke moved out of the way, revealing exact current/reference recovery as the next same-path bottleneck

## #257 root cause
- Canonical exact verification currently tries official Affiliate exact search as a recovery source only when seller/offer identity is missing.
- If official detail has complete identity but no numeric current price, verification returns `api_price_missing` without trying exact search, even though exact search is the same exact-ID-checked API path already supplying catalog prices.
- A strict fresh discovery snapshot may safely prove the current selected-offer price while lacking a trusted Walmart was/regular/original reference. It returns immediately as verified, so it never receives a same-cycle exact-search opportunity to recover a trusted reference and becomes `verified_no_reference`.

## #257 implementation
- Keep `_fetch_exact_candidate_off_loop()` as the only canonical proof implementation.
- Add an exact-search-only provider view with HTML forcibly disabled.
- Hide the fresh discovery snapshot only for that recovery pass so the pass must use official exact search rather than short-circuiting on the same snapshot.
- On canonical `api_price_missing`, run one exact-ID Affiliate search recovery through the canonical verifier.
- On a verified candidate with no trusted reference, run one best-effort exact-search reference recovery through the canonical verifier.
- Failed reference recovery preserves the already-valid no-reference candidate; it cannot downgrade it or route it to HTML.
- Recovery remains active while the real HTML challenge circuit is open; that circuit suppresses HTML, not healthy official API work.
- Exact item ID, seller, offer, marketplace, shipping, variant, current-price, and trusted-reference gates remain unchanged.

## #257 regressions
Added coverage for:
1. exact detail has seller/offer identity but no price; exact search recovers current + trusted reference
2. the same recovery still works while the real HTML circuit is open
3. strict fresh current-price proof with no reference gets best-effort exact-search reference recovery without HTML
4. failed reference recovery preserves the valid fresh candidate

## Remaining Definition of Done
1. Validate exact #257 head with targeted Walmart tests, `pip check`, compileall, import smoke, native libsql process tests, and the full suite.
2. Inspect final #257 diff/review state and keep the PR draft until evidence is green.
3. Merge/deploy #257 only after exact-head validation.
4. Live-prove `api_price_missing` materially drops, official trusted references/markdowns become nonzero when Walmart exposes them, page suppression remains 0/appropriately paced, and eligible verified deals reach fanout/posts/DMs.
5. Only then call Walmart deal delivery restored and close this active task.

## Risks / boundaries
- No catalog/relevance neighbor is allowed to donate exact price/reference; Walmart exact search must return the requested exact item ID.
- Trusted reference rules are unchanged. MSRP/list/retail/marketplace context remains blocked where existing code blocks it.
- Product-page HTML parsing is not being blindly changed without raw live page evidence.

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
Run exact-head #257 validation, then merge/deploy only if the full evidence is green.
