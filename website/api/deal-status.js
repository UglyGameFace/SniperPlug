import {
  handleError,
  json,
  methodNotAllowed,
  normalizeStatus,
  readStatusDocument,
} from '../server/deal-desk.js';

function deployedBuildVersion() {
  const environment = globalThis.process?.env || {};
  return String(
    environment.CF_PAGES_COMMIT_SHA
    || environment.GITHUB_SHA
    || environment.CF_PAGES_URL
    || 'local',
  ).slice(0, 12);
}

export default {
  async fetch(request) {
    try {
      if (request.method !== 'GET') return methodNotAllowed(['GET']);
      const document = await readStatusDocument({ publicRead: true });
      const statuses = Object.fromEntries(
        Object.entries(document.entries).map(([id, entry]) => [id, normalizeStatus(entry)]),
      );
      const buildVersion = deployedBuildVersion();
      return json(
        { statuses, checkedAt: new Date().toISOString(), buildVersion },
        200,
        {
          'cache-control': 'public, max-age=0, s-maxage=15, stale-while-revalidate=120',
          'x-status-revision': document.sha || 'empty',
          'x-site-build': buildVersion,
        },
      );
    } catch (error) {
      return handleError(error);
    }
  },
};
