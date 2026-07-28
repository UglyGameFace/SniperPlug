function effectiveGuideStatus(entry) {
  if (window.SniperPlugStatus?.effectiveStatus) return window.SniperPlugStatus.effectiveStatus(entry);
  if (!entry) return 'active';
  if (entry.expiresAt && Date.parse(entry.expiresAt) <= Date.now()) return 'expired';
  return entry.status || 'active';
}

function applyGuideNavigation(payload = {}) {
  const statuses = payload.statuses || {};
  const links = [...document.querySelectorAll('[data-guide-nav-id]')];

  links.forEach((link) => {
    const id = link.getAttribute('data-guide-nav-id');
    const status = effectiveGuideStatus(statuses[id]);
    link.hidden = status === 'paused' || status === 'expired';
    link.dataset.liveStatus = status;
  });
}

async function connectGuideNavigation() {
  const links = [...document.querySelectorAll('[data-guide-nav-id]')];
  if (!links.length) return;

  if (!window.SniperPlugStatus) {
    window.addEventListener('sniperplug-status-ready', connectGuideNavigation, { once: true });
    return;
  }

  window.SniperPlugStatus.subscribe(applyGuideNavigation);
  try {
    applyGuideNavigation(await window.SniperPlugStatus.get());
  } catch {
    // Static navigation remains available if live status cannot be loaded.
  }
}

function addControlCenterPublicButtons() {
  const actions = document.querySelector('.cc-command-actions');
  if (actions && !actions.querySelector('[data-public-page-shortcut]')) {
    const fragment = document.createDocumentFragment();
    const shortcuts = [
      { href: '/', icon: '⌂', label: 'Live site', className: 'cc-live-site-link' },
      { href: '/#library', icon: '▤', label: 'Guide library', className: '' },
    ];

    shortcuts.forEach((shortcut) => {
      const link = document.createElement('a');
      link.href = shortcut.href;
      link.target = '_blank';
      link.rel = 'noreferrer';
      link.className = shortcut.className;
      link.dataset.publicPageShortcut = '';
      link.innerHTML = `<span aria-hidden="true">${shortcut.icon}</span><b>${shortcut.label}</b>`;
      fragment.append(link);
    });

    actions.prepend(fragment);
  }

  const loginPanel = document.querySelector('[data-login-panel]');
  if (loginPanel && !loginPanel.querySelector('.desk-login-public-link')) {
    const link = document.createElement('a');
    link.href = '/';
    link.className = 'desk-login-public-link';
    link.textContent = '← Back to public website';
    loginPanel.append(link);
  }
}

function requestedOwnerIntent() {
  const params = new URLSearchParams(window.location.search);
  return params.get('intent') || '';
}

function showPostMethodLoginMessage() {
  if (requestedOwnerIntent() !== 'new-method') return;
  const loginPanel = document.querySelector('[data-login-panel]');
  if (!loginPanel) return;

  const kicker = loginPanel.querySelector('.desk-kicker');
  const heading = loginPanel.querySelector('h2');
  const copy = loginPanel.querySelector('p:not(.desk-inline-error)');
  if (kicker) kicker.textContent = 'Password-protected posting';
  if (heading) heading.textContent = 'Unlock to post a method';
  if (copy) copy.textContent = 'Enter the same owner password used for the Control Center. The new-method form opens only after authentication succeeds.';
}

let postIntentConsumed = false;
let postIntentObserver = null;

function clearPostMethodIntent() {
  const url = new URL(window.location.href);
  url.searchParams.delete('intent');
  url.hash = 'methods';
  history.replaceState({}, '', `${url.pathname}${url.search}${url.hash}`);
}

function consumePostMethodIntent() {
  if (postIntentConsumed || requestedOwnerIntent() !== 'new-method') return false;

  const root = document.querySelector('[data-control-center]');
  const app = document.querySelector('[data-desk-app]');
  const categoriesReady = Boolean(document.querySelector('[data-category-picker] [data-category]'));
  const newMethodButton = document.querySelector('[data-new-method]');
  const methodsTab = document.querySelector('[data-control-tab="methods"]');
  const unlocked = root?.classList.contains('is-unlocked') && app && !app.hidden;

  if (!unlocked || !categoriesReady || !(newMethodButton instanceof HTMLButtonElement)) return false;

  if (methodsTab instanceof HTMLButtonElement) methodsTab.click();
  newMethodButton.click();
  postIntentConsumed = true;
  clearPostMethodIntent();
  postIntentObserver?.disconnect();
  postIntentObserver = null;

  window.setTimeout(() => {
    const title = document.querySelector('[data-editor] input[name="title"]');
    if (title instanceof HTMLInputElement) title.focus();
  }, 80);
  return true;
}

function connectPasswordGatedPostIntent() {
  if (requestedOwnerIntent() !== 'new-method') return;
  showPostMethodLoginMessage();
  if (consumePostMethodIntent()) return;

  const root = document.querySelector('[data-control-center]');
  if (!root) return;
  postIntentObserver = new MutationObserver(() => consumePostMethodIntent());
  postIntentObserver.observe(root, {
    attributes: true,
    attributeFilter: ['class', 'hidden'],
    childList: true,
    subtree: true,
  });
}

addControlCenterPublicButtons();
connectGuideNavigation();
connectPasswordGatedPostIntent();
