// RAT's look: dark night-blue glass panels, a "tail pink" accent with an amber second tone, centred section titles over
// a gradient bar, toned inline tips and pill badges. Upscalers carry a family colour (DLSS green, FSR coral, XeSS azure,
// forks violet with a gold edge) tuned to sit on the glass without clashing with the pink / amber brand tones.
// Focus is always a clear white outline (controller, keyboard or mouse hover), and every press gets an instant pulse.
import { DialogButton, Focusable, PanelSection, PanelSectionRow } from "@decky/ui";
import type { CSSProperties, ReactNode } from "react";
import { FiAlertOctagon, FiAlertTriangle, FiAperture, FiCheckCircle, FiInfo, FiLayers, FiSettings, FiSliders, FiSun, FiZap } from "react-icons/fi";
import type { Family, RuntimeCheck } from "./api";
import { RatIcon } from "./icon";
import { hintsOn } from "./prefs";

export const RS = {
  accent: "#ff8fa3", // tail pink
  amber: "#ffc36b", // cheese
  info: "#8cc8f5",
  ok: "#6fd6a0",
  warn: "#f6b65e",
  danger: "#f2737f",
  text: "#eef2fa",
  muted: "#aab4c6",
  dim: "#7f8aa1",
  divider: "1px solid rgba(255, 143, 163, 0.14)",
  panel: "linear-gradient(140deg, rgba(20, 24, 46, 0.78), rgba(42, 26, 52, 0.46))",
  panelBorder: "1px solid rgba(255, 143, 163, 0.22)",
};

/** Upscaler family colours: one hue each, desaturated a touch so they read as RAT, not as vendor logos. */
export const FAMILY: Record<Family, { c: string; bg: string; edge?: string; name: string }> = {
  dlss: { c: "#86dc8f", bg: "rgba(134, 220, 143, 0.13)", name: "DLSS" },
  fsr: { c: "#ff8f66", bg: "rgba(255, 143, 102, 0.13)", name: "FSR" },
  xess: { c: "#72b9ff", bg: "rgba(114, 185, 255, 0.13)", name: "XeSS" },
  fork: { c: "#c3a2ff", bg: "rgba(195, 162, 255, 0.14)", edge: "#e8c26b", name: "Fork" },
  neutral: { c: "#aab4c6", bg: "rgba(170, 180, 198, 0.10)", name: "" },
};

/** The family of a label when the catalog does not say (imported forks, injector names). */
export function familyOf(text: string, fallback: Family = "neutral"): Family {
  const t = text.toLowerCase();
  if (/dlss|helix|model e|d4r/.test(t)) return "dlss";
  if (/fsr|fidelityfx/.test(t)) return "fsr";
  if (/xess/.test(t)) return "xess";
  return fallback;
}

export const mono: CSSProperties = {
  fontFamily: "'Fira Mono', 'DejaVu Sans Mono', monospace",
  fontSize: 10.5,
  lineHeight: 1.4,
  wordBreak: "break-all",
};

/** A colour-coded, ALL CAPS name for dropdown menus and status lines. */
export function UpLabel({ family = "neutral", text, sub }: { family?: Family; text: string; sub?: string }) {
  const f = FAMILY[family] ?? FAMILY.neutral;
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 7, minWidth: 0 }}>
      <span
        style={{
          width: 8,
          height: 8,
          borderRadius: 8,
          flex: "0 0 8px",
          background: f.c,
          boxShadow: `0 0 6px ${f.c}88${f.edge ? `, 0 0 0 1.5px ${f.edge}` : ""}`,
        }}
      />
      <span style={{ letterSpacing: 0.5, fontWeight: 600, color: f.c, textTransform: "uppercase" }}>{text}</span>
      {sub ? <span style={{ color: RS.dim, fontSize: "0.85em", textTransform: "uppercase", letterSpacing: 0.4 }}>{sub}</span> : null}
    </span>
  );
}

/** Choice-dialog options sorted alphabetically by their upper-case display name. */
export function sortedOptions<T extends { label: string }>(items: T[]): T[] {
  return [...items].sort((a, b) => a.label.toUpperCase().localeCompare(b.label.toUpperCase(), undefined, { numeric: true }));
}

/** A contextual hint, or undefined when the user switched hints off (Settings → Show hints). */
export function hint(text?: ReactNode): ReactNode | undefined {
  if (!text || !hintsOn()) return undefined;
  return <span className="RS_Hint">{text}</span>;
}

/** Global styles for Decky's own buttons inside RAT wrappers (they render a DialogButton we can't style by prop). */
export function ThemeStyle() {
  return (
    <style>{`
      .RS_Btn button, .RS_Tab, .RS_Icon {
        color: ${RS.text} !important;
        background: linear-gradient(135deg, #1b1f3a 0%, #2a2650 55%, #3a2a5a 100%) !important;
        border: 1px solid rgba(255, 143, 163, 0.32) !important;
        border-radius: 6px !important;
        box-shadow: inset 0 1px 0 rgba(255,255,255,0.07), 0 2px 6px rgba(0,0,0,0.25) !important;
        text-shadow: 0 1px 2px rgba(0,0,0,0.55);
        transition: background 120ms ease, box-shadow 120ms ease, filter 120ms ease, transform 120ms ease;
      }
      .RS_Btn button:hover:not(:disabled), .RS_Tab:hover, .RS_Icon:hover {
        background: linear-gradient(135deg, #232849 0%, #353064 55%, #4a3470 100%) !important;
      }
      /* focus and hover: a clear white outline on every RAT control (controller, keyboard and mouse) */
      .RS_Root .gpfocus, .RS_PickModal .gpfocus,
      .RS_Btn button:focus-visible, .RS_Btn button.gpfocus, .RS_Tab.gpfocus, .RS_Tab:focus-visible, .RS_Icon.gpfocus,
      .RS_Icon:focus-visible, .RS_Pick.gpfocus, .RS_Opt.gpfocus {
        outline: 2px solid #ffffff !important;
        outline-offset: 2px !important;
        box-shadow: 0 0 0 4px rgba(255,255,255,0.16), 0 0 14px rgba(255,255,255,0.28) !important;
        border-radius: 6px;
      }
      .RS_Root button:hover:not(:disabled), .RS_Root .DialogButton:hover, .RS_PickModal .DialogButton:hover,
      .RS_Btn button:hover:not(:disabled), .RS_Tab:hover, .RS_Icon:hover, .RS_Pick:hover, .RS_Opt:hover {
        outline: 2px solid rgba(255,255,255,0.9) !important;
        outline-offset: 2px !important;
      }
      /* press: shrink a touch and brighten while held, then an amber pulse confirms the action */
      .RS_Root button:active:not(:disabled), .RS_Root .DialogButton:active, .RS_PickModal .DialogButton:active {
        transform: scale(0.97);
        filter: brightness(1.25);
      }
      @keyframes RS_pulse {
        0% { box-shadow: 0 0 0 0 rgba(255,195,107,0.95), 0 0 0 0 rgba(255,195,107,0.6); }
        60% { box-shadow: 0 0 0 5px rgba(255,195,107,0.35), 0 0 18px 4px rgba(255,195,107,0.45); }
        100% { box-shadow: 0 0 0 0 rgba(255,195,107,0), 0 0 0 0 rgba(255,195,107,0); }
      }
      .RS_Pressed { animation: RS_pulse 520ms ease-out; outline-color: ${RS.amber} !important; }
      .RS_Pick {
        display: flex !important; align-items: center; justify-content: space-between; gap: 8px;
        min-width: 0 !important; padding: 7px 10px !important; font-size: 12px !important;
        color: ${RS.text} !important;
        background: linear-gradient(135deg, #171b33 0%, #241f45 60%, #2f2450 100%) !important;
        border: 1px solid rgba(255, 143, 163, 0.32) !important; border-radius: 6px !important;
      }
      .RS_Pick--done { animation: RS_pulse 700ms ease-out; border-color: ${RS.amber} !important; }
      .RS_Opt {
        display: flex !important; align-items: center; justify-content: space-between; gap: 10px;
        min-width: 0 !important; width: 100%; padding: 9px 12px !important; font-size: 13px !important; text-align: left;
        color: ${RS.text} !important;
        background: linear-gradient(135deg, rgba(27,31,58,0.92) 0%, rgba(42,38,80,0.92) 100%) !important;
        border: 1px solid rgba(255, 143, 163, 0.22) !important; border-radius: 6px !important;
      }
      .RS_Opt--sel {
        background: linear-gradient(135deg, rgba(122,45,76,0.9) 0%, rgba(166,60,98,0.9) 100%) !important;
        border-color: rgba(255, 200, 210, 0.75) !important;
      }
      .RS_Opt--action { justify-content: center; }
      .RS_OptMark { display: inline-flex; align-items: center; gap: 4px; color: #fff; font-size: 9.5px; font-weight: 800;
        letter-spacing: 0.8px; flex: none; }
      .RS_Tab.gpfocus { transform: translateY(-1px); }
      .RS_Btn button:disabled { filter: saturate(0.35) brightness(0.65); }
      .RS_Btn--primary button {
        background: linear-gradient(135deg, #5a2440 0%, #8e3456 55%, #b8476a 100%) !important;
        border-color: rgba(255, 170, 190, 0.6) !important;
      }
      .RS_Btn--primary button:hover:not(:disabled) {
        background: linear-gradient(135deg, #6a2a4c 0%, #a03c62 55%, #cc5479 100%) !important;
      }
      .RS_Btn--amber button {
        background: linear-gradient(135deg, #3d2a12 0%, #6b4716 55%, #8f6020 100%) !important;
        border-color: rgba(255, 195, 107, 0.55) !important;
      }
      .RS_Btn--danger button {
        background: linear-gradient(135deg, #3a1420 0%, #66202f 55%, #85283b 100%) !important;
        border-color: rgba(242, 115, 127, 0.6) !important;
      }
      .RS_Tab { min-width: 0 !important; padding: 5px 2px 4px !important; font-size: 9.5px !important; font-weight: 700 !important;
        line-height: 1.1 !important; letter-spacing: 0.6px; display: flex !important; flex-direction: column; align-items: center; gap: 3px; }
      .RS_Tab--active {
        color: #fff !important;
        background: linear-gradient(135deg, #7a2d4c 0%, #a63c62 60%, #c9566f 100%) !important;
        border-color: rgba(255, 200, 210, 0.7) !important;
      }
      .RS_Icon { min-width: 0 !important; width: 34px !important; height: 30px !important; padding: 0 !important;
        display: flex !important; align-items: center; justify-content: center; }
      .RS_Icon--active { color: ${RS.amber} !important; border-color: rgba(255, 195, 107, 0.7) !important; }
      .RS_Changed { color: ${RS.amber}; }
      .RS_Hint { font-size: 10px; line-height: 1.3; opacity: 0.92; letter-spacing: 0.1px; }
      .RS_Section { width: 100% !important; min-width: 0 !important; text-align: left !important; display: flex !important;
        justify-content: space-between; align-items: center; padding: 8px 10px !important; font-size: 11px !important;
        font-weight: 700 !important; letter-spacing: 0.6px; text-transform: uppercase; }
      .RS_Section--open { border-color: rgba(255, 195, 107, 0.6) !important; color: ${RS.amber} !important; }
      .RS_Fold { width: 100% !important; min-width: 0 !important; display: flex !important; flex-direction: row !important;
        align-items: center; justify-content: flex-start !important; gap: 8px;
        padding: 9px 10px !important; font-size: 12px !important; font-weight: 750 !important; text-align: left !important; }
      .RS_Fold--open { border-color: rgba(255, 195, 107, 0.6) !important; color: ${RS.amber} !important; }
      .RS_FoldBody { margin-left: 6px; padding-left: 8px; border-left: 2px solid rgba(255, 195, 107, 0.3); }
    `}</style>
  );
}

/** Wraps a Decky ButtonItem (or any button) with RAT's button skin. */
export function Btn({ tone, children }: { tone?: "primary" | "danger" | "amber"; children: ReactNode }) {
  return <div className={`RS_Btn${tone ? ` RS_Btn--${tone}` : ""}`}>{children}</div>;
}

/** A panel row whose focus RAT can find again after a re-render (see scroll.ts). */
export function Row({ id, children }: { id?: string; children: ReactNode }) {
  return (
    <PanelSectionRow>
      <div data-rat-focus={id} style={{ width: "100%" }}>
        {children}
      </div>
    </PanelSectionRow>
  );
}

export function Header({
  version,
  settingsOpen,
  onSettings,
  jobPill,
}: {
  version: string;
  settingsOpen?: boolean;
  onSettings?: () => void;
  jobPill?: ReactNode;
}) {
  return (
    <PanelSectionRow>
      <Focusable
        flow-children="horizontal"
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
          padding: "8px 10px",
          borderRadius: 8,
          border: RS.panelBorder,
          background: RS.panel,
          boxShadow: "inset 0 1px 0 rgba(255,255,255,0.04), 0 2px 8px rgba(0,0,0,0.22)",
        }}
      >
        <RatIcon size={30} style={{ color: RS.accent, flex: "0 0 30px", filter: "drop-shadow(0 1px 2px rgba(0,0,0,0.6))" }} />
        <div style={{ minWidth: 0, flex: 1 }}>
          <div style={{ color: RS.text, fontWeight: 800, fontSize: 15, letterSpacing: 1.2, lineHeight: 1.1 }}>
            RAT <span style={{ color: RS.amber, fontSize: 10.5, fontWeight: 700, letterSpacing: 0.4 }}>{version}</span>
          </div>
          <div style={{ color: RS.muted, fontSize: 9.5, letterSpacing: 0.9, textTransform: "uppercase" }}>Radical Advanced Tools</div>
          {jobPill}
        </div>
        {onSettings && (
          <DialogButton className={`RS_Icon${settingsOpen ? " RS_Icon--active" : ""}`} onClick={onSettings} aria-label="Settings">
            <FiSettings size={15} />
          </DialogButton>
        )}
      </Focusable>
    </PanelSectionRow>
  );
}

export function SectionTitle({ children, desc, badge, top = 14 }: { children: ReactNode; desc?: ReactNode; badge?: ReactNode; top?: number }) {
  const d = hint(desc);
  return (
    <PanelSectionRow>
      <div style={{ width: "100%", marginTop: top, marginBottom: 4 }}>
        <div style={{ textAlign: "center", color: RS.text, fontSize: 12.5, fontWeight: 750, letterSpacing: 1, textTransform: "uppercase", paddingBottom: 6 }}>
          {children}
          {badge && <span style={{ marginLeft: 6, verticalAlign: "2px" }}>{badge}</span>}
        </div>
        <div style={{ height: 3, borderRadius: 3, background: `linear-gradient(90deg, transparent, ${RS.accent} 22%, ${RS.amber} 78%, transparent)`, opacity: 0.85 }} />
        {d && <div style={{ marginTop: 5, color: RS.muted, fontSize: 9.5, lineHeight: 1.3 }}>{d}</div>}
      </div>
    </PanelSectionRow>
  );
}

export function Card({ children, accent, glow }: { children: ReactNode; accent?: string; glow?: boolean }) {
  return (
    <div
      style={{
        width: "100%",
        boxSizing: "border-box",
        padding: "8px 10px",
        margin: "4px 0",
        borderRadius: 8,
        border: RS.panelBorder,
        borderLeft: accent ? `3px solid ${accent}` : RS.panelBorder,
        background: RS.panel,
        boxShadow: `inset 0 1px 0 rgba(255,255,255,0.035), 0 2px 6px rgba(0,0,0,0.2)${glow && accent ? `, 0 0 14px ${accent}33` : ""}`,
      }}
    >
      {children}
    </div>
  );
}

const TONES = {
  info: { c: RS.info, bg: "linear-gradient(90deg, rgba(36,70,104,0.45), rgba(16,32,52,0.18))", Icon: FiInfo },
  warn: { c: RS.warn, bg: "linear-gradient(90deg, rgba(104,66,20,0.5), rgba(60,36,10,0.2))", Icon: FiAlertTriangle },
  danger: { c: RS.danger, bg: "linear-gradient(90deg, rgba(104,28,40,0.5), rgba(60,14,22,0.2))", Icon: FiAlertOctagon },
  ok: { c: RS.ok, bg: "linear-gradient(90deg, rgba(24,84,60,0.45), rgba(10,40,28,0.18))", Icon: FiCheckCircle },
};

/** An inline tip. Info tips are contextual hints and follow the Show hints setting; warnings always show. */
export function Tip({ tone = "info", children, always }: { tone?: keyof typeof TONES; children: ReactNode; always?: boolean }) {
  if (tone === "info" && !always && !hintsOn()) return null;
  const t = TONES[tone];
  return (
    <div
      role="note"
      style={{
        display: "flex",
        gap: 6,
        alignItems: "flex-start",
        width: "100%",
        boxSizing: "border-box",
        margin: "3px 0",
        padding: "6px 8px",
        borderRadius: 6,
        border: `1px solid ${t.c}44`,
        borderLeft: `3px solid ${t.c}`,
        background: t.bg,
        color: RS.text,
        fontSize: tone === "info" ? 9.5 : 10.5,
        lineHeight: 1.32,
      }}
    >
      <t.Icon size={tone === "info" ? 11 : 12} style={{ flex: "0 0 12px", marginTop: 1, color: t.c }} />
      <span style={{ minWidth: 0, overflowWrap: "anywhere" }}>{children}</span>
    </div>
  );
}

export function Badge({ label, tone = "amber", color }: { label: string; tone?: "amber" | "pink" | "info" | "ok"; color?: string }) {
  const c = color ?? (tone === "pink" ? RS.accent : tone === "info" ? RS.info : tone === "ok" ? RS.ok : RS.amber);
  return (
    <span
      style={{
        display: "inline-block",
        padding: "1px 6px",
        borderRadius: 999,
        border: `1px solid ${c}88`,
        background: `${c}22`,
        color: c,
        fontSize: 8.5,
        fontWeight: 700,
        letterSpacing: 0.6,
        textTransform: "uppercase",
        lineHeight: 1.5,
        whiteSpace: "nowrap",
      }}
    >
      {label}
    </span>
  );
}

export function Line({ children, color, size = 11 }: { children: ReactNode; color?: string; size?: number }) {
  return <div style={{ fontSize: size, color: color ?? RS.muted, lineHeight: 1.38, padding: "1px 0" }}>{children}</div>;
}

export function Dot({ color }: { color: string }) {
  return <span style={{ width: 9, height: 9, borderRadius: 9, background: color, boxShadow: `0 0 6px ${color}`, flex: "none", display: "inline-block" }} />;
}

export type TabDef = { id: string; label: string; hidden?: boolean; badge?: string };

/** One icon per section, shared by the panel's tabs and its folds. */
export const SECTION_ICONS: Record<string, any> = { inject: FiZap, tune: FiSliders, framegen: FiLayers, look: FiSun, hdr: FiSun, reshade: FiAperture, settings: FiSettings };
const TAB_ICONS = SECTION_ICONS;

/** Icon + label tabs across the panel: one row, focus moves left / right with the D-pad. */
export function Tabs({ tabs, value, onChange }: { tabs: TabDef[]; value: string; onChange: (id: string) => void }) {
  const shown = tabs.filter((t) => !t.hidden);
  return (
    <PanelSectionRow>
      <Focusable style={{ display: "flex", gap: 4, width: "100%", margin: "6px 0 2px" }} flow-children="horizontal">
        {shown.map((t) => {
          const Icon = TAB_ICONS[t.id];
          return (
            <DialogButton key={t.id} className={`RS_Tab${t.id === value ? " RS_Tab--active" : ""}`} style={{ flex: 1 }} onClick={() => onChange(t.id)}>
              <span style={{ position: "relative", display: "inline-flex" }}>
                {Icon ? <Icon size={14} /> : null}
                {t.badge ? (
                  <span style={{ position: "absolute", top: -5, right: -9, color: "#1b1f3a", background: RS.amber, borderRadius: 8, fontSize: 8, padding: "0 3px", lineHeight: "11px" }}>
                    {t.badge}
                  </span>
                ) : null}
              </span>
              <span>{t.label}</span>
            </DialogButton>
          );
        })}
      </Focusable>
    </PanelSectionRow>
  );
}

/** What is really loaded in the running game (from the game's process and ReShade.log). */
export function RuntimeCard({ rt }: { rt?: RuntimeCheck | null }) {
  if (!rt) return null;
  const item = (name: string, on: boolean | null) =>
    on === null ? null : (
      <Line color={on ? RS.ok : RS.danger}>
        {on ? "✓" : "✕"} {name} {on ? "is loaded in the game" : "is NOT loaded in the game"}
      </Line>
    );
  const bad = rt.reshade === false || rt.renodx === false;
  return (
    <Card accent={bad ? RS.danger : RS.ok}>
      <div style={{ fontSize: 9.5, color: RS.muted, letterSpacing: 0.7, textTransform: "uppercase" }}>Running now</div>
      {item("ReShade", rt.reshade)}
      {item("RenoDX", rt.renodx)}
      {rt.reshade && <Line size={10}>ReShade's overlay: {rt.overlay_key}{rt.mode === "chain" ? " (loaded through OptiScaler; OptiScaler's own menu keeps its key)" : ""}</Line>}
      {bad && <Line size={10}>Close the game and press Reinstall on this tab: RAT relinks ReShade (and RenoDX) and fixes the injector's settings for it.</Line>}
      {rt.errors.map((e, i) => (
        <Line key={i} size={9.5} color={RS.warn}>
          {e}
        </Line>
      ))}
    </Card>
  );
}

/** A foldable group in the panel (Quick + Advanced): one header row, its rows only while open. The header stays the
 * same element when it opens or closes, so the controller's focus never jumps. */
export function Fold({ id, title, icon, count, open, onToggle, children }: { id: string; title: ReactNode; icon?: string; count?: ReactNode; open: boolean; onToggle: () => void; children: ReactNode }) {
  const Icon = icon ? SECTION_ICONS[icon] : null;
  return (
    <>
      <Row id={`fold-${id}`}>
        <DialogButton className={`RS_Tab RS_Fold${open ? " RS_Fold--open" : ""}`} onClick={onToggle}>
          {Icon ? <Icon size={13} style={{ flex: "none" }} /> : null}
          <span style={{ flex: 1 }}>{title}</span>
          {count !== undefined && <span style={{ fontSize: 10, color: RS.dim, fontWeight: 600 }}>{count}</span>}
          <span style={{ fontSize: 11 }}>{open ? "▾" : "▸"}</span>
        </DialogButton>
      </Row>
      {open && <div className="RS_FoldBody">{children}</div>}
    </>
  );
}

/** A tab's outer section; bare inside the panel's LOOK folds, which already sit in one. */
export function Shell({ bare, children }: { bare?: boolean; children: ReactNode }) {
  return bare ? <>{children}</> : <PanelSection>{children}</PanelSection>;
}
