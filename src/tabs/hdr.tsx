// [ HDR ]: RenoDX for the selected game. Official game mods (matched by Steam app id / title from RenoDX's index),
// community forks from the wiki, an engine-based auto profile when no game mod exists, or a custom .addon64 / .addon32.
import { ButtonItem, DialogButton, Focusable, SliderField, TextField, ToggleField, ModalRoot, showModal } from "@decky/ui";
import { openFilePicker } from "@decky/api";
import { useEffect, useRef, useState } from "react";
import { api, errText, HdrCandidate, HdrMod, HdrState } from "../api";
import { ChoiceSlider } from "../controls";
import { toast } from "../launch";
import type { Ctx } from "../ctx";
import { confirm, confirmUninstall } from "../modals";
import { PickerItem } from "../picker";
import { hintsOn } from "../prefs";
import { keep } from "../scroll";
import { Badge, Btn, Card, FAMILY, Shell, hint, Line, RS, Row, RuntimeCard, SectionTitle, Tip, UpLabel } from "../theme";

const SOURCE_FAMILY: Record<string, keyof typeof FAMILY> = { official: "dlss", fork: "fork", auto: "xess", custom: "fsr" };
const SOURCE_LABEL: Record<string, string> = { official: "Official", fork: "Community", auto: "Auto profile", custom: "Custom" };

const ENGINE_NAME: Record<string, string> = { unreal: "Unreal Engine", unity: "Unity", source: "Source", other: "Engine not detected" };

/** What RAT detected about the game's pipeline and the RenoDX settings it applied for it. */
function AutoConfig({ st, running, onChange, gameKey, act }: { st: HdrState; running: boolean; onChange: (s: HdrState) => void; gameKey: string; act: <T>(fn: () => Promise<T>, ok?: string) => Promise<T | null> }) {
  const [open, setOpen] = useState(false);
  const c = st.context;
  const rows = st.auto?.report ?? [];
  const kept = rows.filter((r) => r.kept).length;
  return (
    <>
      <SectionTitle desc="RAT reads the mod's notes from RenoDX's index and wiki, detects the engine and graphics pipeline, and sets what the mod needs to avoid a black screen: swap chain, resource upgrades, brightness. Your own changes in RenoDX's menu are kept.">
        Auto-configuration
      </SectionTitle>
      {c && (
        <Row id="hdr-ctx">
          <Card accent={RS.info}>
            <Line color={RS.text}>
              {ENGINE_NAME[c.engine] ?? c.engine}
              {c.engine_version ? ` ${c.engine_version}` : ""} · {c.api ?? "API ?"}
              {c.api_live ? " (live)" : ""} · {c.arch}
            </Line>
            <Line size={10}>Input: {c.input}</Line>
            <Line size={10}>Output: {c.output} · swap chain {c.swap_chain}</Line>
            {c.injector && (
              <Line size={10}>
                In front of ReShade: {c.injector}
                {c.frame_generation ? " with frame generation" : ""}
                {c.chained ? " (ReShade loaded through it)" : ""}
              </Line>
            )}
            {st.dir && <Line size={9.5}>Folder: {st.dir}</Line>}
          </Card>
        </Row>
      )}
      {(st.auto?.advice ?? []).map((a, i) => (
        <Row key={i}>
          <Tip tone="warn">{a}</Tip>
        </Row>
      ))}
      <Row id="hdr-auto-list">
        <Btn>
          <ButtonItem layout="below" onClick={() => setOpen(!open)}>
            {open ? "Hide" : "Show"} applied settings ({rows.length - kept} set{kept ? `, ${kept} kept as you set them` : ""})
          </ButtonItem>
        </Btn>
      </Row>
      {open &&
        rows.map((r) => (
          <Row key={`${r.section}-${r.key}`}>
            <div style={{ width: "100%", fontSize: 10.5, lineHeight: 1.35, padding: "2px 0" }}>
              <span style={{ fontFamily: "monospace", color: r.kept ? RS.dim : RS.text }}>
                [{r.section}] {r.key}={r.value}
              </span>
              {r.kept && <span style={{ color: RS.amber }}> · yours kept</span>}
              <div style={{ color: RS.muted, fontSize: 9.5 }}>{r.reason}</div>
            </div>
          </Row>
        ))}
      {running && (
        <Row>
          <Tip>The game is running: changes made here take effect at the next start.</Tip>
        </Row>
      )}
      <Row id="hdr-auto-apply">
        <Btn tone="amber">
          <ButtonItem
            layout="below"
            onClick={async () => {
              const r = await act(() => api.hdrAutoconfig(gameKey, false), "Auto-configuration applied (your own changes kept)");
              if (r) onChange(r);
            }}
          >
            Re-detect & apply
          </ButtonItem>
        </Btn>
      </Row>
      <Row id="hdr-auto-force">
        <Btn>
          <ButtonItem
            layout="below"
            onClick={async () => {
              if (!(await confirm("Reset to RAT's configuration?", "Every setting RAT manages (swap chain, resource upgrades, brightness, settings mode) goes back to the detected values, including ones you changed in RenoDX's menu.", "Reset", true))) return;
              const r = await act(() => api.hdrAutoconfig(gameKey, true), "RenoDX settings reset to the detected configuration");
              if (r) onChange(r);
            }}
          >
            Reset to detected configuration
          </ButtonItem>
        </Btn>
      </Row>
    </>
  );
}

function CustomModal({ onLink, onFile, closeModal }: { onLink: (u: string) => void; onFile: () => void; closeModal?: () => void }) {
  const [url, setUrl] = useState("");
  return (
    <ModalRoot closeModal={closeModal}>
      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        <div style={{ fontSize: 18, fontWeight: 600 }}>Custom RenoDX mod</div>
        <div style={{ fontSize: 13, opacity: 0.8 }}>Paste an https:// link to a .addon64 / .addon32 file, or pick one from a drive (for example a mod you downloaded from Nexus Mods). Only use mods you trust.</div>
        <TextField label="Link" value={url} bShowClearAction onChange={(e) => setUrl(e.target.value)} />
        <Focusable style={{ display: "flex", gap: 8 }} flow-children="horizontal">
          <DialogButton
            disabled={!url.trim()}
            onClick={() => {
              onLink(url.trim());
              closeModal?.();
            }}
          >
            Install from link
          </DialogButton>
          <DialogButton
            onClick={() => {
              closeModal?.();
              onFile();
            }}
          >
            Pick a file…
          </DialogButton>
        </Focusable>
      </div>
    </ModalRoot>
  );
}

export function HdrTab({ ctx, bare }: { ctx: Ctx; bare?: boolean }) {
  const { d, key, advanced } = ctx;
  const [st, setSt] = useState<HdrState | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [pick, setPick] = useState<string | null>(null);
  const [rev, setRev] = useState(0);
  // while the game runs, re-check every few seconds what is really loaded in it (the "Running now" card)
  useEffect(() => {
    if (!ctx.d.running) return;
    const t = setInterval(() => Date.now() - lastEdit.current > 3000 && setRev((r) => r + 1), 8000);
    return () => clearInterval(t);
  }, [ctx.d.running]);
  const timers = useRef<Record<string, any>>({});
  const [anyMods, setAnyMods] = useState<HdrMod[] | null>(null);
  const setParam = (name: string, v: number, delay: number) => {
    if (st) setSt({ ...st, params: st.params.map((p) => (p.key === name ? { ...p, value: v } : p)) });
    clearTimeout(timers.current[name]);
    lastEdit.current = Date.now();
    timers.current[name] = setTimeout(() => ctx.act(() => api.hdrSetting(key, name, v)).then((r) => r && setSt(r)), delay);
  };
  const lastEdit = useRef(0);

  useEffect(() => {
    keep(() =>
      api
        .hdrState(key, false)
        .then((s) => {
          setSt(s);
          setErr(null);
        })
        .catch((e) => setErr(errText(e)))
    );
  }, [key, rev, ctx.job?.state]);

  const running = d.running || !!st?.running;
  const c = st?.candidates;
  const all: HdrCandidate[] = c ? [...c.official, ...c.forks, ...(c.auto?.supported ? [c.auto] : [])] : [];
  const best = all.find((x) => x.downloadable) ?? null;
  const chosen = all.find((x) => x.id === pick) ?? best;

  const install = (choiceId: string, url: string | null = null, path: string | null = null) =>
    ctx.startJob(
      () => api.hdrInstall(key, choiceId, url, path),
      () => setRev((r) => r + 1)
    );
  const custom = () =>
    showModal(
      <CustomModal
        onLink={(u) => install("custom", u, null)}
        onFile={async () => {
          try {
            const r = await openFilePicker(0 as any, ctx.ov.browse_root, true, true, undefined, ["addon64", "addon32"], false, false);
            if (r?.path) install("custom", null, r.realpath || r.path);
          } catch {
            /* closed */
          }
        }}
      />
    );

  return (
    <Shell bare={bare}>
      {err && (
        <Row>
          <Tip tone="danger">{err}</Tip>
        </Row>
      )}
      {advanced && (
        <SectionTitle top={8} desc="RenoDX rebuilds a game's tone mapping for real HDR: HDR for SDR games, and fixes for broken native HDR. Turn HDR on in SteamOS (Settings → Display) too.">
          RenoDX HDR
        </SectionTitle>
      )}
      {!st && (
        <Row>
          <Line>Matching RenoDX mods…</Line>
        </Row>
      )}
      {st?.installed && st.record && (
        <>
          <Row id="hdr-installed">
            <Card accent={FAMILY[SOURCE_FAMILY[st.record.source] ?? "neutral"].c} glow={st.enabled}>
              <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <UpLabel family={SOURCE_FAMILY[st.record.source] as any} text={st.record.title} />
                <Badge label={SOURCE_LABEL[st.record.source] ?? st.record.source} color={FAMILY[SOURCE_FAMILY[st.record.source] ?? "neutral"].c} />
              </div>
              <Line>
                {st.record.file} · {st.record.arch}
                {st.record.maintainers?.length ? ` · by ${st.record.maintainers.join(", ")}` : ""}
              </Line>
              <Line>ReShade: {st.reshade.active ? `${st.reshade.version ?? ""} add-on build` : st.reshade.paused ? "switched off on the ReShade section (HDR paused)" : "missing (reinstall HDR)"}</Line>
            </Card>
          </Row>
          <Row id="hdr-on">
            <ToggleField
              label="RenoDX enabled"
              description={hint("Master switch: off keeps the mod and its settings but the game starts in SDR (HDR launch variables removed).")}
              checked={st.enabled}
              disabled={running}
              onChange={async (v) => {
                const r = await ctx.act(() => api.hdrEnable(key, v));
                if (r) {
                  setSt(r);
                  ctx.syncLaunch();
                }
              }}
            />
          </Row>
          {st.reshade.paused && (
            <Row>
              <Tip tone="warn">ReShade is switched off on the ReShade section, so RenoDX cannot load either.</Tip>
            </Row>
          )}
          {!st.reshade.active && !st.reshade.paused && (
            <Row>
              <Tip tone="warn">ReShade is missing from this game, so RenoDX cannot load. Press Reinstall HDR below: it sets up ReShade again too.</Tip>
            </Row>
          )}
          {st.reshade.runtime && (
            <Row id="hdr-runtime">
              <RuntimeCard rt={st.reshade.runtime} />
            </Row>
          )}
          {advanced && <>
          <AutoConfig st={st} running={running} onChange={setSt} gameKey={key} act={ctx.act} />
          <SectionTitle desc="RenoDX's settings for this game, written to ReShade.ini ([renodx-preset1]). After the first start every key RenoDX uses shows here.">
            RenoDX settings
          </SectionTitle>
          {running && (
            <Row>
              <Tip>The game is running: you can still change these with the controller. RenoDX reads them when the game loads it, so they take effect at the next start (or when you switch RenoDX's preset in its menu).</Tip>
            </Row>
          )}
          {st.params.map((k) => (
            <Row key={k.key} id={`hdr-${k.key}`}>
              {k.kind === "choice" && k.choices ? (
                <ChoiceSlider
                  label={k.label}
                  desc={k.desc}
                  values={k.choices.map((c, i) => [String(i), c] as [string, string])}
                  value={String(Math.round(k.value))}
                  onChange={(v) => setParam(k.key, Number(v), 0)}
                />
              ) : (
                <SliderField
                  label={k.label}
                  description={hint(k.desc || (k.known ? undefined : "Written by RenoDX; range guessed from its value."))}
                  value={k.value}
                  min={k.min}
                  max={k.max}
                  step={k.step}
                  showValue
                  valueSuffix={k.unit ? ` ${k.unit}` : undefined}
                  editableValue
                  onChange={(v) => setParam(k.key, v, 450)}
                />
              )}
            </Row>
          ))}
          <Row id="hdr-io">
            <Focusable style={{ display: "flex", gap: 5, width: "100%" }} flow-children="horizontal">
              <DialogButton
                className="RS_Tab"
                style={{ flex: 1, fontSize: 10, padding: "6px 2px" }}
                onClick={async () => {
                  const r = await ctx.act(() => api.hdrExport(key));
                  if (r) toast(`${r.count} RenoDX settings saved to ${r.path}`);
                }}
              >
                SAVE TO .INI
              </DialogButton>
              <DialogButton
                className="RS_Tab"
                style={{ flex: 1, fontSize: 10, padding: "6px 2px" }}
                onClick={async () => {
                  try {
                    const r = await openFilePicker(0 as any, `${ctx.ov.exports}/renodx`, true, true, undefined, ["ini"], false, false);
                    if (!r?.path) return;
                    const res = await ctx.act(() => api.hdrImport(key, r.realpath || r.path));
                    if (res) {
                      setSt(res);
                      toast(`Loaded ${res.imported} RenoDX settings`);
                    }
                  } catch {
                    /* closed */
                  }
                }}
              >
                LOAD .INI…
              </DialogButton>
            </Focusable>
          </Row>
          <Row id="hdr-uninstall">
            <Btn tone="danger">
              <ButtonItem
                layout="below"
                disabled={running || ctx.jobRunning}
                onClick={async () => {
                  const choice = await confirmUninstall(key, d.game.appid, "renodx", "Remove RenoDX?", "Deletes the mod and its settings from ReShade.ini. ReShade goes too unless the ReShade fold uses it.");
                  if (choice) {
                    const r = await ctx.act(() => api.hdrUninstall(key));
                    if (r) {
                      setSt(r);
                      ctx.syncLaunch(choice === "restore");
                    }
                  }
                }}
              >
                Remove HDR
              </ButtonItem>
            </Btn>
          </Row>
          </>}
        </>
      )}

      {c && (advanced || !st?.installed) && (
        <>
          {advanced && <SectionTitle desc={`${d.game.name}: ${[c.engine !== "other" ? c.engine : null, c.api, c.arch].filter(Boolean).join(" · ")}`}>
            {st?.installed ? "Switch mod" : "Available mods"}
          </SectionTitle>}
          {all.length === 0 && (
            <Row>
              <Tip tone="warn" always>
                {c.auto?.reason ?? "No RenoDX mod for this game."}
              </Tip>
            </Row>
          )}
          {all.length > 0 && (
            <Row id="hdr-pick">
              <PickerItem
                label="Mod"
                title="RenoDX mod"
                description={hint(chosen?.source === "auto" ? `${chosen.reason}. ${chosen.notes?.[0] ?? ""}` : chosen?.notes?.[0] ?? (chosen?.maintainers.length ? `by ${chosen.maintainers.join(", ")}` : undefined))}
                options={all.map((x) => ({
                  data: x.id,
                  label: `${x.title}${x.variant ? ` (${x.variant})` : ""}`,
                  family: SOURCE_FAMILY[x.source] as any,
                  sub: `${SOURCE_LABEL[x.source]}${x.status ? ` · ${x.status}` : ""}${x.downloadable ? "" : " · manual"}`,
                }))}
                selected={chosen?.id}
                onChange={(o) => setPick(o.data)}
              />
            </Row>
          )}
          {chosen?.source === "auto" && chosen.known && (
            <Row>
              <Tip tone={chosen.known.status === "working" ? "ok" : "info"} always>
                Listed on RenoDX's {chosen.title} page: {chosen.known.status ?? "listed"}
                {chosen.known.note ? ` · ${chosen.known.note}` : ""}
              </Tip>
            </Row>
          )}
          {chosen?.source === "auto" && !chosen.known && hintsOn() && (
            <Row>
              <Tip>No game-specific mod: RAT recommends the engine-wide mod and configures it for this engine (swap chain, resource upgrades, peak {st?.peak_nits} nits, paper white 203 nits).</Tip>
            </Row>
          )}
          {chosen?.downloadable && hintsOn() && (
            <Row>
              <Tip>Enable HDR downloads, installs and links ReShade (add-on build) together with the mod, next to the game's real executable, then applies the settings this engine needs.</Tip>
            </Row>
          )}
          {st?.pending && !st.installed && (
            <Row>
              <Tip tone="warn">A pasted profile asks for HDR ({st.pending.title ?? st.pending.id}). It is set up with the next ReShade install, or press Enable HDR.</Tip>
            </Row>
          )}
          {chosen && !chosen.downloadable && (
            <Row>
              <Tip tone="warn">
                No {c.arch} download for this mod{chosen.links?.length ? ` (get it from ${chosen.links[0]}, then use Custom mod)` : ""}.
              </Tip>
            </Row>
          )}
          {chosen?.downloadable && (
            <Row id="hdr-install">
              <Btn tone="primary">
                <ButtonItem layout="below" disabled={running || ctx.jobRunning} onClick={() => install(chosen.id)}>
                  {st?.installed ? (st.record?.id === chosen.id ? `Reinstall HDR · ${chosen.title}` : `Switch to ${chosen.title}`) : `Enable HDR · ${chosen.title}`}
                </ButtonItem>
              </Btn>
            </Row>
          )}
          {advanced && <>
          <Row id="hdr-any">
            {anyMods ? (
              <PickerItem
                label="Any game's mod"
                title="All RenoDX mods"
                description={hint("Try a mod made for another game (another edition, the same engine). No app id or title check.")}
                placeholder={`${anyMods.length} mods`}
                options={anyMods.map((x) => ({
                  data: x.id,
                  label: x.title,
                  family: SOURCE_FAMILY[x.source] as any,
                  sub: x.game !== x.title ? x.game : SOURCE_LABEL[x.source],
                }))}
                selected={null}
                disabled={running || ctx.jobRunning}
                onChange={async (o) => {
                  const m = anyMods.find((x) => x.id === o.data);
                  if (m && (await confirm(`Install ${m.title} in ${d.game.name}?`, `This mod was made for ${m.game}. It may not match this game's shaders; if the picture breaks, switch back or remove HDR.`, "Install")))
                    install(m.id);
                }}
              />
            ) : (
              <Btn>
                <ButtonItem layout="below" disabled={running || ctx.jobRunning} onClick={() => keep(() => api.hdrBrowse(false).then(setAnyMods).catch((e) => setErr(errText(e))))}>
                  Browse every game's RenoDX mods…
                </ButtonItem>
              </Btn>
            )}
          </Row>
          <Row id="hdr-custom">
            <Btn>
              <ButtonItem layout="below" disabled={running || ctx.jobRunning} onClick={custom}>
                Custom mod (link or file)…
              </ButtonItem>
            </Btn>
          </Row>
          <Row id="hdr-refresh">
            <Btn>
              <ButtonItem
                layout="below"
                onClick={() =>
                  keep(() =>
                    api
                      .hdrState(key, true)
                      .then(setSt)
                      .catch((e) => setErr(errText(e)))
                  )
                }
              >
                Refresh RenoDX mod list
              </ButtonItem>
            </Btn>
          </Row>
          </>}
        </>
      )}
      {!advanced && st?.installed && st.record && (
        <Row id="hdr-install">
          <Btn>
            <ButtonItem
              layout="below"
              disabled={running || ctx.jobRunning}
              onClick={() => (st.record!.source === "custom" ? custom() : install(st.record!.id))}
            >
              Reinstall HDR
            </ButtonItem>
          </Btn>
        </Row>
      )}
      {advanced && hintsOn() && (
        <Row>
          <Line size={10}>Launch variables RAT adds with HDR on: DXVK_HDR=1 PROTON_ENABLE_HDR=1 ENABLE_HDR_WSI=1 ENABLE_GAMESCOPE_WSI=1. RenoDX by Carlos Lopez Jr. (clshortfuse) and contributors, MIT.</Line>
        </Row>
      )}
    </Shell>
  );
}
