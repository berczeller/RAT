import { callable } from "@decky/api";

// Every backend call answers { ok, data } or { ok: false, error } (see main.py)
type Res<T> = { ok: true; data: T } | { ok: false; error: string };

export class ApiError extends Error {}

function wrap<A extends any[], T>(name: string) {
  const fn = callable<A, Res<T>>(name);
  return async (...args: A): Promise<T> => {
    const r = await fn(...args);
    if (!r || typeof r !== "object") throw new ApiError(`No answer from the backend (${name})`);
    if (!r.ok) throw new ApiError(r.error);
    return r.data;
  };
}

export type Gpu = {
  vendor: string;
  name: string;
  gfx: string | null;
  family: string;
  steam_deck: boolean;
  dlss?: boolean;
  xmx?: boolean;
  fsr4_native?: boolean;
};
export type Prefs = {
  auto_launch_options: boolean;
  allow_unsupported_gpu: boolean;
  include_prerelease: boolean;
  show_hints: boolean;
  show_advanced: boolean;
  boot_banner: boolean;
  reshade_version: string;
  hdr_peak_nits: number;
};

export type Family = "dlss" | "fsr" | "xess" | "fork" | "neutral";
export type Upscaler = { id: string; label: string; hint?: string; route?: string; path?: string; family?: Family; tech?: string; model?: string; requires?: string };
export type Option = {
  id: string;
  type: "toggle" | "dropdown" | "slider";
  label: string;
  desc?: string;
  meta?: boolean;
  route?: string;
  gpu?: string[];
  values?: [string, string][];
  min?: number;
  max?: number;
  step?: number;
  default?: any;
};
export type Addon = {
  id: string;
  kind: string;
  title: string;
  subtitle: string;
  repo: string;
  author: string;
  author_short?: string;
  license: string;
  family?: Family;
  native_fg?: boolean;
  gpu: { families: string[]; experimental?: string[]; note: string };
  proton: { recommended: string | null; match: string | null; note: string };
  upscalers: Upscaler[];
  default_upscaler: string;
  options: Option[];
  needs_prepare?: boolean;
  prepare_note?: string;
  fg_fallback?: boolean;
};
export type Source = {
  id: string;
  title: string;
  subtitle?: string;
  kind: string;
  repo?: string | null;
  official: boolean;
  family?: Family;
  author?: string | null;
  origin?: string;
  packages: { id: string; version: string; prepared: boolean }[];
};
export type JobStep = { id: string; label: string; state: "pending" | "active" | "done" | "skipped" | "error" };
export type Job = {
  id: string;
  title: string;
  game: string | null;
  state: "running" | "done" | "error";
  progress: number;
  stage_progress: number;
  stage: string | null;
  steps: JobStep[];
  message: string;
  lines: string[];
  result: any;
  error: string | null;
};
export type Overview = {
  gpu: Gpu;
  prefs: Prefs;
  sources: Source[];
  catalog: Addon[];
  job: Job | null;
  browse_root: string;
  home: string;
  product: string;
  version: string;
  paths: Record<string, string>;
  exports: string;
};
export type GameItem = {
  key: string;
  appid: string | null;
  name: string;
  folder: string;
  custom: boolean;
  installed: string | null;
  active: boolean | null;
};
export type GameProfile = { arch: string | null; apis: string[]; api: string | null; engine: string; version: string | null; project: string | null; exe: string | null };
export type LaunchStatus = {
  game: string;
  key: string;
  appid: string | null;
  running: boolean;
  api: string | null;
  api_live: boolean;
  header: string;
  product: string;
  version: string;
  show?: boolean;
  injector: { title: string; author: string; version: string; kind: string } | null;
  upscaler: { mode: "Native" | "Spoofed" | "Auto" | "Off"; path: string; name: string; label?: string; model?: string; version?: string | null; family?: Family; api: string | null };
  fg: { active: boolean; provider?: string; method?: string };
  reshade: { active: boolean; mode?: string | null; preset?: string | null; effects?: number; version?: string; effect?: string; runtime?: RuntimeCheck | null };
  hdr: { active: boolean; title?: string; source?: string };
};
export type Detail = {
  game: GameItem & { exe_dir: string | null; exe_candidates: { dir: string; exe: string; score: number }[] };
  scan: { native: Record<string, string[]>; anticheat: string[]; foreign: { kind: string; dir: string }[] };
  record: any | null;
  status: { state: "not_installed" | "active" | "inactive" | "broken"; text: string };
  source_id: string;
  installed_source_id: string | null;
  editing_installed: boolean;
  kind: string;
  addon_id: string;
  route: string | null;
  upscaler: string;
  path: string | null;
  paths: string[];
  upscalers: string[];
  options: Record<string, any>;
  gpu_support: { level: "ok" | "experimental" | "unsupported" | "unknown"; text: string };
  proton: { recommended: string | null; match: string | null; note: string };
  current_tool: string | null;
  warnings: string[];
  running: boolean;
  launch: { env: Record<string, string>; dlls: Record<string, string>; text: string } | null;
  in_game: string;
  needs_reinstall?: boolean;
  external: External[];
  tunable: boolean;
  preserve_swapchain: boolean;
  profile: GameProfile;
  session: LaunchStatus | null;
  has_clipboard: boolean;
};
export type External = {
  kind: string;
  rel_dir: string;
  files: string[];
  dirs: string[];
  shared: string[];
  restore: number;
};
export type IniField = {
  id: string;
  section: string;
  key: string;
  label: string;
  desc: string;
  help: string[];
  value: string;
  default: string | null;
  type: "choice" | "range" | "text";
  choices: [string, string][];
  hints: Record<string, string>;
  min?: number;
  max?: number;
  step?: number;
  auto: boolean;
  custom: boolean;
  locked: string | null;
};
export type IniFiles = {
  files: { id: string; name: string; path: string; copies: number; sections: { name: string; count: number }[] }[];
  running: boolean;
};
export type FgGroup = { title: string; fields: IniField[] };
export type FgMethod = {
  id: string;
  label: string;
  hint: string;
  how: string;
  works: string;
  spoof: string;
  steps: string[];
  missing: string[];
  game_note: string | null;
  edits: [string, string, string][];
};
export type LatencyPlan = { provider: string; tone: "ok" | "info" | "warn"; notes: string[]; keys: string[]; recommended: [string, string, string][] };
export type FgFallback = {
  installed: boolean;
  bundled: string;
  bundled_from: string;
  donor: string | null;
  donor_note?: string | null;
  groups: { id: string; label: string; desc: string; family: Family; default: boolean; on: boolean }[];
  files: string[];
  kept: string[];
  missing: string[];
};
export type FrameGen = {
  available: boolean;
  reason?: string;
  legacy?: boolean;
  methods?: FgMethod[];
  current?: string;
  input?: string;
  output?: string;
  groups?: { title: string; fields: IniField[] }[];
  warnings?: string[];
  edit_fields?: IniField[];
  core?: FgGroup;
  by_output?: Record<string, FgGroup>;
  by_input?: Record<string, FgGroup>;
  spoofing?: FgGroup;
  latency_fields?: (IniField & { rat_hint?: boolean })[];
  plans?: Record<string, LatencyPlan>;
  cap_field?: IniField | null;
  xefg_count?: string | null;
  env?: { vendor: string; kind: string; fakenvapi: boolean; xell: boolean };
  fallback?: FgFallback | null;
  file_id?: string;
  running?: boolean;
  no_fg?: boolean;
};
export type UpdateInfo = {
  source_id: string;
  installed: string[];
  latest: { tag: string; name: string; published: string; page: string; asset: string; size: number; notes: string } | null;
  update: boolean;
  can_check: boolean;
};
/** What uninstalling a part does to the game's launch options (the uninstall dialog). */
export type LaunchRemoval = { current: string; after: string; removed: string[]; changed: boolean };

export type LaunchPlan = { current: string; new: string; changed: boolean; ours: string; can_apply: boolean; cleaned?: string[] };
export type ConfigFile = {
  name: string;
  path: string;
  size: number;
  content: string;
  kind: "ini" | "log";
  truncated: boolean;
  modified: number;
};
export type ReShadeState = {
  installed: boolean;
  record: any | null;
  api: string | null;
  arch: string | null;
  supported: boolean;
  reason: string | null;
  versions: { latest: string; pinned: string[]; installed: string[]; wanted: string };
  packages_installed: number;
  proxy_choices: string[];
  compositor: string | null;
  running: boolean;
  runtime?: RuntimeCheck | null;
};
export type ShaderPackage = {
  key: string;
  name: string;
  description: string;
  url: string;
  repo: string;
  effects: string[];
  required: boolean;
  installed: boolean;
};
export type Presets = { active: string | null; game: { name: string; effects: number; active: boolean }[]; library: { name: string; effects: number }[]; exports: string; exported?: string };
export type Effect = { id: string; technique: string; file: string; folder: string; label: string };
export type PresetEffects = { preset: string | null; enabled: string[]; effects: Effect[]; applied?: "live" | "next" | null };
export type ShaderParam = {
  name: string;
  kind: "bool" | "int" | "float";
  components: number;
  default: number[];
  value: number[];
  min: number;
  max: number;
  step: number;
  label: string;
  tooltip: string;
  category: string;
  items: string[] | null;
  changed: boolean;
  saved_default: number[] | null;
};
export type EffectParams = { file: string; preset: string | null; params: ShaderParam[]; has_saved_default: boolean; applied?: "live" | "next" | null };
export type HdrParam = { key: string; value: number; kind: "slider" | "choice"; known: boolean; present: boolean; label: string; min: number; max: number; step: number; unit?: string; desc?: string; choices?: string[] };
export type HdrCandidate = {
  id: string;
  title: string;
  variant?: string | null;
  status?: string | null;
  maintainers: string[];
  source: "official" | "fork" | "auto" | "custom";
  urls: Record<string, string[]>;
  links?: string[];
  notes: string[];
  downloadable: boolean;
  reason?: string;
  known?: { name: string; status: string | null; note: string | null } | null;
  supported?: boolean;
  settings?: Record<string, string>;
};
export type HdrState = {
  installed: boolean;
  enabled: boolean;
  record: any | null;
  candidates: { official: HdrCandidate[]; forks: HdrCandidate[]; auto: HdrCandidate & { supported: boolean; reason?: string }; arch: string; api: string | null; engine: string; engine_version: string | null };
  settings: Record<string, string>;
  setting_keys: { key: string; label: string; min: number; max: number; step: number; unit: string; desc: string }[];
  params: HdrParam[];
  peak_nits: number;
  reshade: LaunchStatus["reshade"] & { paused?: boolean };
  running: boolean;
  dir?: string | null;
  pending?: { id: string | null; title?: string | null } | null;
  context?: HdrContext | null;
  auto?: { report: HdrAutoRow[]; advice: string[]; at: number | null } | null;
  applied?: "live" | "next" | null;
};
export type HdrContext = {
  engine: string;
  engine_version: string | null;
  api: string | null;
  api_live: boolean;
  arch: string;
  injector: string | null;
  frame_generation: boolean;
  chained: boolean;
  game_hdr: boolean | null;
  game_fullscreen: number | null;
  peak_nits: number;
  input: string;
  output: string;
  swap_chain: string;
};
export type HdrAutoRow = { key: string; value: string; section: string; reason: string; kept: boolean };
export type RuntimeCheck = { reshade: boolean | null; renodx: boolean | null; overlay_key: string; errors: string[]; mode?: string | null };
export type ProfileSummary = { game: string; created: number; parts: string[] };
export type HdrMod = HdrCandidate & { game: string };

export const api = {
  overview: wrap<[], Overview>("overview"),
  listGames: wrap<[boolean], GameItem[]>("list_games"),
  gameDetail: wrap<[string], Detail>("game_detail"),
  setExeDir: wrap<[string, string], Detail>("set_exe_dir"),
  addCustomGame: wrap<[string], { key: string }>("add_custom_game"),
  removeCustomGame: wrap<[string], boolean>("remove_custom_game"),
  setChoice: wrap<[string, string | null, string | null, [string, any] | null], Detail>("set_choice"),
  setPref: wrap<[string, boolean | number | string], Prefs>("set_pref"),
  checkUpdates: wrap<[string, boolean], UpdateInfo>("check_updates"),
  downloadSource: wrap<[string], Job>("download_source"),
  importLink: wrap<[string], Job>("import_link"),
  importFile: wrap<[string], Job>("import_file"),
  removeSource: wrap<[string], boolean>("remove_source"),
  install: wrap<[string, boolean], Job>("install"),
  update: wrap<[string], Job>("update"),
  uninstall: wrap<[string], Job>("uninstall"),
  setActive: wrap<[string, boolean], Detail>("set_active"),
  setUpscaler: wrap<[string, string], Detail>("set_upscaler"),
  setMode: wrap<[string, string], Detail>("set_mode"),
  setOption: wrap<[string, string, any], Detail>("set_option"),
  launchPlan: wrap<[string, string | null, boolean], LaunchPlan>("launch_plan"),
  launchApplied: wrap<[string, string | null], boolean>("launch_applied"),
  launchRemoval: wrap<[string, string, string | null], LaunchRemoval>("launch_removal"),
  launchKeep: wrap<[string, string, string | null], { kept: string[] }>("launch_keep"),
  setPreserveSwapchain: wrap<[string, boolean], Detail>("set_preserve_swapchain"),
  readConfigs: wrap<[string], ConfigFile[]>("read_configs"),
  writeConfigValue: wrap<[string, string, string, string, string], ConfigFile[]>("write_config_value"),
  wipeExternal: wrap<[string], Job>("wipe_external"),
  iniFiles: wrap<[string], IniFiles>("ini_files"),
  iniSection: wrap<[string, string, string], IniField[]>("ini_section"),
  iniEssentials: wrap<[string], { title: string; file: string; fields: IniField[] }[]>("ini_essentials"),
  saveIni: wrap<[string, string, [string, string, string][]], { saved: number; files: number; note?: string | null }>("save_ini"),
  frameGen: wrap<[string], FrameGen>("frame_gen"),
  fgFallbackInstall: wrap<[string, string[]], Job>("fg_fallback_install"),
  fgFallbackRemove: wrap<[string], FrameGen>("fg_fallback_remove"),
  launchStatus: wrap<[string], LaunchStatus>("launch_status"),
  gameStarted: wrap<[string], LaunchStatus | null>("game_started"),
  gameStopped: wrap<[string], boolean>("game_stopped"),
  reshadeState: wrap<[string], ReShadeState>("reshade_state"),
  reshadeInstall: wrap<[string, boolean, string], Job>("reshade_install"),
  reshadeUninstall: wrap<[string], { removed: boolean; kept_for?: string[] }>("reshade_uninstall"),
  reshadePackages: wrap<[boolean], ShaderPackage[]>("reshade_packages"),
  reshadeInstallPackages: wrap<[string[]], Job>("reshade_install_packages"),
  reshadeRemovePackage: wrap<[string], ShaderPackage[]>("reshade_remove_package"),
  reshadePresets: wrap<[string], Presets>("reshade_presets"),
  reshadePresetAction: wrap<[string, string, string, string], Presets>("reshade_preset_action"),
  reshadeEffects: wrap<[string], PresetEffects>("reshade_effects"),
  reshadeToggleEffect: wrap<[string, string, boolean], PresetEffects>("reshade_toggle_effect"),
  reshadeEnable: wrap<[string, boolean], ReShadeState>("reshade_enable"),
  reshadeParams: wrap<[string, string], EffectParams>("reshade_params"),
  reshadeSetParam: wrap<[string, string, string, number[]], EffectParams>("reshade_set_param"),
  reshadeParamDefaults: wrap<[string, string, "save" | "restore"], EffectParams>("reshade_param_defaults"),
  reshadeSetCompositor: wrap<[string, string | null, boolean], { effect: string | null }>("reshade_set_compositor"),
  hdrState: wrap<[string, boolean], HdrState>("hdr_state"),
  hdrInstall: wrap<[string, string, string | null, string | null], Job>("hdr_install"),
  hdrEnable: wrap<[string, boolean], HdrState>("hdr_enable"),
  hdrSetting: wrap<[string, string, number], HdrState>("hdr_setting"),
  hdrBrowse: wrap<[boolean], HdrMod[]>("hdr_browse"),
  hdrExport: wrap<[string], { path: string; count: number }>("hdr_export"),
  hdrImport: wrap<[string, string], HdrState & { imported: number }>("hdr_import"),
  hdrUninstall: wrap<[string], HdrState>("hdr_uninstall"),
  hdrAutoconfig: wrap<[string, boolean], HdrState>("hdr_autoconfig"),
  profileCopy: wrap<[string], ProfileSummary>("profile_copy"),
  profileClipboard: wrap<[], ProfileSummary | null>("profile_clipboard"),
  profilePaste: wrap<[string], { applied: string[]; detail: Detail; job?: Job | null }>("profile_paste"),
  profileCandidates: wrap<[string], { key: string; name: string; parts: string[] }[]>("profile_candidates"),
  profileFromGame: wrap<[string, string], { applied: string[]; detail: Detail; job?: Job | null }>("profile_from_game"),
  profileExport: wrap<[string], { path: string; summary: ProfileSummary }>("profile_export"),
  profileFiles: wrap<[], (ProfileSummary & { path: string; name: string })[]>("profile_files"),
  profileImport: wrap<[string, string], { applied: string[]; detail: Detail; job?: Job | null }>("profile_import"),
  profileDelete: wrap<[string], Detail>("profile_delete"),
  profileDeleteFile: wrap<[string], (ProfileSummary & { path: string; name: string })[]>("profile_delete_file"),
  getJob: wrap<[], Job | null>("get_job"),
  cancelJob: wrap<[], boolean>("cancel_job"),
};

export function errText(e: unknown): string {
  if (e instanceof Error) return e.message;
  return String(e);
}
