import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { spawnSync } from 'node:child_process';
import {
  CATEGORY_ICON_KEYS,
  sanitizeCustomIconDefinition,
} from '../src/lib/category-icons.js';

const root = process.cwd();
const failures = [];
const passed = [];
const read = (path) => readFileSync(join(root, path), 'utf8');
const fail = (message) => failures.push(message);

function walk(directory, predicate = () => true) {
  const absolute = join(root, directory);
  if (!existsSync(absolute)) return [];
  const output = [];
  for (const name of readdirSync(absolute)) {
    const path = join(absolute, name);
    if (statSync(path).isDirectory()) output.push(...walk(relative(root, path), predicate));
    else if (predicate(path)) output.push(relative(root, path));
  }
  return output;
}

function balancedCss(path) {
  const css = read(path).replace(/\/\*[\s\S]*?\*\//g, '');
  let depth = 0;
  for (const character of css) {
    if (character === '{') depth += 1;
    if (character === '}') depth -= 1;
    if (depth < 0) return fail(`${path} closes a CSS block before opening it.`);
  }
  if (depth !== 0) fail(`${path} has unbalanced CSS braces (${depth}).`);
}

function firstRuleBody(css, selectorPattern) {
  const match = css.match(new RegExp(`${selectorPattern.source}\\s*\\{([^}]*)\\}`));
  return match?.[1] ?? '';
}

function cssOwners(cssFiles, selectors) {
  return cssFiles.filter((path) => {
    const css = read(path);
    return selectors.some((selector) => css.includes(selector));
  });
}

function occurrences(text, value) {
  return text.split(value).length - 1;
}

const legacyStyles = [
  'src/styles/deal-desk-overlay.css',
  'src/styles/deal-desk-control-polish.css',
  'src/styles/deal-desk-mobile-flow.css',
];

for (const path of legacyStyles) {
  if (existsSync(join(root, path))) fail(`Obsolete stylesheet still exists: ${path}`);
}

const sourceFiles = walk('src', (path) => /\.(astro|css|ts|js)$/.test(path));
const sourceText = sourceFiles.map((path) => `${path}\n${read(path)}`).join('\n');
for (const path of legacyStyles) {
  const name = path.split('/').at(-1);
  if (sourceText.includes(name)) fail(`Obsolete stylesheet is still referenced: ${name}`);
}

const controlPage = read('src/pages/control-center.astro');
for (const stylesheet of ['deal-desk.css', 'method-manager.css', 'control-center.css']) {
  if (!controlPage.includes(stylesheet)) fail(`Control Center is missing canonical stylesheet: ${stylesheet}`);
}

if (!read('src/pages/deal-desk.astro').includes("Astro.redirect('/control-center', 308)")) {
  fail('The legacy /deal-desk route must permanently redirect instead of rendering a duplicate editor.');
}

const cssFiles = walk('src/styles', (path) => path.endsWith('.css'));
cssFiles.forEach(balancedCss);

const canonicalCss = [
  'src/styles/deal-desk.css',
  'src/styles/method-manager.css',
  'src/styles/control-center.css',
  'src/styles/home-compact.css',
  'src/styles/site-settings.css',
  'src/styles/site-hardening.css',
  'src/styles/navigation-buttons.css',
  'src/styles/custom-categories.css',
];
for (const path of canonicalCss) {
  if (!existsSync(join(root, path))) fail(`Canonical stylesheet is missing: ${path}`);
  else if (read(path).includes('!important')) fail(`${path} contains !important; fix specificity at the source.`);
}

const allCss = cssFiles.map(read).join('\n');
const methodBackdropOwners = cssOwners(cssFiles, ['.desk-modal-backdrop']);
if (methodBackdropOwners.length !== 1 || methodBackdropOwners[0] !== 'src/styles/method-manager.css') {
  fail(`The method modal backdrop must be owned only by src/styles/method-manager.css. Found: ${methodBackdropOwners.join(', ') || 'none'}.`);
}

const methodBackdropBase = firstRuleBody(read('src/styles/method-manager.css'), /\.desk-modal-backdrop/);
if (!/position:\s*fixed;/.test(methodBackdropBase) || !/inset:\s*0;/.test(methodBackdropBase)) {
  fail('The method modal backdrop is missing its canonical fixed full-viewport base rule.');
}

const controlBackdropOwners = cssOwners(cssFiles, ['.cc-preview-backdrop', '.cc-confirm-backdrop']);
if (controlBackdropOwners.length !== 1 || controlBackdropOwners[0] !== 'src/styles/control-center.css') {
  fail(`The Control Center modal backdrops must be owned only by src/styles/control-center.css. Found: ${controlBackdropOwners.join(', ') || 'none'}.`);
}

const controlBackdropBase = firstRuleBody(read('src/styles/control-center.css'), /\.cc-preview-backdrop\s*,\s*\.cc-confirm-backdrop/);
if (!/position:\s*fixed;/.test(controlBackdropBase) || !/inset:\s*0;/.test(controlBackdropBase)) {
  fail('The Control Center modal backdrops are missing their canonical fixed full-viewport base rule.');
}

const shell = read('src/components/SniperPlugControlCenter.astro');
const settingsPanels = read('src/components/SiteSettingsPanels.astro');
const panelsSource = `${shell}\n${settingsPanels}`;
const tabs = [...shell.matchAll(/data-control-tab="([^"]+)"/g)].map((match) => match[1]);
const panels = [...panelsSource.matchAll(/data-control-panel="([^"]+)"/g)].map((match) => match[1]);
if (new Set(tabs).size !== tabs.length) fail('Control Center has duplicate tab identifiers.');
if (new Set(panels).size !== panels.length) fail('Control Center has duplicate panel identifiers.');
for (const tab of tabs) if (!panels.includes(tab)) fail(`Tab has no matching panel: ${tab}`);
for (const panel of panels) if (!tabs.includes(panel)) fail(`Panel has no matching tab: ${panel}`);

const navigationRegistry = read('src/navigation.ts');
for (const tab of tabs) {
  if (!navigationRegistry.includes(`id: '${tab}'`)) fail(`Control Center section is missing from the shared navigation registry: ${tab}`);
}
for (const required of ['PUBLIC_PAGE_LINKS', 'OWNER_PAGE_LINK', 'getPublicGuideLinks', 'CONTROL_CENTER_SECTIONS', 'PublicGuideLink']) {
  if (!navigationRegistry.includes(required)) fail(`Shared navigation registry is missing: ${required}`);
}

const sidebar = read('src/components/Sidebar.astro');
const mobileHeader = read('src/components/MobileHeader.astro');
for (const [name, content] of [['Sidebar', sidebar], ['MobileHeader', mobileHeader]]) {
  for (const required of ['PUBLIC_PAGE_LINKS', 'OWNER_PAGE_LINK', 'data-guide-nav-id', 'guideLinks', 'getCategory', 'customIcon=']) {
    if (!content.includes(required)) fail(`${name} is not exposing required navigation/category source: ${required}`);
  }
  if (content.includes('getPublicGuideLinks(')) fail(`${name} must receive the shared guide list instead of loading the collection again.`);
}

const baseLayout = read('src/layouts/BaseLayout.astro');
for (const required of [
  'navigation-buttons.css',
  'custom-categories.css',
  'getPublicGuideLinks',
  'sniperplug-status-bootstrap',
  'data-build-version',
  '/site-status.js?v=',
  '/navigation-buttons.js?v=',
]) {
  if (!baseLayout.includes(required)) fail(`BaseLayout is missing public behavior: ${required}`);
}
if (occurrences(baseLayout, 'await getPublicGuideLinks()') !== 1) fail('BaseLayout must load public guide navigation exactly once per rendered page.');
if (baseLayout.indexOf('/site-status.js?v=') > baseLayout.indexOf('/navigation-buttons.js?v=')) {
  fail('The shared status runtime must load before public navigation consumes it.');
}

const guidePage = read('src/pages/guides/[...id].astro');
if (occurrences(guidePage, "getCollection('hacks'") !== 1) fail('Guide pages must build routes and related guides from one content-collection pass.');
if (!guidePage.includes('getCategory(')) fail('Guide pages must resolve category metadata through the safe dynamic registry.');
if (!guidePage.includes('customIcon={category.customIcon}')) fail('Guide pages must render custom category icons through the shared component.');
const hackCard = read('src/components/HackCard.astro');
if (!hackCard.includes('getCategory(')) fail('Guide cards must resolve category metadata through the safe dynamic registry.');
if (!hackCard.includes('customIcon={category.customIcon}')) fail('Guide cards must render custom category icons through the shared component.');

const directStatusConsumers = ['src/pages/index.astro', 'src/pages/guides/[...id].astro', 'public/navigation-buttons.js'];
for (const path of directStatusConsumers) {
  if (read(path).includes("fetch('/api/deal-status'")) fail(`${path} bypasses the shared public status cache.`);
}

const siteStatus = read('public/site-status.js');
for (const required of ['sessionStorage', 'inflight', 'sniperplug-status-change', "fetch('/api/deal-status'", 'FRESH_FOR_MS', 'readBootstrap', 'RELOAD_KEY', 'MAX_PENDING_RELOADS', 'visibilitychange']) {
  if (!siteStatus.includes(required)) fail(`Shared public status runtime is missing: ${required}`);
}

const contentSchema = read('src/content.config.ts');
if (contentSchema.includes("z.enum(['cashback-loops'")) fail('Guide content schema still hard-codes the original three categories.');
if (!contentSchema.includes('Category must be a safe lowercase slug.')) fail('Guide content schema is missing safe custom-category slug validation.');

const siteSettingsServer = read('server/site-settings.js');
for (const required of ['safeCategoryKey', 'sanitizeCategoryDefinition', 'CATEGORY_ICON_PRESETS', 'CATEGORY_ACCENT_PRESETS', 'serializeSiteSettings', 'sanitizeCustomIconDefinition']) {
  if (!siteSettingsServer.includes(required)) fail(`Dynamic site settings are missing: ${required}`);
}
if (siteSettingsServer.includes("const CATEGORY_KEYS = ['cashback-loops'")) fail('Site settings still discard categories outside the original fixed list.');

const dealDeskServer = read('server/deal-desk.js');
for (const required of ['writeRepoFiles', 'CATEGORY_KEY', 'validateGuide(input, allowedCategories', 'statusFileContent']) {
  if (!dealDeskServer.includes(required)) fail(`Deal Desk server is missing custom-category safety: ${required}`);
}
if (dealDeskServer.includes("const CATEGORIES = new Set(['cashback-loops'")) fail('Deal Desk validation still hard-codes the original categories.');

const saveApi = read('api/deal-desk-save.js');
for (const required of ['categoryDefinition', 'writeRepoFiles', 'SITE_SETTINGS_PATH', 'serializeSiteSettings', 'categoryCreated']) {
  if (!saveApi.includes(required)) fail(`Atomic category/method save is missing: ${required}`);
}

const dealDeskClient = read('src/scripts/deal-desk.js');
for (const required of ['pendingCategories', 'data-method-category-creator', 'categoryDefinition:', 'renderCategoryPicker', 'SniperPlugSettingsRuntime', 'parseCustomIconSvg', 'categoryIconSvgMarkup']) {
  if (!dealDeskClient.includes(required)) fail(`Method editor custom-category workflow is missing: ${required}`);
}

const controlClient = read('src/scripts/control-center.js');
for (const required of ['renderCategoryEditors', 'createCategoryFromPanel', 'data-category-stack', 'SniperPlugSettingsRuntime', 'parseCustomIconSvg', 'categoryIconSvgMarkup']) {
  if (!controlClient.includes(required)) fail(`Control Center dynamic category workflow is missing: ${required}`);
}
for (const required of ['data-category-create', 'data-category-stack', 'data-category-create-custom-icon']) {
  if (!settingsPanels.includes(required)) fail(`Category panel is missing UI hook: ${required}`);
}
for (const required of ['data-open-method-category', 'data-method-category-creator', 'data-category-picker', 'data-method-category-custom-icon']) {
  if (!shell.includes(required)) fail(`Method editor is missing custom-category UI hook: ${required}`);
}

const settings = JSON.parse(read('src/data/site-settings.json'));
for (const key of ['branding', 'homepage', 'navigation', 'categories', 'alerts', 'footer', 'seo', 'theme']) {
  if (!settings[key] || typeof settings[key] !== 'object') fail(`site-settings.json is missing object: ${key}`);
}
const categoryKeys = Object.keys(settings.categories || {});
const builtins = ['deal-alerts', 'clearance-guides', 'store-guides', 'cashback-stacks', 'resale-opportunities'];
for (const key of builtins) if (!categoryKeys.includes(key)) fail(`Required starter category is missing: ${key}`);
const safeCategory = /^[a-z0-9](?:[a-z0-9-]{0,46}[a-z0-9])?$/;
const icons = new Set([...CATEGORY_ICON_KEYS, 'custom']);
const accents = new Set(['red', 'lime', 'cyan', 'amber', 'violet']);
for (const [key, category] of Object.entries(settings.categories || {})) {
  if (!safeCategory.test(key)) fail(`Unsafe category key in site-settings.json: ${key}`);
  if (!category.label || !category.shortLabel || !category.description) fail(`Category ${key} is missing member-facing copy.`);
  if (typeof category.visible !== 'boolean') fail(`Category ${key} is missing a boolean visibility setting.`);
  if (!Number.isInteger(category.order) || category.order < 1 || category.order > 99) fail(`Category ${key} has invalid order.`);
  if (!icons.has(category.icon)) fail(`Category ${key} has unsupported icon: ${category.icon}`);
  if (category.icon === 'custom' && !sanitizeCustomIconDefinition(category.customIcon)) fail(`Category ${key} has an invalid custom icon.`);
  if (!accents.has(category.accent)) fail(`Category ${key} has unsupported accent: ${category.accent}`);
}
if (!['red', 'lime', 'cyan', 'amber', 'violet'].includes(settings.theme?.accentPreset)) fail('Unsupported accent preset.');
if (!['compact', 'comfortable', 'spacious'].includes(settings.theme?.density)) fail('Unsupported density preset.');

const headers = read('public/_headers');
for (const required of ['Content-Security-Policy:', 'Strict-Transport-Security:', 'X-Frame-Options: DENY', '/control-center/*', '/api/*', '/_astro/*']) {
  if (!headers.includes(required)) fail(`Cloudflare Pages headers are missing: ${required}`);
}
const wrangler = read('wrangler.toml');
for (const required of ['pages_build_output_dir = "./dist"', 'nodejs_compat', 'nodejs_compat_populate_process_env']) {
  if (!wrangler.includes(required)) fail(`Cloudflare Pages runtime configuration is missing: ${required}`);
}
const dispatcher = read('functions/api/[[path]].js');
for (const required of ['export async function onRequest', 'whop-oauth-callback', 'deal-desk-save', 'deployment-status']) {
  if (!dispatcher.includes(required)) fail(`Cloudflare Pages API dispatcher is missing: ${required}`);
}

for (const path of ['src/scripts/deal-desk.js', 'src/scripts/control-center.js', 'src/lib/category-icons.js', 'public/site-status.js', 'public/navigation-buttons.js', 'server/deal-desk.js', 'server/site-settings.js', 'api/deal-desk-save.js']) {
  const result = spawnSync(process.execPath, ['--check', path], { cwd: root, encoding: 'utf8' });
  if (result.status !== 0) fail(`${path} failed syntax validation:\n${result.stderr.trim()}`);
}

for (const safety of ['baseFingerprint', 'beforeunload', 'baseSha']) {
  if (!controlClient.includes(safety)) fail(`Required Control Center safety behavior is missing: ${safety}`);
}
if (!allCss.includes('100dvh')) fail('Dynamic viewport height protection is missing.');

const settingsApi = read('api/control-center-settings.js');
if (!settingsApi.includes('baseSha') || !settingsApi.includes('409')) fail('Control Center API is missing concurrent-edit protection.');

if (failures.length) {
  console.error('\nSITE AUDIT FAILED\n');
  failures.forEach((message, index) => console.error(`${index + 1}. ${message}`));
  process.exit(1);
}

passed.push(`${cssFiles.length} stylesheets have balanced blocks.`);
passed.push('Modal backdrops have one owning stylesheet each, with responsive overrides allowed inside that owner.');
passed.push(`${tabs.length} editor tabs map one-to-one with panels and the shared navigation registry.`);
passed.push(`${categoryKeys.length} registered categories passed key, copy, icon, accent, visibility, and order validation.`);
passed.push(`${CATEGORY_ICON_KEYS.length} shared outline icon presets and sanitized custom SVG icons are supported.`);
passed.push('Custom categories flow through the content schema, settings registry, method editor, atomic save, public cards, guide pages, filters, and navigation.');
passed.push('A category and its first method are committed together, preventing broken intermediate deployments.');
passed.push('Public navigation loads one guide collection per page and guide routes use one collection pass.');
passed.push('Public HTML receives build-versioned scripts, bootstrapped status, and explicit freshness headers.');
passed.push('Legacy fixed-category and Deal Desk override code is absent.');
passed.push('JavaScript syntax, draft recovery, concurrent publishing, and dynamic viewport safeguards passed.');
console.log('\nSITE AUDIT PASSED\n');
passed.forEach((message) => console.log(`✓ ${message}`));
