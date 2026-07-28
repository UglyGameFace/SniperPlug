const root = document.querySelector('[data-whop-importer]');

if (root instanceof HTMLElement && root.dataset.whopImporterReady !== 'true') {
  root.dataset.whopImporterReady = 'true';

  const $ = (selector) => root.querySelector(selector);
  const elements = {
    connection: $('[data-whop-connection]'),
    connectionTitle: $('[data-whop-connection-title]'),
    connectionDetail: $('[data-whop-connection-detail]'),
    connect: $('[data-whop-connect]'),
    disconnect: $('[data-whop-disconnect]'),
    sourceBrowser: $('[data-whop-source-browser]'),
    sourceSummary: $('[data-whop-source-summary]'),
    refreshSources: $('[data-whop-refresh-sources]'),
    groupList: $('[data-whop-group-list]'),
    sourceEmpty: $('[data-whop-source-empty]'),
    selectDefaults: $('[data-whop-select-defaults]'),
    approveSelected: $('[data-whop-approve-selected]'),
    disapproveSelected: $('[data-whop-disapprove-selected]'),
    clearSourceSelection: $('[data-whop-clear-source-selection]'),
    advancedSource: $('[data-whop-advanced-source]'),
    scanForm: $('[data-whop-scan-form]'),
    scanButton: $('[data-whop-scan]'),
    scanStatus: $('[data-whop-scan-status]'),
    sourceReview: $('[data-whop-source-review]'),
    sourceTitle: $('[data-whop-source-title]'),
    sourceDetail: $('[data-whop-source-detail]'),
    sourceState: $('[data-whop-source-state]'),
    sourceApprove: $('[data-whop-source-approve]'),
    sourceDisapprove: $('[data-whop-source-disapprove]'),
    review: $('[data-whop-review]'),
    experienceTitle: $('[data-whop-experience-title]'),
    scanSummary: $('[data-whop-scan-summary]'),
    approveReady: $('[data-whop-approve-ready]'),
    disapproveAll: $('[data-whop-disapprove-all]'),
    resetDecisions: $('[data-whop-reset-decisions]'),
    category: $('[data-whop-category]'),
    rights: $('[data-whop-rights]'),
    guideList: $('[data-whop-guide-list]'),
    empty: $('[data-whop-empty]'),
    approvedCount: $('[data-whop-approved-count]'),
    disapprovedCount: $('[data-whop-disapproved-count]'),
    pendingCount: $('[data-whop-pending-count]'),
    blockedCount: $('[data-whop-blocked-count]'),
    selectedCount: $('[data-whop-selected-count]'),
    importButton: $('[data-whop-import]'),
    previewBackdrop: $('[data-whop-preview-backdrop]'),
    previewTitle: $('[data-whop-preview-title]'),
    previewMeta: $('[data-whop-preview-meta]'),
    previewBody: $('[data-whop-preview-body]'),
  };

  const state = {
    initialized: false,
    connected: false,
    sourceDiscovery: null,
    selectedSources: new Set(),
    discovery: null,
    sourceInput: '',
    decisions: new Map(),
    settings: null,
    busy: false,
  };

  function setHidden(element, hidden) {
    if (element instanceof HTMLElement) element.hidden = hidden;
  }

  function setStatus(message, type = 'ok') {
    if (!(elements.scanStatus instanceof HTMLElement)) return;
    elements.scanStatus.textContent = message;
    elements.scanStatus.dataset.type = type;
    elements.scanStatus.hidden = false;
  }

  function clearStatus() {
    if (!(elements.scanStatus instanceof HTMLElement)) return;
    elements.scanStatus.textContent = '';
    elements.scanStatus.hidden = true;
    delete elements.scanStatus.dataset.type;
  }

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
      const error = new Error(data.error || `Request failed (${response.status}).`);
      error.status = response.status;
      throw error;
    }
    return data;
  }

  function decisionStorageKey() {
    const id = state.discovery?.experience?.id;
    return id ? `sniperplug-whop-decisions:${id}` : '';
  }

  function loadDecisions() {
    state.decisions = new Map();
    const key = decisionStorageKey();
    if (!key) return;
    try {
      const parsed = JSON.parse(localStorage.getItem(key) || '{}');
      for (const [sourceKey, decision] of Object.entries(parsed)) {
        if (decision === 'approved' || decision === 'disapproved') state.decisions.set(sourceKey, decision);
      }
    } catch {
      localStorage.removeItem(key);
    }
  }

  function saveDecisions() {
    const key = decisionStorageKey();
    if (!key) return;
    localStorage.setItem(key, JSON.stringify(Object.fromEntries(state.decisions)));
  }

  function categoryEntries(settings) {
    return Object.entries(settings?.categories || {})
      .filter(([, category]) => category?.visible !== false)
      .sort((left, right) => Number(left[1]?.order || 0) - Number(right[1]?.order || 0) || String(left[1]?.label || '').localeCompare(String(right[1]?.label || '')));
  }

  function renderCategories(settings) {
    state.settings = settings || state.settings;
    if (!(elements.category instanceof HTMLSelectElement)) return;
    const previous = elements.category.value;
    elements.category.replaceChildren();
    for (const [key, definition] of categoryEntries(state.settings)) {
      const option = document.createElement('option');
      option.value = key;
      option.textContent = definition.label || key.replaceAll('-', ' ');
      elements.category.append(option);
    }
    if ([...elements.category.options].some((option) => option.value === previous)) elements.category.value = previous;
  }

  function setConnection(connected, detail = '') {
    state.connected = connected;
    if (elements.connection instanceof HTMLElement) elements.connection.dataset.state = connected ? 'connected' : 'disconnected';
    if (elements.connectionTitle instanceof HTMLElement) elements.connectionTitle.textContent = connected ? 'Whop connected' : 'Whop not connected';
    if (elements.connectionDetail instanceof HTMLElement) elements.connectionDetail.textContent = detail || (connected ? 'Ready to find your joined groups.' : 'Use Whop’s official login. Your password stays with Whop.');
    setHidden(elements.connect, connected);
    setHidden(elements.disconnect, !connected);
    setHidden(elements.sourceBrowser, !connected);
    setHidden(elements.advancedSource, !connected);
  }

  function sourceDecisionLabel(decision) {
    if (decision === 'approved') return 'Approved';
    if (decision === 'disapproved') return 'Disapproved';
    return 'Needs decision';
  }

  function createButton(label, className, onClick) {
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = label;
    if (className) button.className = className;
    button.addEventListener('click', onClick);
    return button;
  }

  function sourceEntries() {
    return (state.sourceDiscovery?.groups || []).flatMap((group) => group.sources || []);
  }

  function updateSourceBulkControls() {
    const entries = sourceEntries();
    const available = new Set(entries.map((entry) => entry.experience?.id).filter(Boolean));
    for (const id of [...state.selectedSources]) if (!available.has(id)) state.selectedSources.delete(id);
    const selectedCount = state.selectedSources.size;
    const defaults = entries.filter((entry) => entry.source?.suggested || entry.source?.defaultKey).map((entry) => entry.experience.id);
    const selectedDefaults = defaults.filter((id) => state.selectedSources.has(id)).length;

    if (elements.approveSelected instanceof HTMLButtonElement) elements.approveSelected.disabled = state.busy || selectedCount === 0;
    if (elements.disapproveSelected instanceof HTMLButtonElement) elements.disapproveSelected.disabled = state.busy || selectedCount === 0;
    if (elements.clearSourceSelection instanceof HTMLButtonElement) elements.clearSourceSelection.disabled = state.busy || selectedCount === 0;
    if (elements.selectDefaults instanceof HTMLInputElement) {
      elements.selectDefaults.disabled = state.busy || defaults.length === 0;
      elements.selectDefaults.checked = defaults.length > 0 && selectedDefaults === defaults.length;
      elements.selectDefaults.indeterminate = selectedDefaults > 0 && selectedDefaults < defaults.length;
    }
  }

  function setSourceSelected(experienceId, selected) {
    if (!experienceId) return;
    if (selected) state.selectedSources.add(experienceId);
    else state.selectedSources.delete(experienceId);
    renderSourceGroups();
  }

  function groupSourceIds(group) {
    return (group?.sources || []).map((entry) => entry.experience?.id).filter(Boolean);
  }

  function setGroupSelected(group, selected) {
    for (const id of groupSourceIds(group)) {
      if (selected) state.selectedSources.add(id);
      else state.selectedSources.delete(id);
    }
    renderSourceGroups();
  }

  function sourceBadge(decision) {
    const badge = document.createElement('strong');
    badge.className = 'whop-decision-badge';
    badge.dataset.state = decision || 'pending';
    badge.textContent = sourceDecisionLabel(decision);
    return badge;
  }

  function renderSourceGroups() {
    if (!(elements.groupList instanceof HTMLElement)) return;
    elements.groupList.replaceChildren();
    const groups = state.sourceDiscovery?.groups || [];
    const forumCount = groups.reduce((total, group) => total + Number(group.sources?.length || 0), 0);

    for (const group of groups) {
      const card = document.createElement('article');
      card.className = 'whop-group-card';
      card.dataset.builtIn = String(Boolean(group.builtIn));

      const heading = document.createElement('header');
      const headingCopy = document.createElement('div');
      const eyebrow = document.createElement('span');
      eyebrow.className = 'desk-kicker';
      eyebrow.textContent = group.builtIn ? 'Default group' : 'Joined group';
      const title = document.createElement('h4');
      title.textContent = group.company?.title || 'Whop group';
      const meta = document.createElement('small');
      const forumWord = group.sources?.length === 1 ? 'forum' : 'forums';
      meta.textContent = `${group.sources?.length || 0} readable ${forumWord}${group.company?.products?.length ? ` · ${group.company.products.length} membership product${group.company.products.length === 1 ? '' : 's'}` : ''}`;
      headingCopy.append(eyebrow, title, meta);

      const groupActions = document.createElement('div');
      groupActions.className = 'whop-group-actions';
      const ids = groupSourceIds(group);
      const allSelected = ids.length > 0 && ids.every((id) => state.selectedSources.has(id));
      const select = createButton(allSelected ? 'Clear group' : 'Select group', '', () => setGroupSelected(group, !allSelected));
      select.disabled = state.busy || ids.length === 0;
      const approveAll = createButton('Approve all', 'whop-approve', () => decideSources(ids, 'approved'));
      approveAll.disabled = state.busy || ids.length === 0;
      const disapproveAll = createButton('Disapprove all', 'whop-disapprove', () => decideSources(ids, 'disapproved'));
      disapproveAll.disabled = state.busy || ids.length === 0;
      groupActions.append(select, approveAll, disapproveAll);
      heading.append(headingCopy, groupActions);
      card.append(heading);

      if (group.error) {
        const warning = document.createElement('p');
        warning.className = 'whop-group-warning';
        warning.textContent = group.error;
        card.append(warning);
      }

      const list = document.createElement('div');
      list.className = 'whop-forum-list';
      for (const entry of group.sources || []) {
        const experienceId = entry.experience?.id || '';
        const decision = entry.source?.decision || 'pending';
        const row = document.createElement('div');
        row.className = 'whop-forum-row';
        row.dataset.state = decision;

        const selectLabel = document.createElement('label');
        selectLabel.className = 'whop-source-select';
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.checked = state.selectedSources.has(experienceId);
        checkbox.disabled = state.busy;
        checkbox.setAttribute('aria-label', `Select ${entry.experience?.name || 'Whop forum'}`);
        checkbox.addEventListener('change', () => setSourceSelected(experienceId, checkbox.checked));
        const copy = document.createElement('span');
        const name = document.createElement('strong');
        name.textContent = entry.experience?.name || 'Forum';
        const detail = document.createElement('small');
        detail.textContent = decision === 'approved'
          ? 'Ready to review posts'
          : decision === 'disapproved'
            ? 'Blocked from scans and imports'
            : 'Choose approve or disapprove';
        copy.append(name, detail);
        selectLabel.append(checkbox, copy);

        const actions = document.createElement('div');
        actions.className = 'whop-forum-actions';
        const review = createButton('Review posts', '', () => scanSource(experienceId));
        review.disabled = state.busy;
        const approve = createButton('Approve', 'whop-approve', () => decideSources([experienceId], 'approved', experienceId));
        approve.disabled = state.busy || decision === 'approved';
        const disapprove = createButton('Disapprove', 'whop-disapprove', () => decideSources([experienceId], 'disapproved'));
        disapprove.disabled = state.busy || decision === 'disapproved';
        actions.append(sourceBadge(decision), review, approve, disapprove);
        row.append(selectLabel, actions);
        list.append(row);
      }
      card.append(list);
      elements.groupList.append(card);
    }

    if (elements.sourceSummary instanceof HTMLElement) {
      const counts = state.sourceDiscovery?.counts || {};
      elements.sourceSummary.textContent = `${counts.groups || 0} joined groups · ${forumCount} readable forums · select one, several, or all default forums.`;
    }
    setHidden(elements.sourceEmpty, forumCount > 0);
    updateSourceBulkControls();
  }

  async function loadSources({ quiet = false } = {}) {
    if (!state.connected || state.busy) return;
    state.busy = true;
    updateSourceBulkControls();
    if (elements.refreshSources instanceof HTMLButtonElement) {
      elements.refreshSources.disabled = true;
      elements.refreshSources.textContent = 'Finding groups…';
    }
    if (!quiet) setStatus('Finding the joined Whop groups and forums your account can read…', 'working');
    try {
      state.sourceDiscovery = await api('/api/whop-sources', { method: 'GET', headers: {} });
      renderSourceGroups();
      if (!quiet) {
        const forums = Number(state.sourceDiscovery?.counts?.forums || 0);
        setStatus(forums ? `Found ${forums} readable Whop forum${forums === 1 ? '' : 's'}.` : 'No readable Whop forums were found.', forums ? 'ok' : 'warning');
      }
    } catch (error) {
      state.sourceDiscovery = null;
      renderSourceGroups();
      setStatus(error.message, 'error');
      if (elements.sourceSummary instanceof HTMLElement) elements.sourceSummary.textContent = 'Automatic discovery needs updated Whop permissions. The advanced fallback remains available.';
    } finally {
      state.busy = false;
      if (elements.refreshSources instanceof HTMLButtonElement) {
        elements.refreshSources.disabled = false;
        elements.refreshSources.textContent = 'Refresh groups';
      }
      updateSourceBulkControls();
    }
  }

  async function decideSources(experienceIds, decision, reviewAfter = '') {
    const ids = [...new Set((experienceIds || []).filter(Boolean))];
    if (!ids.length || state.busy) return;
    state.busy = true;
    updateSourceBulkControls();
    setStatus(`${decision === 'approved' ? 'Approving' : 'Disapproving'} ${ids.length} Whop forum${ids.length === 1 ? '' : 's'}…`, 'working');
    try {
      const output = await api('/api/whop-source-decision', {
        method: 'POST',
        body: JSON.stringify({ experienceIds: ids, decision }),
      });
      state.selectedSources.clear();
      setStatus(output.message, decision === 'approved' ? 'ok' : 'warning');
      state.busy = false;
      await loadSources({ quiet: true });
      if (reviewAfter && decision === 'approved') await scanSource(reviewAfter);
    } catch (error) {
      setStatus(error.message, 'error');
    } finally {
      state.busy = false;
      updateSourceBulkControls();
      renderSourceReview(state.discovery);
    }
  }

  function renderSourceReview(output) {
    const source = output?.source;
    const experience = output?.experience;
    if (!source || !experience) {
      setHidden(elements.sourceReview, true);
      return;
    }
    setHidden(elements.sourceReview, false);
    if (elements.sourceTitle instanceof HTMLElement) elements.sourceTitle.textContent = `${source.label || experience.company?.title || 'Whop group'} · ${experience.name || 'Forum'}`;
    if (elements.sourceDetail instanceof HTMLElement) {
      const suggestion = source.suggested && source.builtInLabel ? `Recognized as ${source.builtInLabel}. ` : '';
      elements.sourceDetail.textContent = source.decision === 'approved'
        ? `${suggestion}This forum is approved and ready for post review.`
        : source.decision === 'disapproved'
          ? `${suggestion}This forum is blocked until you approve it again.`
          : `${suggestion}Approve or disapprove this forum before its posts are loaded.`;
    }
    if (elements.sourceState instanceof HTMLElement) {
      elements.sourceState.dataset.state = source.decision || 'pending';
      elements.sourceState.textContent = sourceDecisionLabel(source.decision);
    }
    if (elements.sourceApprove instanceof HTMLButtonElement) elements.sourceApprove.disabled = source.decision === 'approved' || state.busy;
    if (elements.sourceDisapprove instanceof HTMLButtonElement) elements.sourceDisapprove.disabled = source.decision === 'disapproved' || state.busy;
  }

  function itemDecision(item) {
    if (item.integrity?.blocked) return 'blocked';
    return state.decisions.get(item.sourceKey) || 'pending';
  }

  function setItemDecision(sourceKey, decision) {
    if (decision === 'approved' || decision === 'disapproved') state.decisions.set(sourceKey, decision);
    else state.decisions.delete(sourceKey);
    saveDecisions();
    renderPosts();
  }

  function openPreview(item) {
    if (elements.previewTitle instanceof HTMLElement) elements.previewTitle.textContent = item.title || 'Post preview';
    if (elements.previewMeta instanceof HTMLElement) {
      const author = item.author?.username || item.author?.name || 'Unknown author';
      const updated = item.updatedAt ? new Date(item.updatedAt).toLocaleString() : 'Unknown date';
      elements.previewMeta.textContent = `${author} · ${updated} · ${item.integrity?.structure?.lines || 0} lines`;
    }
    if (elements.previewBody instanceof HTMLElement) elements.previewBody.textContent = item.body || '';
    if (elements.previewBackdrop instanceof HTMLElement) {
      elements.previewBackdrop.hidden = false;
      elements.previewBackdrop.setAttribute('aria-hidden', 'false');
    }
    document.body.classList.add('desk-modal-open');
  }

  function closePreview() {
    if (elements.previewBackdrop instanceof HTMLElement) {
      elements.previewBackdrop.hidden = true;
      elements.previewBackdrop.setAttribute('aria-hidden', 'true');
    }
    document.body.classList.remove('desk-modal-open');
  }

  function renderPosts() {
    const items = state.discovery?.items || [];
    if (!(elements.guideList instanceof HTMLElement)) return;
    elements.guideList.replaceChildren();

    for (const item of items) {
      const decision = itemDecision(item);
      const card = document.createElement('article');
      card.className = 'whop-post-card';
      card.dataset.state = decision;

      const heading = document.createElement('header');
      const copy = document.createElement('div');
      const title = document.createElement('h4');
      title.textContent = item.title || 'Untitled Whop post';
      const meta = document.createElement('small');
      const author = item.author?.username || item.author?.name || 'Unknown author';
      meta.textContent = `${author}${item.sourceMeta?.pinned ? ' · Pinned' : ''}${item.updatedAt ? ` · ${new Date(item.updatedAt).toLocaleDateString()}` : ''}`;
      copy.append(title, meta);
      const badge = sourceBadge(decision);
      badge.textContent = decision === 'blocked' ? 'Blocked' : sourceDecisionLabel(decision);
      heading.append(copy, badge);

      const excerpt = document.createElement('p');
      excerpt.textContent = item.integrity?.blocked ? item.integrity.error : item.description || 'No preview text.';

      const diagnostics = document.createElement('div');
      diagnostics.className = 'whop-post-diagnostics';
      const lines = item.integrity?.structure?.lines || 0;
      const repairs = item.integrity?.repairs?.length || 0;
      diagnostics.textContent = item.integrity?.blocked
        ? `Integrity check blocked this post${item.integrity?.code ? ` · ${item.integrity.code}` : ''}`
        : `${lines} lines · ${repairs ? `${repairs} deterministic repair${repairs === 1 ? '' : 's'}` : 'Exact formatting preserved'}`;

      const actions = document.createElement('div');
      actions.className = 'whop-post-actions';
      const approve = createButton('Approve', 'whop-approve', () => setItemDecision(item.sourceKey, 'approved'));
      approve.disabled = decision === 'approved' || decision === 'blocked';
      approve.setAttribute('aria-pressed', String(decision === 'approved'));
      const disapprove = createButton('Disapprove', 'whop-disapprove', () => setItemDecision(item.sourceKey, 'disapproved'));
      disapprove.disabled = decision === 'disapproved' || decision === 'blocked';
      disapprove.setAttribute('aria-pressed', String(decision === 'disapproved'));
      const undo = createButton('Undo decision', '', () => setItemDecision(item.sourceKey, 'pending'));
      undo.hidden = !['approved', 'disapproved'].includes(decision);
      const preview = createButton('Preview exact post', '', () => openPreview(item));
      actions.append(approve, disapprove, undo, preview);

      card.append(heading, excerpt, diagnostics, actions);
      elements.guideList.append(card);
    }

    setHidden(elements.empty, Boolean(items.length));
    updateDecisionSummary();
  }

  function updateDecisionSummary() {
    const items = state.discovery?.items || [];
    const counts = { approved: 0, disapproved: 0, pending: 0, blocked: 0 };
    for (const item of items) counts[itemDecision(item)] += 1;
    if (elements.approvedCount instanceof HTMLElement) elements.approvedCount.textContent = String(counts.approved);
    if (elements.disapprovedCount instanceof HTMLElement) elements.disapprovedCount.textContent = String(counts.disapproved);
    if (elements.pendingCount instanceof HTMLElement) elements.pendingCount.textContent = String(counts.pending);
    if (elements.blockedCount instanceof HTMLElement) elements.blockedCount.textContent = String(counts.blocked);
    if (elements.selectedCount instanceof HTMLElement) elements.selectedCount.textContent = `${counts.approved} approved for import`;
    if (elements.importButton instanceof HTMLButtonElement) {
      elements.importButton.disabled = state.busy || counts.approved === 0 || !(elements.rights instanceof HTMLInputElement && elements.rights.checked) || !(elements.category instanceof HTMLSelectElement && elements.category.value);
    }
  }

  function renderDiscovery(output) {
    state.discovery = output;
    state.sourceInput = output?.experience?.id || state.sourceInput;
    loadDecisions();
    renderSourceReview(output);
    if (output.approvalRequired) {
      setHidden(elements.review, true);
      setStatus(output.source?.decision === 'disapproved'
        ? 'This forum is disapproved. Approve it to review posts.'
        : 'Approve or disapprove this forum before reviewing its posts.', 'warning');
      return;
    }
    clearStatus();
    setHidden(elements.review, false);
    if (elements.experienceTitle instanceof HTMLElement) elements.experienceTitle.textContent = `${output.source?.label || output.experience?.company?.title || 'Whop'} · ${output.experience?.name || 'Forum'} posts`;
    if (elements.scanSummary instanceof HTMLElement) elements.scanSummary.textContent = `${output.counts?.ready || 0} ready · ${output.counts?.blocked || 0} blocked · approve only what should become a hidden draft.`;
    renderPosts();
  }

  async function scanSource(sourceOverride = '') {
    const fallback = elements.scanForm instanceof HTMLFormElement
      ? String(new FormData(elements.scanForm).get('source') || '').trim()
      : '';
    const source = String(sourceOverride || fallback).trim();
    if (!source || state.busy) return;
    state.sourceInput = source;
    state.busy = true;
    if (elements.scanButton instanceof HTMLButtonElement) {
      elements.scanButton.disabled = true;
      elements.scanButton.textContent = 'Checking forum…';
    }
    setStatus('Checking this Whop forum and loading its approval state…', 'working');
    try {
      const output = await api('/api/whop-discover', {
        method: 'POST',
        body: JSON.stringify({ source }),
      });
      renderDiscovery(output);
      elements.sourceReview?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    } catch (error) {
      setHidden(elements.sourceReview, true);
      setHidden(elements.review, true);
      setStatus(error.message, 'error');
    } finally {
      state.busy = false;
      if (elements.scanButton instanceof HTMLButtonElement) {
        elements.scanButton.disabled = false;
        elements.scanButton.textContent = 'Check forum';
      }
      updateDecisionSummary();
      updateSourceBulkControls();
    }
  }

  async function decideCurrentSource(decision) {
    const id = state.discovery?.experience?.id || state.sourceInput;
    if (!id) return;
    await decideSources([id], decision, decision === 'approved' ? id : '');
    if (decision === 'disapproved') {
      if (state.discovery?.source) state.discovery.source.decision = 'disapproved';
      renderSourceReview(state.discovery);
      setHidden(elements.review, true);
    }
  }

  async function importApproved() {
    const items = state.discovery?.items || [];
    const sourceKeys = items.filter((item) => itemDecision(item) === 'approved').map((item) => item.sourceKey);
    if (!sourceKeys.length || state.busy) return;
    state.busy = true;
    updateDecisionSummary();
    if (elements.importButton instanceof HTMLButtonElement) elements.importButton.textContent = 'Importing drafts…';
    setStatus('Re-fetching approved posts from Whop and verifying their formatting…', 'working');
    try {
      const output = await api('/api/whop-import', {
        method: 'POST',
        body: JSON.stringify({
          experienceId: state.discovery.experience.id,
          sourceKeys,
          category: elements.category instanceof HTMLSelectElement ? elements.category.value : '',
          rightsConfirmed: elements.rights instanceof HTMLInputElement && elements.rights.checked,
        }),
      });
      setStatus(output.message, 'ok');
      document.querySelector('[data-refresh]')?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    } catch (error) {
      setStatus(error.message, 'error');
    } finally {
      state.busy = false;
      if (elements.importButton instanceof HTMLButtonElement) elements.importButton.textContent = 'Import approved drafts';
      updateDecisionSummary();
    }
  }

  async function initialize(settingsOutput = null) {
    if (settingsOutput?.settings) renderCategories(settingsOutput.settings);
    if (state.initialized) return;
    state.initialized = true;
    try {
      const session = await api('/api/whop-session', { method: 'GET', headers: {} });
      if (!session.configured) {
        setConnection(false, 'Whop OAuth is not configured in Cloudflare Pages yet.');
        if (elements.connect instanceof HTMLAnchorElement) elements.connect.setAttribute('aria-disabled', 'true');
        return;
      }
      const name = session.session?.user?.username || session.session?.user?.name || session.session?.user?.email || '';
      setConnection(Boolean(session.connected), session.connected ? `Connected${name ? ` as ${name}` : ''}. Finding your joined groups automatically.` : 'Use Whop’s official login. Your password stays with Whop.');
      if (session.connected) await loadSources();
    } catch (error) {
      if (error.status === 401) {
        state.initialized = false;
        return;
      }
      setConnection(false, error.message);
    }
  }

  elements.refreshSources?.addEventListener('click', () => loadSources());
  elements.selectDefaults?.addEventListener('change', () => {
    const defaults = sourceEntries().filter((entry) => entry.source?.suggested || entry.source?.defaultKey).map((entry) => entry.experience?.id).filter(Boolean);
    for (const id of defaults) {
      if (elements.selectDefaults instanceof HTMLInputElement && elements.selectDefaults.checked) state.selectedSources.add(id);
      else state.selectedSources.delete(id);
    }
    renderSourceGroups();
  });
  elements.approveSelected?.addEventListener('click', () => decideSources([...state.selectedSources], 'approved'));
  elements.disapproveSelected?.addEventListener('click', () => decideSources([...state.selectedSources], 'disapproved'));
  elements.clearSourceSelection?.addEventListener('click', () => {
    state.selectedSources.clear();
    renderSourceGroups();
  });
  elements.scanForm?.addEventListener('submit', (event) => {
    event.preventDefault();
    scanSource();
  });
  elements.sourceApprove?.addEventListener('click', () => decideCurrentSource('approved'));
  elements.sourceDisapprove?.addEventListener('click', () => decideCurrentSource('disapproved'));
  elements.approveReady?.addEventListener('click', () => {
    for (const item of state.discovery?.items || []) if (!item.integrity?.blocked) state.decisions.set(item.sourceKey, 'approved');
    saveDecisions();
    renderPosts();
  });
  elements.disapproveAll?.addEventListener('click', () => {
    for (const item of state.discovery?.items || []) if (!item.integrity?.blocked) state.decisions.set(item.sourceKey, 'disapproved');
    saveDecisions();
    renderPosts();
  });
  elements.resetDecisions?.addEventListener('click', () => {
    state.decisions.clear();
    const key = decisionStorageKey();
    if (key) localStorage.removeItem(key);
    renderPosts();
  });
  elements.rights?.addEventListener('change', updateDecisionSummary);
  elements.category?.addEventListener('change', updateDecisionSummary);
  elements.importButton?.addEventListener('click', importApproved);
  elements.disconnect?.addEventListener('click', async () => {
    try { await api('/api/whop-session', { method: 'DELETE', body: '{}' }); } catch { /* Clear local UI either way. */ }
    state.discovery = null;
    state.sourceDiscovery = null;
    state.decisions.clear();
    state.selectedSources.clear();
    setConnection(false);
    setHidden(elements.sourceReview, true);
    setHidden(elements.review, true);
    renderSourceGroups();
  });
  root.querySelectorAll('[data-whop-preview-close]').forEach((button) => button.addEventListener('click', closePreview));
  elements.previewBackdrop?.addEventListener('click', (event) => {
    if (event.target === elements.previewBackdrop) closePreview();
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && elements.previewBackdrop instanceof HTMLElement && !elements.previewBackdrop.hidden) closePreview();
  });
  window.addEventListener('sniperplug-settings-loaded', (event) => initialize(event.detail));
  if (window.SniperPlugSettingsRuntime?.value) initialize(window.SniperPlugSettingsRuntime.value);
}
