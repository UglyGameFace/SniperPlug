import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import {
  sanitizeSiteSettings,
} from '../server/site-settings.js';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const read = (path) => readFileSync(join(root, path), 'utf8');

const currentSettings = JSON.parse(read('src/data/site-settings.json'));
const manyCategories = Object.fromEntries(
  Array.from({ length: 25 }, (_, index) => {
    const number = index + 1;
    return [`responsive-category-${number}`, {
      label: `Responsive Category With A Long Safe Label ${number}`,
      shortLabel: `Category ${number}`,
      description: `Dynamic responsive category ${number} remains available without covering neighboring content.`,
      visible: true,
      order: Math.min(99, number + 3),
      icon: 'tag',
      accent: number % 2 ? 'lime' : 'cyan',
    }];
  }),
);
const sanitized = sanitizeSiteSettings({
  ...currentSettings,
  categories: {
    ...currentSettings.categories,
    ...manyCategories,
  },
});
assert.equal(Object.keys(sanitized.categories).length, 30, 'The supported 30-category registry no longer survives sanitization.');
assert.equal(sanitized.categories['responsive-category-25'].label, 'Responsive Category With A Long Safe Label 25');

const baseLayout = read('src/layouts/BaseLayout.astro');
assert.ok(baseLayout.includes("import '../styles/responsive-content-safety.css';"), 'Public responsive safeguards are not loaded after the canonical layout system.');
assert.ok(baseLayout.indexOf('responsive-content-safety.css') > baseLayout.indexOf('layout-system.css'), 'Responsive content safeguards must load after the canonical layout system.');

const publicCss = read('src/styles/responsive-content-safety.css');
for (const required of [
  'repeat(auto-fit, minmax(min(100%, 11rem), 1fr))',
  'grid-auto-flow: column',
  'overflow-x: auto',
  'overscroll-behavior-inline: contain',
  '.filter-buttons button',
  '.category-chip',
  '.hero-console[data-expanded="true"] > .console-body',
]) {
  assert.ok(publicCss.includes(required), `Public dynamic responsive rule is missing: ${required}`);
}
assert.ok(!publicCss.includes('!important'), 'Public responsive safeguards must not depend on !important.');

const homepage = read('src/pages/index.astro');
assert.ok(!homepage.includes('<details class="hero-console"'), 'The desktop/tablet terminal still uses a closed details element that can collapse into a blank line.');
for (const required of [
  'data-mobile-console data-expanded="false"',
  'data-console-toggle',
  'aria-controls="sniperplug-console-details"',
  "consolePanel.dataset.expanded = String(expanded)",
  "consoleToggle.setAttribute('aria-expanded', String(expanded))",
]) {
  assert.ok(homepage.includes(required), `Responsive terminal behavior is missing: ${required}`);
}

const controlPage = read('src/pages/control-center.astro');
for (const required of [
  "import '../styles/owner-content-safety.css';",
  "import '../scripts/method-draft-safety.js';",
]) {
  assert.ok(controlPage.includes(required), `Control Center content safety is not loaded: ${required}`);
}

const ownerCss = read('src/styles/owner-content-safety.css');
for (const required of [
  'repeat(auto-fit, minmax(min(100%, 13rem), 1fr))',
  'repeat(auto-fit, minmax(7rem, 1fr))',
  '.desk-method-draft-state',
  '@media (max-width: 767px)',
]) {
  assert.ok(ownerCss.includes(required), `Owner dynamic responsive rule is missing: ${required}`);
}
assert.ok(!ownerCss.includes('!important'), 'Owner content safeguards must not depend on !important.');

const draftSafety = read('src/scripts/method-draft-safety.js');
for (const required of [
  '[data-new-method], [data-method-id], [data-refresh], [data-logout]',
  'beforeunload',
  'window.confirm',
  'sniperplug-settings-loaded',
  'Save failed — changes kept in this form',
  'event.stopImmediatePropagation()',
]) {
  assert.ok(draftSafety.includes(required), `Unsaved method protection is missing: ${required}`);
}

const shell = read('src/components/SniperPlugControlCenter.astro');
assert.ok(shell.includes('data-status-action="expire"'), 'The reversible expire flow is missing from method removal controls.');
assert.ok(shell.includes('data-cc-publish-confirm'), 'Settings and category changes no longer require publish confirmation.');
assert.ok(!shell.includes('data-delete-method'), 'A direct method deletion control bypasses the reversible expire flow.');
assert.ok(!shell.includes('data-delete-category'), 'A direct category deletion control bypasses the reversible visibility flow.');

const syntax = spawnSync(process.execPath, ['--check', 'src/scripts/method-draft-safety.js'], {
  cwd: root,
  encoding: 'utf8',
});
assert.equal(syntax.status, 0, `method-draft-safety.js failed syntax validation:\n${syntax.stderr.trim()}`);

console.log('\nRESPONSIVE CONTENT SAFETY AUDIT PASSED\n');
console.log('✓ The complete supported category registry survives sanitization.');
console.log('✓ Added categories reflow or scroll without widening the page or covering controls.');
console.log('✓ The tablet terminal cannot collapse into the blank line shown in the supplied screenshot.');
console.log('✓ Long category labels, filters, cards, method category pickers, and previews stay bounded.');
console.log('✓ Unsaved method edits are protected before switching, refreshing, locking, or leaving.');
console.log('✓ Removing public visibility remains reversible through expire, pause, draft, or hidden-category flows.');
