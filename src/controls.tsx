// Controller-friendly editors for INI keys, and the store of changes waiting to be saved.
import { ButtonItem, ConfirmModal, Field, SliderField, TextField, ToggleField, showModal } from "@decky/ui";
import { ReactNode, useEffect, useRef, useState } from "react";
import { IniField } from "./api";
import { PickerItem, PickOption } from "./picker";
import { hint, RS } from "./theme";

// --- staged edits ------------------------------------------------------------------------------------------------
// Kept at module level per game and file, so closing the Quick Access menu does not lose unsaved changes.

export type Staged = Record<string, { section: string; key: string; value: string; was: string }>;
const store: Record<string, Staged> = {};
const listeners = new Set<() => void>();

function sk(game: string, file: string) {
  return `${game}\u0000${file}`;
}

export const staged = {
  get(game: string, file: string): Staged {
    return store[sk(game, file)] ?? {};
  },
  count(game: string): number {
    return Object.entries(store)
      .filter(([k]) => k.startsWith(game + "\u0000"))
      .reduce((n, [, v]) => n + Object.keys(v).length, 0);
  },
  files(game: string): string[] {
    return Object.entries(store)
      .filter(([k, v]) => k.startsWith(game + "\u0000") && Object.keys(v).length > 0)
      .map(([k]) => k.split("\u0000")[1]);
  },
  set(game: string, file: string, f: { id: string; section: string; key: string; value: string }, value: string) {
    const s = { ...(store[sk(game, file)] ?? {}) };
    const was = s[f.id]?.was ?? f.value;
    if (value === was) delete s[f.id];
    else s[f.id] = { section: f.section, key: f.key, value, was };
    store[sk(game, file)] = s;
    listeners.forEach((l) => l());
  },
  clear(game: string, file?: string) {
    for (const k of Object.keys(store)) if (k.startsWith(game + "\u0000") && (!file || k === sk(game, file))) delete store[k];
    listeners.forEach((l) => l());
  },
  edits(game: string, file: string): [string, string, string][] {
    return Object.values(this.get(game, file)).map((e) => [e.section, e.key, e.value]);
  },
};

/** Re-renders the caller whenever staged edits change. */
export function useStaged() {
  const [, set] = useState(0);
  useEffect(() => {
    const l = () => set((n) => n + 1);
    listeners.add(l);
    return () => {
      listeners.delete(l);
    };
  }, []);
}

// --- text entry --------------------------------------------------------------------------------------------------

function TextModal({ title, initial, hint, onDone, closeModal }: { title: string; initial: string; hint?: string; onDone: (v: string) => void; closeModal?: () => void }) {
  const [v, setV] = useState(initial);
  return (
    <ConfirmModal
      strTitle={title}
      strOKButtonText="OK"
      onOK={() => {
        onDone(v.trim());
        closeModal?.();
      }}
      onCancel={() => {
        onDone(null);
        closeModal?.();
      }}
      closeModal={closeModal}
      strDescription={
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {hint && <div style={{ fontSize: 12, opacity: 0.75, whiteSpace: "pre-wrap" }}>{hint}</div>}
          <TextField value={v} onChange={(e) => setV(e.target.value)} bShowClearAction />
          <div style={{ fontSize: 11, opacity: 0.6 }}>Type "auto" to go back to the injector's default.</div>
        </div>
      }
    />
  );
}

export function askText(title: string, initial: string, hint?: string): Promise<string | null> {
  return new Promise((resolve) => {
    let done = false;
    showModal(
      <TextModal
        title={title}
        initial={initial}
        hint={hint}
        onDone={(v) => {
          done = true;
          resolve(v);
        }}
        closeModal={() => !done && resolve(null)}
      />
    );
  });
}

export function showHelp(f: IniField) {
  showModal(
    <ConfirmModal
      strTitle={`[${f.section}] ${f.key}`}
      bAlertDialog
      strOKButtonText="Close"
      strDescription={
        <div style={{ fontSize: 12.5, lineHeight: 1.45, display: "flex", flexDirection: "column", gap: 4 }}>
          {f.help.length ? f.help.map((h, i) => <div key={i}>{h}</div>) : <div>No description in the file.</div>}
          <div style={{ marginTop: 6, opacity: 0.7 }}>
            Current value: <b>{f.value}</b>
            {f.default ? ` · default: ${f.default}` : ""}
          </div>
        </div>
      }
    />
  );
}

// --- one key ----------------------------------------------------------------------------------------------------

const CUSTOM = "__custom__";
const HELP = "__help__";

export function IniFieldControl({ f, value, changed, disabled, onChange, simple }: { f: IniField; value: string; changed: boolean; disabled?: boolean; onChange: (v: string) => void; simple?: boolean }) {
  const label = (
    <span>
      {changed && <span className="RS_Changed">● </span>}
      {f.label}
    </span>
  );
  const sliderTimer = useRef<any>(0);
  const [live, setLive] = useState<number | null>(null);
  useEffect(() => setLive(null), [value]);

  if (f.locked) {
    return (
      <Field label={label} description={<span style={{ color: RS.dim }}>{f.locked}</span>} bottomSeparator="standard">
        <span style={{ fontSize: 12, color: RS.muted }}>{value}</span>
      </Field>
    );
  }
  const desc = (f.type === "choice" && f.hints[value.toLowerCase()]) || f.desc;

  // simple: a short list of choices is a notched slider (or a toggle), no dialog
  if (f.type === "choice" && simple && f.choices.length >= 2 && f.choices.length <= 6) {
    const known = f.choices.some(([v]) => v.toLowerCase() === value.toLowerCase());
    if (known) return <ChoiceSlider label={label} desc={desc} values={f.choices as [string, string][]} value={value} disabled={disabled} onChange={onChange} />;
  }
  if (f.type === "choice") {
    const opts: PickOption[] = f.choices.map(([v, l]) => ({ data: v, label: l }));
    if (f.custom) opts.push({ data: CUSTOM, label: "✎ Custom value…", action: true });
    opts.push({ data: HELP, label: "ⓘ What does this do?", action: true });
    const sel = f.choices.find(([v]) => v.toLowerCase() === value.toLowerCase())?.[0] ?? value;
    return (
      <PickerItem
        label={label}
        title={`[${f.section}] ${f.label}`}
        description={hint(desc)}
        disabled={disabled}
        options={opts}
        selected={sel}
        onChange={async (o) => {
          if (o.data === HELP) return showHelp(f);
          if (o.data === CUSTOM) {
            const v = await askText(f.label, value, f.help.join("\n"));
            if (v !== null && v !== "") onChange(v);
            return;
          }
          onChange(String(o.data));
        }}
      />
    );
  }

  if (f.type === "range") {
    const min = f.min ?? 0;
    const max = f.max ?? 1;
    const step = f.step ?? 0.05;
    const isAuto = value.toLowerCase() === "auto";
    const dflt = Number.parseFloat(f.default ?? "");
    const num = live ?? (isAuto ? (Number.isFinite(dflt) ? dflt : min) : Number.parseFloat(value));
    const fmt = (n: number) => {
      const decimals = step >= 1 ? 0 : String(step).split(".")[1]?.length ?? 2;
      let s = n.toFixed(decimals);
      if (!s.includes(".") && /\.\d/.test(f.default ?? "")) s += ".0";
      return s;
    };
    return (
      <>
        {f.auto && (
          <ToggleField
            label={label}
            description={hint(isAuto ? `Auto: the injector's default${f.default ? ` (${f.default})` : ""}` : desc)}
            checked={!isAuto}
            disabled={disabled}
            onChange={(on) => onChange(on ? fmt(Number.isFinite(num) ? num : min) : "auto")}
          />
        )}
        {(!f.auto || !isAuto) && (
          <SliderField
            label={f.auto ? undefined : label}
            description={f.auto ? undefined : hint(desc)}
            value={Number.isFinite(num) ? Math.min(max, Math.max(min, num)) : min}
            min={min}
            max={max}
            step={step}
            showValue
            editableValue
            disabled={disabled}
            onChange={(n) => {
              setLive(n);
              clearTimeout(sliderTimer.current);
              sliderTimer.current = setTimeout(() => onChange(fmt(n)), 250);
            }}
          />
        )}
      </>
    );
  }

  return (
    <ButtonItem
      label={label}
      description={
        <span>
          <span style={{ color: RS.text, fontFamily: "monospace" }}>{value || "(empty)"}</span>
          {hint(desc) ? <span> · {desc}</span> : null}
        </span>
      }
      layout="below"
      disabled={disabled}
      onClick={async () => {
        const v = await askText(f.label, value, f.help.join("\n"));
        if (v !== null && v !== "") onChange(v);
      }}
    >
      Edit value
    </ButtonItem>
  );
}

// --- choices without dropdowns -------------------------------------------------------------------------------------

/** A short list of choices as a notched slider (D-pad left / right), so settings blocks need no dropdown menus. Two
 * choices that are on / off become a toggle. */
export function ChoiceSlider({
  label,
  desc,
  values,
  value,
  disabled,
  onChange,
  colorOf,
  noToggle,
}: {
  label: ReactNode;
  desc?: ReactNode;
  values: [string, string][];
  value: string;
  disabled?: boolean;
  onChange: (v: string) => void;
  /** Colour of the current value's label (a family colour); amber when not given. */
  colorOf?: (v: string) => string;
  /** Always a slider (a list whose length can change while focused must not turn into a toggle). */
  noToggle?: boolean;
}) {
  const idx = Math.max(0, values.findIndex(([v]) => v.toLowerCase() === String(value ?? "").toLowerCase()));
  const offWords = ["false", "off", "0", "never", "none", "disabled", "no"];
  // a toggle only when the off choice is also labelled as off: "Linear / Gamma" stays a two-notch slider
  const isOff = ([v, l]: [string, string]) => offWords.includes(v.toLowerCase()) && (l.toLowerCase() === v.toLowerCase() || offWords.some((w) => l.toLowerCase().startsWith(w)));
  if (!noToggle && values.length === 2 && values.some(isOff)) {
    const offI = values.findIndex(isOff);
    const onI = offI === 0 ? 1 : 0;
    return <ToggleField label={label} description={hint(desc)} checked={idx === onI} disabled={disabled} onChange={(on) => onChange(values[on ? onI : offI][0])} />;
  }
  const short = values.length <= 4 && values.every(([, l]) => l.length <= 12);
  return (
    <SliderField
      label={label}
      description={
        <span>
          <span style={{ color: (colorOf && values[idx] && colorOf(values[idx][0])) || RS.amber, fontWeight: 600 }}>{values[idx]?.[1]}</span>
          {hint(desc) ? <span> · {hint(desc)}</span> : null}
        </span>
      }
      value={idx}
      min={0}
      max={values.length - 1}
      step={1}
      notchCount={values.length}
      notchLabels={short ? values.map(([, l], i) => ({ notchIndex: i, label: l, value: i })) : undefined}
      notchTicksVisible
      disabled={disabled}
      onChange={(n) => {
        const v = values[Math.round(n)]?.[0];
        if (v !== undefined && Math.round(n) !== idx) onChange(v);
      }}
    />
  );
}
