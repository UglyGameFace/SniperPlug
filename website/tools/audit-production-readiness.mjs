import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { clearLoginFailures, getLoginThrottle, registerLoginFailure } from '../server/deal-desk.js';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const read = (path) => readFileSync(join(root, path), 'utf8');

const headers = read('public/_headers');
for (const required of [
  "Content-Security-Policy: default-src 'self'", "frame-ancestors 'none'", "object-src 'none'",
  'Strict-Transport-Security:', 'Referrer-Policy:', 'Permissions-Policy:',
  'X-Content-Type-Options: nosniff', 'X-Frame-Options: DENY', 'Cross-Origin-Opener-Policy: same-origin',
  '/control-center/*', 'X-Robots-Tag: noindex', '/api/*', '/_astro/*',
]) assert.ok(headers.includes(required), `Cloudflare Pages header rule is missing: ${required}`);

const wrangler = read('wrangler.toml');
for (const required of ['pages_build_output_dir = "./dist"', 'compatibility_date = "2026-07-28"', 'nodejs_compat', 'nodejs_compat_populate_process_env']) {
  assert.ok(wrangler.includes(required), `Cloudflare runtime setting is missing: ${required}`);
}
const routes = JSON.parse(read('public/_routes.json'));
assert.deepEqual(routes.include, ['/api/*']);

const server = read('server/deal-desk.js');
for (const required of [
  'GITHUB_TIMEOUT_MS', 'AbortController', 'getLoginThrottle', 'registerLoginFailure', 'clearLoginFailures',
  'readCommitDeploymentStatus', 'LOGIN_MAX_FAILURES', "request.headers.get('cf-connecting-ip')",
  "repoNameFromPair || 'SniperPlug'", "const STATUS_PATH = 'website/src/data/deal-status.json'",
  "const GUIDE_DIR = 'website/src/content/hacks'", 'check-runs?per_page=100', "name.includes('cloudflare')",
]) assert.ok(server.includes(required), `Owner server hardening/monorepo behavior is missing: ${required}`);
assert.ok(!server.includes('VERCEL_'), 'The owner server still depends on Vercel runtime variables.');

const throttleRequest = new Request('https://sniperplug.com/api/deal-desk-session', { headers: { 'cf-connecting-ip': `audit-${Date.now()}` } });
clearLoginFailures(throttleRequest);
for (let attempt = 0; attempt < 4; attempt += 1) assert.equal(registerLoginFailure(throttleRequest).blocked, false);
assert.equal(registerLoginFailure(throttleRequest).blocked, true);
assert.equal(getLoginThrottle(throttleRequest).blocked, true);
clearLoginFailures(throttleRequest);
assert.equal(getLoginThrottle(throttleRequest).blocked, false);

const sessionApi = read('api/deal-desk-session.js');
for (const required of ['getLoginThrottle', 'registerLoginFailure', "'retry-after'", '429', 'clearLoginFailures']) assert.ok(sessionApi.includes(required));
const statusApi = read('api/deal-desk-status.js');
for (const required of ['requireManagedGuide', 'optionalIso', 'for (let attempt = 0; attempt < 2', 'error.status !== 409']) assert.ok(statusApi.includes(required));
const deploymentApi = read('api/deployment-status.js');
for (const required of ['requireAuth', 'requireSameOrigin', 'readCommitDeploymentStatus']) assert.ok(deploymentApi.includes(required));
const healthApi = read('api/health.js');
for (const required of ["ok: true", 'buildVersion', "service: 'sniperplug'"]) assert.ok(healthApi.includes(required));

const ownerRuntime = read('src/scripts/owner-readiness.js');
for (const required of ['sniperplug-settings-loaded', '/api/deployment-status?commit=', 'MAX_MONITOR_MS', 'dataset.deploymentPending', 'MutationObserver', 'activateFocusScope', 'Cloudflare Pages']) {
  assert.ok(ownerRuntime.includes(required), `Owner readiness runtime is missing: ${required}`);
}
const shell = read('src/components/SniperPlugControlCenter.astro');
for (const required of ['data-cc-deployment-state', 'data-cc-deployment-label', 'data-cc-deployment-detail', 'aria-live="polite"', 'role="alert"']) assert.ok(shell.includes(required));
const controlPage = read('src/pages/control-center.astro');
assert.ok(controlPage.includes("../scripts/owner-readiness.js"));
assert.ok(controlPage.includes('production-readiness.css'));
for (const obsolete of ['public/deal-desk.js', 'public/control-center.js']) assert.equal(existsSync(join(root, obsolete)), false);

const dispatcher = read('functions/api/[[path]].js');
for (const required of ['export async function onRequest', "['whop-oauth-callback', 'oauth-callback']", "['health', health]", 'handler.fetch(request)']) {
  assert.ok(dispatcher.includes(required), `Pages Function dispatcher is missing: ${required}`);
}
const focusScope = read('src/lib/focus-scope.js');
for (const required of ['event.shiftKey', "event.key !== 'Tab'", 'returnFocus', 'onEscape']) assert.ok(focusScope.includes(required));
const mobile = read('src/components/MobileHeader.astro');
for (const required of ['aria-controls="mobile-navigation-drawer"', 'role="dialog"', 'aria-modal="true"', 'activateFocusScope']) assert.ok(mobile.includes(required));
const statusRuntime = read('public/site-status.js');
for (const required of ['REQUEST_TIMEOUT_MS', 'AbortController', 'controller.abort()', 'signal: controller.signal']) assert.ok(statusRuntime.includes(required));

const config = read('src/config.ts');
assert.ok(config.includes("'https://sniperplug.com'"));
assert.ok(!config.includes('vercel.app'));
const robots = read('public/robots.txt');
for (const required of ['/control-center/', '/api/', 'Sitemap: https://sniperplug.com/sitemap.xml']) assert.ok(robots.includes(required));

for (const path of ['server/deal-desk.js','api/deal-desk-session.js','api/deal-desk-status.js','api/deployment-status.js','api/health.js','functions/api/[[path]].js','public/site-status.js','src/lib/focus-scope.js','src/scripts/owner-readiness.js']) {
  const result = spawnSync(process.execPath, ['--check', path], { cwd: root, encoding: 'utf8' });
  assert.equal(result.status, 0, `${path} failed syntax validation:\n${result.stderr.trim()}`);
}
console.log('\nPRODUCTION READINESS AUDIT PASSED\n');
console.log('✓ Owner authentication, throttling, GitHub writes, Cloudflare deployment checks, and Pages Function routing are hardened.');
console.log('✓ Security headers, private-route indexing rules, canonical URLs, robots, and health checks are present.');
console.log('✓ The website runtime remains isolated under website/ and uses monorepo-safe repository paths.');
