import {
  guidePath,
  handleError,
  HttpError,
  json,
  methodNotAllowed,
  normalizeStatus,
  parseGuideFile,
  readRepoFile,
  readStatusDocument,
  requireAuth,
  requireSameOrigin,
  slugify,
  writeStatusDocument,
} from '../server/deal-desk.js';

const ALLOWED = new Set(['active', 'paused', 'expired']);

function optionalIso(value, label) {
  if (!value) return null;
  const parsed = Date.parse(String(value));
  if (!Number.isFinite(parsed)) throw new HttpError(422, `${label} is invalid.`);
  return new Date(parsed).toISOString();
}

async function requireManagedGuide(id) {
  const file = await readRepoFile(guidePath(id), { allowMissing: true });
  if (!file.content.trim()) throw new HttpError(404, 'That method no longer exists. Refresh the Control Center.');
  const guide = parseGuideFile(id, file.content);
  if (!guide.managed) throw new HttpError(422, 'That guide is not managed by the Control Center.');
}

export default {
  async fetch(request) {
    try {
      if (request.method !== 'POST') return methodNotAllowed(['POST']);
      requireSameOrigin(request);
      requireAuth(request);

      const body = await request.json().catch(() => ({}));
      const id = slugify(body.id);
      const status = String(body.status || '');
      if (!id) throw new HttpError(422, 'Choose a method first.');
      if (!ALLOWED.has(status)) throw new HttpError(422, 'Invalid method status.');
      await requireManagedGuide(id);

      let expiresAt = optionalIso(body.expiresAt, 'Expiration time');
      const requestedVerifiedAt = optionalIso(body.verifiedAt, 'Verification time');
      if (status === 'expired') expiresAt = expiresAt || new Date().toISOString();

      for (let attempt = 0; attempt < 2; attempt += 1) {
        const document = await readStatusDocument();
        const next = {
          status,
          expiresAt,
          verifiedAt: requestedVerifiedAt || document.entries[id]?.verifiedAt || null,
          note: String(body.note || document.entries[id]?.note || '').slice(0, 240),
        };
        document.entries[id] = next;

        try {
          await writeStatusDocument(document.entries, document.sha, `Deal Desk: ${id} → ${status}`);
          return json({ id, live: normalizeStatus(next) });
        } catch (error) {
          if (!(error instanceof HttpError) || error.status !== 409 || attempt > 0) throw error;
        }
      }

      throw new HttpError(409, 'The live-status registry changed while saving. Try again.');
    } catch (error) {
      return handleError(error);
    }
  },
};
