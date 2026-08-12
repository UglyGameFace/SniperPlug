# Active Task

## Status
**Code-side complete — SP-WM-IDENTITY-RECOVERY satisfied its Definition of Done on PR #233. Production behavior remains unclaimed until the merged code is deployed and live Walmart traffic exercises the fallback.**

## Task
**SP-WM-IDENTITY-RECOVERY — Recover strict Walmart seller/offer identity when the official exact-detail payload is incomplete, and make every recovery item directly viewable for manual verification.**

## Scope
1. Trace discovery → exact detail → selected-offer identity → exact queue → global fanout → public/DM delivery.
2. Preserve the official Walmart exact-detail API as the authoritative item/current-price source.
3. Add a bounded product-page structured-data fallback only for exact rows whose seller identity remains unresolved.
4. Preserve all fail-closed item, seller/offer, variant, condition, fulfillment, shipping, reference-price, availability, threshold, category, dedupe, and DM gates.
5. Make `/walmart_recovery` directly open identity-blocked Walmart products even when no verified card exists.
6. Re-arm only a bounded recent slice of terminal seller/offer identity rows so existing recoverable deals get another exact pass without recreating the old queue-drain storm.
7. Complete targeted tests, full regression tests, compile/import/dependency validation, cleanup, conflict inspection, and PR review inspection.

## Root cause / findings
- Walmart catalog discovery was finding products correctly, but exact delivery can proceed only after `exact_offer_identity()` builds a complete exact-offer fingerprint. Rows with unresolved seller identity become `incomplete_identity` and are terminally quarantined before global fanout, so public autoscan and personal DM matching never see them.
- The existing Walmart selected-offer normalizer already preserves strict same-offer pricing/shipping/reference rules. The remaining high-volume identity failures are cases where the official exact-detail response itself does not expose enough selected-seller evidence for the canonical exact identity to pass.
- Recovery intentionally includes those terminal queue rows, which is why the private selector could show their titles with `?% • Exact Identity Blocked` even though they never became verified deal cards.
- Identity-blocked rows have `card=None`. The recovery UI previously built **Open Walmart** only from `item.card.url`, so those rows could not be opened even though their Walmart item ID/product URL was retained.
- Re-arming every historical terminal row would be unsafe: prior production had thousands of terminal identity rows, and bulk rechecks could again crowd out fresh exact verification. Recovery therefore needs a bounded migration, not a global reset.

## Changes delivered
- Added `WalmartPageRecoveryProvider`, registered as the production Walmart provider beneath the existing cache/request-coordinator wrappers.
- Official exact-detail remains first and authoritative. Product-page recovery runs only when seller identity is missing and the exact payload is not already explicitly non-marketplace/Walmart-owned.
- The fallback requests only the canonical `https://www.walmart.com/ip/<item_id>` page using ordinary HTTP. It parses bounded JSON/JSON-LD/Next structured scripts rather than visible-text scraping or raw-page heuristics.
- Product-page identity may fill seller name/ID, offer ID, and marketplace classification only after the structured product matches the exact requested Walmart item ID.
- When the official API has an item price, the recovered page offer must agree cent-for-cent before seller/offer identity can be attached. Price disagreement is retryable and cannot bind a seller.
- Equally strong conflicting page offers, different-item structured data, marketplace-classification conflicts, missing seller evidence, and lookalike/non-Walmart redirects stay fail closed.
- The page fallback never imports a page was/reference price and never substitutes a cheaper seller. Existing same-offer reference proof remains authoritative.
- Existing third-party shipping protection remains intact: recovering a marketplace seller does not make a deal alertable if mandatory shipping is unknown.
- Request/resource bounds: two concurrent fallback page requests, 5 MB wire limit, 2 MB per script, 4 MB total structured-data budget, 24 scripts, and 30,000 JSON nodes. `WALMART_PAGE_RECOVERY_ENABLED=false` is an emergency kill switch; timeout is bounded through `WALMART_PAGE_RECOVERY_TIMEOUT_SECONDS`.
- Challenge/network/HTTP failures are treated as retryable exact-provider failures; there is no CAPTCHA bypass, proxy rotation, fingerprint spoofing, or challenge evasion.
- Added a one-time startup migration that re-arms at most 250 recent/high-priority seller/offer `incomplete_identity` rows from the prior 72 hours. Older terminal rows remain quarantined.
- `/walmart_recovery` now builds a canonical Walmart item URL from the retained numeric item ID when no verified card exists, so identity-blocked rows always have an **Open Walmart** manual-inspection path.
- Hardened the fallback against cross-offer contamination: a parent/root price cannot borrow seller identity from a different nested offer, and redirect host validation requires the actual `walmart.com` domain or a real subdomain.

## Validation
- Two isolated branch validation runs applied the existing-file changes with exact-match guards, ran targeted checks, and removed their temporary workflow files before committing; no applicator workflow remains in the final diff.
- Focused regressions cover API-complete skip behavior, exact item + cent-aligned Walmart seller recovery, third-party seller recovery with shipping still blocked, marketplace conflicts, price-change retry behavior, wrong-item structured data, ambiguous offers, cross-offer seller/price contamination, redirect-host validation, bounded one-time terminal-row rearm, recovery manual-link access, and production provider wiring.
- Targeted Walmart identity/recovery/selected-offer suites passed after both implementation and hardening passes.
- Normal PR CI on implementation head `86d28b5c130552b0cecc95b27fba8cc5df4d7af6`:
  - Import smoke check: **PASS**.
  - Python Check: **PASS** (compile, import smoke, full suite).
  - Python tests on Python 3.11.15: **PASS**.
  - `pip check`: **PASS — no broken requirements**.
  - `python -m compileall -q sniperplug tests *.py`: **PASS**.
  - Full suite: **1,173 passed**, one upstream `discord.py` `audioop` deprecation warning.
- This task-record-only commit must remain green under the same PR workflows before merge/readiness is claimed.

## Cleanup and conflict inspection
- PR #233 is mergeable and the implementation branch was **0 commits behind `main`** at the final implementation inspection.
- Final runtime/test scope is limited to `sniperplug/bot.py`, `sniperplug/cogs/canonical_public_alerts.py`, the new `sniperplug/providers/walmart_page_recovery.py`, focused tests, and this task record.
- Temporary GitHub Actions applicator files removed themselves and are absent from the PR changed-file list.
- No alternate posting path, relaxed identity gate, raw-page storage, page-derived was-price path, CAPTCHA/challenge bypass, or duplicate Walmart provider remains.
- PR #233 had no review comments/threads at the final implementation inspection.

## Merge / deployment boundary
- PR #233: `Recover Walmart seller identity from exact product pages`.
- Production completion is intentionally unclaimed until the PR is merged, deployed to the canonical SniperPlug Discloud app, and live logs show seller-identity recovery/normal fanout behavior without new queue pressure or Discord lag.

## Backlog
- Resume the SniperPlug-Site Whop app-specific content-reader task for Hidden Files experiences such as `Make Money Here`, `Content`, and `Better Content`.
- Investigate repeated Discord gateway `Can't keep up ... websocket is 41.5s behind` warnings during Walmart drain-mode exact verification after this task closes.
- Continue verifying PR #223 delivery-health accounting from live post-deploy logs.
- PR #200 remains isolated and must not be mixed into unrelated work.
