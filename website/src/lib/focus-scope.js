const FOCUSABLE = [
  'a[href]:not([tabindex="-1"])',
  'button:not([disabled]):not([tabindex="-1"])',
  'input:not([disabled]):not([type="hidden"]):not([tabindex="-1"])',
  'select:not([disabled]):not([tabindex="-1"])',
  'textarea:not([disabled]):not([tabindex="-1"])',
  'summary:not([tabindex="-1"])',
  '[contenteditable="true"]:not([tabindex="-1"])',
  '[tabindex]:not([tabindex="-1"])',
].join(',');

function visibleFocusable(container) {
  return [...container.querySelectorAll(FOCUSABLE)].filter((element) => (
    element instanceof HTMLElement
    && !element.hidden
    && element.getAttribute('aria-hidden') !== 'true'
    && element.getClientRects().length > 0
  ));
}

export function activateFocusScope(container, options = {}) {
  if (!(container instanceof HTMLElement)) return () => {};
  const previous = options.returnFocus instanceof HTMLElement
    ? options.returnFocus
    : document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
  let active = true;

  const initialTarget = () => {
    const requested = typeof options.initialFocus === 'function'
      ? options.initialFocus()
      : options.initialFocus;
    if (requested instanceof HTMLElement && !requested.hidden) return requested;
    return visibleFocusable(container)[0] || container;
  };

  const onKeyDown = (event) => {
    if (!active) return;
    if (event.key === 'Escape' && typeof options.onEscape === 'function') {
      event.preventDefault();
      event.stopPropagation();
      options.onEscape();
      return;
    }
    if (event.key !== 'Tab') return;

    const focusable = visibleFocusable(container);
    if (!focusable.length) {
      event.preventDefault();
      container.focus();
      return;
    }

    const first = focusable[0];
    const last = focusable.at(-1);
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

  if (!container.hasAttribute('tabindex')) container.setAttribute('tabindex', '-1');
  container.addEventListener('keydown', onKeyDown);
  requestAnimationFrame(() => initialTarget().focus());

  return (restoreFocus = true) => {
    if (!active) return;
    active = false;
    container.removeEventListener('keydown', onKeyDown);
    if (restoreFocus && previous?.isConnected && !previous.hasAttribute('disabled')) {
      requestAnimationFrame(() => previous.focus());
    }
  };
}
