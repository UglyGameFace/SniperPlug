# Target standalone watcher deployment

The Target watcher is a separate process that discovers Target product pages from Target's published PDP sitemap and verifies exact live offers through Target's RedSky JSON endpoints. It writes durable verified events into the same Turso/libSQL database used by SniperPlug. SniperPlug remains the only Discord process.

## Required environment

```env
TURSO_DATABASE_URL=the_same_value_used_by_SniperPlug
TURSO_AUTH_TOKEN=the_same_value_used_by_SniperPlug
TARGET_REDSKY_API_KEY=the_current_Target_web_key
```

Those three values are global infrastructure secrets. **Do not configure a global Target store, ZIP, state, latitude, or longitude.** Server and user locations are selected inside Discord and stored in Turso.

`TARGET_REDSKY_API_KEY` is intentionally not committed to the repository. Store it as a Discloud environment secret and update it whenever Target rotates the web key. The watcher refuses to start when the secret is missing.

Optional exact TCIN seeds are useful before sitemap coverage has completed:

```env
TARGET_WATCH_TCINS=91234567,92345678
```

## Discord location setup

A server administrator runs:

```text
/target_location zip_code:06605
```

SniperPlug queries Target's nearby-store endpoint and shows a dropdown. The administrator chooses the exact Target store. The saved store, ZIP, state, and coordinates are stored in Turso and Target is added to that server's retailer list only after selection.

Personal Target DMs use:

```text
/target_dm_location zip_code:06605
```

Clear commands disable the corresponding local Target delivery:

```text
/target_location_clear
/target_dm_location_clear
```

Existing servers do not inherit an owner or Connecticut location. The migration removes the original unsafe Target auto-enrollment from every guild without a saved Target profile.

## Multi-tenant scanning model

- The PDP sitemap creates one global catalog row per TCIN.
- Guild and user profiles are grouped by unique `store_id + ZIP`.
- A bounded cursor rotates catalog slices through each unique location.
- Ten thousand servers using the same Target store share one location scan.
- Exact location-state rows are materialized gradually instead of immediately creating catalog × guild rows.
- Public and DM fanout re-check the saved location and block mismatched store/ZIP events.

## Discloud

Create a separate app from the repository using `discloud.target-watcher.config`. Do not give this process a Discord token. Give it the same Turso credentials as the main SniperPlug app and add the required Target RedSky key as a secret.

```bash
cp discloud.target-watcher.config discloud.config
discloud up
```

## Default safety policy

- Official Target PDP sitemap discovery only.
- Sitemap wire bytes are read without transparent decompression, capped by `TARGET_SITEMAP_MAX_COMPRESSED_BYTES`, then gzip-expanded inside the bounded parser with `TARGET_SITEMAP_MAX_EXPANDED_BYTES`.
- Every product and fulfillment request requires an explicit saved Target location.
- Exact numeric TCIN identity required.
- Positive structured current price required.
- Target regular price or retained exact Target history is the only reference.
- Target Plus products require an explicit marketplace seller identity.
- Pickup and Drive Up proof is accepted only for the saved Target store; nearby-store inventory is ignored.
- Shipping, saved-store pickup, or add-to-cart availability must be positively verified.
- Every alert-capable observation is re-fetched with a cache-busting exact TCIN request.
- The confirmation must agree on TCIN, current price, regular price, seller, and fulfillment state.
- HTTP rejection, rate limiting, malformed JSON, schema drift, missing fulfillment, or disagreement blocks the event.
- No proxy rotation, CAPTCHA bypass, challenge bypass, or fake-account automation is implemented.

## Durable work leases

Sitemap and product polling use token-checked database leases stored in the shared Turso database. This prevents two Target watcher instances from polling the same row simultaneously. Leases expire automatically after a crash, and a stale worker cannot complete or overwrite work after another process has reclaimed it.

## Default polling policy

```env
TARGET_WATCHER_LOOP_SECONDS=15
TARGET_SITEMAP_BATCH_SIZE=2
TARGET_PRODUCT_BATCH_SIZE=20
TARGET_LOCATIONS_PER_CYCLE=2
TARGET_PRODUCTS_PER_LOCATION_BATCH=20
TARGET_LOCATION_SCAN_SPACING_SECONDS=15
TARGET_NORMAL_OFFER_INTERVAL_MINUTES=30
TARGET_MARKDOWN_OFFER_INTERVAL_SECONDS=90
TARGET_BIG_TICKET_MIN_REFERENCE_PRICE=200
TARGET_PRICE_ERROR_MIN_DISCOUNT_PERCENT=69
TARGET_BIG_TICKET_OFFER_INTERVAL_SECONDS=45
TARGET_SITEMAP_MAX_COMPRESSED_BYTES=26214400
TARGET_SITEMAP_MAX_EXPANDED_BYTES=104857600
```

Products with a trusted reference of at least $200 and an independently confirmed markdown of at least 69% use the protected fast interval. Other verified markdowns remain eligible for each server's normal threshold and category filters.

## Delivery path

```text
Target PDP sitemap
    -> global TCIN catalog
    -> bounded unique-location cursor
    -> RedSky product + exact saved-store fulfillment
    -> independent confirmation
    -> verified_retailer_events outbox
    -> location-safe SniperPlug fanout
    -> server/user filters and dedupe
```

Use `/autoscan_health` in Discord to inspect Target catalog coverage, selected server store, source failures, product failures, pending fanout, and watcher freshness.
