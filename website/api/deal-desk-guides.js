import {
  handleError,
  json,
  listGuideFiles,
  methodNotAllowed,
  normalizeStatus,
  parseGuideFile,
  readRepoFile,
  readStatusDocument,
  requireAuth,
} from '../server/deal-desk.js';

const SAFE_GUIDE_ID = /^[a-z0-9](?:[a-z0-9-]{0,70}[a-z0-9])?$/;

async function registeredGuideIds(statusEntries) {
  const ids = Object.keys(statusEntries || {}).filter((id) => SAFE_GUIDE_ID.test(id));
  if (ids.length) return { ids, source: 'status-registry' };

  // Recovery path for an older/manual repository that has not registered statuses yet.
  const files = await listGuideFiles();
  return {
    ids: files.map((file) => file.name.replace(/\.mdx?$/i, '')).filter((id) => SAFE_GUIDE_ID.test(id)),
    source: 'directory-recovery',
  };
}

export default {
  async fetch(request) {
    try {
      if (request.method !== 'GET') return methodNotAllowed(['GET']);
      requireAuth(request);

      const statusDoc = await readStatusDocument();
      const registry = await registeredGuideIds(statusDoc.entries);

      const parsedGuides = await Promise.all(registry.ids.map(async (id) => {
        const raw = await readRepoFile(`website/src/content/hacks/${id}.md`, { allowMissing: true });
        if (!raw.content.trim()) return null;
        const guide = parseGuideFile(id, raw.content);
        if (!guide.managed) return null;
        return {
          ...guide,
          sha: raw.sha,
          live: normalizeStatus(statusDoc.entries[id]),
        };
      }));

      const guides = parsedGuides.filter(Boolean);
      guides.sort((a, b) => {
        const rank = { active: 0, paused: 1, expired: 2 };
        return (rank[a.live.status] - rank[b.live.status]) || a.order - b.order || a.title.localeCompare(b.title);
      });

      return json({
        guides,
        source: registry.source,
        loadedAt: new Date().toISOString(),
      });
    } catch (error) {
      return handleError(error);
    }
  },
};
