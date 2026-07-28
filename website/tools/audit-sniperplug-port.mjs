import assert from 'node:assert/strict';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
const root=join(dirname(fileURLToPath(import.meta.url)),'..');
const read=(path)=>readFileSync(join(root,path),'utf8');
function walk(dir){const out=[];for(const name of readdirSync(join(root,dir))){const abs=join(root,dir,name);if(statSync(abs).isDirectory())out.push(...walk(relative(root,abs)));else out.push(relative(root,abs));}return out;}
const textFiles=walk('.').filter((p)=>p!=='tools/audit-sniperplug-port.mjs'&&!p.startsWith('node_modules/')&&!p.startsWith('dist/')&&/\.(astro|css|js|mjs|ts|json|md|svg|txt|toml|example)$/.test(p));
const combined=textFiles.map((p)=>`${p}\n${read(p)}`).join('\n');
for(const forbidden of ['The 420 Lobby','the-420-lobby-hacks','sniperplug.vercel.app','VERCEL_GIT','discord.gg/your-permanent-invite','Discord promotion','Discord button','Discord promo','Money &amp; Food Hacks','>420<']) assert.ok(!combined.includes(forbidden),`Stale source branding/host remains: ${forbidden}`);
for(const required of ['SniperPlug','https://sniperplug.com','website/src/content/hacks','website/src/data/site-settings.json','Cloudflare Pages']) assert.ok(combined.includes(required),`SniperPlug port marker is missing: ${required}`);
const settings=JSON.parse(read('src/data/site-settings.json'));
assert.equal(settings.branding.name,'SniperPlug');
assert.equal(settings.branding.brandMark,'SP');
assert.equal(settings.theme.accentPreset,'red');
assert.ok(settings.alerts?.url?.startsWith('https://sniperplug.com/'));
assert.deepEqual(Object.keys(settings.categories),['deal-alerts','clearance-guides','store-guides','cashback-stacks','resale-opportunities']);
const guides=walk('src/content/hacks').filter((p)=>p.endsWith('.md')&&!p.split('/').at(-1).startsWith('__'));
assert.ok(guides.length>=5);
for(const path of guides){const text=read(path);assert.ok(text.includes('managed: true'));assert.ok(!/420 Lobby|The 420|discord\.gg/i.test(text),`${path} contains copied community content.`);}
for(const path of ['src/pages/deals.astro','src/pages/alerts.astro','src/pages/partners.astro','src/pages/privacy.astro','src/pages/terms.astro','src/pages/affiliate-disclosure.astro','src/pages/contact.astro']) assert.ok(read(path).includes('BaseLayout'));
const dispatcher=read('functions/api/[[path]].js');assert.ok(dispatcher.includes('onRequest'));
const packageJson=JSON.parse(read('package.json'));assert.equal(packageJson.name,'sniperplug-site');
console.log('\nSNIPERPLUG PORT AUDIT PASSED\n');
console.log(`✓ ${guides.length} original SniperPlug guides, Cloudflare Pages routing, legal pages, canonical branding, and monorepo paths passed.`);
