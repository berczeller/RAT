// Game profile card: copy this game's setup, paste it onto another game, export / import a .rat.json file, delete the
// stored configuration. Used at the bottom of the Upscale tab.
import { ConfirmModal, DialogButton, Focusable, showModal } from "@decky/ui";
import { openFilePicker } from "@decky/api";
import { useEffect, useState } from "react";
import { api, ProfileSummary } from "./api";
import type { Ctx } from "./ctx";
import { toast } from "./launch";
import { confirm } from "./modals";
import { PickerItem } from "./picker";
import { watcher } from "./progress";
import { hintsOn } from "./prefs";
import { Card, hint, Line, RS, Row, SectionTitle } from "./theme";

function applied(list: string[]) {
  showModal(
    <ConfirmModal
      strTitle="Profile pasted"
      bAlertDialog
      strOKButtonText="Close"
      strDescription={<div style={{ display: "flex", flexDirection: "column", gap: 4 }}>{list.length ? list.map((x, i) => <div key={i}>• {x}</div>) : <div>Nothing applied.</div>}</div>}
    />
  );
}

export function ProfileCard({ ctx }: { ctx: Ctx }) {
  const { d, key } = ctx;
  const [clip, setClip] = useState<ProfileSummary | null>(null);
  const [others, setOthers] = useState<{ key: string; name: string; parts: string[] }[]>([]);
  useEffect(() => {
    api.profileCandidates(key).then(setOthers).catch(() => {});
  }, [key]);
  const fromGame = async (src: string) => {
    const o = others.find((x) => x.key === src);
    if (!o) return;
    if (!(await confirm(`Apply ${o.name}'s profile?`, `Onto ${d.game.name}: ${o.parts.join(", ")}. No app id or title check: any game's setup can be tried here. Keys this game's injector needs are left alone.`, "Apply")))
      return;
    const r = await ctx.act(() => api.profileFromGame(key, src));
    if (r) {
      ctx.setD(r.detail);
      applied(r.applied);
      if (r.job) watcher.start(r.job);
      ctx.syncLaunch();
    }
  };
  useEffect(() => {
    api.profileClipboard().then(setClip).catch(() => {});
  }, [d.has_clipboard, key]);

  const copy = async () => {
    const s = await ctx.act(() => api.profileCopy(key));
    if (s) {
      setClip(s);
      toast(`Copied ${d.game.name}'s profile: ${s.parts.join(", ") || "defaults"}`);
    }
  };
  const paste = async () => {
    if (!clip) return;
    if (!(await confirm(`Paste ${clip.game}'s profile?`, `Onto ${d.game.name}: ${clip.parts.join(", ") || "defaults"}. Keys this game's injector needs, and keys its .ini does not have, are left alone.`, "Paste")))
      return;
    const r = await ctx.act(() => api.profilePaste(key));
    if (r) {
      ctx.setD(r.detail);
      applied(r.applied);
      if (r.job) watcher.start(r.job);
      ctx.syncLaunch();
    }
  };
  const exportIt = async () => {
    const r = await ctx.act(() => api.profileExport(key));
    if (r) toast(`Exported to ${r.path}`);
  };
  const importIt = async () => {
    try {
      const res = await openFilePicker(0 as any, ctx.ov.exports + "/profiles", true, true, undefined, ["json"], false, false);
      if (!res?.path) return;
      const r = await ctx.act(() => api.profileImport(key, res.realpath || res.path));
      if (r) {
        ctx.setD(r.detail);
        applied(r.applied);
        if (r.job) watcher.start(r.job);
        ctx.syncLaunch();
      }
    } catch {
      /* closed */
    }
  };
  const del = async () => {
    if (
      await confirm(
        `Delete ${d.game.name}'s profile?`,
        "Forgets RAT's stored choices for this game (add-on and upscaler picks, launch toggles and compositor settings). Installed injectors, ReShade and HDR stay until you remove them on their tabs.",
        "Delete",
        true
      )
    )
      await ctx.act(() => api.profileDelete(key), "Profile deleted");
  };

  const btn = (label: string, onClick: () => void, disabled = false) => (
    <DialogButton style={{ flex: 1, minWidth: 0, padding: "6px 2px", fontSize: 11 }} className="RS_Tab" disabled={disabled} onClick={onClick}>
      {label}
    </DialogButton>
  );
  return (
    <>
      <SectionTitle desc="Carry a whole setup between similar games: injector, upscaler, .ini values, frame generation, ReShade preset and HDR brightness.">
        Game profile
      </SectionTitle>
      {clip && (
        <Row id="clip">
          <Card accent={RS.info}>
            <Line color={RS.text}>Clipboard: {clip.game}</Line>
            <Line>{clip.parts.join(" · ") || "defaults"}</Line>
          </Card>
        </Row>
      )}
      {others.length > 0 && (
        <Row id="profile-from">
          <PickerItem
            label="Apply from another game"
            title="Games with a RAT setup"
            description={hint("Fetches that game's whole setup and applies it here in one step.")}
            placeholder={`${others.length} games`}
            options={others.map((o) => ({ data: o.key, label: o.name, sub: o.parts.join(" · "), family: "fork" as const }))}
            selected={null}
            disabled={d.running}
            onChange={(o) => fromGame(o.data)}
          />
        </Row>
      )}
      <Row id="profile-a">
        <Focusable style={{ display: "flex", gap: 5, width: "100%" }} flow-children="horizontal">
          {btn("COPY", copy)}
          {btn("PASTE", paste, !clip || d.running)}
          {btn("EXPORT", exportIt)}
        </Focusable>
      </Row>
      <Row id="profile-b">
        <Focusable style={{ display: "flex", gap: 5, width: "100%", marginTop: 5 }} flow-children="horizontal">
          {btn("IMPORT…", importIt, d.running)}
          {btn("DELETE", del)}
        </Focusable>
      </Row>
      {hintsOn() && (
        <Row>
          <Line size={10}>Exports go to {ctx.ov.exports}/profiles.</Line>
        </Row>
      )}
    </>
  );
}
