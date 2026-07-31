# Active Task

## Status
Complete — SniperPlug now has a unified `/movies` command group for official reusable free movie-ticket drops.

## Scope
Add official Atom Promotions Hub monitoring without requiring an Atom API. Server owners choose the Discord destination through a channel selector. SniperPlug fetches the public first-party page, extracts reusable free-ticket codes and their terms, stores discoveries, suppresses duplicate delivery per guild, and posts only public reusable offers.

## Execution path inspected
- `sniperplug.bot.SniperPlugBot.setup_hook` remains the single cog-registration path.
- `MovieTicketsCog` is registered once and owns the one automatic polling loop.
- `MovieTicketStore` uses the existing SQLite/Turso connection contract.
- `AtomPromotionsClient` is the one external fetch path and only accepts allowlisted HTTPS Atom hosts.

## Implemented command surface
- `/movies setup` — enable alerts and select a text channel.
- `/movies status` — show destination, source health, last scan, and totals.
- `/movies latest` — refresh and show currently detected official offers.
- `/movies scan` — run an immediate official-source refresh and deliver new drops.
- `/movies test-alert` — verify channel permissions and delivery safely.
- `/movies disable` — stop automatic delivery.
- `/movies sources` — explain which official channels are automated versus informational.

## Safety and reliability
- No hardcoded guild or channel IDs.
- One global Atom source check every 60 seconds regardless of server count.
- Conditional requests, bounded timeouts, response-size limits, a descriptive user agent, and a source lock.
- Public reusable free-ticket codes are separated from partner-issued, account-targeted, emailed, SMS, app-push, or unique codes.
- Sweepstakes, discounts, concessions, BOGO offers, and non-free promotional copy fail closed.
- Source state, discoveries, active status, and per-guild delivery reservations persist across restarts.
- Failed page validation preserves the last verified cache instead of deleting or guessing.

## Validation
- Repository compilation passed.
- Import smoke passed: 30 critical modules and 15 required symbols.
- Full pytest suite passed: 676 tests.
- Current-style Atom fixture extracted `NIMRODSATOM` and `ATOMICECREAM` while excluding the Samsung-issued private code.
- SQLite config, source-state, active-drop replacement, failed-delivery retry, sent-delivery dedupe, and deactivation regressions passed.
- PR #157 is mergeable with current `main`.

## Cleanup status
Complete. The final diff contains the task record, explicit HTTP dependency, smoke wiring, one registered cog, one source/store service, and focused tests. No temporary workflow, duplicate runtime, or unrelated feature change remains.

## Blockers
None.

## Backlog
- Add authenticated/consented email, SMS, or mobile-push ingestion only when a safe source connection is available.
- Add official studio/distributor adapters individually after validating stable first-party pages or feeds.
- Improve scheduled Walmart zero-post diagnostics surfaced to server owners.
