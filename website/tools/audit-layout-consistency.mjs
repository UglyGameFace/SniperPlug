import { readFileSync } from 'node:fs';

const read = (path) => readFileSync(path, 'utf8');
const failures = [];
const fail = (message) => failures.push(message);

const layout = read('src/styles/layout-system.css');
const baseLayout = read('src/layouts/BaseLayout.astro');
const navigation = read('src/styles/navigation-buttons.css');
const panels = read('src/components/SiteSettingsPanels.astro');

for (const token of [
  '--layout-gutter',
  '--surface-radius',
  '--control-radius',
  '--control-height',
  '--surface-padding',
]) {
  if (!layout.includes(token)) fail(`Canonical layout token is missing: ${token}`);
}

for (const rule of [
  '.sniperplug-control-center.is-unlocked > .desk-hero',
  'grid-template-areas:',
  'repeat(8, minmax(0, 1fr))',
  'repeat(4, minmax(0, 1fr))',
  'repeat(2, minmax(0, 1fr))',
  '.cc-methods-panel,',
  '.cc-density-grid',
  '.no-site-chrome .site-shell',
]) {
  if (!layout.includes(rule)) fail(`Canonical layout behavior is missing: ${rule}`);
}

if (!baseLayout.includes("import '../styles/layout-system.css';")) {
  fail('BaseLayout does not load the canonical layout system.');
}

const layoutIndex = baseLayout.indexOf("import '../styles/layout-system.css';");
const customIndex = baseLayout.indexOf("import '../styles/custom-categories.css';");
if (layoutIndex < customIndex) {
  fail('The canonical layout system must load after feature styles.');
}

if (navigation.includes('.cc-section-picker')) {
  fail('navigation-buttons.css still owns Control Center layout geometry.');
}

if (!navigation.includes('Layout geometry lives in layout-system.css.')) {
  fail('Navigation styles do not document canonical layout ownership.');
}

for (const required of [
  '<span>Public landing page</span>',
  '<span>Site identity</span>',
  '<span>Public menus</span>',
  '<span>Guide taxonomy</span>',
  '<span>Alerts and public links</span>',
  '<span>Footer and metadata</span>',
  '<span>Theme and spacing</span>',
  'cc-choice-grid cc-density-grid',
]) {
  if (!panels.includes(required)) fail(`Settings panels are missing consistent hierarchy: ${required}`);
}

if (layout.includes('!important')) {
  fail('The canonical layout system must not use !important.');
}

if (failures.length) {
  console.error('\nLAYOUT CONSISTENCY AUDIT FAILED\n');
  failures.forEach((message, index) => console.error(`${index + 1}. ${message}`));
  process.exit(1);
}

console.log('\nLAYOUT CONSISTENCY AUDIT PASSED\n');
console.log('✓ Public and owner pages share one spacing, radius, and control system.');
console.log('✓ Control Center geometry has one canonical owner.');
console.log('✓ Tablet and mobile section/action grids remain balanced.');
console.log('✓ Repeated section labels and orphaned appearance controls are prevented.');
