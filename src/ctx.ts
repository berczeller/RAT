// What every tab gets from the panel shell (index.tsx).
import type { Addon, Detail, GameItem, Job, LaunchPlan, Overview, Source, UpdateInfo } from "./api";
import type { CompatTool } from "./steam";

export type Ctx = {
  ov: Overview;
  setOv: (o: Overview) => void;
  d: Detail;
  setD: (d: Detail) => void;
  key: string;
  games: GameItem[];
  setGames: (g: GameItem[]) => void;
  job: Job | null;
  jobRunning: boolean;
  addon: Addon;
  source: Source | undefined;
  upd: Record<string, UpdateInfo>;
  setUpd: (f: (u: Record<string, UpdateInfo>) => Record<string, UpdateInfo>) => void;
  plan: LaunchPlan | null;
  setPlan: (p: LaunchPlan | null) => void;
  compat: { current: string | null; tools: CompatTool[] } | null;
  setCompat: (c: { current: string | null; tools: CompatTool[] } | null) => void;
  /** Runs a backend call with scroll / focus kept; a returned Detail replaces the panel's. Errors become toasts. */
  act: <T>(fn: () => Promise<T>, okMsg?: string) => Promise<T | null>;
  /** Starts a background job (progress modal); `then` runs when it finished. */
  startJob: (fn: () => Promise<Job>, then?: (j: Job) => void) => Promise<void>;
  /** Reloads the game's detail without clearing the panel. */
  reload: () => Promise<void>;
  reloadOverview: () => Promise<Overview | null>;
  /** Re-applies launch options after a change (when automatic launch options are on). */
  syncLaunch: (force?: boolean) => Promise<void>;
  selectGame: (k: string) => Promise<void>;
  setTab: (t: string) => void;
  /** Show advanced (end of the TUNE tab): every row for power users; off shows the essential ones. */
  advanced: boolean;
  setAdvanced: (v: boolean) => Promise<void>;
};
