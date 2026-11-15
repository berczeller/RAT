// Launch options: computed by the backend, read and written through the running Steam client.
import { toaster } from "@decky/api";
import { api, errText, LaunchPlan } from "./api";
import { getLaunchOptions, setLaunchOptions } from "./steam";

export const TITLE = "RAT";

export function toast(body: string, title: string = TITLE) {
  toaster.toast({ title, body });
}

export async function applyLaunch(key: string, quiet = false, stripForeign = false): Promise<LaunchPlan | null> {
  try {
    const games = await api.listGames(false);
    const g = games.find((x) => x.key === key);
    if (!g?.appid) return null;
    const appId = Number(g.appid);
    const current = await getLaunchOptions(appId);
    const plan = await api.launchPlan(key, current, stripForeign);
    if (plan.changed) {
      if (!setLaunchOptions(appId, plan.new)) throw new Error("Steam refused the launch options");
      if (plan.cleaned?.length) toast(`Cleaned up: ${plan.cleaned.join("; ")}`, "RAT · launch options");
      toast(plan.new ? `Launch options: ${plan.new}` : "Launch options cleared");
    } else if (!quiet) {
      toast("Launch options already up to date");
    }
    await api.launchApplied(key, current);
    return { ...plan, current: plan.new, changed: false };
  } catch (e) {
    toast(`Launch options: ${errText(e)}`);
    return null;
  }
}
