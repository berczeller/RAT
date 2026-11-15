// What runs in a game, shown twice:
//  * the boot HUD: when Steam starts a game RAT manages, a transient RAT-branded notification appears once the game's
//    renderer is up ("RAT (Radical Advanced Tools) 1.1 (Running OptiScaler by cdozdil)", then native / spoofed upscaler,
//    frame generation, ReShade and HDR). It fades out on its own after the same 7 s OptiScaler's splash uses.
//    The injectors' own splash screens (OptiScaler, HelixSR Scaler) are left as their authors made them.
//  * the launch banner in the panel while the selected game runs.
import { toaster } from "@decky/api";
import { api, LaunchStatus } from "./api";
import { RatIcon } from "./icon";
import { prefs } from "./prefs";
import { Card, FAMILY, RS } from "./theme";

declare const SteamClient: any;

const HUD_MS = 7000;
let reg: { unregister?: () => void } | null = null;
let seq = 0;

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export function statusLines(st: LaunchStatus): { mode: string; fx: string } {
  const u = st.upscaler;
  const parts = [u.mode === "Off" ? "NO INJECTION" : u.mode.toUpperCase(), u.name, u.model, u.version ? `v${u.version}` : "", u.api ?? ""]
    .filter(Boolean)
    .map((s) => String(s).toUpperCase());
  const fx: string[] = [];
  fx.push(st.fg.active ? `FG: ${(st.fg.method || st.fg.provider || "ON").toUpperCase()}` : "FG: OFF");
  const rt = st.reshade.runtime;
  const tag = (on: boolean | null | undefined) => (on === false ? " (NOT LOADED)" : on ? " ✓" : "");
  if (st.reshade.active)
    fx.push(st.reshade.mode === "compositor" ? "RESHADE: COMPOSITOR" : `RESHADE: ${st.reshade.effects ?? 0} FX${tag(rt?.reshade)}${rt?.reshade ? ` · ${rt.overlay_key.toUpperCase()}` : ""}`);
  if (st.hdr.active) fx.push(`HDR: RENODX${st.hdr.source === "auto" ? " (AUTO)" : ""}${tag(rt?.renodx)}`);
  return { mode: parts.join(" · "), fx: fx.join(" · ") };
}

export function showBootHud(st: LaunchStatus) {
  const { mode, fx } = statusLines(st);
  toaster.toast({
    title: st.header,
    body: mode,
    subtext: fx,
    logo: <RatIcon size={40} style={{ color: RS.accent }} />,
    duration: HUD_MS,
    playSound: false,
  });
}

async function onLifetime(e: { unAppID: number; bRunning: boolean }) {
  const appid = String(e.unAppID);
  if (!e.bRunning) {
    api.gameStopped(appid).catch(() => {});
    return;
  }
  const my = ++seq;
  let st: LaunchStatus | null = null;
  try {
    st = await api.gameStarted(appid);
  } catch {
    return;
  }
  if (!st || !st.show || prefs.get()?.boot_banner === false) return;
  // wait until the game's renderer is loaded (its graphics DLL is mapped), so the banner lands on the game itself,
  // together with the injector's own startup splash
  for (let i = 0; i < 60 && my === seq; i++) {
    await sleep(2000);
    try {
      st = await api.launchStatus(st.key);
    } catch {
      return;
    }
    if (!st.running && i > 5) return;
    if (st.api_live) break;
  }
  if (my === seq && st) showBootHud(st);
}

export function startHud() {
  try {
    reg = SteamClient.GameSessions.RegisterForAppLifetimeNotifications(onLifetime);
  } catch (e) {
    console.error("RAT: cannot follow game launches", e);
  }
}

export function stopHud() {
  try {
    reg?.unregister?.();
  } catch {
    /* gone */
  }
  reg = null;
}

/** The in-panel banner while the selected game runs: native or spoofed, name, version, model and API. */
export function LaunchBanner({ st }: { st: LaunchStatus }) {
  const u = st.upscaler;
  const fam = FAMILY[u.family ?? "neutral"] ?? FAMILY.neutral;
  const modeColor = u.mode === "Native" ? FAMILY.dlss.c : u.mode === "Spoofed" ? fam.c : RS.muted;
  const cell = (label: string, value?: string | null) => (
    <div style={{ minWidth: 0 }}>
      <div style={{ fontSize: 8.5, color: RS.dim, letterSpacing: 0.8, textTransform: "uppercase" }}>{label}</div>
      <div style={{ fontSize: 11, color: RS.text, fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{value || "n/a"}</div>
    </div>
  );
  return (
    <Card accent={modeColor} glow>
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <RatIcon size={16} style={{ color: RS.accent, flex: "0 0 16px" }} />
        <div style={{ fontSize: 10, color: RS.muted, letterSpacing: 0.5, lineHeight: 1.3 }}>{st.header}</div>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 8, margin: "6px 0 4px" }}>
        <span
          style={{
            padding: "2px 8px",
            borderRadius: 999,
            fontSize: 10,
            fontWeight: 800,
            letterSpacing: 1,
            color: "#141828",
            background: modeColor,
            boxShadow: `0 0 10px ${modeColor}66`,
          }}
        >
          {u.mode === "Off" ? "NOT INJECTED" : u.mode.toUpperCase()}
        </span>
        <span style={{ fontSize: 12, fontWeight: 700, color: fam.c, textTransform: "uppercase", letterSpacing: 0.5 }}>{u.name}</span>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "4px 10px" }}>
        {cell("Name", u.label || u.name)}
        {cell("Version", u.version || st.injector?.version)}
        {cell("Model", u.model)}
        {cell("API", u.api)}
      </div>
      <div style={{ marginTop: 6, fontSize: 9.5, color: RS.muted, letterSpacing: 0.4 }}>{statusLines(st).fx}</div>
    </Card>
  );
}
