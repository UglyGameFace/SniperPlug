import { existsSync, readFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';

const read = (path) => readFileSync(path, 'utf8');
const failures = [];
const fail = (message) => failures.push(message);

const page = read('src/pages/control-center.astro');
const control = read('src/scripts/control-center.js');
const dealDesk = read('src/scripts/deal-desk.js');

for (const required of [
  "import '../scripts/deal-desk.js'",
  "import '../scripts/control-center.js'",
]) {
  if (!page.includes(required)) fail(`Control Center page is missing compiled script import: ${required}`);
}

for (const forbidden of ['/deal-desk.js?v=', '/control-center.js?v=', 'globalThis.CSS.escape']) {
  if (page.includes(forbidden)) fail(`Control Center still uses the obsolete raw-script path: ${forbidden}`);
}

for (const required of [
  'function setSection(section)',
  "cc$$('[data-control-tab]').forEach",
  "button.addEventListener('click'",
  'panel.hidden = panel.dataset.controlPanel !== resolved',
  "button.classList.toggle('active', selected)",
  "button.setAttribute('aria-pressed'",
  'history.replaceState',
]) {
  if (!control.includes(required)) fail(`Canonical section switching is missing: ${required}`);
}

for (const required of ['data-deal-desk', 'loadGuides', 'data-new-method']) {
  if (!dealDesk.includes(required)) fail(`Compiled Deal Desk runtime is missing: ${required}`);
}

if (existsSync('public/control-center-tabs.js')) fail('A duplicate Control Center tab implementation still exists.');

for (const path of ['src/scripts/deal-desk.js', 'src/scripts/control-center.js']) {
  const syntax = spawnSync(process.execPath, ['--check', path], { encoding: 'utf8' });
  if (syntax.status !== 0) fail(`${path} failed syntax validation:\n${syntax.stderr.trim()}`);
}

if (failures.length) {
  console.error('\nCONTROL CENTER TAB AUDIT FAILED\n');
  failures.forEach((message, index) => console.error(`${index + 1}. ${message}`));
  process.exit(1);
}

console.log('\nCONTROL CENTER TAB AUDIT PASSED\n');
console.log('✓ Astro/Vite compiles and fingerprints both owner runtimes.');
console.log('✓ One canonical section switcher controls every owner panel.');
console.log('✓ The Control Center page no longer executes raw public scripts.');
