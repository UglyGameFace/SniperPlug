import controlCenterSettings from '../../api/control-center-settings.js';
import dealDeskGuides from '../../api/deal-desk-guides.js';
import dealDeskSave from '../../api/deal-desk-save.js';
import dealDeskSession from '../../api/deal-desk-session.js';
import dealDeskStatus from '../../api/deal-desk-status.js';
import dealStatus from '../../api/deal-status.js';
import deploymentStatus from '../../api/deployment-status.js';
import health from '../../api/health.js';
import whop from '../../api/whop.js';

const routes = new Map([
  ['control-center-settings', controlCenterSettings],
  ['deal-desk-guides', dealDeskGuides],
  ['deal-desk-save', dealDeskSave],
  ['deal-desk-session', dealDeskSession],
  ['deal-desk-status', dealDeskStatus],
  ['deal-status', dealStatus],
  ['deployment-status', deploymentStatus],
  ['health', health],
  ['whop', whop],
]);
const whopActions = new Map([
  ['whop-oauth-start', 'oauth-start'], ['whop-oauth-callback', 'oauth-callback'],
  ['whop-session', 'session'], ['whop-sources', 'sources'],
  ['whop-source-decision', 'source-decision'], ['whop-discover', 'discover'], ['whop-import', 'import'],
]);

export async function onRequest(context) {
  const segments = Array.isArray(context.params.path) ? context.params.path : [context.params.path];
  const route = String(segments.filter(Boolean)[0] || '');
  const handler = routes.get(route) || (whopActions.has(route) ? whop : null);
  if (!handler?.fetch) return new Response(JSON.stringify({ error: 'API route not found.' }), { status: 404, headers: { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' } });
  let request = context.request;
  const action = whopActions.get(route);
  if (action) {
    const url = new URL(request.url);
    url.searchParams.set('action', action);
    request = new Request(url, request);
  }
  return handler.fetch(request);
}
