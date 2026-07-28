# SniperPlug Website

Standalone Astro + Cloudflare Pages website for `https://sniperplug.com`, stored inside the public SniperPlug monorepo.

## Public repository safety

All real secrets belong in Cloudflare Pages environment variables, never in Git. The repository includes a history-aware credential scanner, and `.env` files remain ignored. `.env.example` contains variable names and obvious placeholders only.

## Isolation

The website lives entirely inside `website/`. The Discord bot remains a Python application at the repository root and does not import website code or dependencies.

## Cloudflare Pages settings

- Production branch: `main`
- Root directory: `website`
- Build command: `npm run build`
- Build output directory: `dist`
- Node.js: 22
- Build watch include path: `website/*`

Pages Functions under `functions/api/` serve owner authentication, GitHub-backed publishing, live statuses, and the authorized Whop importer.

## Local commands

```bash
npm install
npm run check
npm run build
```

See `.env.example` for required public variables and private Cloudflare Pages secrets. Never commit actual credentials.
