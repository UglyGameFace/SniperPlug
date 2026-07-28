import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { assertApprovedWhopSource, whopSourceDecision, whopSourceOptions } from '../server/whop-source-policy.js';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const read = (path) => readFileSync(join(root, path), 'utf8');
const page = read('src/pages/control-center.astro');
const component = read('src/components/WhopImporter.astro');
const client = read('src/scripts/whop-importer.js');
const styles = read('src/styles/whop-importer.css');
const whopApi = read('api/whop.js');
const dispatcher = read('functions/api/[[path]].js');
const oauth = read('server/whop-oauth.js');
const discovery = read('server/whop-discovery.js');
const importer = read('server/whop-import.js');
const sourcePolicy = read('server/whop-source-policy.js');
const envExample = read('.env.example');
const docs = read('docs/WHOP_IMPORTER.md');
const requiredScopes = 'openid profile email forum:read member:basic:read member:email:read';

assert.ok(page.includes("import WhopImporter from '../components/WhopImporter.astro'"));
assert.ok(page.includes('<WhopImporter />'));
for (const required of ['data-whop-importer hidden','data-whop-source-browser hidden','data-whop-group-list','data-whop-select-defaults','data-whop-approve-selected','data-whop-disapprove-selected','Advanced fallback: paste a Whop experience ID or link','data-whop-approve-ready','data-whop-disapprove-all','data-whop-rights','Everything imports as a hidden draft','SniperPlug']) assert.ok(component.includes(required), `Whop UI is missing: ${required}`);
for (const required of ['sniperplug-whop-decisions:','state.selectedSources','experienceIds: ids',"decideSources([...state.selectedSources], 'approved')","decideSources([...state.selectedSources], 'disapproved')","setItemDecision(item.sourceKey, 'approved')","setItemDecision(item.sourceKey, 'disapproved')",'sourceKeys',"'/api/whop-source-decision'","'/api/whop-import'"]) assert.ok(client.includes(required), `Whop client is missing: ${required}`);
assert.ok(!client.includes('items: selected'), 'Browser-submitted post bodies must never be trusted.');
for (const required of [`PUBLIC_SITE_URL=https://sniperplug.com`,`WHOP_REDIRECT_URI=https://sniperplug.com/api/whop-oauth-callback`,`WHOP_OAUTH_SCOPES=${requiredScopes}`]) assert.ok(envExample.includes(required));
assert.ok(docs.includes('https://sniperplug.com/api/whop-oauth-callback'));
assert.ok(docs.includes('ownership or explicit permission'));

for (const required of ["VALID_DECISIONS = new Set(['approved', 'disapproved'])","Object.freeze({ key: 'black-box', label: 'Black Box' })","Object.freeze({ key: 'hidden-files', label: 'Hidden Files' })",'saveWhopSourceDecisions','assertApprovedWhopSource',"WHOP_SOURCES_PATH = 'website/src/data/whop-sources.json'"]) assert.ok(sourcePolicy.includes(required));
for (const required of ["allPages(session, 'memberships'","allPages(session, 'forums'",'product_id: product.id',"allPages(session, 'experiences'",'isForumExperience','experienceTypes','discoverWhopSources',"source.decision !== 'approved'","'forum_posts'"]) assert.ok(discovery.includes(required), `Whop discovery is missing: ${required}`);
assert.ok(!discovery.includes('membership?.user?.email'));
assert.ok(oauth.includes(`DEFAULT_SCOPES = '${requiredScopes}'`));
assert.ok(!oauth.includes('courses:read'));
for (const required of ['assertApprovedWhopSource',"type !== 'forum-post'",'draft: true','assertGuideBodyRoundTrip',"IMPORTS_PATH = 'website/src/data/whop-imports.json'",'website/src/content/hacks/']) assert.ok(importer.includes(required), `Whop writer is missing: ${required}`);

const fixture={version:2,sources:{exp_black:{experienceId:'exp_black',label:'Black Box',decision:'approved',defaultKey:'black-box'},exp_hidden:{experienceId:'exp_hidden',label:'Hidden Files',decision:'disapproved',defaultKey:'hidden-files'},exp_other:{experienceId:'exp_other',label:'Another Group',decision:'approved',defaultKey:null}}};
const pending=whopSourceDecision({id:'exp_black_new',name:'Deals',company:{title:'Black Box'}},'exp_black_new',{version:2,sources:{}});
assert.equal(pending.decision,'pending');
assert.equal(pending.defaultKey,'black-box');
assert.equal(assertApprovedWhopSource(fixture,'exp_black').experienceId,'exp_black');
assert.throws(()=>assertApprovedWhopSource(fixture,'exp_hidden'),/Approve this Whop source/);
assert.ok(whopSourceOptions(fixture).some((source)=>source.experienceId==='exp_other'&&!source.builtIn));

for (const action of ['oauth-start','oauth-callback','session','sources','source-decision','discover','import']) assert.ok(whopApi.includes(`action === '${action}'`));
for (const route of ['whop-oauth-start','whop-oauth-callback','whop-session','whop-sources','whop-source-decision','whop-discover','whop-import']) assert.ok(dispatcher.includes(route), `Cloudflare dispatcher is missing ${route}`);
for (const required of ['.whop-group-card','.whop-source-toolbar','.whop-decision-badge[data-state="approved"]','.whop-decision-badge[data-state="disapproved"]']) assert.ok(styles.includes(required));
assert.ok(!styles.includes('!important'));
for (const path of ['src/scripts/whop-importer.js','api/whop.js','server/whop-discovery.js','server/whop-import.js','server/whop-oauth.js','server/whop-source-policy.js','functions/api/[[path]].js']) {
  const result=spawnSync(process.execPath,['--check',path],{cwd:root,encoding:'utf8'});
  assert.equal(result.status,0,`${path} failed syntax validation:\n${result.stderr}`);
}
console.log('\nWHOP IMPORTER AUDIT PASSED\n');
console.log('✓ Automatic product-scoped discovery, source/post bulk decisions, authoritative re-fetch, exact formatting, and hidden drafts are preserved on Cloudflare Pages.');
