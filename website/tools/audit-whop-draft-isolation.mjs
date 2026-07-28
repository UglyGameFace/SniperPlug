import assert from 'node:assert/strict';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const pagesRoot = join(root, 'src/pages');

function filesUnder(directory) {
  return readdirSync(directory).flatMap((name) => {
    const path = join(directory, name);
    return statSync(path).isDirectory() ? filesUnder(path) : [path];
  });
}

const astroPages = filesUnder(pagesRoot).filter((path) => path.endsWith('.astro'));
const collectionPages = astroPages.map((path) => ({
  path,
  source: readFileSync(path, 'utf8'),
})).filter(({ source }) => /getCollection\(\s*['"]hacks['"]/.test(source));

assert.ok(collectionPages.length >= 2, 'Expected the homepage and public guide route to load the hacks collection.');
for (const page of collectionPages) {
  assert.match(
    page.source,
    /getCollection\(\s*['"]hacks['"]\s*,\s*\(\s*\{\s*data\s*\}\s*\)\s*=>[\s\S]{0,180}!\s*data\.draft/,
    `${relative(root, page.path)} loads public guides without explicitly excluding hidden drafts.`,
  );
}

const homepage = readFileSync(join(pagesRoot, 'index.astro'), 'utf8');
assert.ok(homepage.includes('data.managed && !data.draft'), 'The homepage library no longer excludes hidden drafts.');

const importer = readFileSync(join(root, 'server/whop-import.js'), 'utf8');
assert.ok(importer.includes('draft: true'), 'Whop imports are no longer forced into hidden drafts.');
assert.ok(importer.includes('featured: false'), 'Whop imports could be featured before owner review.');

console.log('\nWHOP DRAFT ISOLATION AUDIT PASSED\n');
console.log(`✓ ${collectionPages.length} public guide collection routes explicitly exclude hidden drafts.`);
console.log('✓ Whop imports are always hidden and non-featured until the owner publishes them manually.');
