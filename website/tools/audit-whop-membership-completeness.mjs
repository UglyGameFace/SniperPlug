import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { membershipCompanies } from '../server/whop-discovery.js';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const discovery = readFileSync(join(root, 'server/whop-discovery.js'), 'utf8');

const memberships = [
  {
    id: 'mem_active',
    status: 'active',
    company: { id: 'biz_black', title: 'Black Box', route: 'black-box' },
    product: { id: 'prod_black_main', title: 'Black Box Main' },
  },
  {
    id: 'mem_canceled',
    status: 'canceled',
    company: { id: 'biz_black', title: 'Black Box', route: 'black-box' },
    product: { id: 'prod_black_archive', title: 'Black Box Archive' },
  },
  {
    id: 'mem_expired',
    status: 'expired',
    company: { id: 'biz_hidden', title: 'Hidden Files', route: 'hidden-files' },
    product: { id: 'prod_hidden', title: 'Hidden Files' },
  },
  {
    id: 'mem_unresolved',
    status: 'unresolved',
    company: { id: 'biz_other', title: 'Another Group', route: 'another-group' },
    product: { id: 'prod_other', title: 'Another Product' },
  },
  {
    id: 'mem_drafted',
    status: 'drafted',
    company: { id: 'biz_other', title: 'Another Group', route: 'another-group' },
    product: { id: 'prod_other_extra', title: 'Extra Product' },
  },
  {
    id: 'mem_without_company',
    status: 'active',
    company: null,
    product: { id: 'prod_invalid', title: 'Invalid' },
  },
];

const companies = membershipCompanies(memberships);
assert.equal(companies.length, 3, 'Every distinct Whop company with an ID must survive membership discovery.');

const blackBox = companies.find((company) => company.id === 'biz_black');
assert.ok(blackBox, 'Black Box was dropped from membership discovery.');
assert.equal(blackBox.memberships, 2, 'Multiple memberships for one company must remain accounted for.');
assert.deepEqual([...blackBox.products.keys()].sort(), ['prod_black_archive', 'prod_black_main'], 'Products with different lifecycle statuses must not be dropped.');
assert.deepEqual([...blackBox.statuses].sort(), ['active', 'canceled'], 'Membership statuses must remain visible for diagnostics without controlling discovery.');

const hiddenFiles = companies.find((company) => company.id === 'biz_hidden');
assert.ok(hiddenFiles, 'An expired-status membership company was silently removed.');
assert.deepEqual([...hiddenFiles.products.keys()], ['prod_hidden'], 'The Hidden Files product was not preserved.');

const other = companies.find((company) => company.id === 'biz_other');
assert.ok(other, 'Unresolved or drafted memberships were silently removed.');
assert.deepEqual([...other.products.keys()].sort(), ['prod_other', 'prod_other_extra'], 'Every returned product must be checked independently for readable experiences.');

assert.ok(!discovery.includes('ACCESS_STATUSES'), 'Membership discovery must not use a hard-coded billing-status allowlist.');
assert.ok(!discovery.includes('!ACCESS_STATUSES.has(status)'), 'A membership status must never silently suppress a company or product.');
assert.ok(discovery.includes('product_id: product.id'), 'Every preserved product must still use product-scoped experience discovery.');
assert.ok(discovery.includes('current.memberships += 1'), 'Merged company cards must report how many membership records they represent.');
assert.ok(discovery.includes('values.length > MAX_COMPANIES'), 'The company safety limit must fail visibly instead of silently slicing away groups.');

console.log('\nWHOP MEMBERSHIP COMPLETENESS AUDIT PASSED\n');
console.log('✓ Active, canceled, expired, unresolved, and drafted membership records all survive discovery.');
console.log('✓ Multiple products under one Whop company are preserved and checked independently.');
console.log('✓ Missing company IDs are ignored safely, while company-limit overflow fails visibly.');
console.log('✓ Billing status is diagnostic metadata, not an authority for content access.');
