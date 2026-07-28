import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  assertGuideBodyRoundTrip,
  contentFingerprint,
  GuideContentIntegrityError,
  prepareGuideBody,
} from '../server/guide-content-integrity.js';
import { composeGuideFile, parseGuideFile } from '../server/deal-desk.js';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const read = (path) => readFileSync(join(root, path), 'utf8');

const exactMarkdown = [
  '# Café, crème & 👩🏽‍💻',
  '',
  'Paragraph one keeps curly quotes “like this,” an em dash —, CJK 中文, العربية, and decomposed e\u0301 beside composed é.  ',
  'That previous line intentionally ends with two spaces for a Markdown hard break.',
  '',
  '',
  'Paragraph two intentionally follows two blank lines.',
  '',
  '- First item ✅',
  '- Second item with `inline <script>example()</script>` code',
  '',
  '```html',
  '<script>alert("literal teaching example")</script>',
  '<div onclick="literalExample()">Sample only</div>',
  '```',
  '',
  '> A blockquote with an apostrophe: don’t flatten me.',
  '',
  '| Name | Value |',
  '| --- | ---: |',
  '| Emoji | 🏳️‍🌈 |',
].join('\n');

const prepared = prepareGuideBody(exactMarkdown, { source: 'Regression guide' });
assert.equal(prepared.body, exactMarkdown, 'Valid Markdown must survive normalization byte-for-byte.');
assert.deepEqual(prepared.repairs, [], 'Clean Markdown should not report repairs.');
assert.equal(prepared.structure.headings, 1, 'Heading structure changed.');
assert.equal(prepared.structure.listItems, 2, 'List structure changed.');
assert.equal(prepared.structure.blockquotes, 1, 'Blockquote structure changed.');
assert.ok(prepared.structure.blankLineRuns.includes(2), 'Intentional multi-line paragraph spacing was collapsed.');
assert.equal(prepared.fingerprint, contentFingerprint(exactMarkdown), 'The stored fingerprint must describe the exact body.');

const transported = `\uFEFF\r\n${exactMarkdown.replace(/\n/g, '\r\n')}\r\n\r\n`;
const repaired = prepareGuideBody(transported, { source: 'Transported guide' });
assert.equal(repaired.body, exactMarkdown, 'Only transport-level line and boundary repairs are allowed.');
assert.deepEqual(
  repaired.repairs.sort(),
  ['normalized_line_endings', 'removed_utf8_bom', 'trimmed_boundary_blank_lines'].sort(),
  'Expected transport repairs were not reported accurately.',
);

const guide = {
  id: 'unicode-formatting-proof',
  title: '“Café: 👩🏽‍💻” — Formatting Proof',
  description: 'Preserves punctuation, emoji, accents, paragraphs, tables, lists, and code examples.',
  category: 'retail-deals',
  managed: true,
  featured: false,
  draft: true,
  badge: 'Draft ✅',
  keywords: ['café', 'emoji 👩🏽‍💻', 'curly “quotes”'],
  published: '2026-07-28',
  updated: '2026-07-28',
  readTime: '5 min',
  order: 10,
  body: exactMarkdown,
};
const serialized = composeGuideFile(guide);
const parsed = parseGuideFile(guide.id, serialized);
assert.equal(parsed.title, guide.title, 'Unicode title changed during frontmatter serialization.');
assert.equal(parsed.description, guide.description, 'Punctuation changed during frontmatter serialization.');
assert.deepEqual(parsed.keywords, guide.keywords, 'Unicode keywords changed during frontmatter serialization.');
assert.equal(parsed.badge, guide.badge, 'Emoji badge changed during frontmatter serialization.');
assertGuideBodyRoundTrip(exactMarkdown, parsed.body);

assert.doesNotThrow(
  () => prepareGuideBody('Use `<script>` as literal inline code.\n\n```js\nconst sample = "<script>";\n```'),
  'Literal unsafe-looking text inside code examples must remain publishable.',
);
assert.doesNotThrow(
  () => prepareGuideBody('<details>\n<summary>Safe details</summary>\n\nVisible text\n</details>'),
  'Safe formatting HTML should remain available.',
);

const expectIntegrityError = (value, code) => {
  assert.throws(
    () => prepareGuideBody(value),
    (error) => error instanceof GuideContentIntegrityError && error.code === code,
    `Expected guide integrity error: ${code}`,
  );
};

expectIntegrityError('Before\n\n```js\nconst broken = true;', 'unclosed_code_fence');
expectIntegrityError('Before\n\n<script>alert(1)</script>', 'unsafe_html');
expectIntegrityError('[Unsafe](javascript:alert(1))', 'unsafe_html');
expectIntegrityError('Broken\u0000content', 'disallowed_control_character');
expectIntegrityError('Decoded badly: \uFFFD', 'replacement_character');
expectIntegrityError(`Broken surrogate: ${String.fromCharCode(0xD800)}`, 'invalid_unicode');

assert.throws(
  () => assertGuideBodyRoundTrip('Paragraph A\n\nParagraph B', 'Paragraph A\nParagraph B'),
  (error) => error instanceof GuideContentIntegrityError && error.code === 'round_trip_mismatch',
  'Paragraph collapse must fail the round-trip gate.',
);

const saveApi = read('api/deal-desk-save.js');
assert.ok(saveApi.includes("from '../server/guide-content-integrity.js'"), 'The actual save API bypasses content integrity checks.');
assert.ok(saveApi.includes('prepareGuideBody(body.body'), 'Guide input is not normalized before validation.');
assert.ok(saveApi.includes('assertGuideBodyRoundTrip'), 'Serialized guides are not verified before repository writes.');
assert.ok(saveApi.includes('contentIntegrity:'), 'The save response does not expose integrity diagnostics.');

console.log('\nGUIDE CONTENT INTEGRITY AUDIT PASSED\n');
console.log('✓ Emoji, ZWJ sequences, accents, non-Latin scripts, punctuation, and frontmatter survive intact.');
console.log('✓ Paragraph spacing, Markdown hard breaks, headings, lists, tables, blockquotes, and code fences stay intact.');
console.log('✓ Transport defects are repaired explicitly without rewriting valid prose.');
console.log('✓ Broken Unicode, dangerous publishable HTML, dangerous links, and unclosed code fences are blocked.');
console.log('✓ The real save path verifies a content fingerprint and exact body round trip before writing.');
