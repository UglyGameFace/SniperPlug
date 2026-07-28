import { json, methodNotAllowed } from '../server/deal-desk.js';

function buildVersion() {
  return String(
    process.env.CF_PAGES_COMMIT_SHA
    || process.env.GITHUB_SHA
    || process.env.CF_PAGES_URL
    || 'local',
  ).slice(0, 12);
}

export default {
  async fetch(request) {
    if (request.method !== 'GET') return methodNotAllowed(['GET']);
    return json(
      {
        ok: true,
        service: 'sniperplug',
        buildVersion: buildVersion(),
        checkedAt: new Date().toISOString(),
      },
      200,
      { 'cache-control': 'public, max-age=0, s-maxage=30, stale-while-revalidate=60' },
    );
  },
};
