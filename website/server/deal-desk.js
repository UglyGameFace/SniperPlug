import { createHmac, timingSafeEqual } from 'node:crypto';

const COOKIE_NAME = 'sniperplug_control_center';
const STATUS_PATH = 'website/src/data/deal-status.json';
const GUIDE_DIR = 'website/src/content/hacks';
const CATEGORY_KEY = /^[a-z0-9](?:[a-z0-9-]{0,46}[a-z0-9])?$/;
const COMMIT_SHA = /^[a-f0-9]{7,40}$/i;
const GITHUB_TIMEOUT_MS = 15_000;
const LOGIN_WINDOW_MS = 10 * 60 * 1000;
const LOGIN_BLOCK_MS = 15 * 60 * 1000;
const LOGIN_MAX_FAILURES = 5;
const LOGIN_ATTEMPTS = globalThis.__sniperplugLoginAttempts instanceof Map
  ? globalThis.__sniperplugLoginAttempts
  : new Map();
globalThis.__sniperplugLoginAttempts = LOGIN_ATTEMPTS;

export class HttpError extends Error {
  constructor(status, message, details) {
    super(message);
    this.status = status;
    this.details = details;
  }
}

export function json(data, status = 200, extraHeaders = {}) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      'content-type': 'application/json; charset=utf-8',
      'cache-control': 'no-store, max-age=0',
      'x-content-type-options': 'nosniff',
      ...extraHeaders,
    },
  });
}

export function methodNotAllowed(allowed) {
  return json({ error: 'Method not allowed.' }, 405, { allow: allowed.join(', ') });
}

function config() {
  const repository = process.env.GITHUB_REPOSITORY || '';
  const [repoOwnerFromPair, repoNameFromPair] = repository.includes('/') ? repository.split('/', 2) : [];
  const owner = process.env.GITHUB_REPO_OWNER || repoOwnerFromPair || 'UglyGameFace';
  const repo = process.env.GITHUB_REPO_NAME || repoNameFromPair || 'SniperPlug';
  const branch = process.env.GITHUB_BRANCH || 'main';
  const token = process.env.GITHUB_TOKEN || '';
  return { owner, repo, branch, token };
}

function requireGitHubToken() {
  const value = config();
  if (!value.token) {
    throw new HttpError(503, 'GITHUB_TOKEN is not configured in Cloudflare Pages.');
  }
  return value;
}

function sessionSecret() {
  const secret = process.env.DEAL_DESK_SESSION_SECRET || process.env.DEAL_DESK_PASSWORD || '';
  if (!secret) throw new HttpError(503, 'DEAL_DESK_PASSWORD is not configured in Cloudflare Pages.');
  return secret;
}

function safeEqual(left, right) {
  const a = Buffer.from(String(left));
  const b = Buffer.from(String(right));
  if (a.length !== b.length) {
    timingSafeEqual(a, Buffer.alloc(a.length));
    return false;
  }
  return timingSafeEqual(a, b);
}

function sign(payload) {
  return createHmac('sha256', sessionSecret()).update(payload).digest('base64url');
}

export function createSessionCookie() {
  const exp = Date.now() + 12 * 60 * 60 * 1000;
  const payload = Buffer.from(JSON.stringify({ exp, v: 1 })).toString('base64url');
  const token = `${payload}.${sign(payload)}`;
  return `${COOKIE_NAME}=${token}; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=43200`;
}

export function clearSessionCookie() {
  return `${COOKIE_NAME}=; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=0`;
}

function readCookie(request, name) {
  const cookie = request.headers.get('cookie') || '';
  for (const part of cookie.split(';')) {
    const [key, ...rest] = part.trim().split('=');
    if (key === name) return rest.join('=');
  }
  return '';
}

function loginClientKey(request) {
  const forwarded = request.headers.get('cf-connecting-ip')
    || request.headers.get('x-forwarded-for')
    || request.headers.get('x-real-ip')
    || 'unknown';
  return String(forwarded).split(',')[0].trim().slice(0, 128) || 'unknown';
}

function cleanupLoginAttempts(now = Date.now()) {
  if (LOGIN_ATTEMPTS.size < 1000) return;
  for (const [key, entry] of LOGIN_ATTEMPTS) {
    const expiresAt = Math.max(Number(entry.windowStartedAt || 0) + LOGIN_WINDOW_MS, Number(entry.blockedUntil || 0));
    if (expiresAt <= now) LOGIN_ATTEMPTS.delete(key);
  }
}

export function getLoginThrottle(request) {
  const now = Date.now();
  cleanupLoginAttempts(now);
  const key = loginClientKey(request);
  const entry = LOGIN_ATTEMPTS.get(key);
  if (!entry || Number(entry.blockedUntil || 0) <= now) {
    return { key, blocked: false, retryAfter: 0 };
  }
  return {
    key,
    blocked: true,
    retryAfter: Math.max(1, Math.ceil((entry.blockedUntil - now) / 1000)),
  };
}

export function registerLoginFailure(request) {
  const now = Date.now();
  const key = loginClientKey(request);
  const previous = LOGIN_ATTEMPTS.get(key);
  const withinWindow = previous && now - Number(previous.windowStartedAt || 0) < LOGIN_WINDOW_MS;
  const failures = withinWindow ? Number(previous.failures || 0) + 1 : 1;
  const blockedUntil = failures >= LOGIN_MAX_FAILURES ? now + LOGIN_BLOCK_MS : 0;
  const entry = {
    failures,
    windowStartedAt: withinWindow ? previous.windowStartedAt : now,
    blockedUntil,
  };
  LOGIN_ATTEMPTS.set(key, entry);
  return {
    blocked: blockedUntil > now,
    retryAfter: blockedUntil > now ? Math.ceil((blockedUntil - now) / 1000) : 0,
    remaining: Math.max(0, LOGIN_MAX_FAILURES - failures),
  };
}

export function clearLoginFailures(request) {
  LOGIN_ATTEMPTS.delete(loginClientKey(request));
}

export function isAuthenticated(request) {
  try {
    const token = readCookie(request, COOKIE_NAME);
    const [payload, signature] = token.split('.', 2);
    if (!payload || !signature || !safeEqual(signature, sign(payload))) return false;
    const parsed = JSON.parse(Buffer.from(payload, 'base64url').toString('utf8'));
    return parsed?.v === 1 && Number(parsed.exp) > Date.now();
  } catch {
    return false;
  }
}

export function requireAuth(request) {
  if (!isAuthenticated(request)) throw new HttpError(401, 'Deal Desk login required.');
}

export function verifyPassword(password) {
  const expected = process.env.DEAL_DESK_PASSWORD || '';
  if (!expected) throw new HttpError(503, 'DEAL_DESK_PASSWORD is not configured in Cloudflare Pages.');
  return safeEqual(String(password || ''), expected);
}

export function requireSameOrigin(request) {
  const origin = request.headers.get('origin');
  if (!origin) return;
  const expected = new URL(request.url).origin;
  if (origin !== expected) throw new HttpError(403, 'Cross-origin request blocked.');
}

async function github(path, options = {}, needsToken = true) {
  const cfg = needsToken ? requireGitHubToken() : config();
  const headers = {
    accept: 'application/vnd.github+json',
    'x-github-api-version': '2022-11-28',
    'user-agent': 'sniperplug-control-center',
    ...options.headers,
  };
  if (cfg.token) headers.authorization = `Bearer ${cfg.token}`;

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), GITHUB_TIMEOUT_MS);
  let response;
  try {
    response = await fetch(`https://api.github.com${path}`, {
      ...options,
      headers,
      cache: 'no-store',
      signal: controller.signal,
    });
  } catch (error) {
    if (controller.signal.aborted || error?.name === 'AbortError' || error?.name === 'TimeoutError') {
      throw new HttpError(504, 'GitHub did not respond in time. Try again.');
    }
    throw error;
  } finally {
    clearTimeout(timer);
  }

  const text = await response.text();
  let payload = null;
  try { payload = text ? JSON.parse(text) : null; } catch { payload = text; }
  if (!response.ok) {
    const message = payload?.message || `GitHub request failed (${response.status}).`;
    const status = response.status === 404
      ? 404
      : response.status === 409
        ? 409
        : response.status === 422
          ? 422
          : response.status === 429
            ? 503
            : 502;
    throw new HttpError(status, message, payload);
  }
  return payload;
}

function repoRoot() {
  const { owner, repo } = config();
  return `/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}`;
}

function repoPath(path) {
  return `${repoRoot()}/contents/${path.split('/').map(encodeURIComponent).join('/')}`;
}

function branchPath(branch) {
  return branch.split('/').map(encodeURIComponent).join('/');
}

export async function readCommitDeploymentStatus(commitSha) {
  const commit = String(commitSha || '').trim();
  if (!COMMIT_SHA.test(commit)) throw new HttpError(422, 'A valid deployment commit is required.');

  const [checksPayload, statusPayload] = await Promise.all([
    github(`${repoRoot()}/commits/${encodeURIComponent(commit)}/check-runs?per_page=100`).catch(() => ({ check_runs: [] })),
    github(`${repoRoot()}/commits/${encodeURIComponent(commit)}/status`).catch(() => ({ sha: commit, statuses: [], state: 'pending' })),
  ]);
  const checks = Array.isArray(checksPayload?.check_runs) ? checksPayload.check_runs : [];
  const pagesCheck = checks.find((check) => {
    const name = `${check?.name || ''} ${check?.app?.name || ''}`.toLowerCase();
    return name.includes('cloudflare') || name.includes('pages');
  }) || null;
  const statuses = Array.isArray(statusPayload?.statuses) ? statusPayload.statuses : [];
  const pagesStatus = statuses.find((status) => {
    const name = String(status?.context || '').toLowerCase();
    return name.includes('cloudflare') || name.includes('pages');
  }) || null;

  let state = 'pending';
  let description = 'Waiting for Cloudflare Pages.';
  let targetUrl = null;
  if (pagesCheck) {
    const conclusion = String(pagesCheck.conclusion || '').toLowerCase();
    state = pagesCheck.status !== 'completed' ? 'pending' : conclusion === 'success' ? 'success' : conclusion ? 'failure' : 'pending';
    description = String(pagesCheck.output?.summary || pagesCheck.output?.title || pagesCheck.name || description).slice(0, 180);
    targetUrl = /^https:\/\//i.test(String(pagesCheck.details_url || '')) ? String(pagesCheck.details_url) : null;
  } else if (pagesStatus) {
    const raw = String(pagesStatus.state || 'pending').toLowerCase();
    state = new Set(['pending', 'success', 'failure', 'error']).has(raw) ? raw : 'pending';
    description = String(pagesStatus.description || (state === 'pending' ? description : `Cloudflare Pages reported ${state}.`)).slice(0, 180);
    targetUrl = /^https:\/\//i.test(String(pagesStatus.target_url || '')) ? String(pagesStatus.target_url) : null;
  }
  return {
    commit: String(statusPayload?.sha || commit),
    state,
    completed: state !== 'pending',
    description,
    targetUrl,
    checkedAt: new Date().toISOString(),
  };
}

export async function readRepoFile(path, { allowMissing = false, publicRead = false } = {}) {
  const { branch } = config();
  try {
    const payload = await github(`${repoPath(path)}?ref=${encodeURIComponent(branch)}`, {}, !publicRead);
    if (Array.isArray(payload)) throw new HttpError(400, `${path} is a directory.`);
    return {
      sha: payload.sha,
      content: Buffer.from(String(payload.content || '').replace(/\n/g, ''), 'base64').toString('utf8'),
    };
  } catch (error) {
    if (allowMissing && error instanceof HttpError && error.status === 404) return { sha: null, content: '' };
    throw error;
  }
}

export async function listGuideFiles() {
  const { branch } = requireGitHubToken();
  const payload = await github(`${repoPath(GUIDE_DIR)}?ref=${encodeURIComponent(branch)}`);
  if (!Array.isArray(payload)) throw new HttpError(502, 'GitHub did not return the guide directory.');
  return payload.filter((item) => item.type === 'file' && /\.mdx?$/i.test(item.name));
}

export async function writeRepoFile(path, content, message, sha = null) {
  const { branch } = requireGitHubToken();
  const body = {
    message,
    content: Buffer.from(content, 'utf8').toString('base64'),
    branch,
  };
  if (sha) body.sha = sha;
  return github(repoPath(path), {
    method: 'PUT',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export async function writeRepoFiles(files, message) {
  const { branch } = requireGitHubToken();
  const updates = Array.isArray(files)
    ? files.filter((file) => file?.path && typeof file.content === 'string')
    : [];
  if (!updates.length) throw new HttpError(422, 'No repository files were provided.');

  const refName = branchPath(branch);
  const reference = await github(`${repoRoot()}/git/ref/heads/${refName}`);
  const baseCommitSha = reference?.object?.sha;
  if (!baseCommitSha) throw new HttpError(502, 'GitHub did not return the current branch commit.');

  const baseCommit = await github(`${repoRoot()}/git/commits/${encodeURIComponent(baseCommitSha)}`);
  const baseTreeSha = baseCommit?.tree?.sha;
  if (!baseTreeSha) throw new HttpError(502, 'GitHub did not return the current repository tree.');

  const treeEntries = await Promise.all(updates.map(async (file) => {
    const blob = await github(`${repoRoot()}/git/blobs`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ content: file.content, encoding: 'utf-8' }),
    });
    return {
      path: file.path,
      mode: '100644',
      type: 'blob',
      sha: blob.sha,
    };
  }));

  const tree = await github(`${repoRoot()}/git/trees`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ base_tree: baseTreeSha, tree: treeEntries }),
  });
  const commit = await github(`${repoRoot()}/git/commits`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ message, tree: tree.sha, parents: [baseCommitSha] }),
  });

  try {
    await github(`${repoRoot()}/git/refs/heads/${refName}`, {
      method: 'PATCH',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ sha: commit.sha, force: false }),
    });
  } catch (error) {
    throw new HttpError(409, 'The repository changed while saving. Refresh and try again.', error?.details);
  }

  return {
    commit,
    files: Object.fromEntries(treeEntries.map((entry) => [entry.path, entry.sha])),
  };
}

export async function deleteRepoFile(path, message, sha) {
  const { branch } = requireGitHubToken();
  return github(repoPath(path), {
    method: 'DELETE',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ message, sha, branch }),
  });
}

function parseValue(raw) {
  const value = raw.trim();
  if (!value) return '';
  if (value === 'true') return true;
  if (value === 'false') return false;
  if (/^-?\d+(\.\d+)?$/.test(value)) return Number(value);
  if ((value.startsWith('[') && value.endsWith(']')) || (value.startsWith('"') && value.endsWith('"'))) {
    try { return JSON.parse(value); } catch { return value.replace(/^"|"$/g, ''); }
  }
  return value;
}

export function parseGuideFile(id, raw) {
  const match = raw.match(/^---\s*\n([\s\S]*?)\n---\s*\n?([\s\S]*)$/);
  if (!match) throw new HttpError(422, `Guide ${id} has invalid frontmatter.`);
  const data = {};
  for (const line of match[1].split(/\r?\n/)) {
    const pair = line.match(/^([A-Za-z][A-Za-z0-9]*):\s*(.*)$/);
    if (pair) data[pair[1]] = parseValue(pair[2]);
  }
  return {
    id,
    title: String(data.title || id),
    description: String(data.description || ''),
    category: CATEGORY_KEY.test(String(data.category || '')) ? String(data.category) : 'deal-alerts',
    managed: Boolean(data.managed),
    featured: Boolean(data.featured),
    draft: Boolean(data.draft),
    badge: String(data.badge || ''),
    keywords: Array.isArray(data.keywords) ? data.keywords.map(String) : [],
    published: String(data.published || new Date().toISOString().slice(0, 10)),
    updated: data.updated ? String(data.updated) : '',
    readTime: String(data.readTime || '5 min'),
    order: Number.isFinite(Number(data.order)) ? Number(data.order) : 999,
    body: match[2].trim(),
  };
}

function quoted(value) {
  return JSON.stringify(String(value || ''));
}

export function slugify(value) {
  return String(value || '')
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 72);
}

export function validateGuide(input, allowedCategories = null) {
  const title = String(input.title || '').trim();
  const description = String(input.description || '').trim();
  const category = String(input.category || '').trim();
  const body = String(input.body || '').trim();
  if (title.length < 3 || title.length > 140) throw new HttpError(422, 'Title must be 3–140 characters.');
  if (description.length < 8 || description.length > 260) throw new HttpError(422, 'Description must be 8–260 characters.');
  if (!CATEGORY_KEY.test(category)) throw new HttpError(422, 'Choose a valid category.');
  if (allowedCategories && !new Set(allowedCategories).has(category)) {
    throw new HttpError(422, 'That category is not registered yet. Create it or choose another category.');
  }
  if (body.length < 8 || body.length > 100000) throw new HttpError(422, 'Guide content is missing or too large.');

  const existingId = slugify(input.id || '');
  const id = existingId || slugify(title);
  if (!id) throw new HttpError(422, 'A valid guide slug could not be created.');
  const keywords = Array.isArray(input.keywords)
    ? input.keywords.map((item) => String(item).trim()).filter(Boolean).slice(0, 24)
    : String(input.keywords || '').split(',').map((item) => item.trim()).filter(Boolean).slice(0, 24);

  return {
    id,
    title,
    description,
    category,
    managed: true,
    featured: Boolean(input.featured),
    draft: Boolean(input.draft),
    badge: String(input.badge || '').trim().slice(0, 36),
    keywords,
    published: /^\d{4}-\d{2}-\d{2}$/.test(String(input.published || ''))
      ? String(input.published)
      : new Date().toISOString().slice(0, 10),
    updated: new Date().toISOString().slice(0, 10),
    readTime: /^\d+\s*min$/i.test(String(input.readTime || '').trim())
      ? String(input.readTime).trim()
      : '5 min',
    order: Math.max(0, Math.min(9999, Number.parseInt(input.order, 10) || 999)),
    body,
  };
}

export function composeGuideFile(guide) {
  const lines = [
    '---',
    `title: ${quoted(guide.title)}`,
    `description: ${quoted(guide.description)}`,
    `category: ${quoted(guide.category)}`,
    'managed: true',
    `featured: ${guide.featured ? 'true' : 'false'}`,
    `draft: ${guide.draft ? 'true' : 'false'}`,
  ];
  if (guide.badge) lines.push(`badge: ${quoted(guide.badge)}`);
  lines.push(`keywords: ${JSON.stringify(guide.keywords)}`);
  lines.push(`published: ${guide.published}`);
  lines.push(`updated: ${guide.updated}`);
  lines.push(`readTime: ${quoted(guide.readTime)}`);
  lines.push(`order: ${guide.order}`);
  lines.push('---', '', guide.body.trim(), '');
  return lines.join('\n');
}

export async function readStatusDocument({ publicRead = false } = {}) {
  const file = await readRepoFile(STATUS_PATH, { allowMissing: true, publicRead });
  let entries = {};
  if (file.content.trim()) {
    try { entries = JSON.parse(file.content); } catch { throw new HttpError(502, 'The deal status file contains invalid JSON.'); }
  }
  return { sha: file.sha, entries: entries && typeof entries === 'object' ? entries : {} };
}

export function normalizeStatus(entry = {}) {
  const allowed = new Set(['active', 'paused', 'expired']);
  const status = allowed.has(entry.status) ? entry.status : 'active';
  const expiresAt = entry.expiresAt ? String(entry.expiresAt) : null;
  const expiredByTime = expiresAt && Number.isFinite(Date.parse(expiresAt)) && Date.parse(expiresAt) <= Date.now();
  return {
    status: expiredByTime ? 'expired' : status,
    expiresAt,
    verifiedAt: entry.verifiedAt ? String(entry.verifiedAt) : null,
    note: entry.note ? String(entry.note).slice(0, 240) : '',
  };
}

export async function writeStatusDocument(entries, sha, message) {
  const content = `${JSON.stringify(entries, null, 2)}\n`;
  return writeRepoFile(STATUS_PATH, content, message, sha);
}

export function statusFileContent(entries) {
  return `${JSON.stringify(entries, null, 2)}\n`;
}

export function guidePath(id) {
  return `${GUIDE_DIR}/${slugify(id)}.md`;
}

export function handleError(error) {
  if (error instanceof HttpError) return json({ error: error.message, details: error.details || undefined }, error.status);
  console.error(error);
  return json({ error: 'Unexpected Deal Desk error.' }, 500);
}
