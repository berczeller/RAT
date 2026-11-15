"""Small helpers shared by RAT's backend: paths, logging, JSON state, subprocess environment."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable, Iterable

log = logging.getLogger("rat")


class Paths:
    """Where RAT keeps its own files. Filled from the decky module in main.py (or by tests)."""

    user_home: Path = Path.home()
    plugin_dir: Path = Path(__file__).resolve().parents[2]
    settings_dir: Path = Path.home() / ".config" / "rat"
    runtime_dir: Path = Path.home() / ".local" / "share" / "rat"

    @classmethod
    def configure(cls, user_home: str | os.PathLike, plugin_dir: str | os.PathLike,
                  settings_dir: str | os.PathLike, runtime_dir: str | os.PathLike) -> None:
        cls.user_home = Path(user_home)
        cls.plugin_dir = Path(plugin_dir)
        cls.settings_dir = Path(settings_dir)
        cls.runtime_dir = Path(runtime_dir)
        for d in (cls.settings_dir, cls.runtime_dir):
            d.mkdir(parents=True, exist_ok=True)

    @classmethod
    def packages(cls) -> Path:
        p = cls.runtime_dir / "packages"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @classmethod
    def downloads(cls) -> Path:
        p = cls.runtime_dir / "downloads"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @classmethod
    def bin_dir(cls) -> Path:
        return cls.plugin_dir / "bin"

    @classmethod
    def data(cls, *parts: str) -> Path:
        """A folder in RAT's runtime data (reshade/, renodx/, models/...), created on first use."""
        p = cls.runtime_dir.joinpath(*parts)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @classmethod
    def exports(cls) -> Path:
        """~/RAT: where exported profiles and presets go, easy to find from Desktop Mode or a file manager."""
        p = cls.user_home / "RAT"
        p.mkdir(parents=True, exist_ok=True)
        return p


def clean_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Environment for child processes. Decky runs plugins from a frozen Python whose LD_LIBRARY_PATH and PYTHON*
    variables break system tools (bash, python3, curl), so they are removed. HOME points at the real user."""
    env = dict(os.environ)
    for k in ("LD_LIBRARY_PATH", "LD_PRELOAD", "PYTHONHOME", "PYTHONPATH", "PYTHONSAFEPATH", "PYINSTALLER_RESET_ENVIRONMENT",
              "_MEIPASS2", "_PYI_APPLICATION_HOME_DIR", "_PYI_ARCHIVE_FILE", "_PYI_PARENT_PROCESS_LEVEL", "SSL_CERT_FILE"):
        env.pop(k, None)
    # restore the original library path PyInstaller saved, if any
    orig = os.environ.get("LD_LIBRARY_PATH_ORIG")
    if orig:
        env["LD_LIBRARY_PATH"] = orig
    env["HOME"] = str(Paths.user_home)
    env.setdefault("USER", Paths.user_home.name)
    path = [str(Paths.bin_dir()), "/usr/local/bin", "/usr/bin", "/bin"]
    for p in env.get("PATH", "").split(":"):
        if p and p not in path:
            path.append(p)
    env["PATH"] = ":".join(path)
    env.setdefault("XDG_DATA_HOME", str(Paths.user_home / ".local" / "share"))
    env.setdefault("XDG_CACHE_HOME", str(Paths.user_home / ".cache"))
    env["TERM"] = "dumb"
    env["LANG"] = env.get("LANG") or "C.UTF-8"
    if extra:
        env.update(extra)
    return env


def which(name: str) -> str | None:
    return shutil.which(name, path=clean_env()["PATH"])


def run_streaming(cmd: list[str], cwd: str | os.PathLike | None = None, on_line: Callable[[str], None] | None = None,
                  env: dict[str, str] | None = None, timeout: float | None = None,
                  should_cancel: Callable[[], bool] | None = None) -> tuple[int, list[str]]:
    """Runs cmd with stdin closed, calling on_line for every output line (stdout and stderr merged). Returns the exit
    code and the last 200 lines. Carriage-return progress bars are split into lines."""
    import time
    proc = subprocess.Popen([str(c) for c in cmd], cwd=str(cwd) if cwd else None, env=env or clean_env(),
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            start_new_session=True)
    tail: list[str] = []
    start = time.monotonic()
    buf = b""
    assert proc.stdout is not None
    os.set_blocking(proc.stdout.fileno(), False)
    while True:
        chunk = b""
        try:
            chunk = proc.stdout.read(4096) or b""
        except (BlockingIOError, OSError):
            chunk = b""
        if chunk:
            buf += chunk
            parts = buf.replace(b"\r", b"\n").split(b"\n")
            buf = parts.pop()
            for raw in parts:
                line = raw.decode("utf-8", "replace").rstrip()
                if not line:
                    continue
                tail.append(line)
                del tail[:-200]
                if on_line:
                    on_line(line)
            continue
        if proc.poll() is not None:
            break
        if should_cancel and should_cancel():
            _kill(proc)
            raise CancelledError("cancelled")
        if timeout and time.monotonic() - start > timeout:
            _kill(proc)
            raise TimeoutError(f"{Path(str(cmd[0])).name} took longer than {int(timeout)} s")
        time.sleep(0.1)
    # the child may print its last lines between the final read and its exit: drain the pipe before closing it
    try:
        os.set_blocking(proc.stdout.fileno(), True)
        buf += proc.stdout.read() or b""
    except (OSError, ValueError):
        pass
    proc.stdout.close()
    for raw in buf.replace(b"\r", b"\n").split(b"\n"):
        line = raw.decode("utf-8", "replace").rstrip()
        if not line:
            continue
        tail.append(line)
        del tail[:-200]
        if on_line:
            on_line(line)
    return proc.returncode, tail


def _kill(proc: subprocess.Popen) -> None:
    import signal
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(5)
    except Exception:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except Exception:
            pass


class CancelledError(Exception):
    pass


class UserError(Exception):
    """An error with a message meant for the user (shown as is in the UI)."""


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path: Path, data: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def same_file(a: Path, b: Path) -> bool:
    try:
        if a.stat().st_size != b.stat().st_size:
            return False
        return sha256_of(a) == sha256_of(b)
    except OSError:
        return False


def is_within(child: Path, parent: Path) -> bool:
    try:
        Path(child).resolve().relative_to(Path(parent).resolve())
        return True
    except ValueError:
        return False


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def first_existing(paths: Iterable[Path]) -> Path | None:
    for p in paths:
        if p and Path(p).exists():
            return Path(p)
    return None


def slug(text: str, n: int = 60) -> str:
    """'Space Game: Remastered' -> 'space-game-remastered' (file names, profile ids)."""
    import re
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (s or "item")[:n]


def norm_title(text: str) -> str:
    """Loose game-title key for matching: lower case, letters and digits only, (R)/(TM) and editions dropped."""
    import re
    import unicodedata
    t = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    t = re.sub(r"\((dx\d+|vulkan|[^)]*edition[^)]*)\)", " ", t)
    t = re.sub(r"\b(the|edition|remastered|definitive|enhanced|goty|game of the year|directors cut|director s cut)\b",
               " ", t)
    return re.sub(r"[^a-z0-9]+", "", t)


def safe_rel(path: str) -> str | None:
    """A Windows-style relative path from an INI or a package list ('.\\OptiScaler', 'd4r') as a clean POSIX relative
    path, or None when it is absolute or climbs out ('..')."""
    import re
    rel = (path or "").strip().replace("\\", "/")
    rel = re.sub(r"^(\./)+", "", rel).strip("/")
    if rel in ("", "."):
        return ""
    if ":" in rel or rel.startswith("/") or ".." in rel.split("/"):
        return None
    return rel


def ci_path(p: Path) -> Path:
    """The file Wine would load for `p`: p itself when it exists, else a sibling whose name differs only in case
    (Wine's loader is case-insensitive, Linux is not: a game's D3DCompiler_47.dll is the same DLL as
    d3dcompiler_47.dll). Returns p unchanged when there is none."""
    p = Path(p)
    if p.exists() or p.is_symlink():
        return p
    low = p.name.lower()
    try:
        for e in p.parent.iterdir():
            if e.name.lower() == low:
                return e
    except OSError:
        pass
    return p


def place_file(src: Path, dst: Path) -> Path:
    """Copies src to dst so dst always ends up a regular file: the source is checked first, missing parent folders are
    created, and a symlink already at dst (dangling or not; LetMeReShade leaves them for dxgi.dll / d3dcompiler_47.dll)
    is replaced instead of followed. Written through a temporary file and renamed into place."""
    src, dst = Path(src), Path(dst)
    if not src.is_file():
        raise UserError(f"{src.name} is missing from RAT's store ({src.parent}). Reinstall to download it again.")
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".rat-tmp")
    if tmp.exists() or tmp.is_symlink():
        tmp.unlink()
    shutil.copy2(src, tmp)
    os.replace(tmp, dst)
    return dst
