export const CATEGORY_ICON_DEFINITIONS = Object.freeze({
  loop: {
    label: 'Cashback loop',
    body: '<path d="M17.5 6.5A7 7 0 0 0 5.4 8"/><path d="m3 5 2.4 3L9 6"/><path d="M6.5 17.5A7 7 0 0 0 18.6 16"/><path d="m21 19-2.4-3-3.6 2"/>',
  },
  food: {
    label: 'Food',
    body: '<path d="M7 3v8"/><path d="M4 3v5a3 3 0 0 0 6 0V3"/><path d="M7 11v10"/><path d="M16 3v18"/><path d="M16 3c3 2 4 5 4 8h-4"/>',
  },
  tag: {
    label: 'Price tag',
    body: '<path d="M20 13 13 20l-9-9V4h7l9 9Z"/><circle cx="8.5" cy="8.5" r="1.25"/>',
  },
  spark: {
    label: 'Spark',
    body: '<path d="m12 3 1.4 4.1L17.5 8.5l-4.1 1.4L12 14l-1.4-4.1-4.1-1.4 4.1-1.4L12 3Z"/><path d="m18.5 14 .8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8.8-2.2Z"/>',
  },
  book: {
    label: 'Guide book',
    body: '<path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H11v17H6.5A2.5 2.5 0 0 0 4 22V5.5Z"/><path d="M20 5.5A2.5 2.5 0 0 0 17.5 3H13v17h4.5A2.5 2.5 0 0 1 20 22V5.5Z"/>',
  },
  shield: {
    label: 'Shield',
    body: '<path d="M12 3 5 6v5c0 4.4 2.6 8 7 10 4.4-2 7-5.6 7-10V6l-7-3Z"/><path d="m9 12 2 2 4-4"/>',
  },
  gamepad: {
    label: 'Game controller',
    body: '<path d="M8 12h4M10 10v4"/><path d="M16 11h.01M18 13h.01"/><path d="M7.2 6h9.6A5.2 5.2 0 0 1 22 11.2V15a3 3 0 0 1-5.3 1.9L15.2 15H8.8l-1.5 1.9A3 3 0 0 1 2 15v-3.8A5.2 5.2 0 0 1 7.2 6Z"/>',
  },
  dollar: {
    label: 'Dollar sign',
    body: '<circle cx="12" cy="12" r="9"/><path d="M16 8.5C15.2 7.5 14 7 12.5 7 10.4 7 9 8 9 9.5s1.3 2.1 3.5 2.5S16 13 16 14.5 14.6 17 12.5 17c-1.6 0-2.9-.5-3.8-1.5M12 5v14"/>',
  },
  percent: {
    label: 'Percent',
    body: '<path d="m19 5-14 14"/><circle cx="7" cy="7" r="2"/><circle cx="17" cy="17" r="2"/>',
  },
  wallet: {
    label: 'Wallet',
    body: '<path d="M4 6.5A2.5 2.5 0 0 1 6.5 4H18a2 2 0 0 1 2 2v13H6.5A2.5 2.5 0 0 1 4 16.5v-10Z"/><path d="M4 8h16"/><path d="M16 12h4v4h-4a2 2 0 0 1 0-4Z"/>',
  },
  coupon: {
    label: 'Coupon ticket',
    body: '<path d="M3 8a2 2 0 0 0 0 4v4h18v-4a2 2 0 0 0 0-4V4H3v4Z"/><path d="M13 4v12"/><path d="M8 8h.01M8 12h.01"/>',
  },
  cart: {
    label: 'Shopping cart',
    body: '<circle cx="9" cy="20" r="1"/><circle cx="18" cy="20" r="1"/><path d="M3 4h2l2.4 10.4A2 2 0 0 0 9.3 16H18a2 2 0 0 0 1.9-1.4L22 7H6"/>',
  },
  store: {
    label: 'Storefront',
    body: '<path d="M4 10v10h16V10"/><path d="M3 10h18L19 4H5l-2 6Z"/><path d="M7 10v2a2 2 0 0 0 4 0v-2M11 10v2a2 2 0 0 0 4 0v-2M15 10v2a2 2 0 0 0 4 0v-2"/><path d="M9 20v-5h6v5"/>',
  },
  gift: {
    label: 'Gift',
    body: '<rect x="3" y="9" width="18" height="12" rx="1"/><path d="M12 9v12M3 13h18"/><path d="M12 9H7.5A2.5 2.5 0 1 1 10 6.5L12 9Zm0 0h4.5A2.5 2.5 0 1 0 14 6.5L12 9Z"/>',
  },
  phone: {
    label: 'Phone',
    body: '<rect x="7" y="2" width="10" height="20" rx="2"/><path d="M10 5h4M11 18h2"/>',
  },
  bolt: {
    label: 'Lightning',
    body: '<path d="m13 2-8 12h7l-1 8 8-12h-7l1-8Z"/>',
  },
  package: {
    label: 'Package',
    body: '<path d="m12 3 8 4.5v9L12 21l-8-4.5v-9L12 3Z"/><path d="m4 7.5 8 4.5 8-4.5M12 12v9M8 5.2l8 4.5"/>',
  },
  key: {
    label: 'Key',
    body: '<circle cx="8" cy="15" r="4"/><path d="m11 12 9-9M16 7l2 2M14 9l2 2"/>',
  },
  coffee: {
    label: 'Coffee cup',
    body: '<path d="M4 8h13v7a5 5 0 0 1-5 5H9a5 5 0 0 1-5-5V8Z"/><path d="M17 10h1.5a2.5 2.5 0 0 1 0 5H17M7 3v2M11 3v2M15 3v2"/>',
  },
  burger: {
    label: 'Burger',
    body: '<path d="M4 11h16M5 8a7 5 0 0 1 14 0v1H5V8ZM4 14h16v2a3 3 0 0 1-3 3H7a3 3 0 0 1-3-3v-2Z"/><path d="M7 11v3M12 11v3M17 11v3"/>',
  },
  card: {
    label: 'Payment card',
    body: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 10h18M7 15h4"/>',
  },
  globe: {
    label: 'Web or global',
    body: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>',
  },
  truck: {
    label: 'Delivery truck',
    body: '<path d="M3 6h11v10H3V6ZM14 10h4l3 3v3h-7v-6Z"/><circle cx="7" cy="18" r="2"/><circle cx="18" cy="18" r="2"/>',
  },
  link: {
    label: 'Link',
    body: '<path d="M10 13a5 5 0 0 0 7.1 0l2-2a5 5 0 0 0-7.1-7.1l-1.1 1.1"/><path d="M14 11a5 5 0 0 0-7.1 0l-2 2A5 5 0 0 0 12 20.1l1.1-1.1"/>',
  },
});

export const CATEGORY_ICON_KEYS = Object.freeze(Object.keys(CATEGORY_ICON_DEFINITIONS));
export const CATEGORY_ICON_OPTIONS = Object.freeze(
  CATEGORY_ICON_KEYS.map((value) => Object.freeze({ value, label: CATEGORY_ICON_DEFINITIONS[value].label })),
);
export const DEFAULT_CATEGORY_ICON = 'tag';

const PATH_DATA_PATTERN = /^[MmLlHhVvCcSsQqTtAaZzEe0-9,.+\-\s]+$/;

function normalizeViewBox(value) {
  const parts = String(value || '')
    .replace(/,/g, ' ')
    .trim()
    .split(/\s+/)
    .map(Number);
  if (parts.length !== 4 || parts.some((part) => !Number.isFinite(part))) return '';
  const [x, y, width, height] = parts;
  if (width <= 0 || height <= 0 || width > 4096 || height > 4096) return '';
  if ([x, y].some((part) => Math.abs(part) > 4096)) return '';
  return parts.map((part) => Number(part.toFixed(4))).join(' ');
}

function normalizePathData(value) {
  const path = String(value || '').trim().replace(/\s+/g, ' ');
  if (!path || path.length > 1500 || !PATH_DATA_PATTERN.test(path)) return '';
  return path;
}

export function sanitizeCustomIconDefinition(input) {
  if (!input || typeof input !== 'object') return null;
  const viewBox = normalizeViewBox(input.viewBox);
  const incomingPaths = Array.isArray(input.paths) ? input.paths : [];
  const paths = incomingPaths.map(normalizePathData).filter(Boolean).slice(0, 12);
  if (!viewBox || paths.length === 0) return null;
  if (paths.reduce((total, path) => total + path.length, 0) > 8000) return null;
  return { viewBox, paths };
}

export function isCategoryIconPreset(value) {
  return Object.hasOwn(CATEGORY_ICON_DEFINITIONS, String(value || ''));
}

function escapeAttribute(value) {
  return String(value).replace(/[&<>"']/g, (character) => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  })[character]);
}

export function categoryIconSvgMarkup(icon, customIcon = null, className = 'category-icon-svg') {
  const cleanCustom = icon === 'custom' ? sanitizeCustomIconDefinition(customIcon) : null;
  const preset = CATEGORY_ICON_DEFINITIONS[icon] || CATEGORY_ICON_DEFINITIONS[DEFAULT_CATEGORY_ICON];
  const viewBox = cleanCustom?.viewBox || '0 0 24 24';
  const body = cleanCustom
    ? cleanCustom.paths.map((path) => `<path d="${escapeAttribute(path)}"/>`).join('')
    : preset.body;
  return `<svg class="${escapeAttribute(className)}" viewBox="${escapeAttribute(viewBox)}" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" preserveAspectRatio="xMidYMid meet" aria-hidden="true">${body}</svg>`;
}

function numericSvgDimension(value) {
  const parsed = Number.parseFloat(String(value || '').replace(/px$/i, ''));
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

export function parseCustomIconSvg(svgText) {
  if (typeof DOMParser === 'undefined') throw new Error('SVG upload is not supported in this browser.');
  const document = new DOMParser().parseFromString(String(svgText || ''), 'image/svg+xml');
  if (document.querySelector('parsererror')) throw new Error('That file is not valid SVG.');
  const root = document.documentElement;
  if (root?.localName?.toLowerCase() !== 'svg') throw new Error('Choose an SVG file.');
  if (root.querySelector('[transform]')) throw new Error('Use a simple outline SVG without transforms.');

  let viewBox = root.getAttribute('viewBox') || '';
  if (!viewBox) {
    const width = numericSvgDimension(root.getAttribute('width'));
    const height = numericSvgDimension(root.getAttribute('height'));
    if (width && height) viewBox = `0 0 ${width} ${height}`;
  }

  const paths = [...root.querySelectorAll('path')].map((path) => path.getAttribute('d') || '');
  const clean = sanitizeCustomIconDefinition({ viewBox, paths });
  if (!clean) {
    throw new Error('Use a path-based outline SVG with a valid viewBox. Up to 12 paths are supported.');
  }
  return clean;
}
