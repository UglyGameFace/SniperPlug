const editor = document.querySelector('[data-editor]');
const saveButton = document.querySelector('[data-save-method]');
const editorId = document.querySelector('[data-editor-id]');

if (editor instanceof HTMLFormElement) {
  let dirty = false;
  let saving = false;

  const status = document.createElement('span');
  status.className = 'desk-method-draft-state';
  status.hidden = true;
  status.setAttribute('role', 'status');
  status.setAttribute('aria-live', 'polite');

  const savebar = editor.querySelector('.desk-savebar');
  const submitButton = savebar?.querySelector('[data-save-method]');
  if (savebar) savebar.insertBefore(status, submitButton || null);

  function showStatus(message, state = 'dirty') {
    status.textContent = message;
    status.dataset.state = state;
    status.hidden = false;
  }

  function setDirty(next, message = 'Unsaved method changes', state = 'dirty') {
    dirty = Boolean(next);
    editor.dataset.methodDraftDirty = String(dirty);
    if (dirty) showStatus(message, state);
    else {
      status.hidden = true;
      status.textContent = '';
      delete status.dataset.state;
    }
  }

  function markDirty() {
    if (editor.hidden || saving) return;
    setDirty(true);
  }

  function confirmDiscard() {
    return window.confirm('You have unsaved method changes. Leave this method and discard those changes?');
  }

  editor.addEventListener('input', markDirty);
  editor.addEventListener('change', markDirty);

  document.addEventListener('click', (event) => {
    const target = event.target instanceof Element ? event.target : null;
    if (!target) return;
    if (target.closest('[data-category], [data-feature-toggle], [data-draft-toggle], [data-create-method-category]')) {
      queueMicrotask(markDirty);
    }
  });

  document.addEventListener('click', (event) => {
    const target = event.target instanceof Element ? event.target : null;
    const action = target?.closest('[data-new-method], [data-method-id], [data-refresh], [data-logout]');
    if (!action || !dirty) return;
    if (confirmDiscard()) {
      setDirty(false);
      return;
    }
    event.preventDefault();
    event.stopImmediatePropagation();
  }, true);

  editor.addEventListener('submit', () => {
    saving = true;
    showStatus('Saving method…', 'saving');
  });

  window.addEventListener('sniperplug-settings-loaded', () => {
    if (!saving) return;
    saving = false;
    setDirty(false);
  });

  if (saveButton) {
    const saveObserver = new MutationObserver(() => {
      const finished = saving
        && !saveButton.disabled
        && saveButton.textContent.trim() === 'Save method';
      if (!finished) return;
      saving = false;
      setDirty(true, 'Save failed — changes kept in this form', 'error');
    });
    saveObserver.observe(saveButton, { attributes: true, childList: true, subtree: true });
  }

  const resetObserver = new MutationObserver(() => {
    if (!saving) setDirty(false);
  });
  resetObserver.observe(editor, { attributes: true, attributeFilter: ['hidden'] });
  if (editorId) resetObserver.observe(editorId, { childList: true, subtree: true });

  window.addEventListener('beforeunload', (event) => {
    if (!dirty) return;
    event.preventDefault();
    event.returnValue = '';
  });
}
