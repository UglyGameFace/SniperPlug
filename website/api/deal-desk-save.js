import {
  composeGuideFile,
  guidePath,
  handleError,
  HttpError,
  json,
  listGuideFiles,
  methodNotAllowed,
  normalizeStatus,
  parseGuideFile,
  readRepoFile,
  readStatusDocument,
  requireAuth,
  requireSameOrigin,
  statusFileContent,
  validateGuide,
  writeRepoFiles,
} from '../server/deal-desk.js';
import {
  assertGuideBodyRoundTrip,
  GuideContentIntegrityError,
  prepareGuideBody,
} from '../server/guide-content-integrity.js';
import { resolveAutomaticMethodOrder } from '../server/method-order.js';
import {
  readSiteSettings,
  safeCategoryKey,
  sanitizeCategoryDefinition,
  sanitizeSiteSettings,
  serializeSiteSettings,
  SITE_SETTINGS_PATH,
} from '../server/site-settings.js';

async function managedGuideOrders(excludeId) {
  const files = await listGuideFiles();
  const orders = await Promise.all(files.map(async (file) => {
    const id = file.name.replace(/\.mdx?$/i, '');
    if (!id || id === excludeId) return null;

    try {
      const raw = await readRepoFile(`website/src/content/hacks/${id}.md`, { allowMissing: true });
      if (!raw.content.trim()) return null;
      const guide = parseGuideFile(id, raw.content);
      return guide.managed ? guide.order : null;
    } catch {
      return null;
    }
  }));
  return orders.filter((order) => Number.isFinite(Number(order)));
}

export default {
  async fetch(request) {
    try {
      if (request.method !== 'POST') return methodNotAllowed(['POST']);
      requireSameOrigin(request);
      requireAuth(request);

      const body = await request.json().catch(() => ({}));
      const preparedBody = prepareGuideBody(body.body, { source: 'Guide content' });
      const categoryKey = safeCategoryKey(body.category);
      const siteDocument = await readSiteSettings();
      let siteSettings = siteDocument.settings;
      let categoryCreated = false;

      if (categoryKey && !siteSettings.categories[categoryKey]) {
        if (!body.categoryDefinition || typeof body.categoryDefinition !== 'object') {
          throw new HttpError(422, 'That category is not registered yet. Add its details or choose another category.');
        }
        const definition = sanitizeCategoryDefinition(body.categoryDefinition, null, categoryKey);
        siteSettings = sanitizeSiteSettings({
          ...siteSettings,
          categories: {
            ...siteSettings.categories,
            [categoryKey]: definition,
          },
        });
        categoryCreated = true;
      }

      const provisionalGuide = validateGuide(
        { ...body, body: preparedBody.body, order: 0 },
        Object.keys(siteSettings.categories),
      );
      const path = guidePath(provisionalGuide.id);
      const [current, statusDocument] = await Promise.all([
        readRepoFile(path, { allowMissing: true }),
        readStatusDocument(),
      ]);

      if (!current.sha && body.id) {
        throw new HttpError(409, 'That guide no longer exists. Refresh the Control Center and try again.');
      }

      const existingOrder = current.content.trim()
        ? parseGuideFile(provisionalGuide.id, current.content).order
        : null;
      const otherOrders = existingOrder === null
        ? await managedGuideOrders(provisionalGuide.id)
        : [];
      const guide = {
        ...provisionalGuide,
        body: preparedBody.body,
        order: resolveAutomaticMethodOrder(existingOrder, otherOrders),
      };
      const guideFileContent = composeGuideFile(guide);
      const parsedRoundTrip = parseGuideFile(guide.id, guideFileContent);
      const verifiedBody = assertGuideBodyRoundTrip(preparedBody.body, parsedRoundTrip.body);

      const files = [{ path, content: guideFileContent }];
      if (!statusDocument.entries[guide.id]) {
        statusDocument.entries[guide.id] = {
          status: 'active',
          expiresAt: null,
          verifiedAt: new Date().toISOString(),
          note: '',
        };
        files.push({
          path: 'website/src/data/deal-status.json',
          content: statusFileContent(statusDocument.entries),
        });
      }
      if (categoryCreated) {
        files.push({
          path: SITE_SETTINGS_PATH,
          content: serializeSiteSettings(siteSettings),
        });
      }

      const action = current.sha ? 'Update' : 'Add';
      const result = await writeRepoFiles(
        files,
        categoryCreated
          ? `${action} method and category: ${guide.title}`
          : `${action} guide: ${guide.title}`,
      );
      const savedGuide = {
        ...guide,
        sha: result.files[path] || current.sha || null,
        live: normalizeStatus(statusDocument.entries[guide.id]),
      };

      return json({
        guide: savedGuide,
        settings: siteSettings,
        settingsSha: categoryCreated ? result.files[SITE_SETTINGS_PATH] : siteDocument.sha,
        categories: siteSettings.categories,
        categoryCreated,
        contentIntegrity: {
          fingerprint: verifiedBody.fingerprint,
          repairs: preparedBody.repairs,
          structure: verifiedBody.structure,
        },
        commit: result.commit?.sha || null,
        message: categoryCreated
          ? 'Category and method saved together. Cloudflare Pages is publishing both in one build.'
          : current.sha
            ? 'Method saved. Cloudflare Pages will rebuild the full guide page.'
            : 'Method created. Cloudflare Pages will publish it after the next build.',
      });
    } catch (error) {
      if (error instanceof GuideContentIntegrityError) {
        return handleError(new HttpError(422, error.message, {
          code: error.code,
          ...error.details,
        }));
      }
      return handleError(error);
    }
  },
};
