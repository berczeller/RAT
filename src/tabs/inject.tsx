// [ INJECT ]: pick the injector and upscaling mode for the selected game, install / update / remove it, external
// injector cleanup, Proton and launch options, and the game's profile (copy / paste / export / delete).
import { ButtonItem, ConfirmModal, Field, PanelSection, SliderField, ToggleField, showModal } from "@decky/ui";
import { useRef, useState } from "react";
import { api, Detail, Option } from "../api";
import type { Ctx } from "../ctx";
import { ChoiceSlider, staged } from "../controls";
import { applyLaunch, toast } from "../launch";
import { hintsOn } from "../prefs";
import { confirm, confirmUninstall, showAdvanced, showImport } from "../modals";
import { PickerItem } from "../picker";
import { ProfileCard } from "../profiles";
import { watcher } from "../progress";
import { pickTool, setCompatTool, getCompatInfo, verifyApp } from "../steam";
import { Btn, Card, Dot, familyOf, hint, Line, mono, RS, Row, SectionTitle, Tip } from "../theme";
import { ModePicker, UpscalerPicker } from "../upscaler";

const IMPORT = "__import__";

export function StatusCard({ d }: { d: Detail }) {
  const color = { active: RS.ok, inactive: RS.warn, broken: RS.danger, not_installed: "rgba(255,255,255,0.35)" }[d.status.state];
  const native = Object.keys(d.scan.native);
  const p = d.profile;
  return (
    <Card accent={color}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12.5, fontWeight: 650, color: RS.text }}>
        <Dot color={color} />
        <span>{d.status.text}</span>
      </div>
      {d.record?.route && (
        <Line>
          Route: {d.record.route === "scaler" ? "HelixSR Scaler" : "FSR 3.1 drop-in"}
          {d.record.proxy ? ` · ${d.record.proxy}` : ""}
        </Line>
      )}
      <Line>
        {[p?.engine && p.engine !== "other" ? `${p.engine === "unreal" ? "Unreal" : p.engine === "unity" ? "Unity" : "Source"}${p.version ? ` ${p.version}` : ""}` : null, p?.api, p?.arch]
          .filter(Boolean)
          .join(" · ") || "Engine / API not detected"}
      </Line>
      <Line>Game ships: {native.length ? native.join(" · ") : "no known upscaler DLL"}</Line>
      {d.running && <Line color={RS.warn}>Running now: close the game to change files.</Line>}
    </Card>
  );
}

function OptionControl({ o, value, disabled, onChange }: { o: Option; value: any; disabled: boolean; onChange: (v: any) => void }) {
  if (o.type === "toggle") {
    return <ToggleField label={o.label} description={hint(o.desc)} checked={!!value} disabled={disabled} onChange={onChange} />;
  }
  if (o.type === "slider") {
    return (
      <SliderField
        label={o.label}
        description={hint(o.desc)}
        value={typeof value === "number" ? value : Number(value ?? o.default ?? 0)}
        min={o.min ?? 0}
        max={o.max ?? 1}
        step={o.step ?? 0.05}
        showValue
        editableValue
        disabled={disabled}
        onChange={onChange}
      />
    );
  }
  // no dropdowns in this block: every choice is a notched slider (or a toggle for on / off pairs)
  return <ChoiceSlider label={o.label} desc={o.desc} values={o.values ?? []} value={String(value ?? o.default)} disabled={disabled} onChange={onChange} />;
}

function ExternalCard({ ctx, onWipe }: { ctx: Ctx; onWipe: () => void }) {
  const { d } = ctx;
  if (!d.external.length) return null;
  return (
    <>
      <Row id="external">
        <Card accent={RS.danger}>
          <div style={{ fontSize: 12.5, fontWeight: 650, color: RS.danger }}>External injector found</div>
          {d.external.map((x, i) => (
            <div key={i} style={{ marginTop: 4 }}>
              <Line color={RS.text}>
                {x.kind} in <span style={mono}>{x.rel_dir}</span>
              </Line>
              <Line>
                {x.files.length} file{x.files.length === 1 ? "" : "s"}
                {x.dirs.length ? `, ${x.dirs.length} folder${x.dirs.length === 1 ? "" : "s"}` : ""}
                {x.restore ? ` · ${x.restore} game file${x.restore === 1 ? "" : "s"} to restore` : ""}
                {x.shared.length ? ` · ${x.shared.length} runtime DLL${x.shared.length === 1 ? "" : "s"} checked` : ""}
              </Line>
            </div>
          ))}
          {hintsOn() && <Line>Installed by hand or by another tool. RAT removes it, wipes its settings and logs and cleans the launch options, all from Game Mode.</Line>}
        </Card>
      </Row>
      <Row id="wipe">
        <Btn tone="danger">
          <ButtonItem layout="below" disabled={ctx.jobRunning || d.running} onClick={onWipe}>
            Remove & wipe external injector
          </ButtonItem>
        </Btn>
      </Row>
      {d.game.appid && (
        <Row id="verify">
          <Btn>
            <ButtonItem
              layout="below"
              disabled={ctx.jobRunning || d.running}
              onClick={async () => {
                if (d.game.appid && (await verifyApp(Number(d.game.appid)))) toast(`Steam is verifying ${d.game.name}`);
                else toast("Steam could not start the verification");
              }}
            >
              Verify game files in Steam
            </ButtonItem>
          </Btn>
        </Row>
      )}
    </>
  );
}

export function InjectTab({ ctx }: { ctx: Ctx }) {
  const { d, ov, key, addon, source, upd, plan, compat, advanced } = ctx;
  const installed = !!d.record;
  const live = !!d.editing_installed;
  const jobRunning = ctx.jobRunning;
  const sliderTimer = useRef<any>(0);
  const [launchDone, setLaunchDone] = useState(false);

  const changeOption = async (id: string, v: any) => {
    const r: any = await ctx.act(() => api.setOption(key, id, v));
    const label = addon.options.find((o) => o.id === id)?.label ?? id;
    if (r?.needs_reinstall) toast(`${label}: applies when you reinstall`);
    if (r && live && d.record) ctx.syncLaunch();
  };
  const setOptionDebounced = (id: string, v: any) => {
    ctx.setD({ ...d, options: { ...d.options, [id]: v } });
    clearTimeout(sliderTimer.current);
    sliderTimer.current = setTimeout(() => changeOption(id, v), 450);
  };

  const selectSource = async (sid: string) => {
    if (sid === IMPORT) {
      showImport(ov.browse_root || "/home/deck", (j) => watcher.start(j));
      return;
    }
    await ctx.act(() => api.setChoice(key, sid, null, null));
  };

  const wipe = async (thenInstall = false) => {
    const files = d.external.reduce((n, x) => n + x.files.length + x.dirs.length, 0);
    const ok = await confirm(
      "Remove & wipe external injector?",
      <div style={{ whiteSpace: "pre-wrap" }}>
        {`RAT deletes ${files} injector file${files === 1 ? "" : "s"}/folder${files === 1 ? "" : "s"} (DLL proxies, .ini settings, logs), puts back the game files they replaced and removes their launch options (e.g. WINEDLLOVERRIDES=dxgi=n,b).\n\nRuntime DLLs the game may own (FSR, XeSS, DLSS) are only removed when they are identical to an injector's copy. If the game misbehaves afterwards, use "Verify game files in Steam".`}
        {thenInstall ? `\n\nThen ${source?.title} is installed.` : ""}
      </div>,
      thenInstall ? "Wipe & install" : "Remove & wipe",
      true
    );
    if (!ok) return;
    ctx.startJob(
      () => api.wipeExternal(key),
      thenInstall
        ? (j) => {
            if (j.state === "done" && !j.result?.left) ctx.startJob(() => api.install(key, true));
            else if (j.state === "done") toast("Some injector files could not be removed, so nothing was installed. Check the Upscale tab.");
          }
        : undefined
    );
  };

  const install = async () => {
    if (!source) return;
    if (d.external.length) {
      const choice = await new Promise<"wipe" | "keep" | null>((resolve) => {
        let done = false;
        const fin = (v: "wipe" | "keep" | null) => {
          if (!done) {
            done = true;
            resolve(v);
          }
        };
        showModal(
          <ConfirmModal
            strTitle="External injector in this game"
            strDescription={`${d.external.map((x) => `${x.kind} in ${x.rel_dir}`).join(", ")} is already there. Two injectors in one game fight each other. Remove & wipe it first?`}
            strOKButtonText="Wipe & install"
            strMiddleButtonText="Install anyway"
            onOK={() => fin("wipe")}
            onMiddleButton={() => fin("keep")}
            onCancel={() => fin(null)}
            closeModal={() => fin(null)}
          />
        );
      });
      if (choice === "wipe") return wipe(true);
      if (choice !== "keep") return;
    }
    const notes: string[] = [];
    if (d.record && d.installed_source_id !== d.source_id) notes.push(`This replaces ${d.record.title} in ${d.game.name}.`);
    if (d.gpu_support.level !== "ok") notes.push(d.gpu_support.text);
    notes.push(...d.warnings.filter((w) => !w.startsWith("An injector RAT did not install")));
    const pk = source.packages[0];
    if (addon.needs_prepare && !pk?.prepared) notes.push(addon.prepare_note || "");
    if (!pk) notes.push(`Downloads ${source.title} from ${source.repo ? `github.com/${source.repo}` : "its source"}.`);
    if (d.kind === "d4r") notes.push("Frame generation with d4r: after the install, add the FG fallback files on the FG tab.");
    if (notes.length) {
      const ok = await confirm(`Inject ${source.title}?`, <div style={{ whiteSpace: "pre-wrap" }}>{notes.join("\n\n")}</div>, "Install");
      if (!ok) return;
    }
    ctx.startJob(() => api.install(key, true));
  };

  const uninstall = async () => {
    if (!d.record) return;
    const choice = await confirmUninstall(
      key,
      d.game.appid,
      "injector",
      `Remove ${d.record.title}?`,
      "RAT deletes the injector's files from the game and puts back every game file it replaced. ReShade and HDR stay (remove them in LOOK)."
    );
    if (choice) {
      staged.clear(key);
      ctx.startJob(() => api.uninstall(key), (j) => j.state === "done" && ctx.syncLaunch(choice === "restore"));
    }
  };

  const u = upd[d.source_id];
  const pk = source?.packages[0];
  const fam = ov.gpu.family;
  const options = addon.options.filter((o) => {
    if (o.gpu && !o.gpu.includes(fam)) return false;
    if (o.route) return !!d.route && o.route === d.route;
    return true;
  });
  const tool = compat ? pickTool(compat.tools, d.proton.recommended, d.proton.match) : null;
  const curTool = compat?.current || d.current_tool || null;
  const toolOk = !d.proton.match || (curTool ? new RegExp(d.proton.match, "i").test(curTool) : false);
  const updateAvailable = !!(installed && live && u?.latest && d.record?.package_version !== u.latest.tag);
  const pending = staged.count(key);
  const lock = jobRunning || d.running;
  const sourceOpts = ov.sources.map((s) => ({
    data: s.id,
    label: s.title,
    family: (s.official ? s.family ?? familyOf(s.title) : "fork") as any,
    sub: `${s.official ? "" : "FORK "}${d.installed_source_id === s.id ? "● INSTALLED" : ""}`.trim() || undefined,
  }));

  return (
    <PanelSection>
      <ExternalCard ctx={ctx} onWipe={() => wipe(false)} />

      {advanced && (
        <SectionTitle top={8} desc="Pick what runs in this game. Everything is per game and fully reversible.">
          Injector
        </SectionTitle>
      )}
      <Row id="addon">
        <PickerItem
          label="Add-on"
          description={hint(source?.subtitle || addon.subtitle)}
          options={[...sourceOpts, { data: IMPORT, label: "+ Add fork (link or file)…", action: true }]}
          selected={d.source_id}
          disabled={jobRunning}
          onChange={(o) => selectSource(o.data)}
        />
      </Row>
      {advanced && <Row id="addon-info">
        <div style={{ width: "100%" }}>
          <Line color={{ ok: RS.ok, experimental: RS.warn, unsupported: RS.danger, unknown: RS.warn }[d.gpu_support.level]}>
            {ov.gpu.name} ({fam}
            {ov.gpu.gfx ? ` · ${ov.gpu.gfx}` : ""}): {d.gpu_support.text}
          </Line>
          <Line>
            {pk ? `Downloaded: ${pk.version}${addon.needs_prepare ? (pk.prepared ? " · network built" : " · network not built yet") : ""}` : "Not downloaded yet"}
            {u?.latest ? ` · latest ${u.latest.tag}` : ""}
            {u?.update && u.latest ? "  ⬆" : ""}
          </Line>
        </div>
      </Row>}

      <ModePicker ctx={ctx} disabled={lock} />
      <UpscalerPicker ctx={ctx} disabled={lock} />
      {d.in_game && (
        <Row>
          <Tip>{d.in_game}</Tip>
        </Row>
      )}
      {d.warnings
        .filter((w) => !w.startsWith("An injector RAT did not install"))
        .map((w, i) => (
          <Row key={i}>
            <Tip tone="warn">{w}</Tip>
          </Row>
        ))}

      {!jobRunning && ctx.job?.state === "error" && ctx.job.game === key && (
        <Row>
          <Tip tone="danger">
            {ctx.job.title} failed: {ctx.job.error}
          </Tip>
        </Row>
      )}
      {!installed || !live ? (
        <Row id="inject">
          <Btn tone="primary">
            <ButtonItem layout="below" disabled={lock} onClick={install}>
              {installed ? `Switch to ${source?.title}` : `Inject ${source?.title ?? addon.title}`}
            </ButtonItem>
          </Btn>
        </Row>
      ) : (
        <>
          <Row id="active">
            <ToggleField
              label="Injection active"
              description={hint("Off keeps the files but the game starts without the injector.")}
              checked={d.status.state === "active"}
              disabled={lock}
              onChange={async (v) => {
                const r = await ctx.act(() => api.setActive(key, v));
                if (r) ctx.syncLaunch();
              }}
            />
          </Row>
          {advanced && (
            <Row id="to-tune">
              <Btn tone="amber">
                <ButtonItem layout="below" disabled={jobRunning} onClick={() => ctx.setTab("tune")}>
                  Tune injector settings{pending ? ` (${pending} unsaved)` : ""}
                </ButtonItem>
              </Btn>
            </Row>
          )}
          {updateAvailable ? (
            <Row id="update">
              <Btn tone="primary">
                <ButtonItem layout="below" disabled={lock} onClick={() => ctx.startJob(() => api.update(key))}>
                  Update to {u!.latest!.tag} (keeps your settings)
                </ButtonItem>
              </Btn>
            </Row>
          ) : (
            <Row id="reinstall">
              <Btn>
                <ButtonItem layout="below" disabled={lock} onClick={() => ctx.startJob(() => api.install(key, true))}>
                  {d.status.state === "broken" ? "Repair" : "Reinstall"}
                </ButtonItem>
              </Btn>
            </Row>
          )}
          <Row id="uninstall">
            <Btn tone="danger">
              <ButtonItem layout="below" disabled={lock} onClick={uninstall}>
                Uninstall
              </ButtonItem>
            </Btn>
          </Row>
        </>
      )}

      {advanced && options.length > 0 && (
        <>
          <SectionTitle desc="Install-time choices and the most used switches. Everything else is on the Tune tab.">{addon.title} options</SectionTitle>
          {options.map((o) => (
            <Row key={o.id} id={`opt-${o.id}`}>
              <OptionControl o={o} value={d.options[o.id]} disabled={lock} onChange={(v) => (o.type === "slider" ? setOptionDebounced(o.id, v) : changeOption(o.id, v))} />
            </Row>
          ))}
          {d.record?.pending && live && Object.keys(d.record.pending).length > 0 && (
            <Row>
              <Tip tone="warn">Some changes apply after Reinstall.</Tip>
            </Row>
          )}
        </>
      )}

      {!advanced && d.game.appid && (
        <>
          {d.proton.match && !toolOk && (
            <Row>
              <Tip tone="warn">
                {d.proton.recommended ? `Recommended: ${d.proton.recommended}. ` : ""}
                {d.proton.note}
              </Tip>
            </Row>
          )}
          {d.proton.match && !toolOk && tool && (
            <Row id="use-tool">
              <Btn>
                <ButtonItem
                  layout="below"
                  onClick={async () => {
                    if (setCompatTool(Number(d.game.appid), tool.strToolName)) {
                      toast(`${d.game.name} now uses ${tool.strDisplayName}`);
                      ctx.setCompat(await getCompatInfo(Number(d.game.appid)));
                    }
                  }}
                >
                  Use {tool.strDisplayName}
                </ButtonItem>
              </Btn>
            </Row>
          )}
          {(plan?.changed || launchDone) && (
            <Row id="apply-launch">
              <Btn tone={plan?.changed ? "primary" : undefined}>
                <ButtonItem
                  layout="below"
                  onClick={async () => {
                    if (!plan?.changed) return;
                    ctx.setPlan(await applyLaunch(key));
                    setLaunchDone(true);
                  }}
                >
                  {plan?.changed ? "Apply launch options" : "✓ Launch options are set"}
                </ButtonItem>
              </Btn>
            </Row>
          )}
        </>
      )}

      {advanced && d.game.appid && (
        <>
          <SectionTitle desc="RAT merges its variables into your launch options; gamemoderun, mangohud and your own variables stay.">Proton & launch options</SectionTitle>
          <Row id="tool">
            <Field label="Compatibility tool" bottomSeparator="none">
              <span style={{ fontSize: 12 }}>{curTool || "Steam default"}</span>
            </Field>
          </Row>
          {(hintsOn() || !toolOk) && (
            <Row>
              <Tip tone={toolOk ? "info" : "warn"}>
                {d.proton.recommended ? `Recommended: ${d.proton.recommended}. ` : ""}
                {d.proton.note}
              </Tip>
            </Row>
          )}
          {d.proton.match && !toolOk && tool && (
            <Row id="use-tool">
              <Btn>
                <ButtonItem
                  layout="below"
                  onClick={async () => {
                    if (setCompatTool(Number(d.game.appid), tool.strToolName)) {
                      toast(`${d.game.name} now uses ${tool.strDisplayName}`);
                      ctx.setCompat(await getCompatInfo(Number(d.game.appid)));
                    }
                  }}
                >
                  Use {tool.strDisplayName}
                </ButtonItem>
              </Btn>
            </Row>
          )}
          {d.proton.match && !toolOk && !tool && compat && (
            <Row>
              <Tip tone="warn">Not installed: get it with ProtonUp-Qt or Wine Cellar (Desktop Mode), then restart Steam.</Tip>
            </Row>
          )}
          {plan?.cleaned && plan.cleaned.length > 0 && (
            <Row id="plan-cleaned">
              <Tip tone="warn">
                Redundant launch options found: {plan.cleaned.join("; ")}. Applying writes a cleaned, deduplicated version.
              </Tip>
            </Row>
          )}
          {plan && (
            <Row id="plan">
              <div style={{ width: "100%", fontSize: 10.5, padding: "4px 0" }}>
                <div style={{ color: RS.muted }}>RAT needs:</div>
                <div style={{ ...mono, color: plan.ours ? RS.text : RS.dim }}>{plan.ours || (installed ? "nothing" : "nothing yet (inject first)")}</div>
                <div style={{ color: RS.muted, marginTop: 4 }}>Current:</div>
                <div style={mono}>{plan.current || "(empty)"}</div>
              </div>
            </Row>
          )}
          {plan?.changed && (
            <Row id="apply-launch">
              <Btn tone="primary">
                <ButtonItem layout="below" onClick={async () => ctx.setPlan(await applyLaunch(key))}>
                  Apply launch options
                </ButtonItem>
              </Btn>
            </Row>
          )}
          {plan && !plan.changed && plan.ours && (
            <Row>
              <Line color={RS.ok}>✓ Launch options are set</Line>
            </Row>
          )}
          <Row id="swapchain">
            <ToggleField
              label="Preserve Swapchain Image Count"
              description={hint("Pins vkd3d-proton (DX12) to 3 swapchain images (VKD3D_SWAPCHAIN_IMAGES=3). Try it if a game fails to start or paces badly with frame generation.")}
              checked={!!d.preserve_swapchain}
              disabled={d.running}
              onChange={async (v) => {
                const r = await ctx.act(() => api.setPreserveSwapchain(key, v));
                if (r) ctx.syncLaunch();
              }}
            />
          </Row>
        </>
      )}

      {advanced && (
        <>
          <ProfileCard ctx={ctx} />
          <Row id="files">
            <Btn>
              <ButtonItem layout="below" onClick={() => showAdvanced(key, d.game.name)}>
                View .ini files & logs
              </ButtonItem>
            </Btn>
          </Row>
        </>
      )}
    </PanelSection>
  );
}
