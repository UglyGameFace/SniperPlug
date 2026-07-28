import {
  createCipheriv,
  createDecipheriv,
  createHash,
  randomBytes,
} from 'node:crypto';
import { HttpError } from './deal-desk.js';

const OAUTH_BASE = 'https://api.whop.com/oauth';
const STATE_COOKIE = 'sniperplug_whop_oauth_state';
const SESSION_COOKIE = 'sniperplug_whop_session';
const OAUTH_STATE_TTL_SECONDS = 10 * 60;
const SESSION_TTL_SECONDS = 30 * 24 * 60 * 60;
const REFRESH_BUFFER_MS = 5 * 60 * 1000;
const REQUEST_TIMEOUT_MS = 20_000;
const DEFAULT_SCOPES = 'openid profile email forum:read member:basic:read member:email:read';

function oauthSecret() {
  const secret = process.env.WHOP_TOKEN_SECRET
    || process.env.DEAL_DESK_SESSION_SECRET
    || process.env.DEAL_DESK_PASSWORD
    || '';
  if (!secret) throw new HttpError(503, 'WHOP_TOKEN_SECRET or the Control Center session secret is not configured.');
  return createHash('sha256').update(secret, 'utf8').digest();
}

function oauthConfig(request) {
  const clientId = String(process.env.WHOP_CLIENT_ID || '').trim();
  if (!clientId) throw new HttpError(503, 'WHOP_CLIENT_ID is not configured in Cloudflare Pages.');
  const origin = new URL(request.url).origin;
  const redirectUri = String(process.env.WHOP_REDIRECT_URI || `${origin}/api/whop-oauth-callback`).trim();
  const scopes = String(process.env.WHOP_OAUTH_SCOPES || DEFAULT_SCOPES).trim();
  const companyId = String(process.env.WHOP_COMPANY_ID || '').trim();
  return { clientId, redirectUri, scopes, companyId };
}

function base64url(value) {
  return Buffer.from(value).toString('base64url');
}

function seal(value) {
  const iv = randomBytes(12);
  const cipher = createCipheriv('aes-256-gcm', oauthSecret(), iv);
  const plaintext = Buffer.from(JSON.stringify(value), 'utf8');
  const encrypted = Buffer.concat([cipher.update(plaintext), cipher.final()]);
  const tag = cipher.getAuthTag();
  return [base64url(iv), base64url(tag), base64url(encrypted)].join('.');
}

function unseal(value) {
  try {
    const [ivPart, tagPart, encryptedPart] = String(value || '').split('.', 3);
    if (!ivPart || !tagPart || !encryptedPart) return null;
    const decipher = createDecipheriv('aes-256-gcm', oauthSecret(), Buffer.from(ivPart, 'base64url'));
    decipher.setAuthTag(Buffer.from(tagPart, 'base64url'));
    const decrypted = Buffer.concat([
      decipher.update(Buffer.from(encryptedPart, 'base64url')),
      decipher.final(),
    ]);
    return JSON.parse(decrypted.toString('utf8'));
  } catch {
    return null;
  }
}

function cookieValue(request, name) {
  const cookie = request.headers.get('cookie') || '';
  for (const part of cookie.split(';')) {
    const [key, ...rest] = part.trim().split('=');
    if (key === name) return rest.join('=');
  }
  return '';
}

function secureCookie(name, value, maxAge, sameSite = 'Lax') {
  return `${name}=${value}; Path=/; HttpOnly; Secure; SameSite=${sameSite}; Max-Age=${maxAge}`;
}

function clearCookie(name) {
  return `${name}=; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=0`;
}

function randomToken(size = 32) {
  return randomBytes(size).toString('base64url');
}

function pkceChallenge(verifier) {
  return createHash('sha256').update(verifier, 'utf8').digest('base64url');
}

async function whopRequest(url, options = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  let response;
  try {
    response = await fetch(url, {
      ...options,
      cache: 'no-store',
      signal: controller.signal,
    });
  } catch (error) {
    if (controller.signal.aborted || error?.name === 'AbortError') {
      throw new HttpError(504, 'Whop did not respond in time. Try again.');
    }
    throw error;
  } finally {
    clearTimeout(timer);
  }

  const text = await response.text();
  let payload = null;
  try { payload = text ? JSON.parse(text) : null; } catch { payload = text; }
  if (!response.ok) {
    const message = payload?.error_description
      || payload?.error?.message
      || payload?.message
      || `Whop request failed (${response.status}).`;
    const status = response.status === 401
      ? 401
      : response.status === 403
        ? 403
        : response.status === 404
          ? 404
          : response.status === 429
            ? 503
            : response.status >= 500
              ? 502
              : 422;
    throw new HttpError(status, message, payload);
  }
  return payload;
}

function normalizeTokens(payload, previous = null) {
  const accessToken = String(payload?.access_token || '').trim();
  const refreshToken = String(payload?.refresh_token || previous?.refreshToken || '').trim();
  const expiresIn = Math.max(60, Number(payload?.expires_in) || 3600);
  if (!accessToken || !refreshToken) throw new HttpError(502, 'Whop did not return a complete OAuth session.');
  return {
    accessToken,
    refreshToken,
    tokenType: String(payload?.token_type || 'Bearer'),
    scope: String(payload?.scope || previous?.scope || ''),
    expiresIn,
    obtainedAt: Date.now(),
    companyId: previous?.companyId || '',
    user: previous?.user || null,
  };
}

async function userInfo(accessToken) {
  return whopRequest(`${OAUTH_BASE}/userinfo`, {
    headers: { authorization: `Bearer ${accessToken}` },
  });
}

async function refreshSession(session, config) {
  const payload = await whopRequest(`${OAUTH_BASE}/token`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({
      grant_type: 'refresh_token',
      refresh_token: session.refreshToken,
      client_id: config.clientId,
      ...(session.companyId && { company_id: session.companyId }),
    }),
  });
  const refreshed = normalizeTokens(payload, session);
  refreshed.user = session.user || await userInfo(refreshed.accessToken);
  return refreshed;
}

export function beginWhopOAuth(request) {
  const config = oauthConfig(request);
  const verifier = randomToken(48);
  const state = randomToken(24);
  const nonce = randomToken(24);
  const pending = {
    verifier,
    state,
    nonce,
    redirectUri: config.redirectUri,
    companyId: config.companyId,
    expiresAt: Date.now() + OAUTH_STATE_TTL_SECONDS * 1000,
  };
  const params = new URLSearchParams({
    response_type: 'code',
    client_id: config.clientId,
    redirect_uri: config.redirectUri,
    scope: config.scopes,
    state,
    nonce,
    code_challenge: pkceChallenge(verifier),
    code_challenge_method: 'S256',
    ...(config.companyId && { company_id: config.companyId }),
  });
  return new Response(null, {
    status: 302,
    headers: {
      location: `${OAUTH_BASE}/authorize?${params}`,
      'cache-control': 'no-store',
      'set-cookie': secureCookie(STATE_COOKIE, seal(pending), OAUTH_STATE_TTL_SECONDS),
    },
  });
}

export async function finishWhopOAuth(request) {
  const config = oauthConfig(request);
  const url = new URL(request.url);
  const error = url.searchParams.get('error');
  if (error) {
    throw new HttpError(422, url.searchParams.get('error_description') || `Whop OAuth failed: ${error}`);
  }

  const code = String(url.searchParams.get('code') || '').trim();
  const returnedState = String(url.searchParams.get('state') || '').trim();
  const pending = unseal(cookieValue(request, STATE_COOKIE));
  if (!pending || Number(pending.expiresAt) <= Date.now()) throw new HttpError(401, 'The Whop login request expired. Start again.');
  if (!code || !returnedState || returnedState !== pending.state) throw new HttpError(403, 'Whop login state validation failed.');
  if (pending.redirectUri !== config.redirectUri) throw new HttpError(403, 'Whop callback URL validation failed.');

  const payload = await whopRequest(`${OAUTH_BASE}/token`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({
      grant_type: 'authorization_code',
      code,
      redirect_uri: config.redirectUri,
      client_id: config.clientId,
      code_verifier: pending.verifier,
    }),
  });
  const session = normalizeTokens(payload);
  session.companyId = pending.companyId || '';
  session.user = await userInfo(session.accessToken);

  const headers = new Headers({
    location: '/control-center?whop=connected#methods',
    'cache-control': 'no-store',
  });
  headers.append('set-cookie', secureCookie(SESSION_COOKIE, seal(session), SESSION_TTL_SECONDS));
  headers.append('set-cookie', clearCookie(STATE_COOKIE));
  return new Response(null, { status: 302, headers });
}

export async function readWhopSession(request, { refresh = true } = {}) {
  const session = unseal(cookieValue(request, SESSION_COOKIE));
  if (!session?.accessToken || !session?.refreshToken) return { session: null, setCookie: null };
  const expiresAt = Number(session.obtainedAt || 0) + Number(session.expiresIn || 0) * 1000;
  if (!refresh || Date.now() < expiresAt - REFRESH_BUFFER_MS) return { session, setCookie: null };

  try {
    const refreshed = await refreshSession(session, oauthConfig(request));
    return {
      session: refreshed,
      setCookie: secureCookie(SESSION_COOKIE, seal(refreshed), SESSION_TTL_SECONDS),
    };
  } catch (error) {
    if (error instanceof HttpError && error.status === 401) {
      return { session: null, setCookie: clearCookie(SESSION_COOKIE) };
    }
    throw error;
  }
}

export async function requireWhopSession(request) {
  const result = await readWhopSession(request);
  if (!result.session) throw new HttpError(401, 'Connect your Whop account first.');
  return result;
}

export async function revokeWhopSession(request) {
  const { session } = await readWhopSession(request, { refresh: false });
  if (session?.refreshToken) {
    const config = oauthConfig(request);
    try {
      await whopRequest(`${OAUTH_BASE}/revoke`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ token: session.refreshToken, client_id: config.clientId }),
      });
    } catch {
      // Clear the local encrypted session even if Whop is temporarily unavailable.
    }
  }
  return clearCookie(SESSION_COOKIE);
}

export function whopSessionSummary(session) {
  if (!session) return null;
  return {
    user: session.user ? {
      id: session.user.sub || null,
      name: session.user.name || null,
      username: session.user.preferred_username || null,
      picture: session.user.picture || null,
      email: session.user.email || null,
    } : null,
    scope: String(session.scope || '').split(/\s+/).filter(Boolean),
    expiresAt: new Date(Number(session.obtainedAt) + Number(session.expiresIn) * 1000).toISOString(),
    companyId: session.companyId || null,
  };
}
