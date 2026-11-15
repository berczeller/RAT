// Instant press feedback: whatever control is pressed inside a RAT root (panel or dialog) gets a short amber pulse
// (.RS_Pressed, see ThemeStyle), whether it was clicked, tapped or activated with the controller's A button. The
// backend answer comes later; the pulse confirms the press right away.

const PRESSABLE = "button, .DialogButton, [role='button'], [role='switch'], [role='checkbox'], .gpfocus";
const EVENTS = ["click", "vgp_onok", "keydown"];

function pulse(target: EventTarget | null) {
  const el = (target as HTMLElement | null)?.closest?.(PRESSABLE) as HTMLElement | null;
  if (!el) return;
  el.classList.remove("RS_Pressed");
  // restart the animation when the same control is pressed twice in a row
  void el.offsetWidth;
  el.classList.add("RS_Pressed");
  window.setTimeout(() => el.classList.remove("RS_Pressed"), 560);
}

function handler(e: Event) {
  if (e.type === "keydown") {
    const k = (e as KeyboardEvent).key;
    if (k !== "Enter" && k !== " ") return;
  }
  pulse(e.target);
}

const attached = new WeakSet<HTMLElement>();

/** Ref callback for a root element: attaches the press listeners once. */
export function pressFeedback(el: HTMLElement | null) {
  if (!el || attached.has(el)) return;
  attached.add(el);
  for (const ev of EVENTS) el.addEventListener(ev, handler, true);
}
