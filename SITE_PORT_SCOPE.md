# SniperPlug Website Port

## Target

Build the standalone `https://sniperplug.com` website inside this public repository under `website/`, using the proven responsive publishing architecture from the 420 Lobby site without copying its branding, content, community configuration, or Discord bot settings.

## Public repository safety

- All credentials must remain in Discloud or Cloudflare environment variables; never commit real values.
- Root and website ignore rules block `.env` files, local databases, build output, and runtime data.
- Pull requests and `main` pushes run a history-aware secret scan with redacted findings.
- Example environment files may contain variable names and unmistakable placeholders only.
- If a real credential is ever committed, rotate it immediately before cleaning Git history.

## Isolation

- The existing Discord bot remains a Python application at the repository root.
- Website source, dependencies, Cloudflare Pages Functions, content, and build configuration remain under `website/`.
- Cloudflare Pages must use `website` as the project root.
- Bot-only changes do not belong in the website build, and website code is never imported by the bot.

## SniperPlug-specific product surface

- SniperPlug branding and red tactical visual system.
- Deal, clearance, store, cashback, and resale guide categories from one canonical registry.
- Deals, alerts, partners, privacy, terms, affiliate disclosure, and contact pages.
- Owner-only Control Center with responsive previews and draft protection.
- GitHub-backed publishing paths scoped to `website/`.
- Cloudflare Pages Functions for owner APIs and the authorized Whop importer.
- Automatic Whop membership/product/forum discovery with source and post bulk decisions.
- Hidden-draft imports, deduplication, attachment review, and exact formatting integrity gates.

## Explicit exclusions

- No 420 Lobby branding, guides, categories, domain, invite copy, guild IDs, channel IDs, bot commands, server setup controls, credentials, or source IDs.
- No coupling to the SniperPlug Discord bot runtime or Discloud deployment.
- No automatic publication of imported Whop content.

## Validation status

- Complete Node audit suite: passed in GitHub Actions.
- Astro type checking and Cloudflare production build: passed in GitHub Actions.
- Responsive, category, content-integrity, stale-brand, Whop completeness, bulk-action, and draft-isolation audits: passed.
- Python compile and bot import smoke checks: passed.
- Bot tree outside the isolated website and CI/scope files: unchanged.
- Permanent website CI: added; installs Node 22 dependencies and runs the full Astro production build.
- Public repository secret scan: added for the working tree and complete reachable Git history.
- Cloudflare Pages production deployment and live-domain cutover remain blocked until the PR is ready, merged, and the project is connected to this repository with `website` as its root.
