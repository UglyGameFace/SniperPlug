import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';

const root = process.cwd();
const read = (path) => readFileSync(join(root, path), 'utf8');
const failures = [];
const fail = (message) => failures.push(message);

const navigation = read('src/navigation.ts');
const sidebar = read('src/components/Sidebar.astro');
const mobile = read('src/components/MobileHeader.astro');
const shell = read('src/components/SniperPlugControlCenter.astro');
const runtime = read('public/navigation-buttons.js');
const styles = read('src/styles/navigation-buttons.css');

for (const required of [
  'POST_METHOD_LINK',
  "href: '/control-center?intent=new-method#methods'",
  "label: 'Post a Method'",
  "description: 'Password-protected owner form'",
]) {
  if (!navigation.includes(required)) fail(`Post-method navigation registry is missing: ${required}`);
}

for (const [name, content, className] of [
  ['desktop sidebar', sidebar, 'sidebar-post-link'],
  ['mobile navigation', mobile, 'mobile-post-link'],
]) {
  if (!content.includes('POST_METHOD_LINK')) fail(`${name} does not use POST_METHOD_LINK.`);
  if (!content.includes(className)) fail(`${name} is missing its visible Post a Method button class.`);
}

for (const required of [
  'data-login-panel',
  'type="password"',
  'data-desk-app hidden',
  'data-new-method',
]) {
  if (!shell.includes(required)) fail(`Control Center password gate is missing: ${required}`);
}

for (const required of [
  "params.get('intent')",
  "requestedOwnerIntent() !== 'new-method'",
  "root?.classList.contains('is-unlocked')",
  "!app.hidden",
  "[data-category-picker] [data-category]",
  "newMethodButton.click()",
  "url.searchParams.delete('intent')",
]) {
  if (!runtime.includes(required)) fail(`Password-gated post intent is missing: ${required}`);
}

if (runtime.includes("fetch('/api/deal-desk-save'")) {
  fail('Public navigation must not call the method-save API directly.');
}
if (runtime.includes('password:')) {
  fail('Public navigation must not collect or forward the owner password itself.');
}
if (styles.includes('!important')) {
  fail('Post-method navigation styling must not use !important.');
}

const syntax = spawnSync(process.execPath, ['--check', 'public/navigation-buttons.js'], {
  cwd: root,
  encoding: 'utf8',
});
if (syntax.status !== 0) fail(`public/navigation-buttons.js failed syntax validation:\n${syntax.stderr.trim()}`);

if (failures.length) {
  console.error('\nPOST METHOD AUDIT FAILED\n');
  failures.forEach((message, index) => console.error(`${index + 1}. ${message}`));
  process.exit(1);
}

console.log('\nPOST METHOD AUDIT PASSED\n');
console.log('✓ Desktop and mobile expose a visible Post a Method button.');
console.log('✓ The link enters the existing Control Center password gate.');
console.log('✓ The New Method editor opens only after authentication and category loading succeed.');
console.log('✓ No public code receives the password or calls the save API directly.');
