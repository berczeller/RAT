// Steam client helpers: launch options and compatibility tools are read and changed through the running Steam
// client (never by editing Steam's VDF files, which Steam overwrites on exit).

declare const SteamClient: any;

export type CompatTool = { strToolName: string; strDisplayName: string };

export function getAppDetails(appId: number, timeoutMs = 2500): Promise<any | null> {
  return new Promise((resolve) => {
    let done = false;
    let reg: { unregister: () => void } | null = null;
    const finish = (v: any) => {
      if (done) return;
      done = true;
      setTimeout(() => reg?.unregister?.(), 0);
      resolve(v);
    };
    try {
      reg = SteamClient.Apps.RegisterForAppDetails(appId, (d: any) => finish(d));
    } catch {
      finish(null);
    }
    setTimeout(() => finish(null), timeoutMs);
  });
}

export async function getLaunchOptions(appId: number): Promise<string | null> {
  const d = await getAppDetails(appId);
  if (!d) return null;
  return typeof d.strLaunchOptions === "string" ? d.strLaunchOptions : null;
}

export function setLaunchOptions(appId: number, options: string): boolean {
  try {
    SteamClient.Apps.SetAppLaunchOptions(appId, options);
    return true;
  } catch (e) {
    console.error("RAT: SetAppLaunchOptions failed", e);
    return false;
  }
}

export async function getCompatInfo(appId: number): Promise<{ current: string | null; tools: CompatTool[] }> {
  let tools: CompatTool[] = [];
  try {
    tools = (await SteamClient.Apps.GetAvailableCompatTools(appId)) || [];
  } catch {
    tools = [];
  }
  const d = await getAppDetails(appId);
  const current = d && typeof d.strCompatToolName === "string" && d.strCompatToolName ? d.strCompatToolName : null;
  return { current, tools };
}

export function setCompatTool(appId: number, toolName: string): boolean {
  try {
    SteamClient.Apps.SpecifyCompatTool(appId, toolName);
    return true;
  } catch (e) {
    console.error("RAT: SpecifyCompatTool failed", e);
    return false;
  }
}

/** The best installed tool for a recommendation: the exact name first, then the newest matching one. */
export function pickTool(tools: CompatTool[], recommended: string | null, match: string | null): CompatTool | null {
  if (recommended) {
    const exact = tools.find((t) => t.strToolName === recommended || t.strDisplayName === recommended);
    if (exact) return exact;
  }
  if (match) {
    let re: RegExp;
    try {
      re = new RegExp(match, "i");
    } catch {
      return null;
    }
    const hits = tools.filter((t) => re.test(t.strToolName) || re.test(t.strDisplayName));
    hits.sort((a, b) => b.strDisplayName.localeCompare(a.strDisplayName, undefined, { numeric: true }));
    return hits[0] ?? null;
  }
  return null;
}

/** Asks Steam to verify the game's files (restores game DLLs a manual injector install overwrote). */
export async function verifyApp(appId: number): Promise<boolean> {
  try {
    await SteamClient.Apps.VerifyApp(appId);
    return true;
  } catch (e) {
    console.error("RAT: VerifyApp failed", e);
    return false;
  }
}
