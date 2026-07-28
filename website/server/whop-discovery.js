import { HttpError } from './deal-desk.js';
import { prepareGuideBody } from './guide-content-integrity.js';
import {
  assertApprovedWhopSource,
  normalizeWhopGroupName,
  readWhopSourcePolicy,
  WHOP_DEFAULT_GROUPS,
  whopExperienceId,
  whopSourceDecision,
  whopSourceOptions,
} from './whop-source-policy.js';

const API_BASE = 'https://api.whop.com/api/v1';
const REQUEST_TIMEOUT_MS = 20_000;
const PAGE_SIZE = 50;
const MAX_PAGES = 100;
const MAX_ITEMS = 1000;
const MAX_COMPANIES = 100;
const SOURCE_CONCURRENCY = 5;

function plainExcerpt(value, limit = 240) {
  return String(value || '')
    .replace(/^ {0,3}#{1,6}\s+/gm, '')
    .replace(/!\[[^\]]*\]\([^)]*\)/g, '')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/[`*_~>|]/g, '')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, limit);
}

function fallbackTitle(content, prefix) {
  const heading = String(content || '').match(/^ {0,3}#{1,6}\s+(.+)$/m)?.[1]?.trim();
  if (heading) return heading.slice(0, 140);
  return plainExcerpt(content, 100) || prefix;
}

function normalizedAttachments(value) {
  return (Array.isArray(value) ? value : []).map((attachment) => ({
    id: String(attachment?.id || ''),
    filename: String(attachment?.filename || 'attachment'),
    contentType: String(attachment?.content_type || ''),
    url: /^https:\/\//i.test(String(attachment?.url || '')) ? String(attachment.url) : null,
  })).filter((attachment) => attachment.id || attachment.url);
}

async function requestWhop(session, path, query = {}) {
  const url = new URL(`${API_BASE}/${String(path || '').replace(/^\/+/, '')}`);
  for (const [key, value] of Object.entries(query)) {
    if (value === null || value === undefined || value === '') continue;
    if (Array.isArray(value)) {
      for (const item of value) url.searchParams.append(key, String(item));
    } else {
      url.searchParams.set(key, String(value));
    }
  }

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  let response;
  try {
    response = await fetch(url, {
      headers: { authorization: `Bearer ${session.accessToken}` },
      cache: 'no-store',
      signal: controller.signal,
    });
  } catch (error) {
    if (controller.signal.aborted || error?.name === 'AbortError') {
      throw new HttpError(504, 'Whop did not respond in time while loading content.');
    }
    throw error;
  } finally {
    clearTimeout(timer);
  }

  const text = await response.text();
  let payload = null;
  try { payload = text ? JSON.parse(text) : null; } catch { payload = text; }
  if (!response.ok) {
    const message = payload?.error?.message || payload?.message || `Whop request failed (${response.status}).`;
    const status = response.status === 401 ? 401 : response.status === 403 ? 403 : response.status === 404 ? 404 : response.status === 429 ? 503 : response.status >= 500 ? 502 : 422;
    throw new HttpError(status, message, payload);
  }
  return payload;
}

async function allPages(session, path, query, options = {}) {
  const items = [];
  let after = '';
  const label = String(options.label || 'items');
  const maxItems = Math.min(MAX_ITEMS, Math.max(1, Number(options.maxItems) || MAX_ITEMS));
  for (let page = 0; page < MAX_PAGES; page += 1) {
    const payload = await requestWhop(session, path, {
      ...query,
      first: PAGE_SIZE,
      ...(after && { after }),
    });
    const data = Array.isArray(payload?.data) ? payload.data : [];
    items.push(...data);
    if (items.length > maxItems) throw new HttpError(422, `Whop returned more than ${maxItems} ${label}. Narrow the request before continuing.`);
    if (!payload?.page_info?.has_next_page) return items;
    const next = String(payload?.page_info?.end_cursor || '');
    if (!next || next === after) throw new HttpError(502, 'Whop returned an invalid pagination cursor.');
    after = next;
  }
  throw new HttpError(502, 'Whop pagination exceeded the safe page limit.');
}

async function mapConcurrent(values, mapper, concurrency = SOURCE_CONCURRENCY) {
  const output = new Array(values.length);
  let cursor = 0;
  async function worker() {
    while (cursor < values.length) {
      const index = cursor;
      cursor += 1;
      output[index] = await mapper(values[index], index);
    }
  }
  await Promise.all(Array.from({ length: Math.min(concurrency, Math.max(1, values.length)) }, () => worker()));
  return output;
}

function experienceSummary(experience, fallbackId) {
  return {
    id: String(experience?.id || fallbackId),
    name: String(experience?.name || 'Whop experience'),
    app: experience?.app ? { id: experience.app.id || null, name: experience.app.name || null } : null,
    company: experience?.company ? {
      id: experience.company.id || null,
      title: experience.company.title || experience.company.name || null,
      route: experience.company.route || null,
    } : null,
    isPublic: Boolean(experience?.is_public),
  };
}

function normalizeForumPost(post, experience) {
  const item = {
    sourceType: 'forum-post',
    sourceId: String(post?.id || ''),
    sourceKey: `forum-post:${String(post?.id || '')}`,
    experienceId: String(experience.id || ''),
    experienceName: String(experience.name || ''),
    company: experience.company || null,
    title: String(post?.title || fallbackTitle(post?.content, 'Untitled forum post')).trim().slice(0, 140),
    body: String(post?.content || ''),
    description: '',
    createdAt: post?.created_at || null,
    updatedAt: post?.updated_at || post?.created_at || null,
    author: post?.user ? {
      id: post.user.id || null,
      name: post.user.name || null,
      username: post.user.username || null,
    } : null,
    attachments: normalizedAttachments(post?.attachments),
    sourceMeta: {
      pinned: Boolean(post?.is_pinned),
      edited: Boolean(post?.is_edited),
      posterAdmin: Boolean(post?.is_poster_admin),
    },
  };

  try {
    const integrity = prepareGuideBody(item.body, { source: `forum post ${item.sourceId}` });
    return {
      ...item,
      body: integrity.body,
      description: plainExcerpt(integrity.body),
      decision: 'pending',
      integrity: {
        fingerprint: integrity.fingerprint,
        repairs: integrity.repairs,
        structure: integrity.structure,
        blocked: false,
      },
    };
  } catch (error) {
    return {
      ...item,
      description: plainExcerpt(item.body),
      decision: 'blocked',
      integrity: {
        fingerprint: null,
        repairs: [],
        structure: null,
        blocked: true,
        error: String(error?.message || 'Content integrity validation failed.'),
        code: error?.code || 'invalid_content',
      },
    };
  }
}

function builtInGroup(companyTitle) {
  const normalized = normalizeWhopGroupName(companyTitle);
  return WHOP_DEFAULT_GROUPS.find((group) => normalizeWhopGroupName(group.label) === normalized) || null;
}

export function membershipCompanies(memberships) {
  const companies = new Map();
  for (const membership of memberships) {
    const status = String(membership?.status || '').toLowerCase();
    const id = String(membership?.company?.id || '').trim();
    if (!id) continue;
    const current = companies.get(id) || {
      id,
      title: String(membership?.company?.title || 'Whop group'),
      route: String(membership?.company?.route || '') || null,
      products: new Map(),
      statuses: new Set(),
      memberships: 0,
    };
    const productId = String(membership?.product?.id || '').trim();
    if (productId) current.products.set(productId, String(membership?.product?.title || 'Whop product'));
    if (status) current.statuses.add(status);
    current.memberships += 1;
    companies.set(id, current);
  }
  const values = [...companies.values()];
  if (values.length > MAX_COMPANIES) {
    throw new HttpError(422, `Whop returned more than ${MAX_COMPANIES} joined companies. Narrow the connected account before continuing.`);
  }
  return values;
}

function forumExperience(forum, company) {
  const id = whopExperienceId(forum?.experience?.id || forum?.id);
  if (!id) return null;
  return {
    id,
    name: String(forum?.experience?.name || forum?.name || 'Forum'),
    is_public: Boolean(forum?.experience?.is_public),
    company: {
      id: company.id,
      title: company.title,
      route: company.route,
    },
    app: {
      id: forum?.experience?.app?.id || null,
      name: forum?.experience?.app?.name || 'Forums',
    },
  };
}

function listedExperience(experience, company) {
  const id = whopExperienceId(experience?.id);
  if (!id) return null;
  return {
    ...experience,
    id,
    name: String(experience?.name || 'Whop experience'),
    company: {
      id: company.id,
      title: company.title,
      route: company.route,
    },
    app: experience?.app ? {
      id: experience.app.id || null,
      name: experience.app.name || null,
    } : null,
  };
}

function isForumExperience(experience) {
  const appName = normalizeWhopGroupName(experience?.app?.name || '');
  return appName === 'forum' || appName === 'forums' || appName.includes('forum');
}

function discoveryFailure(label, error) {
  if (error instanceof HttpError && error.status === 403) return `${label} was denied`;
  if (error instanceof HttpError && error.status === 404) return `${label} was not found`;
  return `${label} failed${error?.message ? `: ${String(error.message).slice(0, 160)}` : ''}`;
}

async function discoverCompanyForumSources(session, company, policy) {
  const productScopes = [...company.products].map(([id, title]) => ({ id, title }));
  if (!productScopes.length) productScopes.push({ id: null, title: 'company access' });

  const attempts = await mapConcurrent(productScopes, async (product) => {
    const query = {
      company_id: company.id,
      ...(product.id && { product_id: product.id }),
    };
    const output = { product, forums: [], experiences: [], failures: [] };

    try {
      output.forums = await allPages(session, 'forums', query, { label: 'forums', maxItems: 250 });
    } catch (error) {
      output.failures.push(discoveryFailure(`${product.title} forum lookup`, error));
    }

    try {
      output.experiences = await allPages(session, 'experiences', query, { label: 'experiences', maxItems: 250 });
    } catch (error) {
      output.failures.push(discoveryFailure(`${product.title} experience lookup`, error));
    }
    return output;
  }, Math.min(3, SOURCE_CONCURRENCY));

  const discovered = new Map();
  const experienceTypes = new Set();
  const failures = new Set();

  for (const attempt of attempts) {
    for (const failure of attempt.failures) failures.add(failure);
    for (const forum of attempt.forums) {
      const experience = forumExperience(forum, company);
      if (experience) discovered.set(experience.id, experience);
    }
    for (const raw of attempt.experiences) {
      const experience = listedExperience(raw, company);
      if (!experience) continue;
      const appName = String(experience.app?.name || 'Unknown app').trim() || 'Unknown app';
      experienceTypes.add(appName);
      if (isForumExperience(experience)) discovered.set(experience.id, experience);
    }
  }

  const sources = [...discovered.values()].map((experience) => ({
    experience: experienceSummary(experience, experience.id),
    source: whopSourceDecision(experience, experience.id, policy.registry),
  }));

  let error = null;
  if (!sources.length) {
    if (experienceTypes.size) {
      error = `No native Whop forum is attached to your membership product. Available experience types: ${[...experienceTypes].sort().join(', ')}.`;
    } else if (failures.size) {
      error = `Whop found the membership, but product-scoped discovery could not read its forum modules: ${[...failures].join('; ')}.`;
    } else {
      error = 'Whop found the membership product, but it has no readable forum or experience modules attached.';
    }
  }

  return {
    company,
    sources,
    experienceTypes: [...experienceTypes].sort(),
    error,
  };
}

export async function discoverWhopSources(session) {
  let memberships;
  try {
    memberships = await allPages(session, 'memberships', {}, { label: 'memberships', maxItems: MAX_ITEMS });
  } catch (error) {
    if (error instanceof HttpError && error.status === 403) {
      throw new HttpError(403, 'Reconnect Whop after enabling member:basic:read and member:email:read so joined groups can be discovered automatically.');
    }
    throw error;
  }

  const companies = membershipCompanies(memberships);
  const policy = await readWhopSourcePolicy();
  const results = await mapConcurrent(companies, (company) => discoverCompanyForumSources(session, company, policy));

  const groups = results.map(({ company, sources, experienceTypes, error }) => {
    const defaultGroup = builtInGroup(company.title);
    return {
      company: {
        id: company.id,
        title: company.title,
        route: company.route,
        products: [...company.products].map(([id, title]) => ({ id, title })),
        statuses: [...company.statuses],
        memberships: company.memberships,
      },
      defaultKey: defaultGroup?.key || null,
      builtIn: Boolean(defaultGroup),
      sources,
      experienceTypes,
      error,
    };
  }).filter((group) => group.sources.length || group.builtIn || group.error);

  const priority = new Map(WHOP_DEFAULT_GROUPS.map((group, index) => [group.key, index]));
  groups.sort((left, right) => {
    const leftRank = left.defaultKey ? priority.get(left.defaultKey) ?? 50 : 100;
    const rightRank = right.defaultKey ? priority.get(right.defaultKey) ?? 50 : 100;
    return leftRank - rightRank || left.company.title.localeCompare(right.company.title);
  });

  const sources = groups.flatMap((group) => group.sources);
  return {
    groups,
    sources,
    sourceOptions: whopSourceOptions(policy.registry),
    counts: {
      memberships: memberships.length,
      groups: groups.length,
      forums: sources.length,
      builtInGroups: groups.filter((group) => group.builtIn).length,
      approved: sources.filter((entry) => entry.source.decision === 'approved').length,
      disapproved: sources.filter((entry) => entry.source.decision === 'disapproved').length,
      pending: sources.filter((entry) => entry.source.decision === 'pending').length,
    },
  };
}

export async function resolveWhopExperience(session, input = {}) {
  const experienceId = whopExperienceId(input.experienceId || input.source);
  if (!experienceId) throw new HttpError(422, 'Choose a discovered Whop forum or paste an experience ID beginning with exp_.');
  const experience = await requestWhop(session, `experiences/${encodeURIComponent(experienceId)}`);
  return { experience, experienceId };
}

export async function discoverWhopGuides(session, input = {}) {
  const { experience, experienceId } = await resolveWhopExperience(session, input);
  const policy = await readWhopSourcePolicy();
  const source = whopSourceDecision(experience, experienceId, policy.registry);
  const summary = experienceSummary(experience, experienceId);

  if (source.decision !== 'approved') {
    return {
      experience: summary,
      source,
      sourceOptions: whopSourceOptions(policy.registry),
      approvalRequired: true,
      items: [],
      errors: [],
      counts: { total: 0, ready: 0, blocked: 0, forum: 0 },
    };
  }

  assertApprovedWhopSource(policy.registry, experienceId);
  const posts = await allPages(session, 'forum_posts', { experience_id: experienceId }, { label: 'posts', maxItems: MAX_ITEMS });
  const items = posts
    .filter((post) => !post?.parent_id)
    .map((post) => normalizeForumPost(post, summary));
  items.sort((left, right) => {
    const pinned = Number(Boolean(right.sourceMeta?.pinned)) - Number(Boolean(left.sourceMeta?.pinned));
    return pinned || String(right.updatedAt || '').localeCompare(String(left.updatedAt || '')) || left.title.localeCompare(right.title);
  });

  return {
    experience: summary,
    source,
    sourceOptions: whopSourceOptions(policy.registry),
    approvalRequired: false,
    items,
    errors: [],
    counts: {
      total: items.length,
      ready: items.filter((item) => !item.integrity.blocked).length,
      blocked: items.filter((item) => item.integrity.blocked).length,
      forum: items.length,
    },
  };
}
