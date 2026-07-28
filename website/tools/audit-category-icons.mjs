import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  CATEGORY_ICON_DEFINITIONS,
  CATEGORY_ICON_KEYS,
  categoryIconSvgMarkup,
  sanitizeCustomIconDefinition,
} from '../src/lib/category-icons.js';
import {
  DEFAULT_SITE_SETTINGS,
  sanitizeSiteSettings,
  serializeSiteSettings,
} from '../server/site-settings.js';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const read = (path) => readFileSync(join(root, path), 'utf8');

assert.ok(CATEGORY_ICON_KEYS.length >= 20, 'The category icon library is too small.');
assert.equal(new Set(CATEGORY_ICON_KEYS).size, CATEGORY_ICON_KEYS.length, 'Category icon keys must be unique.');
for (const key of CATEGORY_ICON_KEYS) {
  const definition = CATEGORY_ICON_DEFINITIONS[key];
  assert.ok(definition?.label, `Category icon ${key} is missing a label.`);
  assert.ok(definition?.body?.includes('<'), `Category icon ${key} is missing SVG body markup.`);
}
for (const expected of ['gamepad', 'dollar', 'percent', 'wallet', 'coupon', 'cart', 'store', 'gift', 'phone', 'bolt', 'package', 'coffee', 'burger', 'card', 'globe', 'truck']) {
  assert.ok(CATEGORY_ICON_KEYS.includes(expected), `Site-matched icon preset is missing: ${expected}`);
}

const customIcon = {
  viewBox: '0 0 32 32',
  paths: ['M4 16h24', 'M16 4v24'],
};
assert.deepEqual(sanitizeCustomIconDefinition(customIcon), customIcon, 'A valid custom icon was changed or rejected.');
assert.equal(
  sanitizeCustomIconDefinition({ viewBox: '0 0 24 24', paths: ['M0 0"/><script>alert(1)</script>'] }),
  null,
  'Unsafe custom SVG path data was not rejected.',
);
assert.equal(
  sanitizeCustomIconDefinition({ viewBox: '0 0 0 24', paths: ['M0 0h24'] }),
  null,
  'A zero-width custom icon viewBox was not rejected.',
);

const settingsWithCustomIcon = sanitizeSiteSettings({
  ...structuredClone(DEFAULT_SITE_SETTINGS),
  categories: {
    ...structuredClone(DEFAULT_SITE_SETTINGS.categories),
    'custom-icon-test': {
      label: 'Custom Icon Test',
      shortLabel: 'Custom',
      description: 'Regression coverage for custom SVG category icons.',
      visible: true,
      order: 4,
      icon: 'custom',
      customIcon,
      accent: 'violet',
    },
  },
});
assert.deepEqual(
  settingsWithCustomIcon.categories['custom-icon-test'].customIcon,
  customIcon,
  'The settings sanitizer dropped a valid custom icon.',
);
const reloaded = sanitizeSiteSettings(JSON.parse(serializeSiteSettings(settingsWithCustomIcon)));
assert.deepEqual(
  reloaded.categories['custom-icon-test'],
  settingsWithCustomIcon.categories['custom-icon-test'],
  'A custom icon did not survive settings serialization and reload.',
);

const customMarkup = categoryIconSvgMarkup('custom', customIcon, 'audit-icon');
assert.ok(customMarkup.includes('viewBox="0 0 32 32"'), 'Custom icon markup lost its viewBox.');
assert.ok(customMarkup.includes('preserveAspectRatio="xMidYMid meet"'), 'Custom icon markup does not preserve its proportions.');
assert.ok(customMarkup.includes('class="audit-icon"'), 'Custom icon markup lost its sizing class.');
assert.ok(!customMarkup.includes('<script'), 'Custom icon markup contains executable SVG content.');

const settingsPanel = read('src/components/SiteSettingsPanels.astro');
const shell = read('src/components/SniperPlugControlCenter.astro');
const controlClient = read('src/scripts/control-center.js');
const methodClient = read('src/scripts/deal-desk.js');
const iconComponent = read('src/components/Icon.astro');
const iconOptions = read('src/components/CategoryIconOptions.astro');
const categoryCss = read('src/styles/custom-categories.css');

for (const required of ['data-category-create-custom-icon', 'Custom SVG', 'CategoryIconOptions']) {
  assert.ok(settingsPanel.includes(required), `Categories panel is missing custom icon behavior: ${required}`);
}
for (const required of ['data-method-category-custom-icon', 'Custom SVG', 'CategoryIconOptions']) {
  assert.ok(shell.includes(required), `New Method category creator is missing custom icon behavior: ${required}`);
}
for (const [name, source] of [['Control Center', controlClient], ['New Method', methodClient]]) {
  for (const required of ['parseCustomIconSvg', 'categoryIconSvgMarkup', 'customIcon']) {
    assert.ok(source.includes(required), `${name} icon runtime is missing ${required}.`);
  }
}
assert.ok(iconOptions.includes('CATEGORY_ICON_OPTIONS'), 'Owner icon choices are not generated from the shared icon registry.');
assert.ok(iconOptions.includes('value="custom"'), 'Owner icon choices do not expose custom SVG upload.');
assert.ok(iconComponent.includes('preserveAspectRatio="xMidYMid meet"'), 'Public icon rendering does not keep custom icons flush.');
for (const required of ['width: 1.15rem', 'height: 1.15rem', '.cc-custom-icon-preview', '.desk-category-icon-svg']) {
  assert.ok(categoryCss.includes(required), `Category icon sizing CSS is missing: ${required}`);
}

for (const path of [
  'src/components/HackCard.astro',
  'src/components/Sidebar.astro',
  'src/components/MobileHeader.astro',
  'src/pages/index.astro',
  'src/pages/guides/[...id].astro',
]) {
  assert.ok(read(path).includes('customIcon={category.customIcon}'), `${path} does not render custom category icons.`);
}

console.log('\nCATEGORY ICON AUDIT PASSED\n');
console.log(`✓ ${CATEGORY_ICON_KEYS.length} shared outline presets are available in both owner category creators.`);
console.log('✓ Valid path-based SVG icons survive sanitization, persistence, and reload.');
console.log('✓ Unsafe SVG path content and invalid viewBoxes are rejected.');
console.log('✓ Preset and custom icons use one fixed, proportion-preserving site slot.');
console.log('✓ Homepage, cards, navigation, guide pages, and owner previews render the same icon metadata.');
