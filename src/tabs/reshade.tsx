// [ RESHADE ]: ReShade for the selected game (official binaries from reshade.me), presets, effects, ReShade's official
// shader packages and a gamescope compositor mode for Vulkan games.
import { ButtonItem, DialogButton, Focusable, ToggleField } from "@decky/ui";
import { openFilePicker } from "@decky/api";
import { useEffect, useState } from "react";
import { api, errText, PresetEffects, Presets, ReShadeState, ShaderPackage } from "../api";
import type { Ctx } from "../ctx";
import { askText } from "../controls";
import { toast } from "../launch";
import { confirm, confirmUninstall } from "../modals";
import { PickerItem } from "../picker";
import { hintsOn } from "../prefs";
import { keep } from "../scroll";
import { ShaderParams } from "../shaderparams";
import { Badge, Btn, Card, FAMILY, Shell, hint, Line, RS, Row, RuntimeCard, SectionTitle, sortedOptions, Tip, UpLabel } from "../theme";

const openParams: Record<string, boolean> = {};

/** Feedback after a preset edit while the game runs: applied live (ReShade re-read the preset) or at the next start. */
export function liveToast(applied?: string | null) {
  if (applied === "live") toast("Applied in the game");
  else if (applied === "next") toast("Saved: ReShade picks it up at the next start");
}

export function ReShadeTab({ ctx, bare }: { ctx: Ctx; bare?: boolean }) {
  const { d, key, ov, advanced } = ctx;
  const [st, setSt] = useState<ReShadeState | null>(null);
  const [presets, setPresets] = useState<Presets | null>(null);
  const [fx, setFx] = useState<PresetEffects | null>(null);
  const [pkgs, setPkgs] = useState<ShaderPackage[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [rev, setRev] = useState(0);
  const [addon, setAddon] = useState(true);
  const [proxy, setProxy] = useState("auto");
  const [showPkgs, setShowPkgs] = useState(false);
  const [turnedOff, setTurnedOff] = useState<Set<string>>(new Set());
  // while the game runs, re-check every few seconds what is really loaded in it (the "Running now" card)
  useEffect(() => {
    if (!ctx.d.running) return;
    const t = setInterval(() => setRev((r) => r + 1), 8000);
    return () => clearInterval(t);
  }, [ctx.d.running]);
  const [, setParamsRev] = useState(0);

  useEffect(() => {
    keep(async () => {
      try {
        const s = await api.reshadeState(key);
        setSt(s);
        setErr(null);
        if (s.record) {
          setAddon(s.record.addon !== false);
          setProxy(s.record.proxy_pref || "auto");
        }
        setFx(await api.reshadeEffects(key)); // also lists effects for compositor mode
        setPresets(s.installed ? await api.reshadePresets(key) : null);
      } catch (e) {
        setErr(errText(e));
      }
    });
  }, [key, rev, ctx.job?.state]);

  useEffect(() => {
    if (showPkgs && !pkgs) api.reshadePackages(false).then(setPkgs).catch((e) => setErr(errText(e)));
  }, [showPkgs]);

  const running = d.running || !!st?.running;
  const reload = () => setRev((r) => r + 1);
  const presetAction = async (action: string, name = "", arg = "") => {
    const r = await ctx.act(() => api.reshadePresetAction(key, action, name, arg));
    if (r && ["activate", "new", "delete", "from_library", "import"].includes(action)) setTurnedOff(new Set());
    if (r) {
      setPresets(r);
      if (r.exported) toast(`Exported to ${r.exported}`);
      setFx(await api.reshadeEffects(key));
    }
  };
  const versionOpts = st
    ? [
        { data: "latest", label: `Latest from reshade.me (${st.versions.latest})` },
        ...st.versions.pinned.map((v) => ({ data: v, label: v === "6.7.3" ? `${v} (stable on Steam Deck)` : v })),
      ]
    : [];
  const enabled = new Set(fx?.enabled ?? []);
  // an effect switched off stays listed (unchecked) until the tab reopens, so the focused toggle never disappears
  const shownFx = [...enabled, ...[...turnedOff].filter((id) => !enabled.has(id))];
  const fxById = new Map((fx?.effects ?? []).map((e) => [e.id, e]));
  const addable = sortedOptions((fx?.effects ?? []).filter((e) => !enabled.has(e.id) && !turnedOff.has(e.id)).map((e) => ({ data: e.id, label: e.label, sub: e.file })));

  return (
    <Shell bare={bare}>
      {err && (
        <Row>
          <Tip tone="danger">{err}</Tip>
        </Row>
      )}
      {advanced && (
        <SectionTitle top={8} desc="Official ReShade from reshade.me, copied next to the game as the DLL its graphics API loads. With an OptiScaler-based injector, OptiScaler loads ReShade so both run.">
          ReShade
        </SectionTitle>
      )}
      {!st && (
        <Row>
          <Line>Loading…</Line>
        </Row>
      )}
      {st && (
        <>
          <Row id="rs-status">
            <Card accent={st.installed ? FAMILY.fork.c : RS.dim} glow={st.installed}>
              <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <span style={{ fontSize: 12, fontWeight: 650, color: RS.text }}>{st.installed ? `ReShade ${st.record?.reshade_version}` : "Not installed"}</span>
                {st.installed && <Badge label={st.record?.addon ? "add-on build" : "standard"} color={FAMILY.fork.c} />}
                {st.installed && <Badge label={st.record?.mode === "chain" ? "via OptiScaler" : st.record?.proxy ?? ""} tone="info" />}
              </div>
              <Line>
                {[st.api, st.arch].filter(Boolean).join(" · ") || "API not detected"}
                {st.installed && st.record?.requested_by?.includes("hdr") ? " · used by HDR" : ""}
              </Line>
              {hintsOn() && st.installed && !st.runtime && <Line size={10}>Home opens ReShade's overlay in game.</Line>}
            </Card>
          </Row>
          {st.runtime && (
            <Row id="rs-runtime">
              <RuntimeCard rt={st.runtime} />
            </Row>
          )}
          {st.installed && (
            <Row id="rs-on">
              <ToggleField
                label="ReShade enabled"
                description={hint("Master switch: off takes ReShade's DLL out of the game (RenoDX HDR pauses with it); presets, shaders and settings stay.")}
                checked={st.record?.enabled !== false}
                disabled={running}
                onChange={async (v) => {
                  const r = await ctx.act(() => api.reshadeEnable(key, v));
                  if (r) {
                    setSt(r);
                    ctx.syncLaunch();
                  }
                }}
              />
            </Row>
          )}
          {!st.supported && (
            <Row>
              <Tip tone="warn">{st.reason}</Tip>
            </Row>
          )}
          {advanced && <>
          <Row id="rs-version">
            <PickerItem
              label="ReShade version"
              description={hint("If ReShade does not load in a game, try 6.7.3. Applies at the next install.")}
              options={versionOpts}
              selected={ov.prefs.reshade_version}
              onChange={async (o) => {
                const p = await ctx.act(() => api.setPref("reshade_version", o.data));
                if (p) ctx.setOv({ ...ov, prefs: p });
              }}
            />
          </Row>
          <Row id="rs-addon">
            <ToggleField
              label="Full add-on support"
              description={hint("Needed for RenoDX HDR and other add-ons. Avoid in online games: the add-on build is unsigned.")}
              checked={addon}
              disabled={running || (st.record?.requested_by ?? []).includes("hdr")}
              onChange={setAddon}
            />
          </Row>
          <Row id="rs-proxy">
            <PickerItem
              label="Load as"
              description={hint("Auto picks from the game's graphics API: dxgi.dll for DX10-12, d3d9.dll, opengl32.dll…")}
              options={st.proxy_choices.map((p) => ({ data: p, label: p === "auto" ? `Auto${st.api ? ` (${st.api})` : ""}` : p, family: "neutral" as const }))}
              selected={proxy}
              disabled={running}
              onChange={(o) => setProxy(o.data)}
            />
          </Row>
          </>}
          <Row id="rs-install">
            <Btn tone="primary">
              <ButtonItem
                layout="below"
                disabled={running || ctx.jobRunning || (!st.supported && proxy === "auto")}
                onClick={() => ctx.startJob(() => api.reshadeInstall(key, addon, proxy), reload)}
              >
                {st.installed ? "Reinstall / update ReShade" : "Install ReShade"}
              </ButtonItem>
            </Btn>
          </Row>
          {advanced && st.installed && (
            <Row id="rs-remove">
              <Btn tone="danger">
                <ButtonItem
                  layout="below"
                  disabled={running || ctx.jobRunning}
                  onClick={async () => {
                    const choice = await confirmUninstall(key, d.game.appid, "reshade", "Remove ReShade?", "Deletes ReShade's DLL and ReShade.ini from the game. Your presets stay.");
                    if (!choice) return;
                    const r = await ctx.act(() => api.reshadeUninstall(key));
                    if (r?.kept_for?.length) toast("ReShade stays: the HDR section uses it");
                    reload();
                    if (!r?.removed) return;
                    ctx.syncLaunch(choice === "restore");
                  }}
                >
                  Remove ReShade
                </ButtonItem>
              </Btn>
            </Row>
          )}
        </>
      )}

      {st?.installed && presets && (
        <>
          {advanced && <SectionTitle desc="Presets are .ini files next to the game. The library keeps copies you can reuse in other games.">Presets</SectionTitle>}
          {running && (
            <Row>
              <Tip>The game is running: effect toggles and parameters apply live (RAT taps ReShade's preset key). Switching to another preset applies at the next start.</Tip>
            </Row>
          )}
          <Row id="rs-preset">
            <PickerItem
              label="Active preset"
              options={presets.game.map((p) => ({ data: p.name, label: p.name, sub: `${p.effects} FX`, family: "fork" as const }))}
              selected={presets.active}
              placeholder="No preset"
              onChange={(o) => presetAction("activate", o.data)}
            />
          </Row>
          {advanced && <>
          <Row id="rs-preset-a">
            <Focusable style={{ display: "flex", gap: 5, width: "100%" }} flow-children="horizontal">
              {[
                ["NEW", async () => {
                  const n = await askText("New preset", "MyPreset", "Name of the new preset file");
                  if (n) presetAction("new", n);
                }],
                ["COPY", () => presets.active && presetAction("duplicate", presets.active)],
                ["EXPORT", () => presets.active && presetAction("export", presets.active)],
                ["DELETE", async () => {
                  if (presets.active && (await confirm(`Delete ${presets.active}?`, "The preset file is deleted from the game folder.", "Delete", true))) presetAction("delete", presets.active);
                }],
              ].map(([label, fn]) => (
                <DialogButton key={label as string} className="RS_Tab" style={{ flex: 1, fontSize: 10, padding: "6px 2px" }} onClick={fn as () => void}>
                  {label as string}
                </DialogButton>
              ))}
            </Focusable>
          </Row>
          <Row id="rs-preset-b">
            <Focusable style={{ display: "flex", gap: 5, width: "100%", marginTop: 5 }} flow-children="horizontal">
              <DialogButton className="RS_Tab" style={{ flex: 1, fontSize: 10, padding: "6px 2px" }} disabled={!presets.active} onClick={() => presets.active && presetAction("save_library", presets.active)}>
                SAVE TO LIBRARY
              </DialogButton>
              <DialogButton
                className="RS_Tab"
                style={{ flex: 1, fontSize: 10, padding: "6px 2px" }}
                onClick={async () => {
                  try {
                    const r = await openFilePicker(0 as any, ov.browse_root, true, true, undefined, ["ini"], false, false);
                    if (r?.path) presetAction("import", "", r.realpath || r.path);
                  } catch {
                    /* closed */
                  }
                }}
              >
                IMPORT FILE…
              </DialogButton>
            </Focusable>
          </Row>
          {presets.library.length > 0 && (
            <Row id="rs-library">
              <PickerItem
                label="From library"
                description={hint("Copies a library preset into this game and makes it active.")}
                placeholder="Pick a preset"
                options={presets.library.map((p) => ({ data: p.name, label: p.name, family: "fork" as const }))}
                selected={null}
                onChange={(o) => presetAction("from_library", o.data)}
              />
            </Row>
          )}
          </>}

          {advanced ? (
            <SectionTitle desc="Effects enabled in the active preset. Fine-tune each one in ReShade's overlay (Home).">Effects</SectionTitle>
          ) : (
            <Row>
              <div style={{ marginTop: 6, color: RS.accent, fontSize: 11, fontWeight: 700, letterSpacing: 0.6, textTransform: "uppercase" }}>Effects</div>
            </Row>
          )}
          {!presets.active && (
            <Row>
              <Tip always>{advanced ? "Create a preset first (NEW)." : "Create a preset first: turn on Show advanced (end of TUNE), then NEW."}</Tip>
            </Row>
          )}
          {presets.active && (fx?.effects.length ?? 0) === 0 && (
            <Row>
              <Tip always>{advanced ? "No effects installed yet: add a shader package below." : "No effects installed yet: turn on Show advanced (end of TUNE) and add a shader package."}</Tip>
            </Row>
          )}
          {presets.active &&
            shownFx.map((id) => {
              const e = fxById.get(id);
              const file = e?.file ?? id.split("@")[1] ?? "";
              const open = !!openParams[`${key}|${id}`];
              return (
                <div key={id}>
                  <Row id={`fx-${id}`}>
                    <ToggleField
                      label={<UpLabel family="fork" text={e?.label ?? id.split("@")[0]} />}
                      description={hint(e ? `${e.file}${e.folder ? ` · ${e.folder}` : ""}` : "Not in the installed shaders")}
                      checked={enabled.has(id)}
                      onChange={async (v) => {
                        const r = await ctx.act(() => api.reshadeToggleEffect(key, id, v));
                        if (r) {
                          setTurnedOff((t) => {
                            const n = new Set(t);
                            if (v) n.delete(id);
                            else n.add(id);
                            return n;
                          });
                          setFx(r);
                          liveToast(r.applied);
                        }
                      }}
                    />
                  </Row>
                  {advanced && e && (
                    <Row id={`fx-par-${id}`}>
                      <DialogButton
                        className={`RS_Tab RS_Section${open ? " RS_Section--open" : ""}`}
                        style={{ marginLeft: 10, width: "calc(100% - 10px)" }}
                        onClick={() => {
                          openParams[`${key}|${id}`] = !open;
                          setParamsRev((n) => n + 1);
                        }}
                      >
                        <span>{open ? "▾" : "▸"} Parameters</span>
                        <span style={{ fontSize: 9.5, opacity: 0.8 }}>{file}</span>
                      </DialogButton>
                    </Row>
                  )}
                  {advanced && e && open && <ShaderParams ctx={ctx} file={file} running={false} />}
                </div>
              );
            })}
          {presets.active && addable.length > 0 && (
            <Row id="fx-add">
              <PickerItem
                label="Add effect"
                placeholder={`${addable.length} available`}
                options={addable.map((e) => ({ data: e.data, label: e.label, sub: e.sub, family: "fork" as const }))}
                selected={null}
                onChange={async (o) => {
                  const r = await ctx.act(() => api.reshadeToggleEffect(key, o.data, true));
                  if (r) {
                    setFx(r);
                    liveToast(r.applied);
                  }
                }}
              />
            </Row>
          )}
        </>
      )}

      {advanced && <>
      <SectionTitle desc="Only packages from ReShade's own official list, downloaded from their authors' GitHub repositories.">Shader packages</SectionTitle>
      <Row id="pkgs-toggle">
        <Btn>
          <ButtonItem layout="below" onClick={() => setShowPkgs((x) => !x)}>
            {showPkgs ? "Hide shader packages" : `Browse shader packages${st ? ` (${st.packages_installed} installed)` : ""}`}
          </ButtonItem>
        </Btn>
      </Row>
      {showPkgs && !pkgs && (
        <Row>
          <Line>Loading ReShade's package list…</Line>
        </Row>
      )}
      {showPkgs &&
        pkgs?.map((p) => (
          <Row key={p.key} id={`pkg-${p.key}`}>
            <ToggleField
              label={
                <span>
                  <UpLabel family={p.installed ? "fork" : "neutral"} text={p.name} />
                  {p.required ? <span style={{ marginLeft: 6 }}><Badge label="required" tone="info" /></span> : null}
                </span>
              }
              description={hint(p.description)}
              checked={p.installed}
              disabled={ctx.jobRunning || (p.required && p.installed)}
              onChange={async (v) => {
                if (v) {
                  ctx.startJob(
                    () => api.reshadeInstallPackages([p.key]),
                    async () => {
                      setPkgs(await api.reshadePackages(false));
                      reload();
                    }
                  );
                } else {
                  const r = await ctx.act(() => api.reshadeRemovePackage(p.key));
                  if (r) setPkgs(r);
                  reload();
                }
              }}
            />
          </Row>
        ))}

      <SectionTitle desc="Gamescope applies one effect to the whole screen, no injection: works for Vulkan games and anti-cheat titles. No depth buffer, one effect at a time.">
        Compositor mode
      </SectionTitle>
      <Row id="rs-compositor">
        <PickerItem
          label="Gamescope effect"
          description={hint("Applied when this game starts and removed when it closes.")}
          options={[
            { data: "__none", label: "None", family: "neutral" as const },
            ...(fx?.effects ?? []).map((e) => ({ data: e.id, label: e.label, sub: e.file, family: "fork" as const })),
          ]}
          selected={st?.compositor ?? "__none"}
          onChange={async (o) => {
            const eff = o.data === "__none" ? null : o.data;
            const r = await ctx.act(() => api.reshadeSetCompositor(key, eff, d.running));
            if (r && st) setSt({ ...st, compositor: r.effect });
          }}
        />
      </Row>
      {(fx?.effects.length ?? 0) === 0 && hintsOn() && (
        <Row>
          <Line size={10}>Install a shader package (and ReShade once, for its shader list) to pick a compositor effect.</Line>
        </Row>
      )}
      </>}
    </Shell>
  );
}
