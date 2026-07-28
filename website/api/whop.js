import {
  handleError,
  HttpError,
  json,
  methodNotAllowed,
  requireAuth,
  requireSameOrigin,
} from '../server/deal-desk.js';
import { verifyWhopAttachments } from '../server/whop-attachments.js';
import {
  discoverWhopGuides,
  discoverWhopSources,
  resolveWhopExperience,
} from '../server/whop-discovery.js';
import { importWhopDrafts } from '../server/whop-import.js';
import {
  beginWhopOAuth,
  finishWhopOAuth,
  readWhopSession,
  requireWhopSession,
  revokeWhopSession,
  whopSessionSummary,
} from '../server/whop-oauth.js';
import {
  readWhopSourcePolicy,
  saveWhopSourceDecision,
  saveWhopSourceDecisions,
  whopSourceOptions,
} from '../server/whop-source-policy.js';

const MAX_BULK_SOURCES = 100;

function actionFrom(request) {
  return String(new URL(request.url).searchParams.get('action') || '').trim();
}

function attachCookie(response, cookie) {
  if (cookie) response.headers.append('set-cookie', cookie);
  return response;
}

function oauthErrorRedirect(request, error) {
  const url = new URL('/control-center', request.url);
  url.searchParams.set('whop', 'error');
  url.searchParams.set('message', String(error?.message || 'Whop login failed.').slice(0, 180));
  url.hash = 'methods';
  return new Response(null, {
    status: 302,
    headers: {
      location: url.toString(),
      'cache-control': 'no-store',
    },
  });
}

async function handleOAuthCallback(request) {
  if (request.method !== 'GET') return methodNotAllowed(['GET']);
  try {
    return await finishWhopOAuth(request);
  } catch (error) {
    return oauthErrorRedirect(request, error);
  }
}

async function handleSession(request) {
  if (request.method === 'GET') {
    const result = await readWhopSession(request);
    return attachCookie(json({
      configured: Boolean(process.env.WHOP_CLIENT_ID),
      connected: Boolean(result.session),
      session: whopSessionSummary(result.session),
    }), result.setCookie);
  }
  if (request.method === 'DELETE') {
    requireSameOrigin(request);
    const cookie = await revokeWhopSession(request);
    return attachCookie(json({ connected: false }), cookie);
  }
  return methodNotAllowed(['GET', 'DELETE']);
}

async function handleSources(request) {
  if (request.method !== 'GET') return methodNotAllowed(['GET']);
  const { session, setCookie } = await requireWhopSession(request);
  const result = await discoverWhopSources(session);
  return attachCookie(json(result), setCookie);
}

async function handleSourceDecision(request) {
  if (request.method === 'GET') {
    const policy = await readWhopSourcePolicy();
    return json({ sources: whopSourceOptions(policy.registry) });
  }
  if (request.method !== 'POST') return methodNotAllowed(['GET', 'POST']);
  requireSameOrigin(request);
  const body = await request.json().catch(() => ({}));
  const decision = String(body.decision || '').trim();
  const experienceIds = Array.isArray(body.experienceIds)
    ? [...new Set(body.experienceIds.map((value) => String(value || '').trim()).filter(Boolean))]
    : [];
  if (experienceIds.length > MAX_BULK_SOURCES) throw new HttpError(422, `Update at most ${MAX_BULK_SOURCES} Whop forums at once.`);

  const { session, setCookie } = await requireWhopSession(request);
  let result;
  let changedCount = 1;
  if (experienceIds.length) {
    let available = new Map();
    try {
      const discovery = await discoverWhopSources(session);
      available = new Map(discovery.sources.map((entry) => [entry.experience.id, entry]));
    } catch {
      // The advanced fallback remains usable even when membership discovery lacks permission.
    }

    const selected = await Promise.all(experienceIds.map(async (id) => {
      const discovered = available.get(id);
      if (discovered) return { experience: discovered.experience, experienceId: discovered.experience.id };
      const resolved = await resolveWhopExperience(session, { experienceId: id });
      return { experience: resolved.experience, experienceId: resolved.experienceId };
    }));
    result = await saveWhopSourceDecisions(selected, decision);
    changedCount = result.sources.length;
  } else {
    const { experience, experienceId } = await resolveWhopExperience(session, body);
    const single = await saveWhopSourceDecision(experience, experienceId, decision);
    result = { sources: [single.source], commit: single.commit };
  }

  const policy = await readWhopSourcePolicy();
  return attachCookie(json({
    ...result,
    source: result.sources[0] || null,
    sourceOptions: whopSourceOptions(policy.registry),
    message: decision === 'approved'
      ? `${changedCount} Whop forum${changedCount === 1 ? '' : 's'} approved for post review.`
      : `${changedCount} Whop forum${changedCount === 1 ? '' : 's'} disapproved and blocked from scans and imports.`,
  }), setCookie);
}

async function handleDiscover(request) {
  if (request.method !== 'POST') return methodNotAllowed(['POST']);
  requireSameOrigin(request);
  const body = await request.json().catch(() => ({}));
  const { session, setCookie } = await requireWhopSession(request);
  const result = await discoverWhopGuides(session, body);
  return attachCookie(json(result), setCookie);
}

async function handleImport(request) {
  if (request.method !== 'POST') return methodNotAllowed(['POST']);
  requireSameOrigin(request);
  const body = await request.json().catch(() => ({}));
  const sourceKeys = Array.isArray(body.sourceKeys)
    ? [...new Set(body.sourceKeys.map((value) => String(value || '').trim()).filter(Boolean))]
    : [];
  if (!sourceKeys.length) throw new HttpError(422, 'Approve at least one Whop post before importing.');
  if (sourceKeys.length > 50) throw new HttpError(422, 'Import at most 50 Whop posts at once.');

  const { session, setCookie } = await requireWhopSession(request);
  const discovery = await discoverWhopGuides(session, {
    experienceId: body.experienceId || body.source,
  });
  if (discovery.approvalRequired) throw new HttpError(403, 'Approve this Whop source before importing its posts.');

  const byKey = new Map(discovery.items.map((item) => [item.sourceKey, item]));
  const selected = sourceKeys.map((key) => byKey.get(key)).filter(Boolean);
  if (selected.length !== sourceKeys.length) {
    throw new HttpError(409, 'One or more approved posts changed or are no longer available. Scan the group again.');
  }
  if (selected.some((item) => item.integrity?.blocked)) {
    throw new HttpError(422, 'A selected post is blocked by the formatting integrity check. Review the scan results.');
  }

  const verifiedItems = await verifyWhopAttachments(session, selected);
  const result = await importWhopDrafts({
    category: body.category,
    rightsConfirmed: body.rightsConfirmed === true,
    items: verifiedItems,
  });
  return attachCookie(json({
    ...result,
    experience: discovery.experience,
    message: result.imported
      ? `${result.imported} approved Whop post${result.imported === 1 ? '' : 's'} imported as hidden drafts.`
      : 'Every approved Whop post is already up to date.',
  }), setCookie);
}

export default {
  async fetch(request) {
    const action = actionFrom(request);
    if (action === 'oauth-callback') return handleOAuthCallback(request);

    try {
      requireAuth(request);
      if (action === 'oauth-start') {
        if (request.method !== 'GET') return methodNotAllowed(['GET']);
        return beginWhopOAuth(request);
      }
      if (action === 'session') return await handleSession(request);
      if (action === 'sources') return await handleSources(request);
      if (action === 'source-decision') return await handleSourceDecision(request);
      if (action === 'discover') return await handleDiscover(request);
      if (action === 'import') return await handleImport(request);
      throw new HttpError(404, 'Unknown Whop Control Center action.');
    } catch (error) {
      return handleError(error);
    }
  },
};
