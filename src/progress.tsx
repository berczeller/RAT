// Background jobs (downloads, extraction, compilation, installs) and their progress modal.
//
// The watcher lives at plugin level, so a download keeps being followed when the Quick Access menu is closed. While a
// job runs, a centred modal shows its stages (Fetch → Download → Verify → Extract → Compile → Install → Configure),
// an overall progress bar and the current step, instead of a log in the sidebar. "Hide" keeps the job running in the
// background (a pill in the panel header reopens the modal); the modal closes itself shortly after a job succeeds.
import { DialogButton, Focusable, ModalRoot, ProgressBar, showModal } from "@decky/ui";
import { toaster } from "@decky/api";
import { useEffect, useState } from "react";
import { FiCheck, FiCircle, FiLoader, FiMinus, FiX } from "react-icons/fi";
import { api, Job, JobStep } from "./api";
import { applyLaunch, TITLE } from "./launch";
import { RatIcon } from "./icon";
import { RS } from "./theme";

type Listener = (j: Job | null) => void;

export const watcher = {
  job: null as Job | null,
  timer: 0 as any,
  listeners: new Set<Listener>(),
  autoLaunch: true,
  polling: false,
  then: null as null | ((j: Job) => void),
  modal: null as null | { Close: () => void },
  start(j: Job, then?: (j: Job) => void, show = true) {
    this.job = j;
    this.then = then ?? null;
    this.emit();
    if (!this.timer) this.timer = setInterval(() => this.poll(), 500);
    if (show) openProgress();
  },
  async poll() {
    if (this.polling || !this.timer) return;
    this.polling = true;
    try {
      let j: Job | null = null;
      try {
        j = await api.getJob();
      } catch {
        return;
      }
      this.job = j;
      if (!j || j.state !== "running") {
        clearInterval(this.timer);
        this.timer = 0;
        if (j) await this.finished(j);
      }
      this.emit();
    } finally {
      this.polling = false;
    }
  },
  async finished(j: Job) {
    const then = this.then;
    this.then = null;
    if (j.state === "error") {
      if (!this.modal) toaster.toast({ title: `${TITLE} · ${j.title}`, body: j.error || "Failed" });
      return;
    }
    if (!this.modal) toaster.toast({ title: `${TITLE} · ${j.title}`, body: j.message || "Done" });
    if (j.game && j.result && typeof j.result === "object" && "launch" in j.result && (this.autoLaunch || j.result.strip)) {
      await applyLaunch(j.game, true, !!j.result.strip);
    }
    then?.(j);
  },
  emit() {
    this.listeners.forEach((l) => l(this.job));
  },
  stop() {
    if (this.timer) clearInterval(this.timer);
    this.timer = 0;
    this.listeners.clear();
    this.modal?.Close();
    this.modal = null;
  },
};

export function useJob(): Job | null {
  const [job, setJob] = useState<Job | null>(watcher.job);
  useEffect(() => {
    const l: Listener = (j) => setJob(j);
    watcher.listeners.add(l);
    return () => {
      watcher.listeners.delete(l);
    };
  }, []);
  return job;
}

function StepChip({ s }: { s: JobStep }) {
  const c = { done: RS.ok, active: RS.amber, error: RS.danger, pending: RS.dim, skipped: "rgba(127,138,161,0.5)" }[s.state];
  const Icon = { done: FiCheck, active: FiLoader, error: FiX, pending: FiCircle, skipped: FiMinus }[s.state];
  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 4, flex: 1, minWidth: 0 }}>
      <div
        style={{
          width: 26,
          height: 26,
          borderRadius: 26,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          border: `2px solid ${c}`,
          background: s.state === "active" ? `${c}22` : s.state === "done" ? `${c}1c` : "transparent",
          boxShadow: s.state === "active" ? `0 0 12px ${c}66` : "none",
          color: c,
        }}
      >
        <Icon size={13} />
      </div>
      <div style={{ fontSize: 10.5, letterSpacing: 0.5, textTransform: "uppercase", color: s.state === "pending" ? RS.dim : RS.text, opacity: s.state === "skipped" ? 0.5 : 1 }}>
        {s.label}
      </div>
    </div>
  );
}

function ProgressModal({ closeModal }: { closeModal?: () => void }) {
  const job = useJob();
  // auto-close shortly after a job finishes; a chained job starting inside that window cancels the timer through the
  // effect cleanup, and its own "done" re-arms it
  useEffect(() => {
    if (job && job.state === "done") {
      const t = setTimeout(() => closeModal?.(), 1600);
      return () => clearTimeout(t);
    }
    return undefined;
  }, [job?.state, job?.id]);
  if (!job) return null;
  const done = job.state === "done";
  const failed = job.state === "error";
  const pct = job.progress >= 0 ? Math.round(job.progress * 100) : null;
  return (
    <ModalRoot closeModal={closeModal} bDisableBackgroundDismiss={!done && !failed}>
      <div style={{ display: "flex", flexDirection: "column", gap: 14, padding: "4px 2px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <RatIcon size={28} style={{ color: RS.accent }} />
          <div style={{ minWidth: 0 }}>
            <div style={{ fontSize: 18, fontWeight: 700, color: RS.text }}>{job.title}</div>
            <div style={{ fontSize: 11, color: RS.muted, letterSpacing: 0.8, textTransform: "uppercase" }}>RAT (Radical Advanced Tools)</div>
          </div>
          <div style={{ marginLeft: "auto", fontSize: 22, fontWeight: 700, color: failed ? RS.danger : done ? RS.ok : RS.amber }}>
            {failed ? "Failed" : done ? "Done" : pct !== null ? `${pct}%` : ""}
          </div>
        </div>
        {job.steps.length > 0 && (
          <div style={{ display: "flex", gap: 4 }}>
            {job.steps.map((s) => (
              <StepChip key={s.id} s={s} />
            ))}
          </div>
        )}
        <ProgressBar nProgress={done ? 100 : pct ?? undefined} indeterminate={!done && !failed && pct === null} nTransitionSec={0.4} />
        <div style={{ minHeight: 34, fontSize: 13, lineHeight: 1.4, color: failed ? RS.danger : RS.text, overflowWrap: "anywhere" }}>
          {failed ? job.error : job.message}
        </div>
        <Focusable style={{ display: "flex", gap: 10, justifyContent: "flex-end" }} flow-children="horizontal">
          {!done && !failed && (
            <>
              <DialogButton style={{ width: 160 }} onClick={() => closeModal?.()}>
                Hide (keep running)
              </DialogButton>
              <DialogButton style={{ width: 120 }} onClick={() => api.cancelJob()}>
                Cancel
              </DialogButton>
            </>
          )}
          {(done || failed) && (
            <DialogButton style={{ width: 120 }} onClick={() => closeModal?.()}>
              Close
            </DialogButton>
          )}
        </Focusable>
      </div>
    </ModalRoot>
  );
}

export function openProgress() {
  if (watcher.modal) return;
  // Steam passes its own closeModal to the modal; fnOnClose tells us whichever way it closed
  watcher.modal = showModal(<ProgressModal />, undefined, {
    strTitle: "RAT",
    fnOnClose: () => {
      watcher.modal = null;
    },
  });
}

/** A small pill under the header while a job runs in the background; selecting it reopens the modal. */
export function JobPill({ job }: { job: Job | null }) {
  if (!job || job.state !== "running") return null;
  const pct = job.progress >= 0 ? `${Math.round(job.progress * 100)}%` : "…";
  return (
    <DialogButton
      onClick={openProgress}
      style={{ marginTop: 4, padding: "2px 8px", minWidth: 0, width: "auto", fontSize: 9.5, lineHeight: "16px", color: RS.amber, background: "rgba(255,195,107,0.12)", border: `1px solid ${RS.amber}66`, borderRadius: 999 }}
    >
      {job.title} · {pct}
    </DialogButton>
  );
}
