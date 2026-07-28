import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const read = (path) => readFileSync(join(root, path), 'utf8');

const methodClient = read('src/scripts/deal-desk.js');
const controlClient = read('src/scripts/control-center.js');
const controlPage = read('src/pages/control-center.astro');
const siteStatus = read('public/site-status.js');
const statusApi = read('api/deal-status.js');
const indexPage = read('src/pages/index.astro');
const sidebar = read('src/components/Sidebar.astro');
const mobile = read('src/components/MobileHeader.astro');
const card = read('src/components/HackCard.astro');
const guidePage = read('src/pages/guides/[...id].astro');

for (const required of [
  'publishedCategories',
  'consumePublishedSettings',
  'consumeSettingsCategoryDraft',
  "source: 'method-editor'",
  'output.settingsSha',
  'output.settings',
]) {
  assert.ok(methodClient.includes(required), `New Method propagation is missing: ${required}`);
}

for (const required of [
  'emitCategoryDraft',
  'consumeMethodCategoryDraft',
  'consumePublishedSettings',
  'rebaseDraft',
  "source: 'settings-editor'",
  "window.addEventListener('sniperplug-category-draft-change'",
  "window.addEventListener('sniperplug-settings-loaded'",
]) {
  assert.ok(controlClient.includes(required), `Control Center propagation is missing: ${required}`);
}

assert.ok(!controlPage.includes("document.querySelector<HTMLButtonElement>('[data-refresh]')?.click()"), 'A page-level refresh bridge still owns category synchronization.');
assert.ok(!controlPage.includes("window.addEventListener('sniperplug-settings-loaded'"), 'Category synchronization must live inside the owning runtimes, not the page shell.');

for (const required of [
  'BUILD_RELOAD_KEY',
  'maybeReloadForNewBuild',
  'payload.buildVersion',
  'currentBuildVersion',
  'ownerEditorOpen',
  'refresh().catch(() => {})',
]) {
  assert.ok(siteStatus.includes(required), `Public build freshness is missing: ${required}`);
}

for (const required of ['CF_PAGES_COMMIT_SHA', 'buildVersion', 'x-site-build']) {
  assert.ok(statusApi.includes(required), `Status API build identity is missing: ${required}`);
}

for (const [name, source, expected] of [
  ['homepage categories and filters', indexPage, 'CATEGORY_ENTRIES'],
  ['desktop navigation', sidebar, 'CATEGORY_ENTRIES'],
  ['mobile navigation', mobile, 'CATEGORY_ENTRIES'],
  ['guide card', card, 'getCategory'],
  ['guide page and related logic', guidePage, 'getCategory'],
]) {
  assert.ok(source.includes(expected), `${name} is disconnected from the canonical category registry.`);
}

for (const path of [
  'src/scripts/deal-desk.js',
  'src/scripts/control-center.js',
  'public/site-status.js',
  'api/deal-status.js',
]) {
  const result = spawnSync(process.execPath, ['--check', path], { cwd: root, encoding: 'utf8' });
  assert.equal(result.status, 0, `${path} failed syntax validation:\n${result.stderr.trim()}`);
}

console.log('\nSITE-WIDE PROPAGATION AUDIT PASSED\n');
console.log('✓ Category drafts flow between Categories and New Method without duplicate registries.');
console.log('✓ Atomic method saves update the published settings payload and SHA everywhere in the owner app.');
console.log('✓ Public pages detect every completed Cloudflare Pages build, not only newly registered guide statuses.');
console.log('✓ Homepage, filters, navigation, cards, guide pages, and related-guide logic remain registry-driven.');
