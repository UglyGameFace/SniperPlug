# Whop → SniperPlug authorized importer

The importer lives inside the password-protected SniperPlug Control Center under **Methods**. It uses Whop OAuth and official Whop APIs and never asks for a Whop password.

## Production callback

```text
https://sniperplug.com/api/whop-oauth-callback
```

## Required private Cloudflare Pages secrets

```text
WHOP_CLIENT_ID=app_...
WHOP_TOKEN_SECRET=<separate high-entropy secret>
WHOP_REDIRECT_URI=https://sniperplug.com/api/whop-oauth-callback
WHOP_OAUTH_SCOPES=openid profile email forum:read member:basic:read member:email:read
```

The connected account’s joined groups and product-scoped forum experiences are discovered automatically. Black Box and Hidden Files remain prioritized suggestions, while any additional group can be approved later. Sources and posts support individual and bulk approve/disapprove actions.

Every import is written as a hidden, non-featured draft under `website/src/content/hacks/`. The shared content-integrity gate preserves Unicode, emoji, paragraphs, Markdown hard breaks, headings, lists, tables, blockquotes, links, and fenced code. Republishing still requires ownership or explicit permission from the content owner.
