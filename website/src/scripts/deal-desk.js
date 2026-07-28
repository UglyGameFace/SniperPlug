import {
  categoryIconSvgMarkup,
  parseCustomIconSvg,
} from '../lib/category-icons.js';

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

const elements = {
  root: $('[data-deal-desk]'),
  login: $('[data-login-panel]'),
  loginForm: $('[data-login-form]'),
  loginError: $('[data-login-error]'),
  app: $('[data-desk-app]'),
  editor: $('[data-editor]'),
  empty: $('[data-empty-editor]'),
  selected: $('[data-selected-label]'),
  selectedLive: $('[data-selected-live]'),
  liveDot: $('[data-live-dot]'),
  liveLabel: $('[data-live-label]'),
  liveDetail: $('[data-live-detail]'),
  liveSummary: $('[data-live-control-summary]'),
  editorMode: $('[data-editor-mode]'),
  editorTitle: $('[data-editor-title]'),
  editorId: $('[data-editor-id]'),
  publicGuide: $('[data-open-public]'),
  quickControls: $('[data-quick-controls]'),
  picker: $('[data-picker-backdrop]'),
  pickerSearch: $('[data-picker-search]'),
  methodList: $('[data-method-list]'),
  pickerEmpty: $('[data-picker-empty]'),
  pickerCount: $('[data-picker-result-count]'),
  confirm: $('[data-confirm-backdrop]'),
  confirmTitle: $('[data-confirm-title]'),
  confirmCopy: $('[data-confirm-copy]'),
  confirmGo: $('[data-confirm-go]'),
  toast: $('[data-desk-toast]'),
  featureToggle: $('[data-feature-toggle]'),
  draftToggle: $('[data-draft-toggle]'),
  customExpiry: $('[data-custom-expiry]'),
  categoryPicker: $('[data-category-picker]'),
  categoryCreator: $('[data-method-category-creator]'),
  categoryCreateError: $('[data-method-category-error]'),
  categoryLabel: $('[data-method-category-label]'),
  categoryShort: $('[data-method-category-short]'),
  categoryDescription: $('[data-method-category-description]'),
  categoryIcon: $('[data-method-category-icon]'),
  categoryAccent: $('[data-method-category-accent]'),
  categoryCustomWrap: $('[data-method-category-custom-wrap]'),
  categoryCustomInput: $('[data-method-category-custom-icon]'),
  categoryCustomPreview: $('[data-method-category-custom-preview]'),
  categoryCustomStatus: $('[data-method-category-custom-status]'),
};

const state = {
  guides: [],
  publishedCategories: {},
  categories: {},
  pendingCategories: {},
  pendingMethodCustomIcon: null,
  selectedId: null,
  working: null,
  category: '',
  featured: false,
  draft: false,
  filter: 'all',
  confirmAction: null,
};

async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: 'same-origin',
    ...options,
    headers: {
      'content-type': 'application/json',
      ...(options.headers || {}),
    },
  });

  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 401) lockDesk();
    throw new Error(data.error || `Request failed (${response.status}).`);
  }
  return data;
}

function settingsRuntime() {
  if (!window.SniperPlugSettingsRuntime) {
    window.SniperPlugSettingsRuntime = {
      value: null,
      promise: null,
      async get(force = false) {
        if (!force && this.value) return this.value;
        if (!force && this.promise) return this.promise;
        this.promise = api('/api/control-center-settings', { method: 'GET', headers: {} })
          .then((output) => {
            this.value = output;
            window.dispatchEvent(new CustomEvent('sniperplug-settings-loaded', { detail: output }));
            return output;
          })
          .finally(() => { this.promise = null; });
        return this.promise;
      },
      set(output) {
        this.value = output;
        window.dispatchEvent(new CustomEvent('sniperplug-settings-loaded', { detail: output }));
      },
      clear() {
        this.value = null;
        this.promise = null;
      },
    };
  }
  return window.SniperPlugSettingsRuntime;
}

function notify(message, type = 'ok') {
  if (!elements.toast) return;
  elements.toast.textContent = message;
  elements.toast.dataset.type = type;
  elements.toast.hidden = false;
  clearTimeout(window.__dealDeskToast);
  window.__dealDeskToast = window.setTimeout(() => {
    elements.toast.hidden = true;
  }, 4200);
}

function lockDesk() {
  if (elements.app) elements.app.hidden = true;
  if (elements.login) elements.login.hidden = false;
  elements.root?.classList.remove('is-unlocked');
  settingsRuntime().clear();
  closePicker();
  closeConfirm();
}

function unlockDesk() {
  if (elements.login) elements.login.hidden = true;
  if (elements.app) elements.app.hidden = false;
  elements.root?.classList.add('is-unlocked');
}

function methodStatus(guide) {
  const live = guide?.live || {};
  if (live.expiresAt && Date.parse(live.expiresAt) <= Date.now()) return 'expired';
  return live.status || 'active';
}

function isExpiringSoon(guide) {
  const difference = guide?.live?.expiresAt
    ? Date.parse(guide.live.expiresAt) - Date.now()
    : 0;
  return methodStatus(guide) === 'active' && difference > 0 && difference <= 6 * 60 * 60 * 1000;
}

function statusGroup(guide) {
  const current = methodStatus(guide);
  if (current === 'expired') return 'expired';
  if (current === 'paused') return 'paused';
  if (isExpiringSoon(guide)) return 'expiring';
  return 'active';
}

function statusLabel(guide) {
  const group = statusGroup(guide);
  if (group === 'expiring') return 'Expiring soon';
  return group.charAt(0).toUpperCase() + group.slice(1);
}

function remainingLabel(iso) {
  const difference = Date.parse(iso) - Date.now();
  if (difference <= 0) return 'Expired now';
  const minutes = Math.ceil(difference / 60000);
  if (minutes < 60) return `Expires in ${minutes}m`;
  const hours = Math.ceil(minutes / 60);
  if (hours < 48) return `Expires in ${hours}h`;
  return `Expires in ${Math.ceil(hours / 24)}d`;
}

function localExpiryValue(iso) {
  if (!iso) return '';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '';
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
  return local.toISOString().slice(0, 16);
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>'"]/g, (character) => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    "'": '&#39;',
    '"': '&quot;',
  })[character]);
}

function categorySlug(value) {
  return String(value || '')
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 48);
}

function categoryEntries() {
  return Object.entries(state.categories)
    .sort((left, right) => Number(left[1].order) - Number(right[1].order) || String(left[1].label).localeCompare(String(right[1].label)));
}

function defaultCategoryKey() {
  if (state.categories['food-hacks']?.visible !== false) return 'food-hacks';
  return categoryEntries().find(([, category]) => category.visible !== false)?.[0]
    || categoryEntries()[0]?.[0]
    || '';
}

function categoryDefinition(key) {
  return state.categories[key] || {
    label: key.replaceAll('-', ' '),
    shortLabel: key.replaceAll('-', ' '),
    description: 'Custom method category.',
    visible: true,
    order: 99,
    icon: 'tag',
    accent: 'lime',
  };
}

function selectCategory(key) {
  if (!key || !state.categories[key]) return;
  state.category = key;
  $$('[data-category]', elements.categoryPicker).forEach((button) => {
    const active = button.dataset.category === key;
    button.classList.toggle('active', active);
    button.setAttribute('aria-pressed', String(active));
  });
}

function renderCategoryPicker() {
  if (!elements.categoryPicker) return;
  const entries = categoryEntries().filter(([key, category]) => category.visible !== false || key === state.category);
  elements.categoryPicker.innerHTML = entries.map(([key, category]) => `
    <button type="button" class="accent-${escapeHtml(category.accent || 'lime')}" data-category="${escapeHtml(key)}" aria-pressed="${key === state.category}">
      <span>${categoryIconSvgMarkup(category.icon, category.customIcon, 'desk-category-icon-svg')}</span>
      <strong>${escapeHtml(category.label)}</strong>
      <small>${escapeHtml(category.description)}${category.visible === false ? ' · hidden category' : ''}</small>
    </button>
  `).join('');
  $$('[data-category]', elements.categoryPicker).forEach((button) => {
    button.addEventListener('click', () => selectCategory(button.dataset.category));
  });
  selectCategory(state.category || defaultCategoryKey());
}

function consumePublishedSettings(event) {
  const categories = event.detail?.settings?.categories;
  if (!categories || typeof categories !== 'object') return;
  state.publishedCategories = structuredClone(categories);
  state.categories = {
    ...structuredClone(state.publishedCategories),
    ...structuredClone(state.pendingCategories),
  };
  if (!state.category || !state.categories[state.category]) state.category = defaultCategoryKey();
  renderCategoryPicker();
  renderPicker();
}

function consumeSettingsCategoryDraft(event) {
  if (event.detail?.source !== 'settings-editor') return;
  const categories = event.detail?.categories;
  if (!categories || typeof categories !== 'object') return;

  const localPending = structuredClone(state.pendingCategories);
  for (const [key, definition] of Object.entries(categories)) {
    if (!state.publishedCategories[key]) localPending[key] = structuredClone(definition);
  }
  state.pendingCategories = localPending;
  state.categories = {
    ...structuredClone(categories),
    ...structuredClone(localPending),
  };
  if (!state.category || !state.categories[state.category]) state.category = defaultCategoryKey();
  renderCategoryPicker();
  renderPicker();
}

function setMethodCustomStatus(message, type = 'ok') {
  if (!elements.categoryCustomStatus) return;
  elements.categoryCustomStatus.textContent = message;
  elements.categoryCustomStatus.dataset.type = type;
  elements.categoryCustomStatus.hidden = false;
}

function syncMethodCustomControls() {
  const isCustom = elements.categoryIcon?.value === 'custom';
  if (elements.categoryCustomWrap) elements.categoryCustomWrap.hidden = !isCustom;
  if (elements.categoryCustomPreview) {
    elements.categoryCustomPreview.innerHTML = categoryIconSvgMarkup(
      isCustom ? 'custom' : elements.categoryIcon?.value || 'tag',
      state.pendingMethodCustomIcon,
      'cc-category-icon-svg',
    );
  }
  if (elements.categoryCustomStatus) {
    elements.categoryCustomStatus.hidden = !isCustom;
    if (isCustom) {
      setMethodCustomStatus(
        state.pendingMethodCustomIcon
          ? 'Custom SVG ready and fitted to the site icon slot.'
          : 'Choose a path-based outline SVG.',
        state.pendingMethodCustomIcon ? 'ok' : 'muted',
      );
    }
  }
}

async function readCustomIconFile(input) {
  const file = input?.files?.[0];
  if (!file) throw new Error('Choose an SVG file.');
  if (file.size > 64 * 1024) throw new Error('Keep the SVG under 64 KB.');
  return parseCustomIconSvg(await file.text());
}

async function loadMethodCustomIcon() {
  try {
    state.pendingMethodCustomIcon = await readCustomIconFile(elements.categoryCustomInput);
    syncMethodCustomControls();
    setMethodCustomStatus('Custom SVG ready and fitted to the site icon slot.');
  } catch (error) {
    state.pendingMethodCustomIcon = null;
    syncMethodCustomControls();
    setMethodCustomStatus(error.message, 'error');
  }
}

function showCategoryCreator(show) {
  if (elements.categoryCreator) elements.categoryCreator.hidden = !show;
  if (elements.categoryCreateError) elements.categoryCreateError.hidden = true;
  syncMethodCustomControls();
  if (show) elements.categoryLabel?.focus();
}

function clearCategoryCreator() {
  if (elements.categoryLabel) elements.categoryLabel.value = '';
  if (elements.categoryShort) elements.categoryShort.value = '';
  if (elements.categoryDescription) elements.categoryDescription.value = '';
  if (elements.categoryIcon) elements.categoryIcon.value = 'tag';
  if (elements.categoryAccent) elements.categoryAccent.value = 'lime';
  if (elements.categoryCustomInput) elements.categoryCustomInput.value = '';
  if (elements.categoryCreateError) elements.categoryCreateError.hidden = true;
  state.pendingMethodCustomIcon = null;
  syncMethodCustomControls();
}

function createPendingCategory() {
  const label = elements.categoryLabel?.value.trim() || '';
  const key = categorySlug(label);
  const shortLabel = elements.categoryShort?.value.trim() || label.slice(0, 20);
  const description = elements.categoryDescription?.value.trim() || '';
  const icon = elements.categoryIcon?.value || 'tag';
  const error = elements.categoryCreateError;

  const fail = (message) => {
    if (error) {
      error.textContent = message;
      error.hidden = false;
    }
  };

  if (label.length < 2) return fail('Enter a category name.');
  if (!key) return fail('That name cannot create a safe category key.');
  if (state.categories[key]) return fail('A category with that name already exists.');
  if (description.length < 4) return fail('Add a short description so members know what belongs here.');
  if (icon === 'custom' && !state.pendingMethodCustomIcon) return fail('Choose a valid custom SVG or select a preset icon.');

  const orders = categoryEntries().map(([, category]) => Number(category.order) || 0);
  const definition = {
    label: label.slice(0, 48),
    shortLabel: shortLabel.slice(0, 20) || label.slice(0, 20),
    description: description.slice(0, 180),
    visible: true,
    order: Math.min(99, Math.max(1, ...orders) + 1),
    icon,
    accent: elements.categoryAccent?.value || 'lime',
    ...(icon === 'custom' ? { customIcon: structuredClone(state.pendingMethodCustomIcon) } : {}),
  };

  state.categories[key] = definition;
  state.pendingCategories[key] = definition;
  state.category = key;
  renderCategoryPicker();
  window.dispatchEvent(new CustomEvent('sniperplug-category-draft-change', {
    detail: {
      source: 'method-editor',
      key,
      definition: structuredClone(definition),
    },
  }));
  clearCategoryCreator();
  showCategoryCreator(false);
  notify(`${definition.label} added to the shared site draft. Save the method to publish both together.`);
}

function filterCounts() {
  return {
    all: state.guides.length,
    active: state.guides.filter((guide) => statusGroup(guide) === 'active').length,
    expiring: state.guides.filter((guide) => statusGroup(guide) === 'expiring').length,
    paused: state.guides.filter((guide) => statusGroup(guide) === 'paused').length,
    expired: state.guides.filter((guide) => statusGroup(guide) === 'expired').length,
  };
}

function renderStats() {
  const counts = filterCounts();
  for (const key of ['active', 'expiring', 'paused', 'expired']) {
    const count = $(`[data-stat-${key}]`);
    if (count) count.textContent = String(counts[key]);
  }

  $$('[data-filter-count]').forEach((count) => {
    count.textContent = String(counts[count.dataset.filterCount] ?? 0);
  });
}

function syncFilterControls() {
  $$('[data-picker-filter]').forEach((button) => {
    const selected = button.dataset.pickerFilter === state.filter;
    button.classList.toggle('active', selected);
    button.setAttribute('aria-pressed', String(selected));
  });

  $$('[data-stat-filter]').forEach((button) => {
    const selected = button.dataset.statFilter === state.filter;
    button.classList.toggle('is-selected', selected);
    button.setAttribute('aria-pressed', String(selected));
  });
}

function setPickerFilter(filter, { open = false } = {}) {
  const allowed = new Set(['all', 'active', 'expiring', 'paused', 'expired']);
  state.filter = allowed.has(filter) ? filter : 'all';
  syncFilterControls();
  renderPicker();
  if (open) openPicker();
}

function methodOptionMarkup(guide) {
  const category = categoryDefinition(guide.category);
  return `<button type="button" class="desk-method-option status-${methodStatus(guide)}${isExpiringSoon(guide) ? ' is-expiring' : ''}" data-method-id="${escapeHtml(guide.id)}">
    <span class="method-status-dot"></span>
    <span class="method-option-copy">
      <small>${escapeHtml(category.label)}</small>
      <strong>${escapeHtml(guide.title)}</strong>
      <em>${escapeHtml(guide.description)}</em>
    </span>
    <span class="method-option-meta">
      <b>${escapeHtml(statusLabel(guide))}</b>
      <small>${guide.live?.expiresAt ? escapeHtml(remainingLabel(guide.live.expiresAt)) : 'No auto-expiration'}</small>
      <i>Choose →</i>
    </span>
  </button>`;
}

function renderPicker() {
  if (!elements.methodList) return;

  const search = elements.pickerSearch?.value.trim().toLowerCase() || '';
  const searched = state.guides.filter((guide) => {
    const category = categoryDefinition(guide.category);
    const haystack = [
      guide.title,
      guide.description,
      guide.id,
      guide.category,
      category.label,
      category.shortLabel,
      ...(guide.keywords || []),
    ].join(' ').toLowerCase();
    return !search || haystack.includes(search);
  });

  const definitions = [
    ['active', 'Active'],
    ['expiring', 'Expiring soon'],
    ['paused', 'Paused'],
    ['expired', 'Expired'],
  ];
  const visibleDefinitions = state.filter === 'all'
    ? definitions
    : definitions.filter(([key]) => key === state.filter);

  const groups = visibleDefinitions
    .map(([key, title]) => ({
      key,
      title,
      rows: searched.filter((guide) => statusGroup(guide) === key),
    }))
    .filter((group) => group.rows.length > 0);

  const total = groups.reduce((sum, group) => sum + group.rows.length, 0);
  elements.methodList.innerHTML = groups.map((group) => `
    <section class="desk-method-group" data-method-group="${group.key}">
      <div class="desk-method-group-title"><span>${escapeHtml(group.title)}</span><b>${group.rows.length}</b></div>
      <div class="desk-method-group-list">${group.rows.map(methodOptionMarkup).join('')}</div>
    </section>
  `).join('');

  if (elements.pickerCount) elements.pickerCount.textContent = `${total} method${total === 1 ? '' : 's'}`;
  if (elements.pickerEmpty) elements.pickerEmpty.hidden = total !== 0;

  $$('[data-method-id]', elements.methodList).forEach((button) => {
    button.addEventListener('click', () => selectMethod(button.dataset.methodId));
  });
}

function setToggle(button, active, activeLabel, inactiveLabel) {
  if (!button) return;
  button.classList.toggle('active', active);
  button.setAttribute('aria-pressed', String(active));
  const label = $('b', button);
  if (label) label.textContent = active ? activeLabel : inactiveLabel;
}

function renderLiveStatus(guide) {
  const isNew = !guide.id;
  if (elements.quickControls) elements.quickControls.hidden = isNew;
  if (elements.selectedLive) elements.selectedLive.hidden = isNew;
  if (isNew) return;

  const current = methodStatus(guide);
  if (elements.selectedLive) elements.selectedLive.dataset.status = current;
  if (elements.liveDot) elements.liveDot.dataset.status = current;
  if (elements.liveLabel) elements.liveLabel.textContent = statusLabel(guide);

  const details = [];
  if (guide.live?.expiresAt) details.push(remainingLabel(guide.live.expiresAt));
  if (guide.live?.verifiedAt) details.push(`Verified ${new Date(guide.live.verifiedAt).toLocaleString()}`);

  if (elements.liveDetail) elements.liveDetail.textContent = details.join(' • ') || 'No expiration set';
  if (elements.liveSummary) {
    elements.liveSummary.textContent = guide.live?.expiresAt
      ? remainingLabel(guide.live.expiresAt)
      : statusLabel(guide);
  }
  if (elements.customExpiry) elements.customExpiry.value = localExpiryValue(guide.live?.expiresAt);
}

function populateEditor(guide) {
  if (!(elements.editor instanceof HTMLFormElement)) return;

  state.working = structuredClone(guide);
  state.selectedId = guide.id || null;
  state.category = state.categories[guide.category] ? guide.category : defaultCategoryKey();
  state.featured = Boolean(guide.featured);
  state.draft = Boolean(guide.draft);

  if (elements.empty) elements.empty.hidden = true;
  elements.editor.hidden = false;
  if (elements.selected) elements.selected.textContent = guide.title || 'New method';
  if (elements.editorMode) elements.editorMode.textContent = guide.id ? 'Edit method' : 'Create method';
  if (elements.editorTitle) elements.editorTitle.textContent = guide.title || 'New method';
  if (elements.editorId) {
    elements.editorId.textContent = guide.id
      ? `Guide ID: ${guide.id}`
      : 'A guide ID will be generated from the title.';
  }

  const fields = elements.editor.elements;
  const values = {
    title: guide.title || '',
    description: guide.description || '',
    badge: guide.badge || '',
    readTime: guide.readTime || '5 min',
    keywords: (guide.keywords || []).join(', '),
    order: String(guide.order ?? 999),
    body: guide.body || '',
  };

  for (const [name, value] of Object.entries(values)) {
    const field = fields.namedItem(name);
    if (field) field.value = value;
  }

  renderCategoryPicker();
  setToggle(elements.featureToggle, state.featured, 'Featured', 'Not featured');
  setToggle(elements.draftToggle, state.draft, 'Hidden draft', 'Public');
  renderLiveStatus(guide);
  clearCategoryCreator();
  showCategoryCreator(false);

  if (elements.publicGuide) {
    elements.publicGuide.hidden = !guide.id;
    elements.publicGuide.onclick = guide.id
      ? () => window.open(`/guides/${guide.id}/`, '_blank', 'noopener')
      : null;
  }
}

function selectMethod(id) {
  const guide = state.guides.find((item) => item.id === id);
  if (!guide) return;
  populateEditor(guide);
  closePicker();
  elements.editor?.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function createNewMethod() {
  populateEditor({
    id: '',
    title: '',
    description: '',
    category: defaultCategoryKey(),
    featured: false,
    draft: false,
    badge: 'New',
    keywords: [],
    published: new Date().toISOString().slice(0, 10),
    readTime: '4 min',
    order: 20,
    body: '## The deal\n\nDescribe the method.\n\n## Steps\n\n1. Add the first step.\n2. Add the next step.\n\n## Important notes\n\nAdd expiration details and restrictions.',
    live: { status: 'active', expiresAt: null, verifiedAt: null },
  });
  closePicker();
  elements.editor?.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function openPicker() {
  if (!elements.picker) return;
  syncFilterControls();
  renderPicker();
  elements.picker.hidden = false;
  document.body.classList.add('desk-modal-open');
  window.setTimeout(() => elements.pickerSearch?.focus(), 30);
}

function closePicker() {
  if (elements.picker) elements.picker.hidden = true;
  if (!elements.confirm || elements.confirm.hidden) document.body.classList.remove('desk-modal-open');
}

function openConfirm(title, copy, action, buttonText = 'Confirm') {
  state.confirmAction = action;
  if (elements.confirmTitle) elements.confirmTitle.textContent = title;
  if (elements.confirmCopy) elements.confirmCopy.textContent = copy;
  if (elements.confirmGo) elements.confirmGo.textContent = buttonText;
  if (elements.confirm) elements.confirm.hidden = false;
  document.body.classList.add('desk-modal-open');
}

function closeConfirm() {
  state.confirmAction = null;
  if (elements.confirm) elements.confirm.hidden = true;
  if (!elements.picker || elements.picker.hidden) document.body.classList.remove('desk-modal-open');
}

async function setLiveStatus(status, expiresAt = null, verifiedAt = null) {
  if (!state.selectedId) return;
  const output = await api('/api/deal-desk-status', {
    method: 'POST',
    body: JSON.stringify({ id: state.selectedId, status, expiresAt, verifiedAt }),
  });

  const guide = state.guides.find((item) => item.id === state.selectedId);
  if (guide) guide.live = output.live;
  if (state.working) state.working.live = output.live;

  renderStats();
  renderPicker();
  renderLiveStatus(state.working);
  notify(`${state.working?.title || 'Method'} is now ${status}.`);
}

async function loadGuides() {
  const [guideOutput, settingsOutput] = await Promise.all([
    api('/api/deal-desk-guides'),
    settingsRuntime().get(),
  ]);
  state.guides = guideOutput.guides || [];
  state.publishedCategories = structuredClone(settingsOutput.settings?.categories || {});
  state.categories = {
    ...structuredClone(state.publishedCategories),
    ...structuredClone(state.pendingCategories),
  };
  state.pendingMethodCustomIcon = null;
  if (!state.category || !state.categories[state.category]) state.category = defaultCategoryKey();
  renderCategoryPicker();
  renderStats();
  syncFilterControls();
  renderPicker();

  if (state.selectedId) {
    const guide = state.guides.find((item) => item.id === state.selectedId);
    if (guide) populateEditor(guide);
  }
}

elements.loginForm?.addEventListener('submit', async (event) => {
  event.preventDefault();
  if (elements.loginError) elements.loginError.hidden = true;
  const button = $('button[type="submit"]', elements.loginForm);
  if (button) {
    button.disabled = true;
    button.textContent = 'Unlocking...';
  }

  try {
    await api('/api/deal-desk-session', {
      method: 'POST',
      body: JSON.stringify({ password: new FormData(elements.loginForm).get('password') }),
    });
    unlockDesk();
    await loadGuides();
    elements.loginForm.reset();
  } catch (error) {
    if (elements.loginError) {
      elements.loginError.textContent = error.message;
      elements.loginError.hidden = false;
    }
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = 'Unlock Deal Desk';
    }
  }
});

$$('[data-open-picker]').forEach((button) => button.addEventListener('click', openPicker));
$$('[data-close-picker]').forEach((button) => button.addEventListener('click', closePicker));
$$('[data-new-method]').forEach((button) => button.addEventListener('click', createNewMethod));
elements.pickerSearch?.addEventListener('input', renderPicker);

$$('[data-picker-filter]').forEach((button) => {
  button.addEventListener('click', () => setPickerFilter(button.dataset.pickerFilter));
});

$$('[data-stat-filter]').forEach((button) => {
  button.addEventListener('click', () => setPickerFilter(button.dataset.statFilter, { open: true }));
});

$('[data-open-method-category]')?.addEventListener('click', () => showCategoryCreator(true));
$('[data-cancel-method-category]')?.addEventListener('click', () => {
  clearCategoryCreator();
  showCategoryCreator(false);
});
$('[data-create-method-category]')?.addEventListener('click', createPendingCategory);
elements.categoryIcon?.addEventListener('change', () => {
  if (elements.categoryIcon.value !== 'custom') state.pendingMethodCustomIcon = null;
  syncMethodCustomControls();
});
elements.categoryCustomInput?.addEventListener('change', loadMethodCustomIcon);

window.addEventListener('sniperplug-settings-loaded', consumePublishedSettings);
window.addEventListener('sniperplug-category-draft-change', consumeSettingsCategoryDraft);

elements.featureToggle?.addEventListener('click', () => {
  state.featured = !state.featured;
  setToggle(elements.featureToggle, state.featured, 'Featured', 'Not featured');
});

elements.draftToggle?.addEventListener('click', () => {
  state.draft = !state.draft;
  setToggle(elements.draftToggle, state.draft, 'Hidden draft', 'Public');
});

$$('[data-extend-hours]').forEach((button) => {
  button.addEventListener('click', async () => {
    try {
      const hours = Number(button.dataset.extendHours);
      await setLiveStatus('active', new Date(Date.now() + hours * 60 * 60 * 1000).toISOString(), new Date().toISOString());
    } catch (error) {
      notify(error.message, 'error');
    }
  });
});

$('[data-set-custom-expiry]')?.addEventListener('click', async () => {
  try {
    if (!elements.customExpiry?.value) throw new Error('Choose an expiration date and time.');
    const when = new Date(elements.customExpiry.value);
    if (Number.isNaN(when.getTime())) throw new Error('That expiration time is invalid.');
    if (when.getTime() <= Date.now()) throw new Error('Choose a future expiration time.');
    await setLiveStatus('active', when.toISOString(), new Date().toISOString());
  } catch (error) {
    notify(error.message, 'error');
  }
});

$('[data-clear-expiry]')?.addEventListener('click', async () => {
  try {
    await setLiveStatus('active', null, new Date().toISOString());
  } catch (error) {
    notify(error.message, 'error');
  }
});

$$('[data-status-action]').forEach((button) => {
  button.addEventListener('click', async () => {
    const action = button.dataset.statusAction;
    if (action === 'expire') {
      openConfirm(
        'Expire this method now?',
        `${state.working?.title || 'This method'} will disappear from the public library immediately.`,
        async () => {
          closeConfirm();
          try {
            await setLiveStatus('expired', new Date().toISOString(), state.working?.live?.verifiedAt || null);
          } catch (error) {
            notify(error.message, 'error');
          }
        },
        'Expire now',
      );
      return;
    }

    try {
      if (action === 'pause') {
        await setLiveStatus('paused', state.working?.live?.expiresAt || null, state.working?.live?.verifiedAt || null);
      }
      if (action === 'verify') {
        const futureExpiry = state.working?.live?.expiresAt
          && Date.parse(state.working.live.expiresAt) > Date.now()
          ? state.working.live.expiresAt
          : null;
        await setLiveStatus('active', futureExpiry, new Date().toISOString());
      }
    } catch (error) {
      notify(error.message, 'error');
    }
  });
});

elements.confirmGo?.addEventListener('click', () => state.confirmAction?.());
$('[data-confirm-back]')?.addEventListener('click', closeConfirm);

elements.editor?.addEventListener('submit', async (event) => {
  event.preventDefault();
  const form = new FormData(elements.editor);
  const saveButton = $('[data-save-method]');
  if (saveButton) {
    saveButton.disabled = true;
    saveButton.textContent = 'Saving...';
  }

  try {
    const output = await api('/api/deal-desk-save', {
      method: 'POST',
      body: JSON.stringify({
        id: state.selectedId || '',
        title: form.get('title'),
        description: form.get('description'),
        category: state.category,
        categoryDefinition: state.pendingCategories[state.category] || null,
        featured: state.featured,
        draft: state.draft,
        badge: form.get('badge'),
        readTime: form.get('readTime'),
        keywords: form.get('keywords'),
        order: form.get('order'),
        published: state.working?.published || new Date().toISOString().slice(0, 10),
        body: form.get('body'),
      }),
    });
    state.selectedId = output.guide.id;
    state.publishedCategories = structuredClone(output.categories || state.publishedCategories);
    state.categories = structuredClone(output.categories || state.categories);
    state.pendingCategories = {};
    state.pendingMethodCustomIcon = null;
    const runtimeValue = settingsRuntime().value || {};
    settingsRuntime().set({
      ...runtimeValue,
      settings: structuredClone(output.settings || {
        ...(runtimeValue.settings || {}),
        categories: state.categories,
      }),
      sha: output.settingsSha || runtimeValue.sha || null,
      commit: output.commit || runtimeValue.commit || null,
    });
    renderCategoryPicker();
    notify(output.message);
    await loadGuides();
  } catch (error) {
    notify(error.message, 'error');
  } finally {
    if (saveButton) {
      saveButton.disabled = false;
      saveButton.textContent = 'Save method';
    }
  }
});

$('[data-refresh]')?.addEventListener('click', () => {
  settingsRuntime().clear();
  loadGuides()
    .then(() => notify('Control Center refreshed.'))
    .catch((error) => notify(error.message, 'error'));
});

$('[data-logout]')?.addEventListener('click', async () => {
  try {
    await api('/api/deal-desk-session', { method: 'DELETE', body: '{}' });
  } catch {
    // Lock locally even if the request fails.
  }
  lockDesk();
});

elements.picker?.addEventListener('click', (event) => {
  if (event.target === elements.picker) closePicker();
});

elements.confirm?.addEventListener('click', (event) => {
  if (event.target === elements.confirm) closeConfirm();
});

document.addEventListener('keydown', (event) => {
  if (event.key !== 'Escape') return;
  if (elements.confirm && !elements.confirm.hidden) closeConfirm();
  else if (elements.categoryCreator && !elements.categoryCreator.hidden) showCategoryCreator(false);
  else closePicker();
});

syncMethodCustomControls();

(async () => {
  try {
    const session = await api('/api/deal-desk-session', { method: 'GET', headers: {} });
    if (!session.authenticated) {
      lockDesk();
      return;
    }
    unlockDesk();
    await loadGuides();
  } catch {
    lockDesk();
  }
})();
