# Target standalone watcher deployment

The Target watcher is a separate process that discovers Target product pages from Target's published PDP sitemap and verifies exact live offers through Target's RedSky JSON endpoints. It writes durable verified events into the same Turso/libSQL database used by SniperPlug. SniperPlug remains the only Discord process.

## Required environment

```env
TURSO_DATABASE_URL=the_same_value_used_by_SniperPlug
TURSO_AUTH_TOKEN=the_same_value_used_by_SniperPlug
TARGET_STORE_ID=1956
TARGET_ZIP=06604
TARGET_STATE=CT
TARGET_LATITUDE=41.1865
TARGET_LONGITUDE=-73.1952
```

The default location above is Bridgeport, Connecticut. Change all five location values together when the watcher should represent a different store and ZIP.

The RedSky key can be overridden without code changes:

```env
TARGET_REDSKY_API_KEY=the_current_Target_web_key
```

Optional exact TCIN seeds are useful before sitemap coverage has completed:

```env
TARGET_WATCH_TCINS=91234567,92345678
```

## Discloud

Create a separate app from the repository using `discloud.target-watcher.config`. Do not give this process a Discord token. Give it the same Turso credentials as the main SniperPlug app.

```bash
cp discloud.target-watcher.config discloud.config
discloud up
```

## Default safety policy

- Official Target PDP sitemap discovery only.
- Exact numeric TCIN identity required.
- Positive structured current price required.
- Target regular price or retained exact Target history is the only reference.
- Shipping, pickup, or add-to-cart availability must be positively verified.
- Every alert-capable observation is re-fetched with a cache-busting exact TCIN request.
- The confirmation must agree on TCIN, current price, regular price, seller, and fulfillment state.
- HTTP rejection, rate limiting, malformed JSON, schema drift, missing fulfillment, or disagreement blocks the event.
- No proxy rotation, CAPTCHA bypass, challenge bypass, or fake-account automation is implemented.

## Default polling policy

```env
TARGET_WATCHER_LOOP_SECONDS=15
TARGET_SITEMAP_BATCH_SIZE=2
TARGET_PRODUCT_BATCH_SIZE=20
TARGET_NORMAL_OFFER_INTERVAL_MINUTES=30
TARGET_MARKDOWN_OFFER_INTERVAL_SECONDS=90
TARGET_BIG_TICKET_MIN_REFERENCE_PRICE=200
TARGET_PRICE_ERROR_MIN_DISCOUNT_PERCENT=69
TARGET_BIG_TICKET_OFFER_INTERVAL_SECONDS=45
```

Products with a trusted reference of at least $200 and an independently confirmed markdown of at least 69% use the protected fast interval. Other verified markdowns remain eligible for each server's normal threshold and category filters.

## Delivery path

```text
Target PDP sitemap
    -> exact TCIN catalog
    -> RedSky product + fulfillment
    -> independent confirmation
    -> verified_retailer_events outbox
    -> SniperPlug fanout
    -> server threshold/category/dedupe/channel + opt-in DMs
```

Use `/autoscan_health` in Discord to inspect Target catalog coverage, due offers, source failures, product failures, pending fanout, and watcher freshness.
