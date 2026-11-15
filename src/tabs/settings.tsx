// Settings (gear in the header): RAT-wide preferences, including the contextual hints toggle, plus library tools and
// About.
import { ButtonItem, PanelSection, SliderField, ToggleField } from "@decky/ui";
import { openFilePicker } from "@decky/api";
import { api, Detail, GameItem, Overview, Prefs } from "../api";
import type { Ctx } from "../ctx";
import { toast } from "../launch";
import { confirm, DISCLAIMER, showAbout } from "../modals";
import { PickerItem } from "../picker";
import { prefs as prefStore } from "../prefs";
import { watcher } from "../progress";
import { keep } from "../scroll";
import { Btn, hint, Line, RS, Row, SectionTitle } from "../theme";

export function SettingsView({
  ov,
  setOv,
  d,
  keyId,
  ctx,
  onGames,
  selectGame,
}: {
  ov: Overview;
  setOv: (o: Overview) => void;
  d: Detail | null;
  keyId: string | null;
  ctx: Ctx | null;
  onGames: (g: GameItem[]) => void;
  selectGame: (k: string) => Promise<void>;
}) {
  const setPref = async <K extends keyof Prefs>(name: K, value: Prefs[K]) => {
    try {
      const p = await keep(() => api.setPref(name, value as any));
      setOv({ ...ov, prefs: p });
      prefStore.set(p);
      if (name === "auto_launch_options") watcher.autoLaunch = !!value;
      return p;
    } catch (e) {
      toast(String(e));
      return null;
    }
  };
  const toggle = (name: keyof Prefs, label: string, desc: string) => (
    <Row id={`pref-${name}`}>
      <ToggleField label={label} description={hint(desc)} checked={!!ov.prefs[name]} onChange={(v) => setPref(name, v as any)} />
    </Row>
  );
  const installed = !!d?.record;
  const source = ov.sources.find((s) => s.id === d?.source_id);

  return (
    <PanelSection>
      <SectionTitle top={8}>Interface</SectionTitle>
      <Row id="pref-show_hints">
        <ToggleField
          label="Show contextual hints"
          description="Short explanations under every setting. Turn off for a compact panel; warnings always show."
          checked={ov.prefs.show_hints}
          onChange={(v) => setPref("show_hints", v)}
        />
      </Row>
      {toggle("boot_banner", "Boot banner & status HUD", "When a game RAT manages starts, a RAT notification shows what runs: native or spoofed upscaler, frame generation, ReShade, HDR. Fades out after 7 s. The injectors' own splash screens stay as they are.")}

      <SectionTitle>Injection</SectionTitle>
      {toggle("auto_launch_options", "Set launch options automatically", "RAT writes the variables and DLL overrides it needs into the game's Steam launch options after every change.")}
      {toggle("allow_unsupported_gpu", "Allow unsupported GPUs", "Install add-ons even when this GPU is not on the author's list.")}
      {toggle("include_prerelease", "Include pre-releases", "Offer pre-release builds of injectors when checking for updates.")}
      <Row id="pref-peak">
        <SliderField
          label="HDR display peak brightness"
          description={hint("Used to prefill RenoDX. 0 = automatic (1000 nits: Steam Deck OLED's gamescope profile, a safe start on TVs).")}
          value={ov.prefs.hdr_peak_nits}
          min={0}
          max={4000}
          step={50}
          showValue
          valueSuffix=" nits"
          onChange={(v) => setOv({ ...ov, prefs: { ...ov.prefs, hdr_peak_nits: v } })}
        />
      </Row>
      <Row id="pref-peak-save">
        <Btn>
          <ButtonItem layout="below" onClick={() => setPref("hdr_peak_nits", ov.prefs.hdr_peak_nits)}>
            Save peak brightness
          </ButtonItem>
        </Btn>
      </Row>

      <SectionTitle>This game</SectionTitle>
      {d && keyId && (
        <Row id="exe-dir">
          <PickerItem
            label="Executable folder"
            description={hint(d.game.exe_dir ? d.game.exe_dir.replace(d.game.folder, "…") : "No .exe found")}
            options={[
              ...d.game.exe_candidates.map((c) => ({ data: c.dir, label: c.exe, family: "neutral" as const, sub: c.dir.replace(d.game.folder, "…") })),
              { data: "__pick", label: "Pick a folder…", action: true },
            ]}
            selected={d.game.exe_dir}
            disabled={installed}
            onChange={async (o) => {
              if (o.data !== "__pick") return ctx?.act(() => api.setExeDir(keyId, o.data));
              try {
                const r = await openFilePicker(1 as any, d.game.folder, false, true);
                if (r?.path) await ctx?.act(() => api.setExeDir(keyId, r.realpath || r.path));
              } catch {
                /* closed */
              }
              return undefined;
            }}
          />
        </Row>
      )}
      {source?.repo && ctx && (
        <Row id="check-updates">
          <Btn>
            <ButtonItem
              layout="below"
              disabled={ctx.jobRunning}
              onClick={async () => {
                const u = await ctx.act(() => api.checkUpdates(source.id, true));
                if (u) {
                  ctx.setUpd((x) => ({ ...x, [u.source_id]: u }));
                  toast(u.latest ? (u.update ? `${source.title} ${u.latest.tag} is available` : `${source.title} is up to date (${u.latest.tag})`) : "No release found");
                }
              }}
            >
              Check {source.title} for updates
            </ButtonItem>
          </Btn>
        </Row>
      )}
      {d && ctx && source && !source.official && (
        <Row id="remove-fork">
          <Btn tone="danger">
            <ButtonItem
              layout="below"
              disabled={ctx.jobRunning || d.installed_source_id === d.source_id}
              onClick={async () => {
                if (await confirm("Remove this fork?", "Deletes its downloaded package from RAT.", "Remove", true)) {
                  await ctx.act(() => api.removeSource(d.source_id));
                  await ctx.reloadOverview();
                  await ctx.reload();
                }
              }}
            >
              Remove this fork
            </ButtonItem>
          </Btn>
        </Row>
      )}

      <SectionTitle>Library</SectionTitle>
      <Row id="add-folder">
        <Btn>
          <ButtonItem
            layout="below"
            onClick={async () => {
              try {
                const r = await openFilePicker(1 as any, ov.home || "/home/deck", false, true);
                if (!r?.path) return;
                const res = await api.addCustomGame(r.realpath || r.path);
                onGames(await api.listGames(true));
                selectGame(res.key);
              } catch (e) {
                if (e instanceof Error && e.message) toast(e.message);
              }
            }}
          >
            Add a non-Steam game folder…
          </ButtonItem>
        </Btn>
      </Row>
      {d?.game.custom && keyId && (
        <Row id="remove-folder">
          <Btn>
            <ButtonItem
              layout="below"
              disabled={installed}
              onClick={async () => {
                await api.removeCustomGame(keyId).catch(() => null);
                const gl = await api.listGames(true);
                onGames(gl);
                if (gl[0]) selectGame(gl[0].key);
              }}
            >
              Remove this folder from the list
            </ButtonItem>
          </Btn>
        </Row>
      )}
      <Row id="rescan">
        <Btn>
          <ButtonItem
            layout="below"
            onClick={async () => {
              const gl = await api.listGames(true);
              onGames(gl);
              toast(`${gl.length} games found`);
            }}
          >
            Rescan library
          </ButtonItem>
        </Btn>
      </Row>

      <SectionTitle>About</SectionTitle>
      <Row>
        <Line size={10.5} color={RS.muted}>
          {ov.gpu.name} · {ov.gpu.vendor} {ov.gpu.family}
          {ov.gpu.dlss ? " · DLSS capable" : ""}
          {ov.gpu.xmx ? " · XMX" : ""}
          {ov.gpu.fsr4_native ? " · native FSR 4" : ""}
        </Line>
      </Row>
      <Row>
        <div style={{ fontSize: 9.5, lineHeight: 1.4, color: RS.dim }}>{DISCLAIMER}</div>
      </Row>
      <Row id="about">
        <Btn>
          <ButtonItem layout="below" onClick={() => showAbout()}>
            About RAT
          </ButtonItem>
        </Btn>
      </Row>
    </PanelSection>
  );
}
