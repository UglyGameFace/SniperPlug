import { HttpError, readRepoFile, writeRepoFile } from './deal-desk.js';
import {
  CATEGORY_ICON_KEYS,
  DEFAULT_CATEGORY_ICON,
  sanitizeCustomIconDefinition,
} from '../src/lib/category-icons.js';

export const SITE_SETTINGS_PATH = 'website/src/data/site-settings.json';
export const BUILTIN_CATEGORY_KEYS = ['deal-alerts', 'clearance-guides', 'store-guides', 'cashback-stacks', 'resale-opportunities'];
export const CATEGORY_ICON_PRESETS = [...CATEGORY_ICON_KEYS, 'custom'];
export const CATEGORY_ACCENT_PRESETS = ['red', 'lime', 'cyan', 'amber', 'violet'];

const CATEGORY_KEY = /^[a-z0-9](?:[a-z0-9-]{0,46}[a-z0-9])?$/;
const ACCENT_PRESETS = new Set(CATEGORY_ACCENT_PRESETS);
const ICON_PRESETS = new Set(CATEGORY_ICON_PRESETS);
const DENSITY_PRESETS = new Set(['compact', 'comfortable', 'spacious']);

function text(value, fallback, max = 240, min = 0) {
  const result = String(value ?? fallback ?? '').trim().slice(0, max);
  if (result.length < min) return String(fallback ?? '').trim().slice(0, max);
  return result;
}

function bool(value, fallback = false) {
  return typeof value === 'boolean' ? value : fallback;
}

function integer(value, fallback, min, max) {
  const parsed = Number.parseInt(value, 10);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.max(min, Math.min(max, parsed));
}

function safeUrl(value, fallback = '') {
  const raw = String(value ?? '').trim();
  if (!raw) return fallback;
  try {
    const parsed = new URL(raw);
    if (parsed.protocol !== 'https:') return fallback;
    return parsed.toString();
  } catch {
    return fallback;
  }
}

function trustItems(value, fallback) {
  const values = Array.isArray(value) ? value : [];
  const cleaned = values.map((item) => text(item, '', 48)).filter(Boolean).slice(0, 3);
  return cleaned.length === 3 ? cleaned : fallback;
}

export function safeCategoryKey(value) {
  const key = String(value ?? '').trim();
  return CATEGORY_KEY.test(key) ? key : '';
}

function categoryLabelFromKey(key) {
  return key
    .split('-')
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ')
    .slice(0, 48) || 'Custom Category';
}

function customCategoryFallback(key, order = 99) {
  const label = categoryLabelFromKey(key);
  return {
    label,
    shortLabel: label.slice(0, 20),
    description: `Methods organized under ${label}.`,
    visible: true,
    order,
    icon: DEFAULT_CATEGORY_ICON,
    accent: 'red',
  };
}

export function sanitizeCategoryDefinition(input, fallback, key = '') {
  const safeFallback = fallback || customCategoryFallback(key || 'custom-category');
  const fallbackIcon = ICON_PRESETS.has(safeFallback.icon) ? safeFallback.icon : DEFAULT_CATEGORY_ICON;
  const requestedIcon = ICON_PRESETS.has(input?.icon) ? input.icon : fallbackIcon;
  const fallbackCustomIcon = fallbackIcon === 'custom'
    ? sanitizeCustomIconDefinition(safeFallback.customIcon)
    : null;
  const requestedCustomIcon = requestedIcon === 'custom'
    ? sanitizeCustomIconDefinition(input?.customIcon)
    : null;
  const customIcon = requestedCustomIcon || fallbackCustomIcon;
  const icon = requestedIcon === 'custom' && !customIcon ? DEFAULT_CATEGORY_ICON : requestedIcon;

  const category = {
    label: text(input?.label, safeFallback.label, 48, 2),
    shortLabel: text(input?.shortLabel, safeFallback.shortLabel, 20, 1),
    description: text(input?.description, safeFallback.description, 180, 4),
    visible: bool(input?.visible, safeFallback.visible),
    order: integer(input?.order, safeFallback.order, 1, 99),
    icon,
    accent: ACCENT_PRESETS.has(input?.accent) ? input.accent : safeFallback.accent,
  };

  if (icon === 'custom' && customIcon) category.customIcon = customIcon;
  return category;
}

export const DEFAULT_SITE_SETTINGS = {
  version: 2,
  branding: {
    name: "SniperPlug",
    productName: "Deal Intelligence",
    brandMark: "SP",
    tagline: "Verify faster. Buy smarter. Catch better deals."
  },
  homepage: {
    eyebrow: "Verified retail intelligence from SniperPlug",
    headlinePrefix: "Find real deals.",
    headlineHighlight: "Verify the exact offer.",
    headlineSuffix: "Move before it disappears.",
    lead: "A fast, no-fluff library for checking markdowns, variants, coupons, cashback, local inventory, and resale value before you spend.",
    primaryCtaLabel: "Browse all guides",
    secondaryCtaLabel: "Get live deal alerts",
    trustItems: ["Variant-safe proof", "Official retailer links", "Clear savings math"],
    showTerminal: true,
    terminalTitle: "SNIPERPLUG_INTEL.exe",
    terminalStatus: "READY",
    showCategories: true,
    showFeatured: true,
    featuredMinimum: 3,
    libraryKicker: "Intel database",
    libraryTitle: "All deal and shopping guides",
    libraryDescription: "Search by retailer, product, offer type, or verification method.",
    showSearch: true,
    showAlertsBanner: true
  },
  navigation: {
    browseLabel: "Browse",
    allHacksLabel: "All Guides",
    allHacksDescription: "Complete intelligence library"
  },
  categories: {
    "deal-alerts": {
      label: "Deal Alerts",
      shortLabel: "Deals",
      description: "Verify fast-moving price drops, coupons, and promotions before checkout.",
      visible: true,
      order: 1,
      icon: "bolt",
      accent: "red"
    },
    "clearance-guides": {
      label: "Clearance Guides",
      shortLabel: "Clearance",
      description: "Check local markdowns, store-specific prices, inventory, and condition.",
      visible: true,
      order: 2,
      icon: "tag",
      accent: "amber"
    },
    "store-guides": {
      label: "Store Guides",
      shortLabel: "Stores",
      description: "Retailer-specific workflows for finding and validating the exact offer.",
      visible: true,
      order: 3,
      icon: "store",
      accent: "cyan"
    },
    "cashback-stacks": {
      label: "Cashback Stacks",
      shortLabel: "Cashback",
      description: "Combine eligible rewards and coupons without hiding the real final cost.",
      visible: true,
      order: 4,
      icon: "wallet",
      accent: "lime"
    },
    "resale-opportunities": {
      label: "Resale Opportunities",
      shortLabel: "Resale",
      description: "Evaluate demand, fees, condition, sell-through, and downside before buying.",
      visible: true,
      order: 5,
      icon: "package",
      accent: "violet"
    }
  },
  alerts: {
    url: "https://sniperplug.com/alerts/",
    sidebarStatus: "Deal feed online",
    sidebarTitle: "Get the newest verified finds.",
    sidebarDescription: "Use SniperPlug alerts for fresh price drops, local markdowns, and fast-moving opportunities.",
    sidebarButtonLabel: "Get deal alerts",
    mobileDescription: "Fresh verified finds, price drops, and local markdowns.",
    bannerKicker: "The guide is step one",
    bannerTitle: "See fresh SniperPlug deal alerts.",
    bannerDescription: "Use the library for evergreen verification methods. Use alerts for current prices, stock changes, and time-sensitive opportunities.",
    bannerButtonLabel: "Open deal alerts"
  },
  footer: {
    description: "Verified retail intelligence, deal-checking workflows, and partner-safe shopping guides.",
    alertsButtonLabel: "Deal alerts"
  },
  seo: {
    title: "SniperPlug | Verified Deals & Retail Intelligence",
    description: "Verify retail deals, clearance prices, cashback stacks, local inventory, and exact product variants with SniperPlug.",
    shareImage: "/og-card.svg"
  },
  theme: {
    accentPreset: "red",
    density: "compact"
  }
};

export function sanitizeSiteSettings(input = {}) {
  const defaults = DEFAULT_SITE_SETTINGS;
  const settings = {
    version: 2,
    branding: {
      name: text(input.branding?.name, defaults.branding.name, 64, 2),
      productName: text(input.branding?.productName, defaults.branding.productName, 64, 2),
      brandMark: text(input.branding?.brandMark, defaults.branding.brandMark, 6, 1),
      tagline: text(input.branding?.tagline, defaults.branding.tagline, 140, 3),
    },
    homepage: {
      eyebrow: text(input.homepage?.eyebrow, defaults.homepage.eyebrow, 90, 3),
      headlinePrefix: text(input.homepage?.headlinePrefix, defaults.homepage.headlinePrefix, 70, 2),
      headlineHighlight: text(input.homepage?.headlineHighlight, defaults.homepage.headlineHighlight, 70, 2),
      headlineSuffix: text(input.homepage?.headlineSuffix, defaults.homepage.headlineSuffix, 70, 2),
      lead: text(input.homepage?.lead, defaults.homepage.lead, 320, 8),
      primaryCtaLabel: text(input.homepage?.primaryCtaLabel, defaults.homepage.primaryCtaLabel, 36, 2),
      secondaryCtaLabel: text(input.homepage?.secondaryCtaLabel, defaults.homepage.secondaryCtaLabel, 42, 2),
      trustItems: trustItems(input.homepage?.trustItems, defaults.homepage.trustItems),
      showTerminal: bool(input.homepage?.showTerminal, defaults.homepage.showTerminal),
      terminalTitle: text(input.homepage?.terminalTitle, defaults.homepage.terminalTitle, 40, 2),
      terminalStatus: text(input.homepage?.terminalStatus, defaults.homepage.terminalStatus, 20, 2),
      showCategories: bool(input.homepage?.showCategories, defaults.homepage.showCategories),
      showFeatured: bool(input.homepage?.showFeatured, defaults.homepage.showFeatured),
      featuredMinimum: integer(input.homepage?.featuredMinimum, defaults.homepage.featuredMinimum, 1, 12),
      libraryKicker: text(input.homepage?.libraryKicker, defaults.homepage.libraryKicker, 48, 2),
      libraryTitle: text(input.homepage?.libraryTitle, defaults.homepage.libraryTitle, 90, 3),
      libraryDescription: text(input.homepage?.libraryDescription, defaults.homepage.libraryDescription, 180, 3),
      showSearch: bool(input.homepage?.showSearch, defaults.homepage.showSearch),
      showAlertsBanner: bool(input.homepage?.showAlertsBanner, defaults.homepage.showAlertsBanner),
    },
    navigation: {
      browseLabel: text(input.navigation?.browseLabel, defaults.navigation.browseLabel, 24, 1),
      allHacksLabel: text(input.navigation?.allHacksLabel, defaults.navigation.allHacksLabel, 32, 2),
      allHacksDescription: text(input.navigation?.allHacksDescription, defaults.navigation.allHacksDescription, 80, 3),
    },
    categories: {},
    alerts: {
      url: safeUrl(input.alerts?.url, ''),
      sidebarStatus: text(input.alerts?.sidebarStatus, defaults.alerts.sidebarStatus, 42, 2),
      sidebarTitle: text(input.alerts?.sidebarTitle, defaults.alerts.sidebarTitle, 90, 3),
      sidebarDescription: text(input.alerts?.sidebarDescription, defaults.alerts.sidebarDescription, 220, 5),
      sidebarButtonLabel: text(input.alerts?.sidebarButtonLabel, defaults.alerts.sidebarButtonLabel, 36, 2),
      mobileDescription: text(input.alerts?.mobileDescription, defaults.alerts.mobileDescription, 160, 4),
      bannerKicker: text(input.alerts?.bannerKicker, defaults.alerts.bannerKicker, 48, 2),
      bannerTitle: text(input.alerts?.bannerTitle, defaults.alerts.bannerTitle, 110, 3),
      bannerDescription: text(input.alerts?.bannerDescription, defaults.alerts.bannerDescription, 260, 5),
      bannerButtonLabel: text(input.alerts?.bannerButtonLabel, defaults.alerts.bannerButtonLabel, 42, 2),
    },
    footer: {
      description: text(input.footer?.description, defaults.footer.description, 260, 5),
      alertsButtonLabel: text(input.footer?.alertsButtonLabel, defaults.footer.alertsButtonLabel, 36, 2),
    },
    seo: {
      title: text(input.seo?.title, defaults.seo.title, 70, 8),
      description: text(input.seo?.description, defaults.seo.description, 180, 20),
      shareImage: text(input.seo?.shareImage, defaults.seo.shareImage, 180, 1),
    },
    theme: {
      accentPreset: ACCENT_PRESETS.has(input.theme?.accentPreset) ? input.theme.accentPreset : defaults.theme.accentPreset,
      density: DENSITY_PRESETS.has(input.theme?.density) ? input.theme.density : defaults.theme.density,
    },
  };

  const incoming = input.categories && typeof input.categories === 'object' ? input.categories : {};
  const customKeys = Object.keys(incoming)
    .map(safeCategoryKey)
    .filter(Boolean)
    .filter((key) => !BUILTIN_CATEGORY_KEYS.includes(key));
  const keys = [...BUILTIN_CATEGORY_KEYS, ...new Set(customKeys)].slice(0, 30);

  for (const [index, key] of keys.entries()) {
    const fallback = defaults.categories[key] || customCategoryFallback(key, Math.min(99, index + 1));
    settings.categories[key] = sanitizeCategoryDefinition(incoming[key], fallback, key);
  }

  return settings;
}

export function serializeSiteSettings(settings) {
  return `${JSON.stringify(sanitizeSiteSettings(settings), null, 2)}\n`;
}

export async function readSiteSettings() {
  const file = await readRepoFile(SITE_SETTINGS_PATH, { allowMissing: true });
  if (!file.content.trim()) {
    return { sha: null, settings: structuredClone(DEFAULT_SITE_SETTINGS) };
  }
  try {
    return { sha: file.sha, settings: sanitizeSiteSettings(JSON.parse(file.content)) };
  } catch {
    throw new HttpError(502, 'The site settings file contains invalid JSON.');
  }
}

export async function writeSiteSettings(settings, sha) {
  const clean = sanitizeSiteSettings(settings);
  const result = await writeRepoFile(
    SITE_SETTINGS_PATH,
    `${JSON.stringify(clean, null, 2)}\n`,
    'SniperPlug Control Center: publish site settings',
    sha,
  );
  return {
    settings: clean,
    sha: result.content?.sha || null,
    commit: result.commit?.sha || null,
  };
}
