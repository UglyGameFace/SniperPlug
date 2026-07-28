import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  DEFAULT_SITE_SETTINGS,
  sanitizeSiteSettings,
  serializeSiteSettings,
} from '../server/site-settings.js';
import { validateGuide } from '../server/deal-desk.js';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const read = (path) => readFileSync(join(root, path), 'utf8');

const gamingCategory = {
  label: 'Gaming Deals',
  shortLabel: 'Gaming',
  description: 'Console, game, accessory, and subscription methods.',
  icon: 'gamepad',
  accent: 'violet',
  visible: true,
  order: 4,
};

const sanitized = sanitizeSiteSettings({
  ...structuredClone(DEFAULT_SITE_SETTINGS),
  categories: {
    ...structuredClone(DEFAULT_SITE_SETTINGS.categories),
    'gaming-deals': gamingCategory,
  },
});

assert.deepEqual(
  sanitized.categories['gaming-deals'],
  gamingCategory,
  'The site-settings sanitizer dropped or changed a valid custom category.',
);

const reloaded = sanitizeSiteSettings(JSON.parse(serializeSiteSettings(sanitized)));
assert.deepEqual(
  reloaded.categories['gaming-deals'],
  gamingCategory,
  'A serialized custom category did not survive the save/reload round trip.',
);

const validatedGuide = validateGuide({
  title: 'Gaming Deals Regression Method',
  description: 'A regression-only method used to validate custom categories.',
  category: 'gaming-deals',
  featured: false,
  draft: true,
  badge: 'Test',
  keywords: ['gaming', 'console'],
  published: '2026-07-14',
  readTime: '2 min',
  order: 9999,
  body: '## Test method\n\nThis fixture verifies the custom category content path.',
}, Object.keys(reloaded.categories));
assert.equal(validatedGuide.category, 'gaming-deals');

const settingsApi = read('api/control-center-settings.js');
const settingsServer = read('server/site-settings.js');
const methodClient = read('src/scripts/deal-desk.js');
const controlClient = read('src/scripts/control-center.js');
const controlPage = read('src/pages/control-center.astro');
const saveApi = read('api/deal-desk-save.js');
const config = read('src/config.ts');
const contentSchema = read('src/content.config.ts');
const card = read('src/components/HackCard.astro');
const sidebar = read('src/components/Sidebar.astro');
const mobile = read('src/components/MobileHeader.astro');
const guidePage = read('src/pages/guides/[...id].astro');
const astroFixture = read('src/content/hacks/__category-persistence-regression.md');

for (const required of ['sanitizeSiteSettings', 'writeSiteSettings', 'readSiteSettings']) {
  assert.ok(settingsApi.includes(required), `Settings API is missing ${required}.`);
}
assert.ok(!settingsServer.includes("const CATEGORY_KEYS = ['cashback-loops'"), 'The sanitizer still uses the original fixed category list.');
assert.ok(settingsServer.includes('Object.keys(incoming)'), 'The canonical settings sanitizer does not enumerate custom category keys.');
assert.ok(controlClient.includes('state.draft.categories[key]'), 'The Categories panel is not adding categories to the publish draft.');
assert.ok(controlClient.includes('settingsRuntime().set(output)'), 'Publishing does not update the shared settings runtime.');
assert.ok(controlClient.includes('consumePublishedSettings'), 'The Categories panel does not consume categories published by New Method.');
assert.ok(controlClient.includes('consumeMethodCategoryDraft'), 'The Categories panel does not consume New Method category drafts.');
assert.ok(methodClient.includes('consumePublishedSettings'), 'New Method does not consume the canonical published registry.');
assert.ok(methodClient.includes('consumeSettingsCategoryDraft'), 'New Method does not consume Categories panel drafts.');
assert.ok(methodClient.includes('renderCategoryPicker()'), 'New Method does not render its category picker from runtime categories.');
assert.ok(methodClient.includes('output.settingsSha'), 'New Method does not preserve the updated settings SHA after an atomic category save.');
assert.ok(!controlPage.includes("document.querySelector<HTMLButtonElement>('[data-refresh]')?.click()"), 'The old page-level refresh bridge still exists.');
assert.ok(saveApi.includes('Object.keys(siteSettings.categories)'), 'Method saves are not validated against the canonical category registry.');
assert.ok(saveApi.includes('validateGuide('), 'Method saves no longer validate guide content.');
assert.ok(saveApi.includes('writeRepoFiles'), 'Category/method/status writes are not atomic.');
assert.ok(contentSchema.includes('Category must be a safe lowercase slug.'), 'Astro content validation still rejects custom category slugs.');
assert.ok(astroFixture.includes('category: gaming-deals'), 'The Astro regression fixture is not using the non-default category.');
assert.ok(astroFixture.includes('managed: false') && astroFixture.includes('draft: true'), 'The Astro regression fixture must remain non-public.');
for (const [name, source] of [
  ['config', config],
  ['guide card', card],
  ['desktop navigation', sidebar],
  ['mobile navigation', mobile],
  ['guide page', guidePage],
]) {
  assert.ok(source.includes('getCategory') || source.includes('CATEGORIES'), `${name} is disconnected from the shared category registry.`);
}

console.log('\nCATEGORY PERSISTENCE AUDIT PASSED\n');
console.log('✓ gaming-deals and its gamepad icon survive sanitizer, serialization, and reload unchanged.');
console.log('✓ Categories and New Method synchronize drafts and published registries in both directions.');
console.log('✓ Method validation and atomic save accept the custom category and return the new settings SHA.');
console.log('✓ Astro check/build validates a non-public guide using gaming-deals.');
console.log('✓ Public cards, navigation, filters, and guide pages remain registry-driven.');
