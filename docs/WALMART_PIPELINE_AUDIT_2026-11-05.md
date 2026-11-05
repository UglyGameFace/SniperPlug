# Walmart deal pipeline audit — 2026-11-05

Base audited: `main` @ `7570a992` (PR #308 merge, 2026-11-05 13:22 UTC).
Method: static import/caller analysis from `main.py`, a full test-suite run, and a
new **production-chain harness** (`tests/walmart_pipeline_harness.py`) that drives
the real discovery → exact queue → verification → classification → persistence →
lane → `channel.send` chain with only Walmart's HTTP endpoint and Discord's gateway
objects simulated. Every finding marked **confirmed** below is reproduced by a test
in `tests/test_walmart_end_to_end_pipeline.py` that fails on `7570a992` and passes on
this branch (9 of 26 scenarios failed on `main`).

Nothing here was verified against the live Discloud app, the live database, or the
live Walmart API; those limits are listed in §9.

---

## 1. Executive finding

SniperPlug was not losing deals in one place. Four independent defects sat at
different hand-offs, and the existing tests could not see them because none ran
two stages together.

**Confirmed root causes (fixed in this branch)**

| # | Defect | Effect in production |
|---|--------|----------------------|
| R1 | **Only the first enrolled server ever received a regular markdown post.** `maybe_post_public_deal_cards` mutates the card it is given (adds the "✅ Public deal lane" proof field whose text says *"…scout leads… did not bypass this gate"*). Fanout passes the same card object to every server, and `is_review_or_watchlist` then matched "scout lead" for servers 2…N. | Every server after the first got `quality:review_or_watchlist`. Consistent with the owner's Oct-27 log line `public guild checks/posts 4/1` and with "quality/proof" rejections in `/autoscan_health`. (HP/Target use their own posters and were not affected.) |
| R2 | **Exact-verified price errors whose only comparison is Walmart's MSRP had no delivery lane at any price above $10.** MSRP is (correctly) never markdown proof, so such items classify `verified_no_reference`; the markdown lane never selects that status and the extreme lane hard-capped price at $10 and accepted only a regex of hardware nouns. | A $49 offer on a $599-list TV, a $2 DEWALT drill kit, a $3 LEGO set, a $25 espresso machine: verified, then silently stranded. PR #306 targeted this but required a *trusted* reference, which by definition makes the item a `verified_markdown`, so #307 correctly removed it as unreachable — the real gap stayed open. |
| R3 | **Observed-price-drop detection existed but was never wired into the scheduled pipeline.** The exact worker maintains a per-offer stable-price memory (`walmart_offer_price_memory`) and computes `new_low`/`lower_price`, and `public_deal_quality` has a complete `price_memory_drop` proof gate — but only the legacy per-guild scan (`/autoscan_now`) ever built those cards. | A watched item that drops from a repeatedly confirmed $179 to $59 with no Walmart was-price produced no alert. This is the only generic, evidence-backed anomaly signal for items with no trusted Walmart reference. |
| R4 | **The markdown lane acknowledged deals for servers that were temporarily absent.** PRs #301–#303 fixed this for the opt-in extreme lane only. In the markdown lane, an enrolled server missing from Discord's live guild cache (reconnect/outage) was skipped and the event finalized; with zero live guilds every event finalized with no post. | Silent, permanent loss of deals for that server. |

**Confirmed latent defect (fixed)**

* R5 — `walmart_confirmed_no_price_legacy_cooldown.py` read `row[0]` (Turso returns
  mapping rows → `KeyError: 0`). It runs inside `ensure_exact_runtime_schema_once`,
  which retries every cycle until it completes, so on Turso with any matching legacy
  row **every exact-verification cycle would fail**. Reproduced with Turso-shaped rows.

**Likely contributors (not code defects; tuned or documented)**

* Verification throughput: 8 rows / 20 s while one batched Product Lookup now
  carries 20 IDs. Raised to 20 rows per cycle (same single request).
* Discovery cadence: 3 broad routes + 1 price-sorted sniper search per minute.
  Full broad rotation ≈ 89 min; each sniper product revisited every ≈ 27 min,
  longer whenever exact backpressure pauses discovery (≥ 12 fresh rows due).
* No posted-deal retirement: nothing edits or retires a Discord post when the offer
  expires (see §7).

**Unresolved unknowns**

* What Discloud is actually running, which database backend it uses today, and
  whether the Galaxy Tab S11 / S24 FE offers were ever returned by the Affiliate API.
* Walmart's real Affiliate quota (no published figure found; budgets are self-imposed).
* CI: GitHub Actions jobs for `28f51524` (#307) and `7570a992` (#308) were refused at
  merge time (*"recent account payments have failed or your spending limit needs to be
  increased"*). After the repository was made public they were re-run on 2026-11-05:
  `main` @ `7570a992` is green (§8).

---

## 2. Actual production architecture (reachable from `main.py`)

```
main.py → process_runtime.run → ProcessIsolatedSniperPlugBot (bot.py setup_hook)
  provider_registry["walmart"] = CachedWalmartProvider(WalmartPageRecoveryProvider)
  GlobalAutoScanRunnerCog (cogs/global_auto_scan_runner.py)   ← only Walmart scheduler
    ├─ catalog worker, every 60 s
    │    claim_next_catalog_routes (3 of 265 routes, durable cursor)
    │    discover_walmart_catalog_candidates  → 4 searches: page 1 × 3 routes
    │                                           + 1 price-ascending sniper query (27)
    │    enqueue_walmart_exact_verification_candidates_bulk → walmart_exact_detail_queue
    │    paused when ≥ 12 fresh rows due (walmart_fresh_work_policy)
    ├─ exact worker, every 20 s  → run_exact_worker_cycle()
    │    process_actionable_walmart_exact_queue_batch
    │      claim_due_rows_batched → prefetch_exact_lookup_items (≤ 20 IDs/request, #308)
    │      fetch_exact_candidate_with_page_pacing (fresh catalog proof → official
    │        Product Lookup → exact search → 1 paced HTML probe)
    │      _classify_exact_candidate → verified_markdown | verified_no_reference |
    │        verified_under_threshold | verified_reference | not_buyable
    │      persist_exact_queue_outcomes_bulk (+ offer price memory; NEW: observed-drop proof)
    │    fanout_recent_extreme_walmart_prices   (opt-in lane; non-markdown statuses)
    │    fanout_recent_exact_walmart_deals      (verified_markdown; per-server threshold; DMs)
    │    fanout_verified_retailer_events        (HP/Target)
    │    bounded post-fanout maintenance
    └─ reconciliation loop, every 30 min (ghost guild repair + fanout)
  All public posts → public_deal_posts.maybe_post_public_deal_cards
    (category prefs → quality gate → durable reservation → channel.send → receipt)
```

**Authoritative decisions (one owner each)**

| Decision | Authority |
|---|---|
| Is a reference price trusted? | `providers/walmart.py::_trusted_reference_price` (was/regular/strikethrough/original only; MSRP never) |
| Queue status | `walmart_exact_verification_queue._classify_exact_candidate` |
| Observed-drop proof (NEW wiring) | `walmart_exact_queue_bulk_persistence.apply_observed_price_drop_proof` |
| Lead evidence for non-markdown offers (NEW single authority) | `walmart_extreme_price_signals.exact_price_lead_evidence` (also drives discovery priority) |
| Public markdown quality | `public_deal_quality.public_deal_rejection_reason` |

---

## 3. Historical regression inventory

| Incident / PR | Remedy present on main | Reachable | Validated by production-chain test | Deployed |
|---|---|---|---|---|
| #256 selected-offer identity | yes | yes | indirectly (all scenarios require identity) | unknown |
| #277–#291 no-price retry, 6001, dormancy | yes | yes | 6001 path exercised by harness | unknown |
| #293/#294 local SQLite | yes, env-gated | yes | — | **Oct-27 logs show Turso still in use**; current backend unknown |
| #297 page-1 freshness | yes (now 3 routes + sniper) | yes | yes | Oct-27 logs: deployed |
| #298 Turso mapping rows (extreme) | yes | yes | yes (mapping-row scenario) | Oct-27 logs: deployed |
| #301–#303 extreme cursor/outbox | yes | yes | existing tests | unknown |
| #301–#303 equivalent for **markdown lane** | **no → fixed here (R4)** | — | yes | — |
| #304 classification/priority | yes | yes | yes | unknown |
| #305 junk rejection | yes | yes | yes (jewelry, earrings, codes, subscription, gift card, case) | unknown |
| #306 ratio lane | removed by #307 (unreachable — confirmed) | no | — | — |
| #307 price-sorted sniper | yes | yes | exercised via discovery | unknown |
| #308 batched lookups | yes | yes | yes (incl. partial batch) | unknown |

---

## 4. Legacy / duplicate-code inventory (Walmart)

| Module | Runs? | Caller | Verdict |
|---|---|---|---|
| `cogs/auto_scan_runner.py` (legacy per-guild runner) | Base class only; its scheduled per-guild loop is **not started** by the global cog. `/autoscan_now` (a canonical manual command) still runs its per-guild scan on demand | inherited by resilient → global | **Keep for now** (live cog inherits its helpers and manual command); fold into the global cog later. That on-demand scan was the only caller that built price-memory-drop cards, so automatic detection never produced them; superseded by R3 wiring. |
| `cogs/resilient_auto_scan_runner.py` | Base class only | inherited by global | Keep (watchdog constants, queue health); fold into global cog later. |
| `cogs/native_auto_scan_runner.py` | imported, no scheduler | resilient | Keep (route helpers); no production loop. |
| `services/walmart_global_deal_fanout.py` | helpers only (`_exact_card_for_candidate`, tables, DM embed) | bulk fanout, health, recovery | **Compatibility/shared** — not a second sender. |
| `services/walmart_extreme_price_fanout.py` | yes | exact worker | Authoritative opt-in lane; own per-guild outbox (15-min tickets). Markdown lane now uses event deferral instead of a second outbox table. |
| `services/walmart_extreme_price_signals.py` | yes | extreme lane, enrichment priority | Now the **single** lead-evidence authority (`exact_price_lead_evidence`). The `trusted_reference_price_anomaly` branch of `public_extreme_value_evidence` cannot fire inside the extreme lane (a trusted ≤ 20% reference makes the item a markdown); it is harmless and #305's unit tests call the helper directly, so it is left for a cleanup PR. |
| `services/autoscan_observed_price_memory.py`, `walmart_observed_price_memory.py` | manual/legacy paths only | auto_scan_runner, auto_discovery | Superseded for scheduled detection by R3; keep for manual commands. |
| `services/walmart_metadata_install.py` | yes | monkeypatches `build_review_card` and renderer at import | Works, but monkeypatching makes call graphs misleading; candidate for consolidation. |
| `cogs/open_box_deals.py`, `services/open_box_autoscan_routes.py`, `autoscan_route_policy.py`, `deal_quality.py`, `resale_hunt.py`, `walmart_accuracy.py`, `walmart_pdp_cash_proof.py`, `autoscan_no_post_*` | **not importable from `main.py`** | none | **Retire candidates** (category E/D). Not deleted in this PR: they carry tests and no runtime cost; deletion should be a separate mechanical PR. |
| `services/walmart_confirmed_no_price_legacy_cooldown.py` | yes (schema-once) | runtime schema | Compatibility; fixed R5. |

---

## 5. Deal-quality audit: why premium bargains were missed while junk posted

Actual decision rules on `main` for an exact-verified, buyable offer:

1. Walmart was/regular/strikethrough/original price present → `verified_markdown` →
   markdown lane, per-server threshold (default 40%). **Worked — but only for the
   first enrolled server (R1).**
2. Only MSRP/list price (Walmart's normal case), price ≤ $10, title/category leaf
   matches a hardware noun list → opt-in extreme lane. **Worked.**
3. Only MSRP/list price, price > $10 → **no lane (R2)**.
4. Only MSRP/list price, ≤ $10, not a listed hardware noun (power tools, LEGO,
   appliances, perfume…) → rejected (R2).
5. No Walmart reference but SniperPlug's own stable observed price is far higher →
   **no lane (R3)**.
6. $9.92 jewelry, $10 earrings, game codes, subscriptions, gift cards → rejected
   since #305. Still rejected (tested).

New rules (this branch):

* **Observed price drop** (R3): same item/offer/seller/variant/condition/fulfillment,
  stable price confirmed ≥ 2 times ≥ 4 h apart within 30 days, drop ≥ 10% and ≥ $5 →
  `verified_markdown` with `price_memory_drop` lane; the public gate re-verifies the
  fingerprint and the server threshold still applies. A trusted Walmart reference
  always wins; memory never invents one.
* **Listed-price anomaly lead** (R2): exact price ≤ 20% of a Walmart-returned
  list/MSRP price ≥ $50, not accessory/part, not digital credit/software/subscription,
  not a cheap consumable, **and** sold by Walmart itself or recognizably substantial
  hardware → opt-in extreme lane at any price. The alert shows both numbers, says the
  list price is not a verified was-price, and claims **no percentage**.
* Product importance is still mostly type-based. Real demand evidence (sell-through,
  resale comps) does not exist in this codebase for Walmart; eBay comps are only in
  open PR #200.

Known tuning risk: Walmart first-party clearance items at ≤ 20% of MSRP will now
appear as opt-in leads. Watch the extreme-lane volume after deploy.

---

## 6. Speed, coverage and cost

**Request plan (per minute, steady state):** 4 Affiliate searches (3 broad page-1 +
1 price-ascending sniper) + exact lookups at 1 request per ≤ 20 claimed items.

| Metric | Before | After |
|---|---|---|
| Broad route rotation (265 routes) | ≈ 89 min (theoretical; longer under backpressure) | unchanged |
| Sniper product revisit (27 queries) | ≈ 27 min | unchanged |
| Exact verification ceiling, normal mode | 24 items/min | 60 items/min, same request count |
| 50-item backlog (measured in harness) | 5 cycles | ≤ 3 cycles, ≤ 4 lookup requests |
| No-reference safety recheck | weekly (catalog price change wakes rows immediately) | unchanged |

Coverage blind spots (source limits, not fixable by classification): no ZIP/store
context in Affiliate search; page 1 of 25 results per query; price-ascending sniper
pages can be crowded by accessories cheaper than a partial price error; store-only and
some open-box offers are invisible. A $1 tablet surfaced only on social media cannot be
proven to have been in the API feed.

Detection latency for a price error therefore ≈ time until the right query is
searched (≤ 27 min for sniper products, ≤ 89 min for broad routes) + ≤ 20 s claim +
seconds of fanout. Real p50/p95 must be measured from production logs.

**Database cost (measured on SQLite with a 1,500-row queue, 3 servers):** idle exact
cycle ≈ 13 statements; busy cycle 23; no full scans of `walmart_exact_detail_queue`
or other large tables in the hot path (`EXPLAIN QUERY PLAN` shows only bounded CTEs,
VALUES lists, and the tiny opt-in settings table). The observed-drop wiring reuses the
existing offer-memory SELECT, so it adds no statement.

---

## 7. Lifecycle and reliability

* Markdown lane (R4): events for enrolled servers missing from the live cache are now
  **deferred** (5-min backoff, not blocking newer events), never finalized; servers
  that already received the deal are protected by existing durable reservations;
  after 6 h the event finalizes with a warning (too old to announce as current).
* Stale replay: before any delivery the event is checked against the current queue
  row; a re-verified different price, `not_buyable`, or loss of markdown status
  supersedes the event (tested).
* Extreme lane: unchanged #301–#303 outbox (15-min freshness).
* **Not implemented:** editing/retiring a Discord post when its offer later expires.
  No background worker does this today; posted markdowns are re-verified only by the
  24 h safety sweep or a catalog price change, and that result is not propagated to
  Discord. This needs a design decision (edit vs. follow-up message vs. reaction).

---

## 8. Validation performed

* Full suite on `main` @ `7570a992` (offline: no package installs available, so
  discord.py/aiohttp/aiosqlite were replaced by minimal local stand-ins): 1,530 passed,
  13 failed — all 13 are environment-only (no `libsql` package, discord UI internals,
  aiohttp internals). Same 13 on this branch; **1,556 passed**.
* New production-chain scenarios: 26; **9 fail on `main`, 26 pass on this branch.**
* Turso-shaped (mapping-row) end-to-end run passes; R5 reproduced and fixed.
* GitHub Actions (real dependencies), re-run 2026-11-05 after the repo went public:
  `main` @ `7570a992` — Python tests / Python Check **1,543 passed**, import smoke check
  passed; this branch @ `ce10e35` — **1,569 passed** (1,543 + 26 new), import smoke check
  passed. The 13 offline-only failures above do not occur in CI.

## 9. Not verified (requires production access)

Deployed SHA on Discloud; live database backend; live Walmart responses for the S24 FE
and Galaxy Tab S11 incidents; real p50/p95 latencies; Turso/local row counts; real
extreme-lane volume after R2; Discord permissions in each server.

## 10. Remaining prioritized work

1. ~~Get green CI on this branch~~ — done (repo made public; all checks green). 
2. Merge and deploy, then confirm from logs: `guild checks/posts N/N` for a qualifying deal,
   `Global Walmart fanout deferred` during a reconnect, first `price_memory_drop` post. (P0)
3. Posted-deal expiry handling (§7). (P1)
4. Decide discovery budget: e.g. 2 broad + 2 sniper searches halves sniper revisit
   (27 → 14 min) but slows broad rotation (89 → 133 min). Needs the real Walmart quota. (P2)
5. Retire unreachable modules in §4 in a mechanical PR. (P2)
