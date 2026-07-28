import {
  handleError,
  json,
  methodNotAllowed,
  readCommitDeploymentStatus,
  requireAuth,
  requireSameOrigin,
} from '../server/deal-desk.js';

export default {
  async fetch(request) {
    try {
      if (request.method !== 'GET') return methodNotAllowed(['GET']);
      requireSameOrigin(request);
      requireAuth(request);
      const commit = new URL(request.url).searchParams.get('commit') || '';
      return json(await readCommitDeploymentStatus(commit));
    } catch (error) {
      return handleError(error);
    }
  },
};
