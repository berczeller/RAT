// Parameters of one ReShade effect, under its toggle on the ReShade section: booleans as toggles, numbers as sliders (one per
// component for float2/3/4 and colours), combo lists as notched sliders. Values go into the active preset's
// [Effect.fx] section, like ReShade's overlay writes them. Save default / Restore default keep your own defaults.
import { DialogButton, Focusable, SliderField, ToggleField } from "@decky/ui";
import { useEffect, useRef, useState } from "react";
import { api, EffectParams, errText, ShaderParam } from "./api";
import type { Ctx } from "./ctx";
import { ChoiceSlider } from "./controls";
import { toast } from "./launch";
import { keep } from "./scroll";
import { hint, Line, RS, Row, Tip } from "./theme";

const COMP = ["R", "G", "B", "A"];
const AXIS = ["X", "Y", "Z", "W"];

export function ShaderParams({ ctx, file, running }: { ctx: Ctx; file: string; running: boolean }) {
  const { key } = ctx;
  const [info, setInfo] = useState<EffectParams | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const timers = useRef<Record<string, any>>({});
  const [applied, setApplied] = useState<string | null>(null);
  useEffect(() => {
    keep(() =>
      api
        .reshadeParams(key, file)
        .then(setInfo)
        .catch((e) => setErr(errText(e)))
    );
  }, [key, file]);

  const write = (p: ShaderParam, vals: number[], delay: number) => {
    if (info) setInfo({ ...info, params: info.params.map((x) => (x.name === p.name ? { ...x, value: vals } : x)) });
    clearTimeout(timers.current[p.name]);
    timers.current[p.name] = setTimeout(
      () =>
        ctx.act(() => api.reshadeSetParam(key, file, p.name, vals)).then((r) => {
          if (!r) return;
          setInfo(r);
          setApplied(r.applied ?? null);
        }),
      delay
    );
  };
  if (err)
    return (
      <Row>
        <Tip tone="warn">{err}</Tip>
      </Row>
    );
  if (!info)
    return (
      <Row>
        <Line size={10}>Reading {file}…</Line>
      </Row>
    );
  if (!info.params.length)
    return (
      <Row>
        <Line size={10}>{file} has no adjustable parameters.</Line>
      </Row>
    );
  let lastCat = "";
  return (
    <>
      {ctx.d.running && (
        <Row>
          {applied === "next" ? (
            <Tip tone="warn">Saved, but ReShade could not be told to reload (another preset is loaded, or no virtual keyboard): the values apply at the next start.</Tip>
          ) : (
            <Tip>The game is running: changes apply live{applied === "live" ? " ✓" : ""}.</Tip>
          )}
        </Row>
      )}
      {info.params.map((p) => {
        const cat = p.category && p.category !== lastCat ? p.category : null;
        if (p.category) lastCat = p.category;
        const label = (
          <span style={{ paddingLeft: 10 }}>
            {p.changed ? <span className="RS_Changed">● </span> : null}
            {p.label}
          </span>
        );
        const color = /color|colour|tint/i.test(p.name + p.label) && p.components >= 3;
        return (
          <div key={p.name}>
            {cat && (
              <Row>
                <div style={{ paddingLeft: 10, marginTop: 6, color: RS.accent, fontSize: 9.5, fontWeight: 700, letterSpacing: 0.6, textTransform: "uppercase" }}>{cat}</div>
              </Row>
            )}
            {p.kind === "bool" ? (
              <Row id={`par-${file}-${p.name}`}>
                <ToggleField label={label} description={hint(p.tooltip)} checked={!!p.value[0]} disabled={running} onChange={(v) => write(p, [v ? 1 : 0], 0)} />
              </Row>
            ) : p.items && p.components === 1 ? (
              <Row id={`par-${file}-${p.name}`}>
                <ChoiceSlider label={label} desc={p.tooltip} values={p.items.map((x, i) => [String(i), x] as [string, string])} value={String(Math.round(p.value[0]))} disabled={running} onChange={(v) => write(p, [Number(v)], 0)} />
              </Row>
            ) : (
              p.value.map((v, i) => (
                <Row key={i} id={`par-${file}-${p.name}-${i}`}>
                  <SliderField
                    label={p.components > 1 ? <span>{label} <span style={{ color: RS.dim }}>{(color ? COMP : AXIS)[i]}</span></span> : label}
                    description={i === 0 ? hint(p.tooltip) : undefined}
                    value={v}
                    min={p.min}
                    max={p.max}
                    step={p.step}
                    showValue
                    editableValue
                    disabled={running}
                    onChange={(n) => {
                      const vals = [...p.value];
                      vals[i] = n;
                      write(p, vals, 350);
                    }}
                  />
                </Row>
              ))
            )}
          </div>
        );
      })}
      <Row id={`par-${file}-defaults`}>
        <Focusable style={{ display: "flex", gap: 5, width: "100%", paddingLeft: 10, boxSizing: "border-box" }} flow-children="horizontal">
          <DialogButton
            className="RS_Tab"
            style={{ flex: 1, fontSize: 10, padding: "6px 2px" }}
            onClick={async () => {
              const r = await ctx.act(() => api.reshadeParamDefaults(key, file, "save"));
              if (r) {
                setInfo(r);
                toast(`${file}: current values saved as your default`);
              }
            }}
          >
            SAVE DEFAULT
          </DialogButton>
          <DialogButton
            className="RS_Tab"
            style={{ flex: 1, fontSize: 10, padding: "6px 2px" }}
            disabled={running}
            onClick={async () => {
              const r = await ctx.act(() => api.reshadeParamDefaults(key, file, "restore"));
              if (r) {
                setInfo(r);
                toast(r.has_saved_default ? `${file}: your default restored` : `${file}: shader defaults restored`);
              }
            }}
          >
            RESTORE DEFAULT
          </DialogButton>
        </Focusable>
      </Row>
    </>
  );
}
