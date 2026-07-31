# Active Task

## Status
In progress — add a reliable first-party free movie-ticket drop monitor under one `/movies` command group.

## Scope
Add official Atom Promotions Hub monitoring without requiring an Atom API. Server owners choose the Discord destination through a channel selector. SniperPlug fetches the public first-party page, extracts reusable free-ticket codes and their terms, stores discoveries, suppresses duplicate delivery per guild, and posts only public reusable offers.

## Execution path inspected
- `sniperplug.bot.SniperPlugBot.setup_hook` is the single cog-registration path.
- Existing polling cogs use `commands.GroupCog`, `tasks.loop`, permission checks, and per-feature stores.
- `Database.require_conn()` provides the common SQLite/Turso connection contract.
- Atom's official promotions page contains structured headings and bullet terms for film promotions, public codes, ticket limits, dates, and partner-only code instructions.

## Planned command surface
- `/movies setup` — enable alerts and select a text channel.
- `/movies status` — show destination, source health, last scan, and totals.
- `/movies latest` — show currently detected official offers.
- `/movies scan` — run an immediate official-source refresh.
- `/movies test-alert` — verify channel permissions and delivery safely.
- `/movies disable` — stop automatic delivery.
- `/movies sources` — explain which official channels are automated versus informational.

## Safety and reliability requirements
- No hardcoded guild or channel IDs.
- Fetch only allowlisted HTTPS first-party Atom URLs.
- Conditional requests, bounded timeouts, a descriptive user agent, and one global source fetch at a time.
- Do not treat partner-issued, account-targeted, emailed, SMS, app-push, or unique codes as reusable public codes.
- Do not auto-post sweepstakes, discounts, concessions, BOGO offers, or merely promotional copy as free-ticket drops.
- Persist source state, discoveries, and per-guild delivery records across restarts.
- Keep code extraction deterministic and covered by offline fixtures.

## Validation required
- Parser tests for current Atom film-promotion structure and targeted partner exclusions.
- Store/deduplication tests for SQLite-compatible behavior.
- Cog registration and command-surface regressions.
- Compilation, import smoke, targeted tests, and complete pytest suite.
- Cleanup and conflict inspection before merge.

## Cleanup status
Pending.

## Blockers
None.

## Backlog
- Add authenticated/consented email, SMS, or mobile-push ingestion only when a safe source connection is available.
- Add official studio/distributor adapters individually after validating stable first-party pages or feeds.
- Improve scheduled Walmart zero-post diagnostics surfaced to server owners.
