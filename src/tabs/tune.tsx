// [ TUNE ]: the injector settings most people change, grouped, then Show advanced (every key of its .ini, and the
// advanced rows on the other tabs). Edited with a controller and saved before the game starts. FG has its own tab.
import { ButtonItem, DialogButton, PanelSection, ToggleField } from "@decky/ui";
import { toaster } from "@decky/api";
import { useEffect, useRef, useState } from "react";
import { api, errText, IniField, IniFiles } from "../api";
import { IniFieldControl, staged, useStaged } from "../controls";
import type { Ctx } from "../ctx";
import { confirm } from "../modals";
import { hintsOn } from "../prefs";
import { keep } from "../scroll";
import { Badge, Btn, Card, Line, RS, Row, SectionTitle, Tip } from "../theme";
import { ModePicker, UpscalerPicker } from "../upscaler";

const NICE: Record<string, string> = { optiscaler: "OptiScaler", d4r: "d4r", helixsr: "HelixSR", fakenvapi: "fakenvapi" };

export function FieldRow({ game, file, f, disabled, onChange, simple }: { game: string; file: string; f: IniField; disabled: boolean; onChange?: (v: string) => void; simple?: boolean }) {
  const st = staged.get(game, file)[f.id];
  return (
    <Row id={`f-${file}-${f.id}`}>
      <IniFieldControl f={f} value={st ? st.value : f.value} changed={!!st} disabled={disabled} simple={simple} onChange={onChange ?? ((v) => staged.set(game, file, f, v))} />
    </Row>
  );
}

/** Save / discard bar for everything staged in this game. */
export function SaveBar({ game, running, onSaved, where }: { game: string; running: boolean; onSaved: () => void; where: string }) {
  useStaged();
  const n = staged.count(game);
  const busy = useRef(false);
  // after Save / Discard the bar stays one step longer with a confirmation: its focused button must not vanish, or
  // Steam's navigation jumps to the top of the panel
  const [after, setAfter] = useState<string | null>(null);
  useEffect(() => {
    if (n) setAfter(null);
  }, [n]);
  if (!n && !after) return null;
  if (!n && after) {
    return (
      <Row id={`save-${where}`}>
        <Btn>
          <ButtonItem layout="below" onClick={() => setAfter(null)}>
            {after}
          </ButtonItem>
        </Btn>
      </Row>
    );
  }
  const save = async () => {
    if (busy.current) return;
    busy.current = true;
    await keep(async () => {
      try {
        let total = 0;
        for (const file of staged.files(game)) {
          const r = await api.saveIni(game, file, staged.edits(game, file));
          total += r.saved;
          if (r.note) toaster.toast({ title: "RAT", body: r.note });
          staged.clear(game, file);
        }
        setAfter(`✓ Saved ${total} setting${total === 1 ? "" : "s"}`);
        toaster.toast({ title: "RAT", body: `Saved ${total} setting${total === 1 ? "" : "s"}. They apply the next time the game starts.` });
      } catch (e) {
        toaster.toast({ title: "RAT", body: errText(e) });
      }
      onSaved(); // files saved before a failure are reloaded; the rest stays staged
    });
    busy.current = false;
  };
  return (
    <>
      <Row id={`save-card-${where}`}>
        <Card accent={RS.amber}>
          <div style={{ fontSize: 12, fontWeight: 650, color: RS.amber }}>
            {n} unsaved change{n === 1 ? "" : "s"}
          </div>
          {hintsOn() && <Line>Saved straight into the injector's .ini, no keyboard or Insert menu needed.</Line>}
        </Card>
      </Row>
      {running && (
        <Row>
          <Tip tone="warn">The game is running: close it to save (the injector reads its .ini when the game starts).</Tip>
        </Row>
      )}
      <Row id={`save-${where}`}>
        <Btn tone="primary">
          <ButtonItem layout="below" disabled={running} onClick={save}>
            Save .ini
          </ButtonItem>
        </Btn>
      </Row>
      <Row id={`discard-${where}`}>
        <Btn>
          <ButtonItem
            layout="below"
            onClick={async () => {
              if (await confirm("Discard changes?", `Forget the ${n} unsaved change${n === 1 ? "" : "s"}.`, "Discard", true)) {
                setAfter("✓ Changes discarded");
                staged.clear(game);
              }
            }}
          >
            Discard
          </ButtonItem>
        </Btn>
      </Row>
    </>
  );
}

/** One .ini section as a full-width, collapsible block: header button, then every key of the section inline. */
function SectionBlock({ game, file, name, count, running }: { game: string; file: string; name: string; count: number; running: boolean }) {
  useStaged();
  const id = `${game}\u0000${file}\u0000${name}`;
  const [open, setOpen] = useState<boolean>(!!openSections[id]);
  const [fields, setFields] = useState<IniField[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const rev = useRef(0);
  useEffect(() => {
    if (!open) return;
    const t = ++rev.current;
    keep(() =>
      api
        .iniSection(game, file, name)
        .then((f) => t === rev.current && setFields(f))
        .catch((e) => setErr(errText(e)))
    );
  }, [open, sectionsRev[game]]);
  const unsaved = Object.values(staged.get(game, file)).filter((e) => e.section.toLowerCase() === name.toLowerCase()).length;
  return (
    <>
      <Row id={`sec-${file}-${name}`}>
        <DialogButton
          className={`RS_Tab RS_Section${open ? " RS_Section--open" : ""}`}
          onClick={() => {
            openSections[id] = !open;
            setOpen(!open);
          }}
        >
          <span>
            {open ? "▾" : "▸"} {name}
          </span>
          <span style={{ fontSize: 9.5, fontWeight: 600, letterSpacing: 0.3, opacity: 0.85 }}>
            {count} {count === 1 ? "key" : "keys"}
            {unsaved ? <span style={{ color: RS.amber }}> · {unsaved} unsaved</span> : null}
          </span>
        </DialogButton>
      </Row>
      {open && err && (
        <Row>
          <Tip tone="danger">{err}</Tip>
        </Row>
      )}
      {open && !fields && !err && (
        <Row>
          <Line>Loading…</Line>
        </Row>
      )}
      {open && fields?.map((f) => <FieldRow key={f.id} game={game} file={file} f={f} disabled={running} />)}
    </>
  );
}

const openSections: Record<string, boolean> = {};
const sectionsRev: Record<string, number> = {};

export function TuneTab({ ctx }: { ctx: Ctx }) {
  useStaged();
  const { d, key: game, advanced } = ctx;
  const [files, setFiles] = useState<IniFiles | null>(null);
  const [ess, setEss] = useState<{ title: string; file: string; fields: IniField[] }[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [rev, setRev] = useState(0);

  useEffect(() => {
    if (!d.tunable) return;
    setErr(null);
    keep(async () => {
      try {
        setEss(await api.iniEssentials(game));
        if (advanced) setFiles(await api.iniFiles(game));
      } catch (e) {
        setErr(errText(e));
      }
    });
  }, [game, d.tunable, rev, advanced]);

  const running = d.running || !!files?.running;
  const reload = () => {
    sectionsRev[game] = (sectionsRev[game] ?? 0) + 1;
    setRev((r) => r + 1);
    ctx.reload();
  };

  return (
    <PanelSection>
      {!d.tunable ? (
        <Row>
          <Tip always>Inject an upscaler on the UPSCALE tab first. Its settings show up here, ready to change with the controller before you start the game.</Tip>
        </Row>
      ) : (
        <>
          {err && (
            <Row>
              <Tip tone="danger">{err}</Tip>
            </Row>
          )}
          <SaveBar game={game} running={running} onSaved={reload} where="top" />
          {!ess && !err && (
            <Row>
              <Line>Loading…</Line>
            </Row>
          )}
          {ess && ess.length === 0 && (
            <Row>
              <Tip always>No common settings for this add-on yet (some injectors create their .ini on the first game start). Every key is under Show advanced.</Tip>
            </Row>
          )}
          {ess?.map((g) => (
            <div key={`${g.file}/${g.title}`}>
              <Row>
                <div style={{ marginTop: 8, color: RS.accent, fontSize: 11, fontWeight: 700, letterSpacing: 0.6, textTransform: "uppercase" }}>{g.title}</div>
              </Row>
              {g.fields.map((f) => (
                <FieldRow key={f.id} game={game} file={g.file} f={f} disabled={running} simple />
              ))}
            </div>
          ))}
        </>
      )}

      <SectionTitle desc="Off: the settings most people change, on every tab. On: every key of the injector's .ini below, plus the advanced rows on UPSCALE, FG and LOOK (add-on options, launch options, profiles, presets, shader packages, RenoDX settings).">
        Advanced
      </SectionTitle>
      <Row id="tune-advanced">
        <ToggleField label="Show advanced" checked={advanced} onChange={(v) => ctx.setAdvanced(v)} />
      </Row>

      {advanced && d.tunable && (
        <>
          <ModePicker ctx={ctx} disabled={ctx.jobRunning || d.running} />
          <UpscalerPicker ctx={ctx} disabled={ctx.jobRunning || d.running} />
          <SectionTitle badge={<Badge label="all keys" tone="info" />} desc="Every setting of the injector's .ini, the same ones its in-game overlay changes. Open a section to edit its keys.">
            All injector settings
          </SectionTitle>
          {files && files.files.length === 0 && (
            <Row>
              <Tip always>No settings file yet. Some injectors create theirs on the first game start.</Tip>
            </Row>
          )}
          {files?.files.map((f) => (
            <div key={f.id}>
              {files.files.length > 1 && (
                <Row>
                  <div style={{ marginTop: 10, color: RS.accent, fontSize: 11, fontWeight: 800, letterSpacing: 0.8, textTransform: "uppercase" }}>
                    {NICE[f.id] ?? f.name} · {f.name}
                    {f.copies > 1 ? ` (${f.copies} copies)` : ""}
                  </div>
                </Row>
              )}
              {f.sections.map((sec) => (
                <SectionBlock key={`${f.id}/${sec.name}`} game={game} file={f.id} name={sec.name} count={sec.count} running={running} />
              ))}
            </div>
          ))}
          <SaveBar game={game} running={running} onSaved={reload} where="bottom" />
        </>
      )}
    </PanelSection>
  );
}
