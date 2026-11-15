// Scroll and focus permanence for the Quick Access panel.
//
// Changing a setting re-renders the panel; when a row is replaced, the focused control is disabled for a moment or the
// content height changes, Steam's gamepad navigation can jump focus to the first element and scroll the panel to the
// top. keep() records the panel's scroll position and the focused row (rows carry a data-rat-focus id, see theme.Row)
// before an action and puts both back after React has re-rendered, retrying over a few frames because follow-up loads
// (launch options, frame generation state) render later. It only ever scrolls back down: if the user moved on, nothing
// is forced.

let anchor: HTMLElement | null = null;

/** Called with any element inside the panel (a ref in the panel's root). */
export function setScrollAnchor(el: HTMLElement | null) {
  if (el) anchor = el;
}

/** The RAT panel the focus is in, else the last one rendered. */
function viewRoot(): HTMLElement | null {
  const a = document.activeElement as HTMLElement | null;
  return (a?.closest?.(".RS_Root") as HTMLElement | null) ?? anchor;
}

function scroller(root: HTMLElement | null = viewRoot()): HTMLElement | null {
  let el: HTMLElement | null = root;
  while (el) {
    const s = getComputedStyle(el);
    if ((s.overflowY === "auto" || s.overflowY === "scroll") && el.scrollHeight > el.clientHeight) return el;
    el = el.parentElement;
  }
  return null;
}

function focusedRow(): string | null {
  const a = document.activeElement as HTMLElement | null;
  return a?.closest?.("[data-rat-focus]")?.getAttribute("data-rat-focus") ?? null;
}

function refocus(id: string | null, root: HTMLElement | null) {
  if (!id) return;
  const a = document.activeElement as HTMLElement | null;
  if (a && a !== document.body && a.isConnected && a.closest?.(`[data-rat-focus="${CSS.escape(id)}"]`)) return;
  const row = (root?.isConnected ? root : document).querySelector<HTMLElement>(`[data-rat-focus="${CSS.escape(id)}"]`);
  const target = row?.querySelector<HTMLElement>("button:not([disabled]), [tabindex]:not([tabindex='-1']), input, .gpfocusable") ?? null;
  try {
    target?.focus({ preventScroll: true });
  } catch {
    /* not focusable */
  }
}

let seq = 0;

/** Remembers the position now and restores it over the next frames. */
export function pin(): () => void {
  const root = viewRoot();
  const s = scroller(root);
  const top = s?.scrollTop ?? 0;
  const focus = focusedRow();
  const my = ++seq;
  const restore = () => {
    if (my !== seq) return; // a newer action took over
    const el = (root?.isConnected ? scroller(root) : null) ?? s;
    if (el && el.scrollTop < top - 8) el.scrollTop = top;
    refocus(focus, root);
  };
  return () => {
    requestAnimationFrame(() => requestAnimationFrame(restore));
    setTimeout(restore, 120);
    setTimeout(restore, 320);
    setTimeout(restore, 700);
  };
}

/** Runs an action with the scroll position and focus kept. */
export async function keep<T>(fn: () => Promise<T>): Promise<T> {
  const done = pin();
  try {
    return await fn();
  } finally {
    done();
  }
}
