# SniperPlug eBay: single-app integration and EPN review evidence

## Authoritative runtime

The old standalone eBay watcher PR #200 was **closed without merging** on Nov 12, 2026. It assumed a second Discloud application that could share Turso with SniperPlug, contrary to the canonical single-Discloud-app, local-SQLite production architecture. Do not deploy, cherry-pick, or enable that abandoned branch. The current integration does **not** import it.

The replacement uses exactly one `EbayBrowse` service and one `EbayResearchCog` inside `sniperplug/bot.py`, plus isolated consent storage in the **existing** SQLite DB. No separate watcher, duplicate sender, remote database, scraping, resale-comp fabrication or generic eBay affiliate broadcast was added.

## Enabling research, without enabling promotions

Production Developer credentials (Client ID / Cert ID), **not EPN portal tokens**, are required. Enter them in **Discloud's protected environment**:

```text
EBAY_CLIENT_ID=<Developer Production Client ID>
EBAY_CLIENT_SECRET=<Developer Production Cert ID>
EBAY_RESEARCH_ENABLED=true
EBAY_SANDBOX=false
EBAY_EPN_CAMPAIGN_ID=
EBAY_EPN_MESSAGING_APPROVED=false
```

Never commit or paste actual secrets into GitHub, Discord, or support tickets. When keys are not installed, `/ebay research` fails safely with a setup message. All live queries use the official `https://api.ebay.com/identity/v1/oauth2/token` client-credentials exchange and `https://api.ebay.com/buy/browse/v1/item_summary/search`. One process permits eight research requests/minute total, with two commands/user/minute, fixed-price listings and USD-only parsing. The output is **current asking prices, not completed sales**; a missing shipping quote means the delivered total is unknown.

A keyset and EPN membership **do not** prove eBay granted special Marketplace Insights sold history, bulk feed access, or written messaging approval. No sold price, sell-through, or net resale-profit score is inferred from this response.

## Discord review walkthrough

These commands are available after a verified, backed-up release, without starting any promotional broadcasts:

1. `/ebay status`: show read-only API/approval/consent status, without secrets.
2. `/ebay consent`: user sees **Opt in to eBay DMs** and **Unsubscribe / Opt out** buttons; buttons are bound to the requesting user. Opt-in persists to the local SQLite consent table, opt-out deletes the row immediately. Walmart preferences are unaffected.
3. `/ebay server enabled:true` in the intended server channel: a **Manage Server** administrator records prospective channel consent. The paired `enabled:false` clears both enablement and channel identity. Neither action starts a sender or constitutes consent from individual server members.
4. `/ebay research query:graphics card`: real, user-initiated private results with asking price, verified free/shipping quote if present, condition, and public item link.
5. `/ebay review_sample query:graphics card`: private response using a **real eBay API search result**, displaying a clear *private sample, not a sent promotion* label, affiliate disclosure example and an opt-out button.

For eBay's Messaging approval, capture authentic opt-in, opt-out, private sample, and server-admin configuration screenshots **after deploying with valid keys**. Send the screenshots only with factual labels. **Do not claim the private sample was a previously sent promotional campaign.** If eBay requires an already-sent approved message, explain the chicken-and-egg restriction and ask its support team for an authorized preview/pilot review process. The eBay form's specific documentation-request deadline (if any) should be honored.

## Hard approval boundary

`EBAY_EPN_MESSAGING_APPROVED=false` is the default. Affiliate links are never generated from user input, and a supplied `EBAY_EPN_CAMPAIGN_ID` is **not** proof of written messaging approval. The future delivery gate requires: approved production settings, explicit recipient/subscriber consent, correct server channel for public messages, trustworthy eBay-provided affiliate URL, and nearby disclosure. No automated eBay affiliate sender exists in the current release. A future sender must reuse this gate and comply with EPN's written approval conditions and platform policies.

Local SQLite deployments require an **off-host verified database backup before redeploy**. Merging GitHub code does not automatically deploy it to Discloud. Verify exact deployed SHA and the real `/ebay` command surface after shipping.

## Remaining requirements

- A live production eBay Browse credential test.
- Real EPN written approval for Messaging (and any other promotion method actually used).
- An authorized sales-history feed or documented alternative to measure real sold prices, velocity, net fees and liquidity. Asking listings are **not** an equivalent substitute.
- A source-backed and statistically evaluated cross-retailer resale-profit engine; no guarantee of catching API-invisible/local-only/instantaneous offers.
- Discloud deployment and real screenshot/permission checks after a verified database backup.

These are explicit external blockers. They must not be marked complete by green unit tests or a merged PR.


## What eBay can actually approve (current EPN conditions, 2026-11-12)

The EPN **Messaging: Email, IM, Chat, Text** application expressly covers electronic messaging. It does not specifically promise approval of *Discord bots*. Do not call EPN registration or Developer key creation written Messaging approval. Request classification in writing for a **hosted Discord bot**: whether Messaging alone covers it or whether eBay additionally treats Discord server installation as a restricted Software Application promotional method.

This bot **does not** automatically place auction bids, buy goods, or interact with eBay checkout. This matters because EPN's Network Agreement prohibits sniping functionality, defined around automated bids/purchases, in certain software/promotional placements. **SniperPlug is price discovery and manual shopping, not auction sniping**. Describe this accurately; the bot name is not an assurance of eligibility. Never add automatic bidding or purchasing to this affiliate integration.

EPN's Network Agreement also prohibits some *placement topics*, including gambling/betting, illegal goods/services and sexually explicit material. Before applying, make sure the exact intended promotion channels and surrounding communities comply. The bot may operate in various servers, but server-admin channel consent is **not** evidence that every server or community is an EPN-acceptable promotion destination. Seek eBay's written guidance for the actual disclosed locations and any expansion to additional servers.

### Submission and evidence sequence

1. Back up and verify Discloud's canonical local SQLite database **off-host** and deploy main with verified SHA. Do not create a second Discloud worker. Configure protected **Developer Production** OAuth keys and `EBAY_RESEARCH_ENABLED=true`, keeping `EBAY_EPN_MESSAGING_APPROVED=false`.
2. Use `/ebay status` to establish setup; `/ebay research` to obtain a real eBay fixed-price listing. Test missing-key, HTTP 401/403/429 and no-result behavior if possible.
3. Show authentic screenshots of **`/ebay consent` before and after opting in**, then **after opting out**, including the button and resulting confirmation. Capture `/ebay server enabled:true` / `enabled:false` in the actual proposed posting channel with a server-admin account. Do not confuse admin-enabled public posts with personal DM consent.
4. Use `/ebay review_sample` for an **ephemeral, private demonstration**. It displays an untracked real eBay URL, listing title, actual asking price, opt-out button, sample affiliate disclosure and a prominent declaration that this is **not** a previously sent affiliate promotion. We cannot meet the literal form's “previously sent promotional message” requirement by fabricating one or sending live affiliate promotions without approval. Explain this to eBay, request a permitted pre-launch screenshot/pilot procedure, and document eBay's response.
5. Submit the **Messaging** special business-model application for chat posts/DMs with accurate proposed recipient counts and actual channel URL(s). When the automatic support email arrives, **reply directly to it within 72 business hours**, attaching authentic screenshots, a clear description of the not-yet-activated promotion, and asking how they classify Discord bot software, what channel placements they approve, and whether the current private mock is sufficient.
6. **Only after eBay grants written approval** should a future automatic affiliate sender be built/activated, retaining source/evidence, opt-out, per-server controls, prominent inline disclosure near every link, campaign attribution, rate limiting, receipts and abuse controls. `EBAY_EPN_MESSAGING_APPROVED=true` is a local switch, not itself proof from eBay; operators must keep the approval record.

### Browse API versus other data

The EPN Developer Questionnaire says standard Browse API access **does not require additional questionnaire approval**. eBay's general Buy API Requirements warn that some production Buy API capabilities/partners may still need onboarding, so verify the actual OAuth/production call and eBay's account status rather than promising production is accessible. Data Feeds are separate, selective and reviewed; Marketplace Insights sold listings cannot currently be requested through that questionnaire. Do not describe active fixed-price eBay listing asks as completed-sales demand, and don't apply for bulk feeds until there is a credible traffic/business case.

### Minimum app-review packet

- Private screenshot showing /ebay consent with *opt-in* button.
- Actual opt-in confirmation and stored state, and independent immediate opt-out/unsubscribe confirmation.
- Screenshot of a real eBay listing in /ebay review_sample, with non-affiliate URL, the disclosure example next to the listing, and the explanation “private pre-launch sample; no affiliate campaign sent”.
- Server-admin channel consent and revocation screenshots for the proposed (disclosed) Discord placement.
- A description of the posting cadence, recipients, source/price accuracy, rate limits, no automated bidding/purchasing, moderation and affiliate disclosure policy.
- EPN written answer on classification/acceptability and review-sample requirement.

**No live screenshot, approval, or delivered commercial message exists merely because this code compiled or merged.**
