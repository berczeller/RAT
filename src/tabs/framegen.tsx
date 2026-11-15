// [ FG ]: OptiScaler frame generation for the selected game. One method picker with a plain-language guide (what it
// does, which games and GPUs, what to spoof, what to do in the game), a base framerate cap applied before frame
// generation, the method's own options, and a latency block that only shows what works with this GPU / add-on /
// method, with a recommended combination one press away. d4r gets its FG fallback card: the frame generation files
// its bundled OptiScaler lacks, taken from the matching official OptiScaler release.
import { ButtonItem, DialogButton, Focusable, PanelSection, SliderField, ToggleField } from "@decky/ui";
import { useEffect, useRef, useState } from "react";
import { api, errText, FgFallback, FgGroup, FgMethod, FrameGen, IniField, LatencyPlan } from "../api";
import { ChoiceSlider, staged, useStaged } from "../controls";
import type { Ctx } from "../ctx";
import { toast } from "../launch";
import { confirm } from "../modals";
import { PickerItem } from "../picker";
import { hintsOn } from "../prefs";
import { keep } from "../scroll";
import { Badge, Btn, Card, FAMILY, Fold, hint, Line, RS, Row, SectionTitle, Tip } from "../theme";
import { FieldRow, SaveBar } from "./tune";

/** OptiScaler's reading of "auto" in the frame generation keys (mirrors framegen._norm). */
function fgNorm(sec: string, key: string, v: string | undefined): string {
  const x = (v ?? "auto").trim().toLowerCase();
  if (x !== "auto") return x;
  const d: Record<string, string> = { "framegen.enabled": "false", "framegen.fginput": "nofg", "framegen.fgoutput": "nofg", "framegen.fgtype": "nofg", "framegen.fgnvngxreplacement": "nukems", "optifg.hudfix": "false" };
  return d[`${sec}.${key}`.toLowerCase()] ?? "auto";
}

function methodFamily(label: string) {
  const l = label.toLowerCase();
  if (l === "off") return "neutral";
  if (l.includes("→ xefg")) return "xess";
  if (l.includes("nukem")) return "fork";
  return "fsr";
}

/** FG input / output a method sets (old FGType builds map to the same pair). */
function methodIO(m: FgMethod | undefined): [string, string] {
  if (!m) return ["nofg", "nofg"];
  const get = (k: string) => m.edits.find(([s, key]) => s.toLowerCase() === "framegen" && key.toLowerCase() === k)?.[2]?.toLowerCase();
  const t = get("fgtype");
  if (t) return [t, ["optifg", "nukems"].includes(t) ? "fsrfg" : "nofg"];
  const i = get("fginput") ?? "nofg";
  const o = get("fgoutput") ?? "nofg";
  return [i, o === "auto" && i !== "nofg" ? "fsrfg" : o];
}

/** Output frames per rendered frame (mirrors framegen.cap_multiplier). */
function capMultiplier(inp: string, outp: string, xefgCount?: string | null): number {
  if ((outp === "nofg" || !outp) && (inp === "nofg" || !inp)) return 1;
  if (outp === "xefg") {
    const n = Number.parseFloat(xefgCount ?? "1");
    return 1 + (Number.isFinite(n) && n >= 1 ? Math.round(n) : 1);
  }
  return 2;
}

const CAP_PRESETS = [0, 30, 40, 45, 60, 72, 90, 120];

// --- d4r: frame generation fallback ---------------------------------------------------------------------------------

function FallbackCard({ ctx, fb, onChanged }: { ctx: Ctx; fb: FgFallback; onChanged: () => void }) {
  const { d, key } = ctx;
  const [sel, setSel] = useState<Record<string, boolean>>(() => Object.fromEntries(fb.groups.map((g) => [g.id, fb.installed ? g.on : g.default])));
  const chosen = fb.groups.filter((g) => sel[g.id]).map((g) => g.id);
  const lock = ctx.jobRunning || d.running;
  return (
    <>
      <SectionTitle top={8} badge={<Badge label={fb.installed ? "added" : "not added"} tone={fb.installed ? "ok" : "amber"} />} desc="d4r bundles OptiScaler without its frame generation libraries. RAT adds them from a matching OptiScaler package, so the OptiScaler FG methods work with d4r's DLSS.">
        FG fallback for d4r
      </SectionTitle>
      <Row id="fb-info">
        <Card accent={fb.installed ? RS.ok : RS.amber}>
          <Line color={RS.text}>
            d4r bundles OptiScaler <b>{fb.bundled}</b> <span style={{ color: RS.dim }}>({fb.bundled_from})</span>
          </Line>
          <Line>
            {fb.installed
              ? `Frame generation files from ${/^v?\d/.test(fb.donor ?? "") ? `OptiScaler ${fb.donor}` : fb.donor}${fb.donor_note ? ` (${fb.donor_note})` : ""}: ${fb.files.length} added.`
              : `RAT looks for the libraries below in this order: a stored OptiScaler package of the same build, the official release on GitHub (v0.9.4 when d4r's build has none), then any stored OptiScaler or HelixSR add-on. Nothing d4r ships is replaced.`}
          </Line>
          {fb.kept.length > 0 && <Line size={10}>Already in d4r, kept as they are: {fb.kept.join(", ")}</Line>}
        </Card>
      </Row>
      {fb.missing.length > 0 && (
        <Row>
          <Tip tone="warn">Some fallback files are gone ({fb.missing.join(", ")}), probably after a game update or file verification. Press Update.</Tip>
        </Row>
      )}
      {fb.groups.map((g) => (
        <Row key={g.id} id={`fb-${g.id}`}>
          <ToggleField label={g.label} description={hint(g.desc)} checked={!!sel[g.id]} disabled={lock} onChange={(v) => setSel({ ...sel, [g.id]: v })} />
        </Row>
      ))}
      <Row id="fb-add">
        <Btn tone="primary">
          <ButtonItem layout="below" disabled={lock || chosen.length === 0} onClick={() => ctx.startJob(() => api.fgFallbackInstall(key, chosen), () => onChanged())}>
            {fb.installed ? "Update fallback files" : "Add frame generation files"}
          </ButtonItem>
        </Btn>
      </Row>
      {fb.installed && (
        <Row id="fb-remove">
          <Btn tone="danger">
            <ButtonItem
              layout="below"
              disabled={lock}
              onClick={async () => {
                if (!(await confirm("Remove the FG fallback?", "RAT deletes the frame generation files it added to d4r and switches frame generation off in d4r's OptiScaler.ini.", "Remove", true))) return;
                const r = await ctx.act(() => api.fgFallbackRemove(key));
                if (r) {
                  staged.clear(key, "optiscaler");
                  onChanged();
                }
              }}
            >
              Remove fallback files
            </ButtonItem>
          </Btn>
        </Row>
      )}
    </>
  );
}

// --- method guide -------------------------------------------------------------------------------------------------------

function MethodGuide({ m }: { m: FgMethod }) {
  if (m.id === "off") return null;
  const row = (title: string, text: string) =>
    text ? (
      <div style={{ marginTop: 4 }}>
        <span style={{ color: RS.amber, fontSize: 9.5, fontWeight: 800, letterSpacing: 0.7, textTransform: "uppercase" }}>{title}</span>
        <div style={{ color: RS.text, fontSize: 10.5, lineHeight: 1.35 }}>{text}</div>
      </div>
    ) : null;
  return (
    <Card accent={RS.info}>
      <div style={{ color: RS.text, fontSize: 11, lineHeight: 1.38 }}>{m.how}</div>
      {hintsOn() && (
        <>
          {row("Works with", m.works)}
          {row("Spoofing", m.spoof)}
          {m.steps.length > 0 && (
            <div style={{ marginTop: 4 }}>
              <span style={{ color: RS.amber, fontSize: 9.5, fontWeight: 800, letterSpacing: 0.7, textTransform: "uppercase" }}>In the game</span>
              {m.steps.map((s, i) => (
                <div key={i} style={{ color: RS.text, fontSize: 10.5, lineHeight: 1.35 }}>
                  {i + 1}. {s}
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </Card>
  );
}

// --- base framerate cap ---------------------------------------------------------------------------------------------------

function BaseCap({ game, file, f, mult, disabled }: { game: string; file: string; f: IniField; mult: number; disabled: boolean }) {
  useStaged();
  const st = staged.get(game, file)[f.id];
  const written = Number.parseFloat((st ? st.value : f.value) ?? "");
  const base = Number.isFinite(written) && written > 0 ? Math.round(written / mult) : 0;
  const [live, setLive] = useState<number | null>(null);
  const timer = useRef<any>(0);
  useEffect(() => setLive(null), [base, mult]);
  const shown = live ?? base;
  const set = (n: number) => staged.set(game, file, f, n > 0 ? `${Math.round(n) * mult}` : "auto");
  return (
    <>
      <Row id="fg-cap">
        <SliderField
          label={
            <span>
              {st && <span className="RS_Changed">● </span>}Base framerate cap
            </span>
          }
          description={
            <span>
              <span style={{ color: RS.amber, fontWeight: 650 }}>{shown > 0 ? `${shown} fps rendered${mult > 1 ? ` → about ${shown * mult} fps shown` : ""}` : "Off"}</span>
              {hint(
                ` · Caps the frames the game renders before frame generation, so every real frame arrives on an even beat: steadier frame times and pacing, less input lag. Pick about half your display's refresh rate with 2× FG (60 for 120 Hz). RAT writes OptiScaler's FramerateLimit, which counts shown frames, as cap × ${mult}.`
              )}
            </span>
          }
          value={shown}
          min={0}
          max={240}
          step={1}
          showValue
          editableValue
          disabled={disabled}
          onChange={(n) => {
            const v = Math.max(0, Math.min(240, Math.round(n)));
            setLive(v);
            clearTimeout(timer.current);
            timer.current = setTimeout(() => set(v), 250);
          }}
        />
      </Row>
      <Row id="fg-cap-presets">
        <Focusable style={{ display: "flex", gap: 4, width: "100%" }} flow-children="horizontal">
          {CAP_PRESETS.map((p) => (
            <DialogButton
              key={p}
              className={`RS_Tab${shown === p ? " RS_Tab--active" : ""}`}
              style={{ flex: 1, fontSize: 9.5 }}
              disabled={disabled}
              onClick={() => {
                setLive(p);
                set(p);
              }}
            >
              {p === 0 ? "OFF" : p}
            </DialogButton>
          ))}
        </Focusable>
      </Row>
    </>
  );
}

// --- latency ------------------------------------------------------------------------------------------------------------

function LatencyBlock({ game, file, fg, plan, effective, disabled, bare }: { game: string; file: string; fg: FrameGen; plan: LatencyPlan; effective: (s: string, k: string) => string; disabled: boolean; bare?: boolean }) {
  useStaged();
  const fields = fg.latency_fields ?? [];
  const byKey = (sk: string) => fields.find((f) => `${f.section}.${f.key}`.toLowerCase() === sk.toLowerCase());
  const shown = plan.keys.map(byKey).filter((f): f is IniField => !!f);
  const recs = plan.recommended.filter(([s, k]) => byKey(`${s}.${k}`));
  const recFor = (f: IniField) => recs.find(([s, k]) => s.toLowerCase() === f.section.toLowerCase() && k.toLowerCase() === f.key.toLowerCase())?.[2];
  const label = (f: IniField, v: string) => f.choices.find(([c]) => c.toLowerCase() === v.toLowerCase())?.[1] ?? v;
  const done = recs.every(([s, k, v]) => effective(s, k) === v.toLowerCase());
  const tone = plan.tone === "ok" ? RS.ok : plan.tone === "warn" ? RS.warn : RS.info;
  return (
    <>
      {!bare && (
        <SectionTitle desc="Frame generation adds a frame of delay; a latency reducer (Reflex or its stand-ins) wins it back. Only the settings that work with this GPU, add-on and method are listed.">
          Latency & Anti-Lag
        </SectionTitle>
      )}
      <Row id="lat-provider">
        <Card accent={tone}>
          <div style={{ fontSize: 9.5, color: RS.muted, letterSpacing: 0.7, textTransform: "uppercase" }}>Latency reducer in use</div>
          <div style={{ fontSize: 12, fontWeight: 700, color: tone }}>{plan.provider}</div>
          {plan.notes.map((n, i) => (
            <Line key={i} size={10.5}>
              {n}
            </Line>
          ))}
        </Card>
      </Row>
      {recs.length > 0 && (
        <Row id="lat-rec">
          {/* always the same, always enabled button: replacing or disabling the focused control makes Steam's
              navigation jump back to the top of the panel, which trapped controller users in this section */}
          <Btn tone={done ? undefined : "amber"}>
            <ButtonItem
              layout="below"
              description={done ? <span style={{ color: RS.ok }}>✓ The recommended latency settings are in place.</span> : undefined}
              onClick={() => {
                if (disabled) return toast("Close the game first: these settings are read when it starts");
                if (done) return toast("Recommended latency settings already in place");
                for (const [s, k, v] of recs) {
                  const f = byKey(`${s}.${k}`);
                  if (f) staged.set(game, file, f, v);
                }
                toast("Recommended latency settings staged: Save .ini to keep them");
              }}
            >
              {done ? "Recommended latency settings ✓" : "Use recommended latency settings"}
            </ButtonItem>
          </Btn>
        </Row>
      )}
      {shown.map((f) => {
        const r = recFor(f);
        return <FieldRow key={f.id} game={game} file={file} f={{ ...f, desc: `${f.desc}${r !== undefined ? ` Recommended: ${label(f, r)}.` : ""}` }} disabled={disabled} />;
      })}
    </>
  );
}

// --- the tab ---------------------------------------------------------------------------------------------------------------

const fgFold: Record<string, string | null> = {};

export function FrameGenTab({ ctx }: { ctx: Ctx }) {
  useStaged();
  const { d, key: game, advanced } = ctx;
  const [fold, setFoldState] = useState<string | null>(fgFold[game] ?? null);
  const toggle = (id: string) => {
    const next = fold === id ? null : id;
    fgFold[game] = next;
    setFoldState(next);
  };
  const [fg, setFg] = useState<FrameGen | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [rev, setRev] = useState(0);

  useEffect(() => {
    keep(() =>
      api
        .frameGen(game)
        .then((r) => {
          setFg(r);
          setErr(null);
        })
        .catch((e) => setErr(errText(e)))
    );
  }, [game, rev, d.record?.updated, d.status.state]);

  const reload = () => {
    setRev((r) => r + 1);
    ctx.reload();
  };
  const running = d.running || !!fg?.running;
  const fgFile = fg?.file_id ?? "optiscaler";
  const fgStaged = staged.get(game, fgFile);
  const allFields = (): IniField[] => [...(fg?.edit_fields ?? []), ...(fg?.groups ?? []).flatMap((g) => g.fields), ...(fg?.latency_fields ?? []), ...(fg?.cap_field ? [fg.cap_field] : [])];
  const fileVal = (sec: string, k: string) => allFields().find((x) => x.section.toLowerCase() === sec.toLowerCase() && x.key.toLowerCase() === k.toLowerCase())?.value;
  const rawEffective = (sec: string, k: string) => {
    const e = Object.values(fgStaged).find((x) => x.section.toLowerCase() === sec.toLowerCase() && x.key.toLowerCase() === k.toLowerCase());
    return e?.value ?? fileVal(sec, k);
  };
  const effective = (sec: string, k: string) => fgNorm(sec, k, rawEffective(sec, k));
  const method = fg?.methods?.find((m) => m.edits.length > 0 && m.edits.every(([sec, k, v]) => ((sec === "OptiFG" && k === "HUDFix") || (sec === "XeFG" && k === "ForceBorderless")) || effective(sec, k) === fgNorm(sec, k, v)));
  const eff = (k: string, fallback: string) => {
    const e = Object.values(fgStaged).find((x) => x.section.toLowerCase() === "framegen" && x.key.toLowerCase() === k);
    const v = (e?.value ?? fallback).toLowerCase();
    return v === "auto" ? "nofg" : v;
  };
  const legacyType = fg?.legacy ? eff("fgtype", fg.input ?? "nofg") : null;
  const inp = legacyType ?? eff("fginput", fg?.input ?? "nofg");
  const outRaw = legacyType ? (["optifg", "nukems"].includes(legacyType) ? "fsrfg" : "nofg") : eff("fgoutput", fg?.output ?? "nofg");
  const outp = outRaw === "auto" && inp !== "nofg" ? "fsrfg" : outRaw;
  const mult = capMultiplier(inp, outp, rawEffective("XeFG", "InterpolationCount") ?? fg?.xefg_count);
  const groups: FgGroup[] = (() => {
    if (!fg?.core) return (fg?.groups ?? []).filter((g) => g.title !== "Spoofing");
    const gs = [fg.core];
    const o = fg.by_output?.[outp];
    if (o) gs.push(o);
    const i = fg.by_input?.[inp];
    if (i && (inp !== outp || !o)) {
      const have = new Set(gs.flatMap((g) => g.fields.map((f) => f.id)));
      gs.push({ title: i.title, fields: i.fields.filter((f) => !have.has(f.id)) });
    }
    return gs.filter((g) => g.fields.length);
  })();
  const plan = fg?.plans?.[method?.id ?? "custom"] ?? fg?.plans?.custom;

  /** The base cap in rendered frames right now (FramerateLimit / multiplier). */
  const currentBase = () => {
    const cap = fg?.cap_field;
    const raw = Number.parseFloat((cap && (staged.get(game, fgFile)[cap.id]?.value ?? cap.value)) || "");
    return Number.isFinite(raw) && raw > 0 ? Math.round(raw / mult) : 0;
  };
  /** XeFG's frame count changes the multiplier: the cap keeps the same number of rendered frames. */
  const setXefgCount = (f: IniField, v: string) => {
    const base = currentBase();
    staged.set(game, fgFile, f, v);
    if (fg?.cap_field && base > 0) staged.set(game, fgFile, fg.cap_field, `${base * capMultiplier(inp, outp, v)}`);
  };

  const stageMethod = (id: string) => {
    const m = fg?.methods?.find((x) => x.id === id);
    if (!m || !fg) return;
    const cap = fg.cap_field;
    const base = currentBase();
    const all = allFields();
    for (const pm of fg.methods ?? [])
      for (const [s, k] of pm.edits) {
        const f = all.find((x) => x.section.toLowerCase() === s.toLowerCase() && x.key.toLowerCase() === k.toLowerCase());
        if (f && staged.get(game, fgFile)[f.id]) staged.set(game, fgFile, f, f.value);
        // keys missing from the .ini were staged under a synthetic id; reset those too so no previous method leaks in
        const sid = `${s}\u0001${k}`;
        const prev = staged.get(game, fgFile)[sid];
        if (!f && prev) staged.set(game, fgFile, { id: sid, section: s, key: k, value: prev.was }, prev.was);
      }
    for (const [s, k, v] of m.edits) {
      const f = all.find((x) => x.section.toLowerCase() === s.toLowerCase() && x.key.toLowerCase() === k.toLowerCase());
      staged.set(game, fgFile, f ?? { id: `${s}\u0001${k}`, section: s, key: k, value: "" }, v);
    }
    // the cap stays the same number of rendered frames when the FG multiplier changes
    if (cap && base > 0) {
      const [mi, mo] = methodIO(m);
      staged.set(game, fgFile, cap, `${base * capMultiplier(mi, mo, rawEffective("XeFG", "InterpolationCount") ?? fg.xefg_count)}`);
    }
  };

  return (
    <PanelSection>
      {err && (
        <Row>
          <Tip tone="danger">{err}</Tip>
        </Row>
      )}
      {!fg && (
        <Row>
          <Line>Loading…</Line>
        </Row>
      )}
      {fg?.fallback && <FallbackCard key={`${fg.fallback.installed}-${fg.fallback.donor}`} ctx={ctx} fb={fg.fallback} onChanged={reload} />}
      {fg && !fg.available && !fg.fallback && (
        <Row>
          <Tip always>{fg.reason}</Tip>
        </Row>
      )}
      {fg?.available && (
        <>
          <SaveBar game={game} running={running} onSaved={reload} where="fg-top" />
          {advanced && (
            <SectionTitle top={8} desc="Pick how frames are generated. RAT sets OptiScaler's FG input, output and their options together; Save .ini, then start the game.">
              Frame generation
            </SectionTitle>
          )}
          {!advanced && (
            <Row id="fg-method">
              <ChoiceSlider
                label="Method"
                desc={method?.hint ?? "Custom combination: open Advanced below."}
                values={[
                  ...(fg.methods ?? []).map((m) => [m.id, `${m.label}${m.missing.length ? " · files missing" : m.game_note ? " · game lacks it" : ""}`] as [string, string]),
                  ["custom", "Custom (see Advanced)"] as [string, string],
                ]}
                value={method?.id ?? "custom"}
                noToggle
                disabled={running}
                colorOf={(id) => FAMILY[methodFamily((fg.methods ?? []).find((m) => m.id === id)?.label ?? "") as keyof typeof FAMILY]?.c ?? RS.amber}
                onChange={(id) => {
                  if (id !== "custom") stageMethod(id);
                }}
              />
            </Row>
          )}
          {advanced && <Row id="fg-method">
            <PickerItem
              label="Method"
              title="Frame generation method"
              description={hint(method?.hint ?? "Custom combination (see the options below).")}
              disabled={running}
              options={[
                ...(fg.methods ?? []).map((m) => ({
                  data: m.id,
                  label: m.label,
                  family: methodFamily(m.label) as any,
                  sub: m.missing.length ? "FILES MISSING" : m.game_note ? "GAME LACKS IT" : undefined,
                })),
                ...(method ? [] : [{ data: "custom", label: "Custom", family: "neutral" as const }]),
              ]}
              selected={method?.id ?? "custom"}
              onChange={(o) => {
                if (o.data !== "custom") stageMethod(o.data);
              }}
            />
          </Row>}
          {advanced && method && method.id !== "off" && (
            <Row id="fg-guide">
              <MethodGuide m={method} />
            </Row>
          )}
          {method?.missing.length ? (
            <Row>
              <Tip tone="warn">
                This install does not have {method.missing.join(" + ")}
                {fg.env?.kind === "d4r" ? ": add that group in the FG fallback above, or pick another method." : ": pick another method."}
              </Tip>
            </Row>
          ) : null}
          {method?.game_note && (
            <Row>
              <Tip tone="warn">{method.game_note}</Tip>
            </Row>
          )}
          {(fg.warnings ?? []).map((w, i) => (
            <Row key={i}>
              <Tip tone="warn">{w}</Tip>
            </Row>
          ))}

          {fg.cap_field ? (
            <BaseCap game={game} file={fgFile} f={fg.cap_field} mult={mult} disabled={running} />
          ) : (
            <Row>
              <Tip always>This OptiScaler build has no framerate limiter ([Framerate] FramerateLimit), so the base framerate cap is not available.</Tip>
            </Row>
          )}

          {!advanced && plan && (
            <Fold id="fg-latency" title="Latency & Anti-Lag" count={`${plan.keys.filter((k) => (fg.latency_fields ?? []).some((f) => `${f.section}.${f.key}`.toLowerCase() === k.toLowerCase())).length} settings`} open={fold === "latency"} onToggle={() => toggle("latency")}>
              <LatencyBlock game={game} file={fgFile} fg={fg} plan={plan} effective={(s, k) => (rawEffective(s, k) ?? "auto").trim().toLowerCase()} disabled={running} bare />
            </Fold>
          )}
          {!advanced && method && method.id !== "off" && (
            <Fold id="fg-guide" title="How this method works" count="guide" open={fold === "guide"} onToggle={() => toggle("guide")}>
              <Row id="fg-guide">
                <MethodGuide m={method} />
              </Row>
            </Fold>
          )}
          {!advanced && (
            <Fold
              id="fg-adv"
              title="Advanced"
              count={`${groups.reduce((n, g) => n + g.fields.length, 0) + (fg.spoofing?.fields.length ?? 0)} settings`}
              open={fold === "adv"}
              onToggle={() => toggle("adv")}
            >
              {groups.map((g) => (
                <div key={g.title}>
                  <Row>
                    <div style={{ marginTop: 8, color: RS.accent, fontSize: 11, fontWeight: 700, letterSpacing: 0.6, textTransform: "uppercase" }}>{g.title}</div>
                  </Row>
                  {g.fields.map((f) => (
                    <FieldRow
                      key={f.id}
                      game={game}
                      file={fgFile}
                      f={f}
                      disabled={running}
                      onChange={f.section.toLowerCase() === "xefg" && f.key.toLowerCase() === "interpolationcount" ? (v) => setXefgCount(f, v) : undefined}
                    />
                  ))}
                </div>
              ))}
              {fg.spoofing && fg.spoofing.fields.length > 0 && (
                <>
                  <Row>
                    <div style={{ marginTop: 8, color: RS.accent, fontSize: 11, fontWeight: 700, letterSpacing: 0.6, textTransform: "uppercase" }}>Spoofing</div>
                  </Row>
                  {fg.spoofing.fields.map((f) => (
                    <FieldRow key={f.id} game={game} file={fgFile} f={f} disabled={running} />
                  ))}
                </>
              )}
            </Fold>
          )}
          {advanced && groups.map((g) => (
            <div key={g.title}>
              <Row>
                <div style={{ marginTop: 8, color: RS.accent, fontSize: 11, fontWeight: 700, letterSpacing: 0.6, textTransform: "uppercase" }}>{g.title}</div>
              </Row>
              {g.fields.map((f) => (
                <FieldRow
                  key={f.id}
                  game={game}
                  file={fgFile}
                  f={f}
                  disabled={running}
                  onChange={f.section.toLowerCase() === "xefg" && f.key.toLowerCase() === "interpolationcount" ? (v) => setXefgCount(f, v) : undefined}
                />
              ))}
            </div>
          ))}

          {advanced && plan && <LatencyBlock game={game} file={fgFile} fg={fg} plan={plan} effective={(s, k) => (rawEffective(s, k) ?? "auto").trim().toLowerCase()} disabled={running} />}

          {advanced && fg.spoofing && fg.spoofing.fields.length > 0 && (
            <>
              <SectionTitle desc="Shows the game an NVIDIA GPU. Needed on AMD / Intel for the game's own DLSS FG methods and to unlock DLSS in menus; leave it alone for OptiFG and FSR / XeSS inputs.">
                Spoofing
              </SectionTitle>
              {fg.spoofing.fields.map((f) => (
                <FieldRow key={f.id} game={game} file={fgFile} f={f} disabled={running} />
              ))}
            </>
          )}
          {advanced && <SaveBar game={game} running={running} onSaved={reload} where="fg-bottom" />}
        </>
      )}
    </PanelSection>
  );
}
