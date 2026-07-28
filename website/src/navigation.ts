import { getCollection } from 'astro:content';
import type { CategoryKey } from './config';

export const PUBLIC_PAGE_LINKS = [
  {
    id: 'home',
    label: 'Home',
    description: 'Verified retail intelligence',
    href: '/',
    icon: 'home',
  },
  {
    id: 'library',
    label: 'Guide Library',
    description: 'Browse every active method',
    href: '/#library',
    icon: 'book',
  },
  {
    id: 'deals',
    label: 'Verified Deals',
    description: 'Current deal board',
    href: '/deals/',
    icon: 'bolt',
  },
  {
    id: 'partners',
    label: 'Partners',
    description: 'Retailer and data partnerships',
    href: '/partners/',
    icon: 'link',
  },
] as const;

export const POST_METHOD_LINK = {
  id: 'post-method',
  label: 'Post a Method',
  description: 'Password-protected owner form',
  href: '/control-center?intent=new-method#methods',
  icon: 'spark',
} as const;

export const OWNER_PAGE_LINK = {
  id: 'control-center',
  label: 'Owner Control Center',
  description: 'Private website editor',
  href: '/control-center',
  icon: 'shield',
} as const;

export const CONTROL_CENTER_SECTIONS = [
  { id: 'methods', number: '01', label: 'Methods', short: 'Guides & status', description: 'Manage active, expiring, paused, and expired guides.' },
  { id: 'homepage', number: '02', label: 'Homepage', short: 'Copy & sections', description: 'Edit the hero, terminal, library, and section visibility.' },
  { id: 'branding', number: '03', label: 'Branding', short: 'Name & identity', description: 'Change the brand name, product name, mark, and tagline.' },
  { id: 'navigation', number: '04', label: 'Navigation', short: 'Menus & labels', description: 'Edit labels used in the desktop sidebar and mobile menu.' },
  { id: 'categories', number: '05', label: 'Categories', short: 'Library structure', description: 'Rename, hide, describe, and reorder guide categories.' },
  { id: 'community', number: '06', label: 'Alerts', short: 'Destinations & links', description: 'Control the alerts URL and public calls to action.' },
  { id: 'footer-seo', number: '07', label: 'Footer & SEO', short: 'Search & sharing', description: 'Edit footer text, search metadata, and the social sharing image.' },
  { id: 'appearance', number: '08', label: 'Appearance', short: 'Color & density', description: 'Choose safe highlight and spacing presets for every screen size.' },
] as const;

export interface PublicGuideLink {
  id: string;
  label: string;
  description: string;
  href: string;
  category: CategoryKey;
  featured: boolean;
}

export async function getPublicGuideLinks(): Promise<PublicGuideLink[]> {
  const guides = await getCollection('hacks', ({ data }) => data.managed && !data.draft);
  return guides
    .sort((left, right) => Number(right.data.featured) - Number(left.data.featured) || left.data.order - right.data.order)
    .map((guide) => ({
      id: guide.id,
      label: guide.data.title,
      description: guide.data.description,
      href: `/guides/${guide.id}/`,
      category: guide.data.category as CategoryKey,
      featured: guide.data.featured,
    }));
}
