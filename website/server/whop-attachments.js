import { HttpError } from './deal-desk.js';

const API_BASE = 'https://api.whop.com/api/v1';
const REQUEST_TIMEOUT_MS = 15_000;
const MAX_ATTACHMENTS_PER_BATCH = 250;
const CONCURRENCY = 6;

async function retrieveFile(session, id) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  let response;
  try {
    response = await fetch(`${API_BASE}/files/${encodeURIComponent(id)}`, {
      headers: { authorization: `Bearer ${session.accessToken}` },
      cache: 'no-store',
      signal: controller.signal,
    });
  } catch (error) {
    if (controller.signal.aborted || error?.name === 'AbortError') {
      return { id, durable: false, reviewReason: 'Whop file verification timed out.' };
    }
    return { id, durable: false, reviewReason: 'Whop file verification failed.' };
  } finally {
    clearTimeout(timer);
  }

  const text = await response.text();
  let payload = null;
  try { payload = text ? JSON.parse(text) : null; } catch { payload = null; }
  if (!response.ok || !payload) {
    return {
      id,
      durable: false,
      reviewReason: response.status === 403
        ? 'The connected Whop account cannot verify this attachment.'
        : 'This Whop attachment is no longer available.',
    };
  }

  const visibility = String(payload.visibility || '').toLowerCase();
  const url = /^https:\/\//i.test(String(payload.url || '')) ? String(payload.url) : null;
  const ready = String(payload.upload_status || '').toLowerCase() === 'ready';
  return {
    id: String(payload.id || id),
    filename: String(payload.filename || 'attachment'),
    contentType: String(payload.content_type || ''),
    size: Number(payload.size) || null,
    visibility: visibility || 'unknown',
    uploadStatus: String(payload.upload_status || 'unknown'),
    url,
    durable: visibility === 'public' && ready && Boolean(url),
    reviewReason: visibility === 'private'
      ? 'This is a private Whop file with an expiring signed URL. Re-upload it to SniperPlug before publishing.'
      : !ready
        ? 'This Whop file is not ready yet. Re-scan later or upload it manually.'
        : !url
          ? 'Whop did not return a usable attachment URL.'
          : visibility !== 'public'
            ? 'Whop did not confirm that this attachment URL is permanent.'
            : null,
  };
}

async function mapConcurrent(values, mapper) {
  const output = new Array(values.length);
  let cursor = 0;
  async function worker() {
    while (cursor < values.length) {
      const index = cursor;
      cursor += 1;
      output[index] = await mapper(values[index], index);
    }
  }
  await Promise.all(Array.from({ length: Math.min(CONCURRENCY, values.length) }, () => worker()));
  return output;
}

export async function verifyWhopAttachments(session, items) {
  const sourceItems = Array.isArray(items) ? items : [];
  const ids = [...new Set(sourceItems.flatMap((item) => (
    Array.isArray(item.attachments) ? item.attachments : []
  )).map((attachment) => String(attachment?.id || '').trim()).filter(Boolean))];
  if (ids.length > MAX_ATTACHMENTS_PER_BATCH) {
    throw new HttpError(422, `Review fewer posts at once; this batch contains more than ${MAX_ATTACHMENTS_PER_BATCH} attachments.`);
  }

  const details = await mapConcurrent(ids, (id) => retrieveFile(session, id));
  const byId = new Map(details.map((file) => [file.id, file]));
  return sourceItems.map((item) => ({
    ...item,
    attachments: (Array.isArray(item.attachments) ? item.attachments : []).map((attachment) => {
      const id = String(attachment?.id || '').trim();
      const verified = byId.get(id);
      if (!verified) {
        return {
          ...attachment,
          visibility: 'unknown',
          durable: false,
          reviewReason: 'This attachment could not be verified. Re-upload it before publishing.',
        };
      }
      return {
        ...attachment,
        ...verified,
        filename: verified.filename || attachment.filename || 'attachment',
        contentType: verified.contentType || attachment.contentType || '',
      };
    }),
  }));
}
