import {
  composeGuideFile,
  guidePath,
  HttpError,
  listGuideFiles,
  normalizeStatus,
  parseGuideFile,
  readRepoFile,
  readStatusDocument,
  slugify,
  statusFileContent,
  validateGuide,
  writeRepoFiles,
} from './deal-desk.js';
import {
  assertGuideBodyRoundTrip,
  contentFingerprint,
  prepareGuideBody,
} from './guide-content-integrity.js';
import { nextAutomaticMethodOrder } from './method-order.js';
import { readSiteSettings } from './site-settings.js';
import {
  assertApprovedWhopSource,
  readWhopSourcePolicy,
} from './whop-source-policy.js';

const IMPORTS_PATH = 'website/src/data/whop-imports.json';
const MAX_BATCH_ITEMS = 50;
const MAX_BATCH_CONTENT = 4_000_000;

function sourceKey(item) {
  const type = String(item?.sourceType || '').trim();
  const id = String(item?.sourceId || '').trim();
  if (type !== 'forum-post' || !id) {
    throw new HttpError(422, 'Every Whop import item must be a valid forum post.');
  }
  return `${type}:${id}`;
}

function safeDate(value) {
  const parsed = Date.parse(String(value || ''));
  return Number.isFinite(parsed) ? new Date(parsed).toISOString().slice(0, 10) : new Date().toISOString().slice(0, 10);
}

function boundedText(value, fallback, max) {
  const text = String(value || '').trim();
  return (text || fallback).slice(0, max);
}

function safeAttachmentLabel(value) {
  return String(value || 'Attachment')
    .replace(/[\[\]]/g, '')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 160) || 'Attachment';
}

function attachmentMarkdown(attachments) {
  const values = Array.isArray(attachments) ? attachments : [];
  if (!values.length) return { markdown: '', reviewCount: 0 };

  const durable = values.filter((attachment) => (
    attachment?.durable === true
    && String(attachment?.visibility || '').toLowerCase() === 'public'
    && /^https:\/\//i.test(String(attachment?.url || ''))
  ));
  const review = values.filter((attachment) => !durable.includes(attachment));
  const lines = durable.map((attachment) => {
    const label = safeAttachmentLabel(attachment.filename);
    const url = String(attachment.url);
    return String(attachment.contentType || '').toLowerCase().startsWith('image/')
      ? `![${label}](${url})`
      : `- [${label}](${url})`;
  });
  const warnings = review.map((attachment) => {
    const label = safeAttachmentLabel(attachment.filename);
    const reason = String(attachment.reviewReason || 'This Whop attachment does not have a verified permanent public URL. Re-upload it before publishing.').trim();
    return `> **Attachment review required — ${label}:** ${reason}`;
  });
  return {
    markdown: `\n\n## Attachments\n\n${[...lines, ...warnings].join('\n\n')}`,
    reviewCount: review.length,
  };
}

function attachmentFingerprintData(attachments) {
  return (Array.isArray(attachments) ? attachments : []).map((attachment) => ({
    id: String(attachment?.id || ''),
    filename: String(attachment?.filename || ''),
    contentType: String(attachment?.contentType || ''),
    visibility: String(attachment?.visibility || 'unknown'),
    durable: attachment?.durable === true,
    url: attachment?.durable === true ? String(attachment?.url || '') : null,
    reviewReason: attachment?.durable === true ? null : String(attachment?.reviewReason || ''),
  }));
}

function preparedSource(item) {
  if (item?.integrity?.blocked) throw new HttpError(422, `${item.title || item.sourceId} is blocked by the formatting integrity check.`);
  const originalBody = String(item?.body || '');
  const attachmentSection = attachmentMarkdown(item?.attachments);
  const combinedBody = `${originalBody}${attachmentSection.markdown}`;
  const prepared = prepareGuideBody(combinedBody, { source: `Whop forum post ${item.sourceId}` });
  const sourceFingerprint = contentFingerprint(JSON.stringify({
    sourceType: item.sourceType,
    sourceId: item.sourceId,
    title: item.title,
    body: prepared.body,
    attachments: attachmentFingerprintData(item.attachments),
    updatedAt: item.updatedAt || null,
  }));
  return {
    prepared,
    sourceFingerprint,
    attachmentReviewCount: attachmentSection.reviewCount,
  };
}

async function readImportRegistry() {
  const file = await readRepoFile(IMPORTS_PATH, { allowMissing: true });
  if (!file.content.trim()) return { sha: file.sha, registry: { version: 1, items: {} } };
  try {
    const parsed = JSON.parse(file.content);
    return {
      sha: file.sha,
      registry: {
        version: 1,
        items: parsed?.items && typeof parsed.items === 'object' ? parsed.items : {},
      },
    };
  } catch {
    throw new HttpError(502, 'The Whop import registry contains invalid JSON.');
  }
}

function registryContent(registry) {
  return `${JSON.stringify({ version: 1, items: registry.items || {} }, null, 2)}\n`;
}

async function existingGuides() {
  const files = await listGuideFiles();
  const entries = await Promise.all(files.map(async (file) => {
    const id = file.name.replace(/\.mdx?$/i, '');
    const raw = await readRepoFile(`website/src/content/hacks/${id}.md`, { allowMissing: true });
    if (!raw.content.trim()) return null;
    try {
      return { id, raw, guide: parseGuideFile(id, raw.content) };
    } catch {
      return null;
    }
  }));
  return new Map(entries.filter(Boolean).map((entry) => [entry.id, entry]));
}

function uniqueGuideId(item, currentGuides, reservedIds) {
  const base = slugify(item.title) || `whop-${slugify(item.sourceId)}` || 'whop-guide';
  if (!currentGuides.has(base) && !reservedIds.has(base)) return base;
  const suffix = contentFingerprint(`${item.sourceType}:${item.sourceId}`).slice(0, 8);
  const candidate = `${base.slice(0, Math.max(1, 63 - suffix.length))}-${suffix}`;
  if (!currentGuides.has(candidate) && !reservedIds.has(candidate)) return candidate;
  throw new HttpError(409, `Could not create a unique guide ID for ${item.title}.`);
}

function readTime(body) {
  const words = String(body || '').trim().split(/\s+/).filter(Boolean).length;
  return `${Math.max(1, Math.ceil(words / 225))} min`;
}

function keywordsFor(item) {
  return [
    'Whop',
    item.experienceName,
    item.company?.title,
    item.author?.username,
    item.author?.name,
  ].map((value) => String(value || '').trim()).filter(Boolean).slice(0, 24);
}

export async function importWhopDrafts(input = {}) {
  if (input.rightsConfirmed !== true) {
    throw new HttpError(422, 'Confirm that you own these guides or have permission to republish them.');
  }
  const category = String(input.category || '').trim();
  const items = Array.isArray(input.items) ? input.items : [];
  if (!items.length) throw new HttpError(422, 'Approve at least one Whop post to import.');
  if (items.length > MAX_BATCH_ITEMS) throw new HttpError(422, `Import at most ${MAX_BATCH_ITEMS} posts per batch.`);

  const [siteDocument, sourcePolicy] = await Promise.all([
    readSiteSettings(),
    readWhopSourcePolicy(),
  ]);
  if (!siteDocument.settings.categories?.[category]) throw new HttpError(422, 'Choose a category from the current SniperPlug category registry.');
  for (const item of items) assertApprovedWhopSource(sourcePolicy.registry, item.experienceId);

  const preparedItems = items.map((item) => ({ item, ...preparedSource(item), key: sourceKey(item) }));
  const totalContent = preparedItems.reduce((sum, entry) => sum + Buffer.byteLength(entry.prepared.body, 'utf8'), 0);
  if (totalContent > MAX_BATCH_CONTENT) throw new HttpError(422, 'That import batch is too large. Import fewer posts at once.');

  const [importsDocument, statusDocument, guideMap] = await Promise.all([
    readImportRegistry(),
    readStatusDocument(),
    existingGuides(),
  ]);
  const registry = importsDocument.registry;
  const reservedIds = new Set();
  const existingOrders = [...guideMap.values()]
    .filter((entry) => entry.guide.managed)
    .map((entry) => entry.guide.order)
    .filter((order) => Number.isFinite(Number(order)));
  let nextOrder = nextAutomaticMethodOrder(existingOrders);
  const files = [];
  const results = [];
  let statusChanged = false;

  for (const entry of preparedItems) {
    const prior = registry.items[entry.key] || null;
    if (prior?.fingerprint === entry.sourceFingerprint && guideMap.has(prior.guideId)) {
      results.push({
        sourceKey: entry.key,
        guideId: prior.guideId,
        action: 'unchanged',
        title: entry.item.title,
        attachmentReviewCount: entry.attachmentReviewCount,
      });
      continue;
    }

    const existing = prior?.guideId ? guideMap.get(prior.guideId) : null;
    const guideId = existing?.id || uniqueGuideId(entry.item, guideMap, reservedIds);
    reservedIds.add(guideId);
    const descriptionFallback = `Imported from ${entry.item.experienceName || entry.item.company?.title || 'Whop'} for review.`;
    const guide = validateGuide({
      id: guideId,
      title: boundedText(entry.item.title, 'Imported Whop post', 140),
      description: boundedText(entry.item.description, descriptionFallback, 260),
      category,
      featured: false,
      draft: true,
      badge: entry.attachmentReviewCount ? 'Review files' : 'Imported',
      keywords: keywordsFor(entry.item),
      published: safeDate(entry.item.createdAt),
      readTime: readTime(entry.prepared.body),
      order: existing?.guide.order ?? nextOrder,
      body: entry.prepared.body,
    }, Object.keys(siteDocument.settings.categories));
    if (!existing) nextOrder = nextAutomaticMethodOrder([nextOrder]);

    const serialized = composeGuideFile(guide);
    const reparsed = parseGuideFile(guide.id, serialized);
    const roundTrip = assertGuideBodyRoundTrip(guide.body, reparsed.body);
    files.push({ path: guidePath(guide.id), content: serialized });

    if (!statusDocument.entries[guide.id]) {
      statusDocument.entries[guide.id] = {
        status: 'active',
        expiresAt: null,
        verifiedAt: new Date().toISOString(),
        note: entry.attachmentReviewCount
          ? `Imported from Whop as a hidden draft; ${entry.attachmentReviewCount} attachment${entry.attachmentReviewCount === 1 ? '' : 's'} require review.`
          : 'Imported from Whop as a hidden draft.',
      };
      statusChanged = true;
    }

    registry.items[entry.key] = {
      guideId: guide.id,
      sourceType: entry.item.sourceType,
      sourceId: entry.item.sourceId,
      experienceId: entry.item.experienceId || null,
      experienceName: entry.item.experienceName || null,
      companyId: entry.item.company?.id || null,
      companyTitle: entry.item.company?.title || null,
      fingerprint: entry.sourceFingerprint,
      sourceUpdatedAt: entry.item.updatedAt || null,
      importedAt: new Date().toISOString(),
      integrityFingerprint: roundTrip.fingerprint,
      repairs: roundTrip.repairs,
      attachmentReviewCount: entry.attachmentReviewCount,
    };
    results.push({
      sourceKey: entry.key,
      guideId: guide.id,
      action: existing ? 'updated-draft' : 'created-draft',
      title: guide.title,
      attachmentReviewCount: entry.attachmentReviewCount,
      integrity: { fingerprint: roundTrip.fingerprint, repairs: roundTrip.repairs },
      live: normalizeStatus(statusDocument.entries[guide.id]),
    });
  }

  const attachmentReviews = results.reduce((sum, result) => sum + Number(result.attachmentReviewCount || 0), 0);
  if (!files.length) {
    return { results, commit: null, imported: 0, unchanged: results.length, attachmentReviews };
  }
  files.push({ path: IMPORTS_PATH, content: registryContent(registry) });
  if (statusChanged) files.push({ path: 'website/src/data/deal-status.json', content: statusFileContent(statusDocument.entries) });

  const write = await writeRepoFiles(files, `Import ${files.filter((file) => file.path.startsWith('website/src/content/hacks/')).length} Whop post drafts`);
  return {
    results,
    commit: write.commit?.sha || null,
    imported: results.filter((result) => result.action !== 'unchanged').length,
    unchanged: results.filter((result) => result.action === 'unchanged').length,
    attachmentReviews,
  };
}
