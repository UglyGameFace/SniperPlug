import { activateFocusScope } from '../lib/focus-scope.js';

const STORAGE_KEY = 'sniperplug-owner-deployment-v1';
const POLL_INTERVAL_MS = 3500;
const REQUEST_TIMEOUT_MS = 12000;
const MAX_MONITOR_MS = 3 * 60 * 1000;
const COMMIT_SHA = /^[a-f0-9]{7,40}$/i;

const deploymentUi = {
  root: document.querySelector('[data-cc-deployment-state]'),
  label: document.querySelector('[data-cc-deployment-label]'),
  detail: document.querySelector('[data-cc-deployment-detail]'),
  link: document.querySelector('[data-cc-deployment-link]'),
};

let monitorToken = 0;
let activeCommit = '';
let deploymentPending = false;

function safeRead() {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === 'object' ? parsed : null;
  } catch {
    return null;
  }
}

function safeWrite(value) {
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(value));
  } catch {
    // Monitoring still works in memory when storage is unavailable.
  }
}

function safeRemove() {
  try {
    sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    // Ignore storage restrictions.
  }
}

function setDeploymentLock(locked) {
  deploymentPending = locked;
  document.documentElement.dataset.deploymentPending = String(locked);
  for (const element of document.querySelectorAll('[data-cc-publish], [data-save-method]')) {
    element.classList.toggle('is-deployment-locked', locked);
    element.setAttribute('aria-disabled', String(locked));
  }
}

function renderDeployment(state, label, detail, targetUrl = '') {
  if (!(deploymentUi.root instanceof HTMLElement)) return;
  deploymentUi.root.hidden = false;
  deploymentUi.root.dataset.state = state;
  if (deploymentUi.label) deploymentUi.label.textContent = label;
  if (deploymentUi.detail) deploymentUi.detail.textContent = detail;
  if (deploymentUi.link instanceof HTMLAnchorElement) {
    const safeTarget = /^https:\/\//i.test(targetUrl) ? targetUrl : '';
    deploymentUi.link.hidden = !safeTarget;
    if (safeTarget) deploymentUi.link.href = safeTarget;
    else deploymentUi.link.removeAttribute('href');
  }
}

async function requestDeployment(commit) {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    const response = await fetch(`/api/deployment-status?commit=${encodeURIComponent(commit)}`, {
      method: 'GET',
      credentials: 'same-origin',
      headers: { accept: 'application/json' },
      cache: 'no-store',
      signal: controller.signal,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(payload.error || `Deployment check failed (${response.status}).`);
      error.status = response.status;
      throw error;
    }
    return payload;
  } catch (error) {
    if (controller.signal.aborted || error?.name === 'AbortError') {
      throw new Error('Deployment status check timed out. Retrying…');
    }
    throw error;
  } finally {
    window.clearTimeout(timeout);
  }
}

function finishDeployment(state, detail, targetUrl = '') {
  monitorToken += 1;
  activeCommit = '';
  setDeploymentLock(false);
  safeRemove();
  if (state === 'success') {
    renderDeployment('success', 'Production passed', detail || 'The new version is live.', targetUrl);
  } else {
    renderDeployment('failure', 'Deployment needs attention', detail, targetUrl);
  }
}

async function pollDeployment(commit, startedAt, token) {
  if (token !== monitorToken || commit !== activeCommit) return;
  if (Date.now() - startedAt >= MAX_MONITOR_MS) {
    finishDeployment(
      'failure',
      'The repository save succeeded, but Cloudflare Pages did not report a result within three minutes. Open the deployment for details.',
    );
    return;
  }

  try {
    const output = await requestDeployment(commit);
    if (token !== monitorToken || commit !== activeCommit) return;
    const targetUrl = String(output.targetUrl || '');
    if (output.state === 'success') {
      finishDeployment('success', 'Cloudflare Pages finished successfully. Public pages will move to this build automatically.', targetUrl);
      return;
    }
    if (output.state === 'failure' || output.state === 'error') {
      finishDeployment(
        'failure',
        'The repository save succeeded, but the Cloudflare Pages production build failed. The previous public site remains online.',
        targetUrl,
      );
      return;
    }
    renderDeployment('pending', 'Publishing to production', output.description || 'Waiting for Cloudflare Pages to finish.', targetUrl);
  } catch (error) {
    if (token !== monitorToken || commit !== activeCommit) return;
    if (error?.status === 401) {
      finishDeployment('failure', 'Your Control Center session expired before deployment verification completed.');
      return;
    }
    renderDeployment('pending', 'Checking deployment', error.message || 'Unable to check Cloudflare Pages yet. Retrying…');
  }

  window.setTimeout(() => pollDeployment(commit, startedAt, token), POLL_INTERVAL_MS);
}

function beginDeployment(commit, resumedAt = Date.now()) {
  const normalized = String(commit || '').trim();
  if (!COMMIT_SHA.test(normalized)) return;
  if (normalized === activeCommit && deploymentPending) return;

  monitorToken += 1;
  const token = monitorToken;
  activeCommit = normalized;
  const startedAt = Number(resumedAt) || Date.now();
  safeWrite({ commit: normalized, startedAt });
  setDeploymentLock(true);
  renderDeployment('pending', 'Publishing to production', 'GitHub saved the change. Waiting for Cloudflare Pages to finish.');
  window.setTimeout(() => pollDeployment(normalized, startedAt, token), 1200);
}

window.addEventListener('sniperplug-settings-loaded', (event) => {
  const commit = event.detail?.commit;
  if (commit) beginDeployment(commit);
});

const savedDeployment = safeRead();
if (
  COMMIT_SHA.test(String(savedDeployment?.commit || ''))
  && Date.now() - Number(savedDeployment?.startedAt || 0) < MAX_MONITOR_MS
) {
  beginDeployment(savedDeployment.commit, savedDeployment.startedAt);
} else {
  safeRemove();
}

document.addEventListener('click', (event) => {
  if (!deploymentPending) return;
  const blocked = event.target.closest?.('[data-cc-publish], [data-save-method]');
  if (!blocked) return;
  event.preventDefault();
  event.stopImmediatePropagation();
  renderDeployment('pending', 'Publishing to production', 'Wait for the current Cloudflare Pages build before starting another publish.');
}, true);

document.addEventListener('submit', (event) => {
  if (!deploymentPending) return;
  if (!event.target.closest?.('[data-editor]')) return;
  event.preventDefault();
  event.stopImmediatePropagation();
  renderDeployment('pending', 'Publishing to production', 'Wait for the current Cloudflare Pages build before saving another method.');
}, true);

const modalDefinitions = [
  {
    backdrop: document.querySelector('[data-picker-backdrop]'),
    dialog: document.querySelector('[data-picker-backdrop] [role="dialog"]'),
    initial: document.querySelector('[data-picker-search]'),
    close: document.querySelector('[data-close-picker]'),
  },
  {
    backdrop: document.querySelector('[data-confirm-backdrop]'),
    dialog: document.querySelector('[data-confirm-backdrop] [role="alertdialog"]'),
    initial: document.querySelector('[data-confirm-back]'),
    close: document.querySelector('[data-confirm-back]'),
  },
  {
    backdrop: document.querySelector('[data-cc-preview-backdrop]'),
    dialog: document.querySelector('[data-cc-preview-backdrop] [role="dialog"]'),
    initial: document.querySelector('[data-cc-close-preview]'),
    close: document.querySelector('[data-cc-close-preview]'),
  },
  {
    backdrop: document.querySelector('[data-cc-publish-confirm]'),
    dialog: document.querySelector('[data-cc-publish-confirm] [role="alertdialog"]'),
    initial: document.querySelector('[data-cc-cancel-publish]'),
    close: document.querySelector('[data-cc-cancel-publish]'),
  },
].filter(({ backdrop, dialog }) => backdrop instanceof HTMLElement && dialog instanceof HTMLElement);

for (const definition of modalDefinitions) {
  let releaseFocus = null;
  let opener = null;
  const syncModal = () => {
    const open = !definition.backdrop.hidden;
    definition.backdrop.setAttribute('aria-hidden', String(!open));
    if (open && !releaseFocus) {
      opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      releaseFocus = activateFocusScope(definition.dialog, {
        initialFocus: definition.initial instanceof HTMLElement ? definition.initial : definition.dialog,
        onEscape: () => definition.close?.click(),
        returnFocus: opener,
      });
    } else if (!open && releaseFocus) {
      releaseFocus();
      releaseFocus = null;
      opener = null;
    }
  };
  new MutationObserver(syncModal).observe(definition.backdrop, { attributes: true, attributeFilter: ['hidden'] });
  syncModal();
}
