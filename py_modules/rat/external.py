"""Injectors RAT did not install: manual OptiScaler / d4r / HelixSR copies, fakenvapi, dlssg-to-fsr3, or files left
by other tools. They are found by their file names and, for DLLs with common names (dxgi.dll, nvapi64.dll, d3d12.dll),
by a marker string inside the file, so a game's own DLL is never mistaken for an injector.

remove() deletes what is certain, puts back the game files the injectors keep (HelixSR's *.original.dll, HelixSR
Scaler's backup folder) and leaves alone runtime DLLs a game can ship itself (amd_fidelityfx_*, libxess*, nvngx_dlss*)
unless they are byte-identical to a file in one of RAT's downloaded packages. Those kept files are listed so the
user can let Steam verify the game, which restores the game's own versions.
"""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from . import sources, steam
from .util import is_within, log, sha256_of

PROXY_DLLS = ["dxgi.dll", "winmm.dll", "version.dll", "dbghelp.dll", "d3d12.dll", "wininet.dll", "winhttp.dll",
              "d3d11.dll", "dinput8.dll", "nvngx.dll", "_nvngx.dll"]
OPTI_FILES = {"optiscaler.ini", "optiscaler.log", "optiscaler.dll", "optiscaler.asi", "optiscaler.ini.bak",
              "fakenvapi.ini", "fakenvapi.log", "fakenvapi.dll", "dlssg_to_fsr3_amd_is_better.dll", "dlssg_to_fsr3.log",
              "dlss-enabler-headless.dll", "dlss-enabler.log"}
# generic names: removed only when the file mentions OptiScaler
OPTI_SCRIPTS = {"setup_linux.sh", "setup_windows.bat", "remove_optiscaler.sh", "remove_optiscaler.bat"}
OPTI_JUNK = re.compile(r"(?i)^!!.*\.txt$")
# folders OptiScaler's own remove_optiscaler.sh deletes, plus its 0.9 library folder
OPTI_DIRS = {"optiscaler", "d3d12_optiscaler", "dlssoverrides"}
# "Licenses" is deleted by OptiScaler's own remover, but games ship one too: removed only when every file in it is a
# copy of a file in one of RAT's packages, otherwise reported as kept
LICENSES = "licenses"
D4R_FILES = {"d4r_readme.txt", "asi_readme.txt", "d4r-check.sh", "d4r.asi"}
HELIX_FILES = {"helixsr.ini", "helixsr.log", "helixsr-install.json", "helixsr-scaler.files", "helixsr.dll"}
HELIX_DIRS = {"helixsr"}
SHARED = re.compile(r"(?i)^(amd_fidelityfx_.*|ffx_.*|libxess.*|libxell|nvngx_dlss[dg]?|sl\..*)\.dll$")
MARK_OPTI = (b"OptiScaler", b"optiscaler")
MARK_FAKENVAPI = (b"fakenvapi",)
MARK_VKD3D = (b"vkd3d",)


def _contains(p: Path, marks: tuple[bytes, ...], limit: int = 96 << 20) -> bool:
    """Whether the file holds one of the marker strings (ASCII or UTF-16), read in chunks."""
    try:
        if p.stat().st_size > limit:
            return False
        wide = tuple(m.decode().encode("utf-16-le") for m in marks)
        tail = b""
        with open(p, "rb") as f:
            while True:
                chunk = f.read(4 << 20)
                if not chunk:
                    return False
                buf = tail + chunk
                if any(m in buf for m in marks + wide):
                    return True
                tail = buf[-64:]
    except OSError:
        return False


def _ours(d: Path) -> bool:
    return steam.has_record_dir(d)


def _rel(p: Path, base: Path) -> str:
    try:
        return p.relative_to(base).as_posix()
    except ValueError:
        return str(p)


def detect(game_folder: str | os.PathLike, ours: list | None = None) -> list[dict]:
    """[{kind, dir, rel_dir, files, dirs, restore, shared, helix}] per folder that holds a foreign injector.
    ours: folders where RAT's own install lives (its record folder, HelixSR drop-in folders), never reported."""
    root = Path(game_folder).resolve()
    skip_dirs = set()
    for o in ours or []:
        try:
            skip_dirs.add(Path(o).resolve())
        except OSError:
            pass
    found: list[dict] = []
    for d, files in steam.walk(root, depth=8, limit=60000, skip=steam.SCAN_SKIP - {"helixsr"}):
        parts = [p.lower() for p in d.relative_to(root).parts]
        if any(p in OPTI_DIRS | HELIX_DIRS | {"d4r", ".rat", "helixsr-scaler-backup"} for p in parts):
            continue   # inside an injector's own folder: handled with its parent
        if _ours(d) or d in skip_dirs:
            continue
        low = {f.lower(): f for f in files}
        subdirs = {e.name.lower(): e.name for e in _scandirs(d)}
        kinds: list[str] = []
        rm_files: set[str] = set()
        rm_dirs: set[str] = set()
        restore: list[list[str]] = []
        licenses_dir: str | None = None
        helix = False
        opti = any(n in low for n in ("optiscaler.ini", "optiscaler.log", "optiscaler.dll", "optiscaler.asi"))
        d4r = "d4r" in subdirs and any((d / subdirs["d4r"] / n).exists() for n in ("d4r.ini", "nvngx.dll"))
        # proxies carrying OptiScaler (manual installs rename OptiScaler.dll)
        proxies = [low[n] for n in PROXY_DLLS if n in low and _contains(d / low[n], MARK_OPTI)]
        if proxies:
            opti = True
        if "nvngx.ini" in low and opti:
            rm_files.add(low["nvngx.ini"])
        if opti or d4r:
            kinds.append("d4r" if d4r else "OptiScaler")
            rm_files.update(proxies)
            rm_files.update(low[n] for n in low if n in OPTI_FILES or OPTI_JUNK.match(n))
            rm_files.update(low[n] for n in low if n in OPTI_SCRIPTS and _contains(d / low[n], MARK_OPTI))
            rm_dirs.update(subdirs[n] for n in subdirs if n in OPTI_DIRS)
            if LICENSES in subdirs:
                licenses_dir = subdirs[LICENSES]
        if d4r:
            rm_dirs.add(subdirs["d4r"])
            rm_files.update(low[n] for n in low if n in D4R_FILES)
            if "install.sh" in low and _contains(d / low["install.sh"], (b"d4r",)):
                rm_files.add(low["install.sh"])
            for n in ("d3d12.dll", "d3d12core.dll"):
                if n in low and _contains(d / low[n], MARK_VKD3D):
                    rm_files.add(low[n])
        if "nvapi64.dll" in low and _contains(d / low["nvapi64.dll"], MARK_FAKENVAPI):
            rm_files.add(low["nvapi64.dll"])
            if not kinds:
                kinds.append("fakenvapi")
        if "dlssg_to_fsr3_amd_is_better.dll" in low and not kinds:
            kinds.append("dlssg-to-fsr3")
            rm_files.update(low[n] for n in low if n.startswith("dlssg_to_fsr3"))
        # HelixSR: FSR 3.1 drop-in keeps the game's DLL as *.original.dll; the Scaler lists its files
        originals = [f for f in files if f.lower().endswith(".original.dll")]
        if originals or any(n in low for n in HELIX_FILES) or "helixsr" in subdirs:
            helix = True
            kinds.append("HelixSR Scaler" if "helixsr-scaler.files" in low or "helixsr" in subdirs else "HelixSR")
            for o in originals:
                cur = o[: -len(".original.dll")] + ".dll"
                cur_low = low.get(cur.lower())
                # the drop-in's DLL is still there: put the game's own back. When a game update replaced it with a
                # newer file of its own, that newer file stays and the old copy is left alone.
                if cur_low is None or _contains(d / cur_low, MARK_HELIX) or _in_packages(d / cur_low, "helixsr"):
                    restore.append([o, cur_low or cur])
            rm_files.update(low[n] for n in low if n in HELIX_FILES)
            rm_dirs.update(subdirs[n] for n in subdirs if n in HELIX_DIRS)
            manifest = low.get("helixsr-scaler.files")
            if manifest:
                for line in _read_lines(d / manifest):
                    p = (d / line).resolve() if line and not line.startswith("#") else None
                    if p and is_within(p, d) and p.is_file():
                        rm_files.add(_rel(p, d))
            if "helixsr-scaler.files" in low or "helixsr" in subdirs:
                rm_files.update(proxies)
                for n in PROXY_DLLS:
                    if n in low and _contains(d / low[n], MARK_HELIX):
                        rm_files.add(low[n])
            if "helixsr-scaler-backup" in subdirs:
                b = d / subdirs["helixsr-scaler-backup"]
                listed = {f.lower() for f in rm_files}
                for p in sorted(b.rglob("*")):
                    if not p.is_file():
                        continue
                    dst = _rel(p, b)
                    cur = d / dst
                    # only over Scaler's own file (or a missing one): a newer game file stays
                    if not cur.exists() or dst.lower() in listed or _contains(cur, MARK_HELIX + MARK_OPTI):
                        restore.append([_rel(p, d), dst])
                rm_dirs.add(subdirs["helixsr-scaler-backup"])
        if not kinds:
            continue
        lic = licenses_dir if (opti or d4r) else None
        restored_names = {r[1].lower() for r in restore}
        rm_files = {f for f in rm_files if f.lower() not in restored_names}
        shared = sorted(f for f in files if SHARED.match(f) and f not in rm_files and f.lower() not in restored_names
                        and not f.lower().endswith(".original.dll"))
        found.append({"kind": " + ".join(dict.fromkeys(kinds)), "dir": str(d), "rel_dir": _rel(d, root) or ".",
                      "files": sorted(rm_files), "dirs": sorted(rm_dirs), "restore": restore,
                      "shared": shared if (opti or d4r) else [], "helix": helix, "licenses": lic})
    return found


MARK_HELIX = (b"HelixSR", b"helixsr")


def _in_packages(p: Path, kind: str | None = None) -> bool:
    """Whether the file is byte-identical to a file in one of RAT's downloaded packages."""
    try:
        size = p.stat().st_size
    except OSError:
        return False
    for pkg in sources.list_packages():
        if kind and pkg.get("kind") != kind:
            continue
        base = Path(pkg.get("path", ""))
        if not base.is_dir():
            continue
        for c in base.rglob(p.name):
            try:
                if c.is_file() and c.stat().st_size == size and sha256_of(c) == sha256_of(p):
                    return True
            except OSError:
                continue
    return False


def _scandirs(d: Path):
    try:
        return [e for e in os.scandir(d) if e.is_dir(follow_symlinks=False)]
    except OSError:
        return []


def _read_lines(p: Path) -> list[str]:
    try:
        return [l.strip().replace("\\", "/") for l in p.read_text(encoding="utf-8", errors="replace").splitlines()]
    except OSError:
        return []


def package_file_index() -> dict[tuple[str, int], list[Path]]:
    """(name, size) -> files in RAT's package store, to recognise runtime DLLs an injector copied."""
    idx: dict[tuple[str, int], list[Path]] = {}
    for pkg in sources.list_packages():
        base = Path(pkg.get("path", ""))
        if not base.is_dir():
            continue
        for p in base.rglob("*.dll"):
            if SHARED.match(p.name):
                try:
                    idx.setdefault((p.name.lower(), p.stat().st_size), []).append(p)
                except OSError:
                    pass
    return idx


def remove(item: dict, log_line=lambda s: None) -> dict:
    """Removes one detected install (an entry of detect()). Returns {removed, restored, kept}."""
    d = Path(item["dir"])
    removed = restored = 0
    kept: list[str] = []
    restore_targets = {dst.lower() for _, dst in item.get("restore", [])}
    restore_failed = False
    for src, dst in item.get("restore", []):
        s, t = d / src, d / dst
        if not (is_within(s, d) and is_within(t, d)) or not s.is_file():
            continue
        try:
            t.parent.mkdir(parents=True, exist_ok=True)
            if t.is_symlink():
                t.unlink()
            os.replace(s, t)   # overwrites the injector's file in one step: the backup is never lost on failure
            restored += 1
            log_line(f"Restored the game's {dst}")
        except OSError as e:
            restore_failed = True
            kept.append(src)
            log.warning("restore %s failed: %s", t, e)
    for f in item.get("files", []):
        p = d / f
        if not is_within(p, d) or f.lower() in restore_targets:
            continue
        try:
            if p.is_file() or p.is_symlink():
                p.unlink()
                removed += 1
                log_line(f"Removed {f}")
        except OSError as e:
            log.warning("remove %s failed: %s", p, e)
    for sub in item.get("dirs", []):
        p = d / sub
        if restore_failed:   # a backup folder may still hold the game's only copy of a file
            log_line(f"Kept folder {sub}/ (a file could not be restored)")
            continue
        if is_within(p, d) and p.is_dir() and p != d:
            shutil.rmtree(p, ignore_errors=True)
            removed += 1
            log_line(f"Removed folder {sub}/")
    if item.get("shared"):
        idx = package_file_index()
        for f in item["shared"]:
            p = d / f
            try:
                size = p.stat().st_size
                cands = idx.get((f.lower(), size), [])
                if cands and any(sha256_of(c) == sha256_of(p) for c in cands):
                    p.unlink()
                    removed += 1
                    log_line(f"Removed {f} (identical to the injector's copy)")
                else:
                    kept.append(f)
            except OSError as e:
                log.warning("check %s failed: %s", p, e)
                kept.append(f)
    lic = item.get("licenses")
    if lic:
        ld = d / lic
        files = [p for p in ld.rglob("*") if p.is_file()] if ld.is_dir() else []
        if files and all(_in_packages(p) for p in files):
            shutil.rmtree(ld, ignore_errors=True)
            removed += 1
            log_line(f"Removed folder {lic}/ (OptiScaler's licence files)")
        elif ld.is_dir():
            kept.append(lic + "/")
    return {"removed": removed, "restored": restored, "kept": kept}
