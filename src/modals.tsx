import { ConfirmModal, DialogButton, Focusable, ModalRoot, TextField, showModal } from "@decky/ui";
import { openFilePicker, toaster } from "@decky/api";
import { useEffect, useState } from "react";
import { api, ConfigFile, errText, Job, LaunchRemoval } from "./api";
import { getLaunchOptions } from "./steam";
import { pressFeedback } from "./feedback";
import { showPicker } from "./picker";
import { ThemeStyle, UpLabel } from "./theme";

const mono: React.CSSProperties = {
  fontFamily: "'Fira Mono', 'DejaVu Sans Mono', monospace",
  fontSize: 11,
  lineHeight: 1.35,
  whiteSpace: "pre-wrap",
  wordBreak: "break-all",
  margin: 0,
};

export const DISCLAIMER =
  "RAT (Radical Advanced Tools) is an independent, unofficial project. It is not affiliated with, endorsed by or sponsored by " +
  "Valve, NVIDIA, AMD or Intel, nor by the authors of d4r, HelixSR, OptiScaler, ReShade or RenoDX. DLSS is a trademark of " +
  "NVIDIA; FSR / FidelityFX of AMD; XeSS of " +
  "Intel. Add-ons are downloaded from their authors' official releases and run under their own licenses; NVIDIA files they " +
  "fetch are subject to NVIDIA's terms. Injecting DLLs can break games and can get accounts banned in online games with " +
  "anti-cheat. Use at your own risk.";

// --- advanced settings: live view of the add-on's INI files and logs --------------------------------------------

function AdvancedModal({ gameKey, title, closeModal }: { gameKey: string; title: string; closeModal?: () => void }) {
  const [files, setFiles] = useState<ConfigFile[]>([]);
  const [sel, setSel] = useState(0);
  const [err, setErr] = useState<string | null>(null);
  const [section, setSection] = useState("");
  const [key, setKey] = useState("");
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);

  const load = async () => {
    try {
      const f = await api.readConfigs(gameKey);
      setFiles(f);
      setErr(f.length ? null : "No configuration files yet. Install the add-on first (some files appear after the first launch).");
    } catch (e) {
      setErr(errText(e));
    }
  };
  useEffect(() => {
    load();
  }, []);

  const file = files[Math.min(sel, Math.max(files.length - 1, 0))];
  const save = async () => {
    if (!file || !section.trim() || !key.trim()) return;
    setBusy(true);
    try {
      const f = await api.writeConfigValue(gameKey, file.path, section.trim(), key.trim(), value);
      setFiles(f);
      toaster.toast({ title: "RAT", body: `${file.name}: [${section}] ${key}=${value}` });
    } catch (e) {
      toaster.toast({ title: "RAT", body: errText(e) });
    }
    setBusy(false);
  };

  return (
    <ModalRoot closeModal={closeModal} bAllowFullSize className="RS_PickModal">
      <ThemeStyle />
      <div ref={pressFeedback} style={{ display: "flex", flexDirection: "column", gap: 10, minHeight: 0 }}>
        <div style={{ fontSize: 18, fontWeight: 600 }}>Files & logs · {title}</div>
        {err && <div style={{ color: "#f5b84b" }}>{err}</div>}
        {files.length > 0 && (
          <Focusable style={{ display: "flex", gap: 8, alignItems: "center" }} flow-children="horizontal">
            <div style={{ flex: 1 }}>
              <DialogButton
                className="RS_Pick"
                onClick={() =>
                  showPicker(
                    "Files & logs",
                    files.map((f, i) => ({ data: i, label: f.name, sub: f.kind === "log" ? "LOG" : "INI", family: f.kind === "log" ? "neutral" : "fork" })),
                    sel,
                    (o) => setSel(o.data)
                  )
                }
              >
                {file ? <UpLabel family={file.kind === "log" ? "neutral" : "fork"} text={file.name} sub={file.kind === "log" ? "LOG" : "INI"} /> : "Pick a file"}
              </DialogButton>
            </div>
            <DialogButton style={{ width: 110, minWidth: 0 }} onClick={load}>
              Refresh
            </DialogButton>
          </Focusable>
        )}
        {file && (
          <>
            <div style={{ fontSize: 11, opacity: 0.6, wordBreak: "break-all" }}>
              {file.path} · {(file.size / 1024).toFixed(1)} KB · {new Date(file.modified * 1000).toLocaleString()}
              {file.truncated ? " · showing the last 200 KB" : ""}
            </div>
            <Focusable
              style={{
                maxHeight: "48vh",
                overflowY: "auto",
                background: "rgba(0,0,0,0.35)",
                borderRadius: 6,
                padding: 10,
              }}
              onActivate={() => {}}
            >
              <pre style={mono}>{file.content}</pre>
            </Focusable>
            {file.kind === "ini" && (
              <Focusable style={{ display: "flex", gap: 6, alignItems: "flex-end" }} flow-children="horizontal">
                <div style={{ flex: 1 }}>
                  <TextField label="Section" value={section} onChange={(e) => setSection(e.target.value)} />
                </div>
                <div style={{ flex: 1 }}>
                  <TextField label="Key" value={key} onChange={(e) => setKey(e.target.value)} />
                </div>
                <div style={{ flex: 1 }}>
                  <TextField label="Value" value={value} onChange={(e) => setValue(e.target.value)} />
                </div>
                <DialogButton style={{ width: 90, minWidth: 0 }} disabled={busy || !section || !key} onClick={save}>
                  Set
                </DialogButton>
              </Focusable>
            )}
          </>
        )}
      </div>
    </ModalRoot>
  );
}

export function showAdvanced(gameKey: string, title: string) {
  showModal(<AdvancedModal gameKey={gameKey} title={title} />);
}

// --- import a fork: pasted link or a local archive ---------------------------------------------------------------

function ImportModal({
  browseRoot,
  onJob,
  closeModal,
}: {
  browseRoot: string;
  onJob: (j: Job) => void;
  closeModal?: () => void;
}) {
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const start = async (fn: () => Promise<Job>) => {
    setBusy(true);
    try {
      onJob(await fn());
      closeModal?.();
    } catch (e) {
      toaster.toast({ title: "RAT", body: errText(e) });
      setBusy(false);
    }
  };
  const browse = async () => {
    try {
      const r = await openFilePicker(
        0 as any, // FileSelectionType.FILE
        browseRoot,
        true,
        true,
        undefined,
        ["zip", "7z", "gz", "tgz", "xz", "txz", "tar"],
        false,
        false
      );
      if (r?.path) start(() => api.importFile(r.realpath || r.path));
    } catch {
      /* picker closed */
    }
  };
  return (
    <ModalRoot closeModal={closeModal}>
      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        <div style={{ fontSize: 18, fontWeight: 600 }}>Add an external fork</div>
        <div style={{ fontSize: 13, opacity: 0.8 }}>
          Paste a GitHub repository or release link, or a direct link to a .zip / .7z / .tar.gz / .tar.xz archive. RAT detects
          whether it is d4r-, HelixSR- or OptiScaler-compatible from its files. Only use forks you trust.
        </div>
        <TextField
          label="Link"
          value={url}
          bShowClearAction
          onChange={(e) => setUrl(e.target.value)}
          description="e.g. https://github.com/owner/fork or …/releases/tag/v1.2"
        />
        <Focusable style={{ display: "flex", gap: 8 }} flow-children="horizontal">
          <DialogButton disabled={busy || !url.trim()} onClick={() => start(() => api.importLink(url.trim()))}>
            Import from link
          </DialogButton>
          <DialogButton disabled={busy} onClick={browse}>
            Browse drive / USB…
          </DialogButton>
        </Focusable>
      </div>
    </ModalRoot>
  );
}

export function showImport(browseRoot: string, onJob: (j: Job) => void) {
  showModal(<ImportModal browseRoot={browseRoot} onJob={onJob} />);
}

// --- confirmations and about ---------------------------------------------------------------------------------------

export function confirm(title: string, body: React.ReactNode, ok: string, destructive = false): Promise<boolean> {
  return new Promise((resolve) => {
    let settled = false;
    const done = (v: boolean) => {
      if (!settled) {
        settled = true;
        resolve(v);
      }
    };
    showModal(
      <ConfirmModal
        strTitle={title}
        strDescription={body}
        strOKButtonText={ok}
        bDestructiveWarning={destructive}
        onOK={() => done(true)}
        onCancel={() => done(false)}
        closeModal={() => done(false)}
      />
    );
  });
}

export function showAbout() {
  showModal(
    <ConfirmModal
      strTitle="RAT (Radical Advanced Tools) 1.1"
      bAlertDialog
      strOKButtonText="Close"
      strDescription={
        <div style={{ display: "flex", flexDirection: "column", gap: 10, fontSize: 13 }}>
          <div>
            The all-in-one upscaler, frame generation, HDR and shader suite for SteamOS and Linux, on AMD, NVIDIA and Intel
            GPUs. Per-game installer, configurator and cleaner for upscaler injectors, ReShade and RenoDX, made by Tom
            Berczeller. Everything comes from its authors' official releases:
          </div>
          <div style={{ ...mono, fontSize: 12 }}>
            d4r · github.com/countervolts/d4r{"\n"}HelixSR · github.com/lonewolf0622/HelixSR{"\n"}OptiScaler · github.com/optiscaler/OptiScaler
            {"\n"}ReShade · reshade.me{"\n"}RenoDX · github.com/clshortfuse/renodx
          </div>
          <div style={{ opacity: 0.85 }}>{DISCLAIMER}</div>
          <div style={{ opacity: 0.7, fontSize: 12 }}>Icon: "Rat" by Thiago (SVG Repo), CC Attribution License.</div>
        </div>
      }
    />
  );
}

/** Uninstall confirmation that also asks what happens to this game's launch options. Shown only when the uninstall
 * would change them; the choice applies to the selected game alone. "keep" makes the arguments the user's own before
 * the uninstall runs (RAT never removes them later); "restore" puts the launch options back as they were before RAT.
 * Resolves null when cancelled. A ConfirmModal: Steam's own dialog, fully navigable with the controller. */
export async function confirmUninstall(
  key: string,
  appid: string | number | null | undefined,
  part: "injector" | "reshade" | "renodx",
  title: string,
  body: string,
): Promise<"restore" | "keep" | null> {
  let cur: string | null = null;
  let r: LaunchRemoval | null = null;
  try {
    cur = appid ? await getLaunchOptions(Number(appid)) : null;
    r = await api.launchRemoval(key, part, cur);
  } catch {
    r = null;
  }
  if (!r || !r.changed || !r.removed.length) return (await confirm(title, body, "Uninstall", true)) ? "restore" : null;
  const removal = r;
  const choice = await new Promise<"restore" | "keep" | null>((resolve) => {
    let settled = false;
    const done = (v: "restore" | "keep" | null) => {
      if (!settled) {
        settled = true;
        resolve(v);
      }
    };
    showModal(
      <ConfirmModal
        strTitle={title}
        bDestructiveWarning
        strDescription={
          <div style={{ display: "flex", flexDirection: "column", gap: 8, fontSize: 13 }}>
            <div>{body}</div>
            <div>This game's launch options have arguments for it:</div>
            <div style={{ fontFamily: "monospace", fontSize: 12, padding: "6px 8px", borderRadius: 6, background: "rgba(0,0,0,0.3)", overflowWrap: "anywhere" }}>
              {removal.removed.join("  ")}
            </div>
            <div style={{ opacity: 0.8 }}>
              Restore puts the launch options back as they were before RAT (your own variables stay). Keep leaves them as they are, for example
              when the game uses its own HDR. Only this game changes.
            </div>
          </div>
        }
        strOKButtonText="Uninstall & restore launch options"
        strMiddleButtonText="Uninstall & keep them"
        onOK={() => done("restore")}
        onMiddleButton={() => done("keep")}
        onCancel={() => done(null)}
        closeModal={() => done(null)}
      />
    );
  });
  if (choice === "keep") {
    try {
      await api.launchKeep(key, part, cur);
    } catch {
      /* the uninstall still runs; RAT would then restore them */
    }
  }
  return choice;
}
