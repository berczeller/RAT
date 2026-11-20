import { PanelSection, Router, staticClasses } from "@decky/ui";
import { definePlugin } from "@decky/api";
import { useEffect, useMemo, useRef, useState } from "react";
import { Addon, api, Detail, errText, GameItem, LaunchPlan, Overview, UpdateInfo } from "./api";
import { staged, useStaged } from "./controls";
import type { Ctx } from "./ctx";
import { pressFeedback } from "./feedback";
import { LaunchBanner, startHud, stopHud } from "./hud";
import { RatIcon } from "./icon";
import { applyLaunch, toast } from "./launch";
import { prefs, usePrefs } from "./prefs";
import { PickerItem } from "./picker";
import { JobPill, useJob, watcher } from "./progress";
import { keep, setScrollAnchor } from "./scroll";
import { CompatTool, getCompatInfo, getLaunchOptions } from "./steam";
import { FrameGenTab } from "./tabs/framegen";
import { InjectTab, StatusCard } from "./tabs/inject";
import { SettingsView } from "./tabs/settings";
import { LookTab } from "./tabs/look";
import { TuneTab } from "./tabs/tune";
import { Dot, Header, hint, Line, RS, Row, Tabs, ThemeStyle, Tip } from "./theme";

const VERSION = "1.1.0";
const SIDEBAR_NAME = "RAT (Radical Advanced Tools)";

// --- state ------------------------------------------------------------------------------------------------------
// Module variables keep the game and the open tab while the Quick Access menu is closed.

let lastGameKey: string | null = null;
let lastTab = "inject";
const PANEL_TABS = ["inject", "framegen", "look", "tune", "settings"];
const gameListeners = new Set<(k: string | null) => void>();
function pickGame(k: string | null) {
  lastGameKey = k;
  gameListeners.forEach((l) => l(k));
}

function useRat(setTab: (t: string) => void) {
  useStaged();
  usePrefs();
  const job = useJob();
  const [ov, setOv] = useState<Overview | null>(null);
  const [games, setGames] = useState<GameItem[]>([]);
  const [key, setKey] = useState<string | null>(lastGameKey);
  const [d, setD] = useState<Detail | null>(null);
  const [upd, setUpd] = useState<Record<string, UpdateInfo>>({});
  const [plan, setPlan] = useState<LaunchPlan | null>(null);
  const [compat, setCompat] = useState<{ current: string | null; tools: CompatTool[] } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const mounted = useRef(true);
  const busyRef = useRef(false);
  const dRef = useRef<Detail | null>(null);
  dRef.current = d;
  const keyRef = useRef<string | null>(lastGameKey);

  const addon: Addon | undefined = useMemo(() => ov?.catalog.find((a) => a.id === d?.addon_id) ?? ov?.catalog.find((a) => a.kind === d?.kind), [ov, d?.addon_id, d?.kind]);
  const source = useMemo(() => ov?.sources.find((s) => s.id === d?.source_id), [ov, d?.source_id]);
  const jobRunning = job?.state === "running";

  const loadOverview = async () => {
    const o = await api.overview();
    if (!mounted.current) return o;
    setOv(o);
    prefs.set(o.prefs);
    watcher.autoLaunch = o.prefs.auto_launch_options;
    if (o.job?.state === "running" && !watcher.timer) watcher.start(o.job, undefined, false);
    return o;
  };

  const loadDetail = async (k: string | null) => {
    if (!k) return;
    try {
      const det = await api.gameDetail(k);
      if (!mounted.current || k !== keyRef.current) return;
      setD(det);
      setErr(null);
      if (det.game.appid) {
        const appId = Number(det.game.appid);
        getCompatInfo(appId)
          .then((c) => mounted.current && k === keyRef.current && setCompat(c))
          .catch(() => {});
        const cur = await getLaunchOptions(appId);
        const p = await api.launchPlan(k, cur, false);
        if (mounted.current && k === keyRef.current) setPlan(p);
      } else {
        setCompat(null);
        setPlan(null);
      }
    } catch (e) {
      setErr(errText(e));
    }
  };

  useEffect(() => {
    mounted.current = true;
    const onGame = (k: string | null) => {
      if (!mounted.current || k === keyRef.current) return;
      keyRef.current = k;
      setKey(k);
      setD(null);
      setPlan(null);
      loadDetail(k);
    };
    gameListeners.add(onGame);
    const l = (j: any) => {
      if (j && j.state !== "running") {
        keep(async () => {
          await loadOverview().catch(() => null);
          await loadDetail(keyRef.current);
          api.listGames(false).then((g) => mounted.current && setGames(g)).catch(() => {});
        });
      }
    };
    watcher.listeners.add(l);
    (async () => {
      try {
        await loadOverview();
        const gl = await api.listGames(true);
        if (!mounted.current) return;
        setGames(gl);
        const runningApp = (Router as any)?.MainRunningApp?.appid;
        let k = lastGameKey;
        if (runningApp && gl.some((g) => g.key === String(runningApp))) k = String(runningApp);
        if (!k || !gl.some((g) => g.key === k)) k = gl.find((g) => g.installed)?.key ?? gl[0]?.key ?? null;
        keyRef.current = k;
        setKey(k);
        if (k !== lastGameKey) pickGame(k);
        await loadDetail(k);
      } catch (e) {
        setErr(errText(e));
      }
    })();
    // while a game runs, the launch banner follows it (renderer, API) without the user doing anything
    const t = setInterval(() => {
      const k = keyRef.current;
      if (k && !watcher.timer && document.visibilityState !== "hidden") {
        api
          .gameDetail(k)
          .then((det) => mounted.current && det.game.key === keyRef.current && (det.running || dRef.current?.running) && setD(det))
          .catch(() => {});
      }
    }, 6000);
    return () => {
      mounted.current = false;
      clearInterval(t);
      watcher.listeners.delete(l);
      gameListeners.delete(onGame);
    };
  }, []);

  useEffect(() => {
    if (!d?.source_id || upd[d.source_id]) return;
    const src = ov?.sources.find((s) => s.id === d.source_id);
    if (!src?.repo) return;
    api.checkUpdates(d.source_id, false).then((u) => mounted.current && setUpd((x) => ({ ...x, [u.source_id]: u }))).catch(() => {});
  }, [d?.source_id, ov]);

  const act = async <T,>(fn: () => Promise<T>, okMsg?: string): Promise<T | null> => {
    // controls stay enabled during calls (disabling the focused one makes Steam jump focus to the top); calls are
    // chained instead, so a slider's last value is never dropped while an earlier call is still running
    while (busyRef.current) await new Promise((r) => setTimeout(r, 60));
    busyRef.current = true;
    try {
      return await keep(async () => {
        const r = await fn();
        // only apply a Detail that belongs to the game still selected (the user may have switched games mid-call)
        if (r && typeof r === "object" && "status" in (r as any) && "game" in (r as any)) {
          const det = r as unknown as Detail;
          if (mounted.current && det.game?.key === keyRef.current) setD(det);
        }
        if (okMsg) toast(okMsg);
        return r;
      });
    } catch (e) {
      toast(errText(e));
      return null;
    } finally {
      busyRef.current = false;
    }
  };

  const startJob = async (fn: () => Promise<any>, then?: (j: any) => void) => {
    try {
      watcher.start(await fn(), then);
    } catch (e) {
      toast(errText(e));
    }
  };

  const syncLaunch = async (force = false) => {
    if (!key || !d?.game.appid) return;
    const k = key;
    try {
      if (ov?.prefs.auto_launch_options || force) {
        const p = await applyLaunch(k, true);
        if (p && mounted.current && k === keyRef.current) setPlan(p);
      } else {
        const cur = await getLaunchOptions(Number(d.game.appid));
        const p = await api.launchPlan(k, cur, false);
        if (mounted.current && k === keyRef.current) setPlan(p);
      }
    } catch (e) {
      toast(errText(e));
    }
  };

  const selectGame = async (k: string) => {
    if (k === keyRef.current) return;
    pickGame(k);
  };

  const ctx: Ctx | null =
    ov && d && key && addon
      ? {
          ov,
          setOv,
          d,
          setD,
          key,
          games,
          setGames,
          job,
          jobRunning,
          addon,
          source,
          upd,
          setUpd,
          plan,
          setPlan,
          compat,
          setCompat,
          act,
          startJob,
          reload: () => keep(() => loadDetail(key)),
          reloadOverview: () => loadOverview().catch(() => null),
          syncLaunch,
          selectGame,
          setTab,
          advanced: !!ov.prefs.show_advanced,
          setAdvanced: async (v: boolean) => {
            try {
              const p = await keep(() => api.setPref("show_advanced", v));
              setOv({ ...ov, prefs: p });
              prefs.set(p);
            } catch (e) {
              toast(errText(e));
            }
          },
        }
      : null;
  return { ov, setOv, games, setGames, key, d, err, job, ctx, selectGame, pending: key ? staged.count(key) : 0 };
}

function GameRow({ games, gameKey, onPick }: { games: GameItem[]; gameKey: string | null; onPick: (k: string) => void }) {
  return (
    <Row id="game">
      <PickerItem
        label="Game"
        description={hint("The running game is picked automatically. ● injector active · ○ installed but off.")}
        placeholder={games.length ? "Pick a game" : "No games found"}
        options={games.map((g) => ({
          data: g.key,
          label: g.name,
          family: g.installed ? "fork" : "neutral",
          sub: g.installed ? `${g.active === false ? "○" : "●"} ${g.installed}` : undefined,
        }))}
        selected={gameKey}
        onChange={(o) => onPick(o.data)}
      />
    </Row>
  );
}

/** The one-line status (the full status card shows with Show advanced). */
function StatusLine({ d }: { d: Detail }) {
  const color = { active: RS.ok, inactive: RS.warn, broken: RS.danger, not_installed: "rgba(255,255,255,0.35)" }[d.status.state];
  return (
    <Row id="status">
      <div style={{ display: "flex", alignItems: "center", gap: 7, fontSize: 11.5, color: RS.text, padding: "2px 2px 0" }}>
        <Dot color={color} />
        <span style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{d.status.text}</span>
      </div>
      {d.running && <Line size={10} color={RS.warn}>Running now: close the game to change files.</Line>}
    </Row>
  );
}

// --- the Quick Access panel --------------------------------------------------------------------------------------

function Content() {
  const [tab, setTabState] = useState(PANEL_TABS.includes(lastTab) ? lastTab : "inject");
  const setTab = (t: string) => {
    const m = t === "hdr" || t === "reshade" ? "look" : t;
    lastTab = m;
    setTabState(m);
  };
  const { ov, setOv, games, setGames, key, d, err, job, ctx, selectGame, pending } = useRat(setTab);
  const settingsOpen = tab === "settings";

  if (!ov) {
    return (
      <PanelSection>
        <ThemeStyle />
        <div className="RS_Root" ref={pressFeedback}>
          <Header version={VERSION} />
        </div>
        <Row>
          <Line>{err ? `Error: ${err}` : "Loading…"}</Line>
        </Row>
      </PanelSection>
    );
  }

  return (
    <div
      className="RS_Root"
      ref={(el) => {
        setScrollAnchor(el);
        pressFeedback(el);
      }}
    >
      <ThemeStyle />
      <PanelSection>
        <Header version={VERSION} settingsOpen={settingsOpen} onSettings={() => setTab(settingsOpen ? "inject" : "settings")} jobPill={<JobPill job={job} />} />
        {err && (
          <Row>
            <Tip tone="danger">{err}</Tip>
          </Row>
        )}
        <GameRow games={games} gameKey={key} onPick={selectGame} />
        {d?.session && d.session.running && (
          <Row id="banner">
            <LaunchBanner st={d.session} />
          </Row>
        )}
        {d && !d.session && (ov.prefs.show_advanced ? (
          <Row id="status">
            <StatusCard d={d} />
          </Row>
        ) : (
          <StatusLine d={d} />
        ))}
        <Tabs
          value={settingsOpen ? "" : tab}
          onChange={setTab}
          tabs={[
            { id: "inject", label: "UPSCALE", badge: d?.external.length ? "!" : undefined },
            { id: "framegen", label: "FG" },
            { id: "look", label: "LOOK" },
            { id: "tune", label: "TUNE", badge: pending ? `${pending}` : undefined },
          ]}
        />
      </PanelSection>
      {settingsOpen && <SettingsView ov={ov} setOv={setOv} d={d} keyId={key} ctx={ctx} onGames={setGames} selectGame={selectGame} />}
      {!settingsOpen && !ctx && key && (
        <PanelSection>
          <Row>
            <Line>Loading…</Line>
          </Row>
        </PanelSection>
      )}
      {!settingsOpen && ctx && tab === "inject" && <InjectTab key={ctx.key} ctx={ctx} />}
      {!settingsOpen && ctx && tab === "framegen" && <FrameGenTab key={ctx.key} ctx={ctx} />}
      {!settingsOpen && ctx && tab === "look" && <LookTab key={ctx.key} ctx={ctx} />}
      {!settingsOpen && ctx && tab === "tune" && <TuneTab key={ctx.key} ctx={ctx} />}
    </div>
  );
}

export default definePlugin(() => {
  startHud();
  api
    .overview()
    .then((o) => prefs.set(o.prefs))
    .catch(() => {});
  return {
    name: SIDEBAR_NAME,
    titleView: (
      <div className={staticClasses.Title} style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 13, lineHeight: 1.15, whiteSpace: "normal" }}>
        <RatIcon size={20} style={{ flex: "0 0 20px" }} />
        <span>{SIDEBAR_NAME}</span>
      </div>
    ),
    content: <Content />,
    icon: <RatIcon />,
    onDismount() {
      watcher.stop();
      stopHud();
    },
  };
});
