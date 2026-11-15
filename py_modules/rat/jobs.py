"""One background operation at a time (download, extract, build, install...), polled by the frontend.

A job walks through named stages (fetch, download, verify, extract, compile, install, configure) so the frontend can
draw a stepped progress modal instead of a log. `progress` is the overall fraction (or -1 while unknown) and
`stage_progress` the fraction inside the current stage.
"""
from __future__ import annotations

import threading
import time
import traceback
import uuid
from typing import Any, Callable

from .util import CancelledError, UserError, log

STAGE_LABELS = {
    "fetch": "Fetch",
    "download": "Download",
    "verify": "Verify",
    "extract": "Extract",
    "compile": "Compile",
    "install": "Install",
    "configure": "Configure",
    "remove": "Remove",
}
# Typical stage lists. A job may skip stages (they are shown as skipped once a later one starts).
STAGES_INSTALL = ["fetch", "download", "verify", "extract", "compile", "install", "configure"]
STAGES_DOWNLOAD = ["fetch", "download", "verify", "extract"]
STAGES_REMOVE = ["remove", "configure"]


class Job:
    def __init__(self, title: str, game: str | None = None, stages: list[str] | None = None):
        self.id = uuid.uuid4().hex[:8]
        self.title = title
        self.game = game
        self.state = "running"
        self.progress = -1.0
        self.stage_progress = -1.0
        self.message = "Starting..."
        self.lines: list[str] = []
        self.result: Any = None
        self.error: str | None = None
        self.started = time.time()
        self.finished: float | None = None
        self.stages = list(stages or [])
        self.stage_id: str | None = self.stages[0] if self.stages else None
        self.skipped: set[str] = set()
        self._cancel = threading.Event()

    def update(self, message: str | None = None, progress: float | None = None) -> None:
        if message is not None:
            self.message = message[:200]
            self.log(message)
        if progress is not None:
            self.progress = progress

    def stage(self, stage_id: str, message: str | None = None, progress: float | None = None) -> None:
        """Moves to a stage. Stages between the current one and the new one count as skipped."""
        if stage_id not in self.stages:
            self.stages.append(stage_id)
        if self.stage_id in self.stages and stage_id in self.stages:
            a, b = self.stages.index(self.stage_id), self.stages.index(stage_id)
            for s in self.stages[a + 1:b]:
                self.skipped.add(s)
        self.stage_id = stage_id
        self.stage_progress = -1.0
        self.update(message, progress)

    def sub(self, frac: float, lo: float, hi: float, text: str | None = None) -> None:
        """Progress inside the current stage, mapped onto the overall range lo..hi."""
        if frac >= 0:
            self.stage_progress = min(1.0, frac)
            self.progress = lo + (hi - lo) * min(1.0, frac)
        else:
            self.stage_progress = -1.0
        if text:
            self.message = text[:200]

    def log(self, line: str) -> None:
        self.lines.append(line[:300])
        del self.lines[:-60]

    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def snapshot(self) -> dict:
        steps = []
        cur = self.stages.index(self.stage_id) if self.stage_id in self.stages else -1
        for i, s in enumerate(self.stages):
            if s in self.skipped:
                st = "skipped"
            elif self.state == "done" or i < cur:
                st = "done"
            elif i == cur:
                st = "error" if self.state == "error" else "active"
            else:
                st = "pending"
            steps.append({"id": s, "label": STAGE_LABELS.get(s, s.title()), "state": st})
        return {"id": self.id, "title": self.title, "game": self.game, "state": self.state,
                "progress": self.progress, "stage_progress": self.stage_progress, "stage": self.stage_id,
                "steps": steps, "message": self.message, "lines": self.lines[-14:],
                "result": self.result, "error": self.error, "started": self.started, "finished": self.finished}


class Jobs:
    def __init__(self):
        self.current: Job | None = None
        self._lock = threading.Lock()

    def busy(self) -> bool:
        return bool(self.current and self.current.state == "running")

    def start(self, title: str, fn: Callable[[Job], Any], game: str | None = None,
              stages: list[str] | None = None) -> dict:
        with self._lock:
            if self.busy():
                raise UserError(f"Busy: {self.current.title}. Wait for it to finish.")
            job = Job(title, game, stages)
            self.current = job

        def run():
            try:
                job.result = fn(job)
                job.state = "done"
                job.progress = 1.0
                job.stage_progress = 1.0
                if job.message in ("Starting...",):
                    job.message = "Done"
            except (UserError, CancelledError) as e:
                job.state = "error"
                job.error = str(e) or "Cancelled"
                job.log("Error: " + job.error)
            except Exception as e:   # unexpected: keep the details in the log
                log.error("job %s failed: %s", title, traceback.format_exc())
                job.state = "error"
                job.error = f"{type(e).__name__}: {e}"
                job.log("Error: " + job.error)
            finally:
                job.finished = time.time()

        threading.Thread(target=run, name=f"rat-{job.id}", daemon=True).start()
        return job.snapshot()

    def cancel(self) -> bool:
        if self.busy():
            self.current._cancel.set()
            return True
        return False

    def get(self) -> dict | None:
        return self.current.snapshot() if self.current else None
