import siteSettings from './data/site-settings.json';
import {
  CATEGORY_ICON_DEFINITIONS,
  CATEGORY_ICON_KEYS,
  DEFAULT_CATEGORY_ICON,
  sanitizeCustomIconDefinition,
} from './lib/category-icons.js';

export type CategoryIcon = keyof typeof CATEGORY_ICON_DEFINITIONS | 'custom';
export type CategoryAccent = 'red' | 'lime' | 'cyan' | 'amber' | 'violet';

export interface CustomCategoryIcon {
  viewBox: string;
  paths: string[];
}

export interface CategoryDefinition {
  label: string;
  shortLabel: string;
  description: string;
  visible: boolean;
  order: number;
  icon: CategoryIcon;
  accent: CategoryAccent;
  customIcon?: CustomCategoryIcon;
}

const CATEGORY_ICONS = new Set<string>([...CATEGORY_ICON_KEYS, 'custom']);
const CATEGORY_ACCENTS = new Set<CategoryAccent>(['red', 'lime', 'cyan', 'amber', 'violet']);
const FALLBACK_CATEGORY: CategoryDefinition = {
  label: 'Other Methods',
  shortLabel: 'Other',
  description: 'Additional community methods and opportunities.',
  visible: true,
  order: 99,
  icon: DEFAULT_CATEGORY_ICON as CategoryIcon,
  accent: 'lime',
};

export const SITE_SETTINGS = siteSettings as Omit<typeof siteSettings, 'categories'> & {
  categories: Record<string, CategoryDefinition>;
};

export const SITE = {
  name: SITE_SETTINGS.branding.name,
  productName: SITE_SETTINGS.branding.productName,
  brandMark: SITE_SETTINGS.branding.brandMark,
  tagline: SITE_SETTINGS.branding.tagline,
  title: SITE_SETTINGS.seo.title,
  description: SITE_SETTINGS.seo.description,
  shareImage: SITE_SETTINGS.seo.shareImage,
  alertsUrl:
    SITE_SETTINGS.alerts.url ||
    import.meta.env.PUBLIC_ALERTS_URL ||
    'https://sniperplug.com/alerts/',
  siteUrl: import.meta.env.PUBLIC_SITE_URL ?? 'https://sniperplug.com',
} as const;

export const CATEGORIES: Record<string, CategoryDefinition> = Object.fromEntries(
  Object.entries(SITE_SETTINGS.categories).map(([key, category]) => {
    const requestedIcon = CATEGORY_ICONS.has(category.icon)
      ? category.icon as CategoryIcon
      : FALLBACK_CATEGORY.icon;
    const customIcon = requestedIcon === 'custom'
      ? sanitizeCustomIconDefinition(category.customIcon) as CustomCategoryIcon | null
      : null;
    const icon = requestedIcon === 'custom' && !customIcon
      ? FALLBACK_CATEGORY.icon
      : requestedIcon;

    return [
      key,
      {
        label: String(category.label || key),
        shortLabel: String(category.shortLabel || category.label || key).slice(0, 20),
        description: String(category.description || FALLBACK_CATEGORY.description),
        visible: category.visible !== false,
        order: Number.isFinite(Number(category.order)) ? Number(category.order) : 99,
        icon,
        accent: CATEGORY_ACCENTS.has(category.accent) ? category.accent : FALLBACK_CATEGORY.accent,
        ...(customIcon ? { customIcon } : {}),
      },
    ];
  }),
);

export type CategoryKey = string;

export function getCategory(key: string): CategoryDefinition {
  return CATEGORIES[key] ?? {
    ...FALLBACK_CATEGORY,
    label: key
      .split('-')
      .filter(Boolean)
      .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
      .join(' ') || FALLBACK_CATEGORY.label,
  };
}

export const CATEGORY_ENTRIES = Object.entries(CATEGORIES)
  .filter(([, category]) => category.visible)
  .sort((a, b) => a[1].order - b[1].order || a[1].label.localeCompare(b[1].label));

export const THEME_PRESETS = {
  red: {
    accent: '#ff3b3b',
    rgb: '255 59 59',
    soft: 'rgb(255 59 59 / .10)',
    onAccent: '#100505',
  },
  lime: {
    accent: '#b7ff3c',
    rgb: '183 255 60',
    soft: 'rgb(183 255 60 / .09)',
    onAccent: '#080b07',
  },
  cyan: {
    accent: '#55dff4',
    rgb: '85 223 244',
    soft: 'rgb(85 223 244 / .09)',
    onAccent: '#061013',
  },
  amber: {
    accent: '#ffbd4a',
    rgb: '255 189 74',
    soft: 'rgb(255 189 74 / .09)',
    onAccent: '#120d03',
  },
  violet: {
    accent: '#a78bfa',
    rgb: '167 139 250',
    soft: 'rgb(167 139 250 / .09)',
    onAccent: '#0e0918',
  },
} as const;

export const ACTIVE_THEME = THEME_PRESETS[SITE_SETTINGS.theme.accentPreset as keyof typeof THEME_PRESETS]
  ?? THEME_PRESETS.red;
