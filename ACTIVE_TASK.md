# Active Task

## Status
**Active — root cause confirmed; implementation and validation in progress.**

## Task
**SP-WM-IDENTITY-RECOVERY — Stop valid Walmart exact-detail deals from being over-blocked by duplicate seller/offer parsing and make every recovery item directly viewable for manual verification.**

## Scope
1. Trace the real discovery → exact-detail → identity → queue → fanout path.
2. Reuse the canonical selected-offer identity already normalized by the Walmart provider instead of reparsing a narrower subset independently.
3. Preserve fail-closed behavior for genuinely unknown marketplace sellers, cross-offer pricing, shipping, item mismatches, and incomplete exact proof.
4. Ensure `/walmart_recovery` gives a direct Walmart product link even when the event has no verified card because exact identity was blocked.
5. Add regressions for canonical selected-offer attribute reuse and manual-review access to terminal identity rows.
6. Run targeted/full CI, compile/import/static validation, cleanup, and conflict inspection before completion is claimed.

## Root cause / findings
- Recovery intentionally includes terminal `incomplete_identity` / `identity_mismatch` queue rows, so it can surface products that never became a verified fanout event.
- Those rows had `card=None`; the recovery UI only rendered **Open Walmart** when `item.card.url` existed, so the exact products most in need of manual inspection could not be opened even though their durable queue row already stored a Walmart `product_url`.
- Exact-detail candidate construction already runs Walmart's canonical selected-offer normalizer, which records `selectedOfferSeller`, `selectedOfferSellerId`, `selectedOfferId`, and `selectedOfferMarketplace` in candidate attributes.
- `_apply_exact_payload_offer_identity()` then independently reparsed only a narrower set of raw payload/root paths and did not consume those canonical selected-offer attributes. That creates a false `missing seller identity` / `incomplete_identity` path when the provider successfully normalized the selected offer but the later verifier fails to rediscover the same fields.
- The fix must consolidate around the existing canonical selected-offer truth rather than weakening identity requirements or guessing that an unknown marketplace seller is Walmart.

## Planned changes
- Prefer canonical selected-offer seller/offer/marketplace attributes produced from the same exact payload, while retaining raw exact-detail fallbacks for compatibility.
- Record the identity source so diagnostics distinguish canonical selected-offer proof from raw-field fallbacks.
- Keep sellerless marketplace/unknown offers blocked.
- Make recovery link access use the durable `product_url` independently from verified-card availability.
- Preserve all current auto-post, DM, threshold, duplicate, price, shipping, variant, condition, and fulfillment gates.

## Validation required
- Focused identity payload tests.
- Recovery loader/view regression for `exact_identity_blocked` rows with no card.
- Full Python 3.11 suite, Python Check, import smoke, `pip check`, and compileall through GitHub Actions.
- Final changed-file, duplicate/conflict, and review-thread inspection.

## Backlog
- Resume the SniperPlug-Site Whop app-specific content-reader task for Hidden Files experiences such as `Make Money Here`, `Content`, and `Better Content`.
- Investigate repeated Discord gateway `Can't keep up ... websocket is 41.5s behind` warnings during Walmart drain-mode exact verification after this task closes.
- Continue verifying PR #223 delivery-health accounting from live post-deploy logs.
- PR #200 remains isolated and must not be mixed into unrelated work.
