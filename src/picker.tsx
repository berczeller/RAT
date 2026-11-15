// Choice lists as centred dialogs. Every "dropdown" in RAT is a row with a trigger button; pressing it opens an
// independent dialog in the middle of the screen (outside the Quick Access sidebar) listing the choices alphabetically,
// ALL CAPS and colour-coded by family, with the current choice marked. Actions such as "+ ADD FORK…" or "Custom value…"
// are pinned under the list.
import { DialogButton, Field, Focusable, ModalRoot, showModal } from "@decky/ui";
import { ReactNode, useEffect, useRef, useState } from "react";
import { FiCheck, FiChevronDown } from "react-icons/fi";
import type { Family } from "./api";
import { pressFeedback } from "./feedback";
import { familyOf, FAMILY, RS, ThemeStyle, UpLabel } from "./theme";

export type PickOption = {
  data: any;
  label: string;
  /** colour family; guessed from the label when missing */
  family?: Family;
  /** small grey text after the name (source, status, size…) */
  sub?: string;
  /** an action pinned under the list (import, custom value, help…) */
  action?: boolean;
  disabled?: boolean;
};

const byName = (a: PickOption, b: PickOption) =>
  a.label.toUpperCase().localeCompare(b.label.toUpperCase(), undefined, { numeric: true, sensitivity: "base" });

function famOf(o: PickOption): Family {
  return o.family ?? familyOf(o.label);
}

function PickerModal({
  title,
  options,
  selected,
  sort,
  onPick,
  closeModal,
}: {
  title: string;
  options: PickOption[];
  selected: any;
  sort: boolean;
  onPick: (o: PickOption) => void;
  closeModal?: () => void;
}) {
  const list = options.filter((o) => !o.action);
  const actions = options.filter((o) => o.action);
  const items = sort ? [...list].sort(byName) : list;
  const pick = (o: PickOption) => {
    closeModal?.();
    // picking the current choice again changes nothing (and never resets staged settings)
    if (!o.action && selected !== undefined && selected !== null && o.data === selected) return;
    // after the dialog closed, so a follow-up dialog (confirm, text entry) opens on top of the panel
    setTimeout(() => onPick(o), 0);
  };
  return (
    <ModalRoot closeModal={closeModal} className="RS_PickModal">
      <ThemeStyle />
      <div ref={pressFeedback} style={{ display: "flex", flexDirection: "column", gap: 8, minWidth: 0 }}>
        <div style={{ textAlign: "center", color: RS.text, fontSize: 15, fontWeight: 800, letterSpacing: 1.2, textTransform: "uppercase" }}>{title}</div>
        <div style={{ height: 3, borderRadius: 3, background: `linear-gradient(90deg, transparent, ${RS.accent} 22%, ${RS.amber} 78%, transparent)`, opacity: 0.85 }} />
        <Focusable style={{ display: "flex", flexDirection: "column", gap: 4, maxHeight: "58vh", overflowY: "auto", padding: "4px 6px" }} flow-children="vertical">
          {items.length === 0 && <div style={{ color: RS.muted, fontSize: 12, padding: 8 }}>Nothing to pick yet.</div>}
          {items.map((o, i) => {
            const on = selected !== undefined && selected !== null && o.data === selected;
            const f = FAMILY[famOf(o)] ?? FAMILY.neutral;
            return (
              <DialogButton
                key={`${String(o.data)}-${i}`}
                className={`RS_Opt${on ? " RS_Opt--sel" : ""}`}
                style={{ borderLeft: `4px solid ${f.c}` }}
                disabled={o.disabled}
                {...({ autoFocus: on || (!items.some((x) => x.data === selected) && i === 0) } as any)}
                onClick={() => pick(o)}
              >
                <UpLabel family={famOf(o)} text={o.label} sub={o.sub} />
                {on ? (
                  <span className="RS_OptMark">
                    <FiCheck size={13} /> SELECTED
                  </span>
                ) : null}
              </DialogButton>
            );
          })}
        </Focusable>
        {actions.length > 0 && (
          <Focusable style={{ display: "flex", flexDirection: "column", gap: 4, padding: "0 6px", borderTop: RS.divider, paddingTop: 8 }} flow-children="vertical">
            {actions.map((o, i) => (
              <DialogButton key={`a-${i}`} className="RS_Opt RS_Opt--action" disabled={o.disabled} onClick={() => pick(o)}>
                <span style={{ color: RS.amber, letterSpacing: 0.6, fontWeight: 700, textTransform: "uppercase" }}>{o.label}</span>
              </DialogButton>
            ))}
          </Focusable>
        )}
      </div>
    </ModalRoot>
  );
}

/** Opens the centred choice dialog. */
export function showPicker(title: string, options: PickOption[], selected: any, onPick: (o: PickOption) => void, sort = true) {
  showModal(<PickerModal title={title} options={options} selected={selected} sort={sort} onPick={onPick} />);
}

/** A settings row whose value opens the centred choice dialog (RAT's replacement for Decky's DropdownItem). */
export function PickerItem({
  label,
  title,
  description,
  options,
  selected,
  placeholder,
  disabled,
  sort = true,
  onChange,
}: {
  label: ReactNode;
  /** the dialog's title (defaults to the label when it is text) */
  title?: string;
  description?: ReactNode;
  options: PickOption[];
  selected: any;
  /** shown when nothing is selected (lists of actions such as "Add effect") */
  placeholder?: string;
  disabled?: boolean;
  sort?: boolean;
  onChange: (o: PickOption) => void | Promise<any>;
}) {
  const cur = options.find((o) => !o.action && selected !== undefined && selected !== null && o.data === selected);
  const [flash, setFlash] = useState(false);
  const timer = useRef<any>(0);
  useEffect(() => () => clearTimeout(timer.current), []);
  const open = () =>
    showPicker(title ?? (typeof label === "string" ? label : "Choose"), options, selected, (o) => {
      // instant confirmation on the trigger: an amber pulse and the new value, before the backend answers
      setFlash(true);
      clearTimeout(timer.current);
      timer.current = setTimeout(() => setFlash(false), 900);
      onChange(o);
    }, sort);
  return (
    <Field label={label} description={description} bottomSeparator="standard" childrenLayout="below" childrenContainerWidth="max">
      <DialogButton className={`RS_Pick${flash ? " RS_Pick--done" : ""}`} disabled={disabled} onClick={open} style={{ width: "100%" }}>
        <span style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", textAlign: "left" }}>
          {cur ? <UpLabel family={famOf(cur)} text={cur.label} sub={cur.sub} /> : <span style={{ color: RS.muted, letterSpacing: 0.5, textTransform: "uppercase", fontSize: 12 }}>{placeholder ?? "Choose…"}</span>}
        </span>
        <FiChevronDown size={15} style={{ flex: "0 0 15px", color: RS.amber }} />
      </DialogButton>
    </Field>
  );
}
