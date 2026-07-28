import type { APIRoute } from 'astro';
import { getCollection } from 'astro:content';
import { SITE } from '../config';

export const prerender = true;

function xml(value: string) {
  return value.replace(/[<>&"']/g, (character) => ({
    '<': '&lt;',
    '>': '&gt;',
    '&': '&amp;',
    '"': '&quot;',
    "'": '&apos;',
  })[character] || character);
}

function dateOnly(value: Date | undefined) {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date.toISOString().slice(0, 10);
}

export const GET: APIRoute = async () => {
  const guides = await getCollection('hacks', ({ data }) => data.managed && !data.draft);
  const entries = [
    { path: '/', lastmod: null },
    ...guides
      .sort((left, right) => left.id.localeCompare(right.id))
      .map((guide) => ({
        path: `/guides/${guide.id}/`,
        lastmod: dateOnly(guide.data.updated || guide.data.published),
      })),
  ];

  const body = entries.map(({ path, lastmod }) => {
    const location = xml(new URL(path, SITE.siteUrl).toString());
    return `  <url>\n    <loc>${location}</loc>${lastmod ? `\n    <lastmod>${xml(lastmod)}</lastmod>` : ''}\n  </url>`;
  }).join('\n');

  return new Response(
    `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n${body}\n</urlset>\n`,
    { headers: { 'content-type': 'application/xml; charset=utf-8' } },
  );
};
