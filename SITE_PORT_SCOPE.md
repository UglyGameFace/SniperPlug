# SniperPlug Website Port

## Target

Build the standalone `https://sniperplug.com` website inside this private repository under `website/`, using the proven responsive publishing architecture from the 420 Lobby site without copying its branding, content, community configuration, or Discord bot settings.

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

- Complete Node audit suite: passed locally.
- Responsive, category, content-integrity, stale-brand, Whop completeness, bulk-action, and draft-isolation audits: passed locally.
- JavaScript syntax validation: passed through the audit suite.
- Bot tree outside the isolated website and CI/scope files: unchanged.
- Permanent website CI: added; installs Node 22 dependencies and runs the full Astro production build.
- Existing bot smoke workflow: fixed to include the repository root on `PYTHONPATH`.
- Cloudflare Pages production deployment and live-domain cutover remain blocked until the PR build is green and the owner approves merge/configuration.
