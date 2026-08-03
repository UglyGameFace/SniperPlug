# HP big-ticket price-error mode

The standalone HP watcher is intentionally tuned for expensive products with extreme markdowns rather than cheap accessories that happen to have a large percentage discount.

## Default policy

```env
HP_BIG_TICKET_MIN_REFERENCE_PRICE=200
HP_PRICE_ERROR_MIN_DISCOUNT_PERCENT=69
HP_BIG_TICKET_OFFER_INTERVAL_SECONDS=45
HP_WATCHER_LOOP_SECONDS=10
HP_OFFER_BATCH_SIZE=80
HP_PRODUCT_PAGE_BATCH_SIZE=24
```

A product can enter the protected big-ticket lane when any trusted exact value is at least the configured floor:

- HP structured MSRP (`lPrice`)
- Current exact HP.com price before a drop
- Prior exact HP.com price history retained by SniperPlug
- A retained trusted reference from an earlier exact observation

A public HP event is created only when the trusted reference is at least the big-ticket floor and the independently confirmed discount is at least the price-error discount floor.

Examples with the defaults:

| Exact price movement | Result |
|---|---|
| $1,000 → $299 | Alert eligible (70.1% off) |
| $800 → $100, no MSRP but prior exact history exists | Alert eligible (87.5% off) |
| $1,000 → $310.01 | Blocked (below 69%) |
| $54.99 → $12.99 | Blocked (reference below $200) |

## Polling behavior

- The watcher runs a cycle every 10 seconds.
- Each structured price request can include up to 80 exact HP product identities.
- Up to 75% of a batch is protected for known due big-ticket products.
- Unused protected capacity immediately goes to products that have never been classified and then the background catalog.
- Known big-ticket products are scheduled every 45 seconds.
- Cheap products are returned to the normal background interval even when their percentage markdown is large.

The effective sweep time depends on how many HP products qualify as big-ticket. For example, 600 due big-ticket products with 60 protected slots per 10-second cycle would rotate in roughly 100 seconds before network and retry overhead. The watcher records live coverage and due counts for `/autoscan_health`; tune only after observing those numbers.

## Verification remains fail-closed

Before an alert is written to SniperPlug's shared outbox, the watcher requires:

1. Exact HP catalog-entry ID and normalized SKU match.
2. Positive structured HP price—not visible page text.
3. Trusted reference price of at least the configured big-ticket floor.
4. Discount at or above the configured price-error floor.
5. Product not explicitly out of stock or blocked from add-to-cart.
6. A second cache-busted exact-product price request that agrees with discovery.

A mismatch, missing identity, malformed response, zero price, parser drift, or failed confirmation blocks the event.

## Deployment

The HP watcher remains a separate Discloud application and must use the exact same remote database as SniperPlug:

```env
TURSO_DATABASE_URL=the_same_value_as_SniperPlug
TURSO_AUTH_TOKEN=the_same_value_as_SniperPlug
```

It does not require a Discord token. SniperPlug remains the only process that posts to Discord and continues to apply server channels, category settings, duplicate protection, feedback controls, and DM subscriptions.
