# SniperPlug Website Port Scope

## Target
Port the proven 420 Lobby website architecture into this private SniperPlug repository under an isolated `website/` directory.

## Preserve
- SniperPlug branding, retail-deal positioning, affiliate/legal intent, and `sniperplug.com` domain.
- Existing Discord bot code, Python dependencies, Discloud configuration, commands, database behavior, and deployment files.

## Exclude
- The 420 Lobby branding, content, categories, Discord invite copy, community settings, bot setup options, Whop default groups, source IDs, guides, credentials, and domain-specific configuration.
- Any coupling between the website build and the Discord bot runtime.

## Deployment model
The website must build independently from `website/`. A static/full-stack website host should use that directory as its project root. Bot-only changes must not trigger or modify website behavior unless they intentionally touch `website/`.

## Quality gates
- Mobile, tablet, and desktop parity.
- Exact Unicode, emoji, Markdown, paragraph, table, list, link, blockquote, and code-fence preservation.
- Dynamic categories from one canonical registry.
- Owner-only Control Center and draft-safe content workflow.
- No stale Lobby branding or Discord-server-specific settings.
- No changes to the live bot runtime outside the isolated site directory and deployment-ignore configuration required for the website host.
