"""Per-game installation, updates, activation and settings of upscaler injectors. The Manager is what main.py exposes to
the frontend; ReShade, RenoDX and profiles are services hanging off it (see their modules)."""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import threading
import time
from pathlib import Path

from . import addons as A
from . import external as ext_mod
from . import d4rfg, framegen, ini, inischema, launchopts, peinfo, sources, steam, ueconfig
from . import gpu as gpu_mod
from .jobs import STAGES_INSTALL, STAGES_DOWNLOAD, STAGES_REMOVE, Job, Jobs
from .records import REC_DIR, REC_FILE, cleanup, rec_file
from .util import Paths, UserError, ci_path, is_within, log, read_json, run_streaming, sha256_of, which, write_json

__all__ = ["Manager", "REC_DIR", "REC_FILE", "OFF", "rec_file"]

VERSION = "1.1"
PRODUCT = "RAT (Radical Advanced Tools)"
OFF = ".rat-off"
RUNTIME_FILES = {
    "d4r": ["OptiScaler.log"],
    "optiscaler": ["OptiScaler.log", "fakenvapi.log", "dlssg_to_fsr3.log", "remove_optiscaler.sh"],
}
# Preserve Swapchain Image Count: vkd3d-proton (DX12) creates max(3, the surface minimum) swapchain images and lets
# VKD3D_SWAPCHAIN_IMAGES override that. Pinning it to 3 keeps the count fixed whatever the Proton build defaults to.
SWAPCHAIN_ENV = ("VKD3D_SWAPCHAIN_IMAGES", "3")
# installed files hashed at install time, so uninstall leaves alone a copy a game update put there (config files are
# left out on purpose: RAT edits them after install)
HASHED_SUFFIXES = {".dll", ".exe", ".so", ".asi", ".addon64", ".addon32"}
DEFAULT_PREFS: dict[str, object] = {
    "auto_launch_options": True,
    "allow_unsupported_gpu": False,
    "include_prerelease": False,
    "show_hints": True,          # contextual hints under every setting
    "show_advanced": False,      # Show advanced (end of TUNE): every row for power users
    "boot_banner": True,         # RAT banner + status HUD when a game starts
    "reshade_version": "latest",
    "hdr_peak_nits": 0,          # 0 = detect
}


# The TUNE tab's essential keys per settings file, in display order (missing keys are skipped).
ESSENTIALS: dict[str, list[tuple[str, list[str]]]] = {
    "optiscaler": [
        ("Sharpening", ["CAS.Enabled", "CAS.Sharpness", "Sharpness.OverrideSharpness", "Sharpness.Sharpness"]),
        ("Render resolution", ["UpscaleRatio.UpscaleRatioOverrideEnabled", "UpscaleRatio.UpscaleRatioOverrideValue",
                               "OutputScaling.Enabled", "OutputScaling.Multiplier"]),
        ("Upscaler model", ["DLSS.RenderPresetOverride", "FSR.Fsr4ForceModel"]),
        ("Overlay menu", ["Menu.ShortcutKey", "Menu.Scale", "Menu.ShowFps", "Menu.DisableSplash"]),
    ],
    "d4r": [
        ("d4r", ["DLSS.Model", "DLSS.Preset", "DLSS.ShowWatermark", "Kernels.PreferAccuracy"]),
    ],
}


def game_key_for_folder(folder: str) -> str:
    return "custom:" + hashlib.sha1(os.path.realpath(folder).encode()).hexdigest()[:10]


def _rel_ok(rel: str) -> bool:
    return bool(rel) and not rel.startswith(("/", "..")) and "/../" not in rel


class Manager:
    def __init__(self):
        from .profiles import Profiles
        from .renodx import RenoDX
        from .reshade import ReShade
        self.jobs = Jobs()
        self._gpu: dict | None = None
        self._games: list[dict] | None = None
        self._games_t = 0.0
        self._scan_cache: dict[str, tuple[float, object]] = {}
        self._settings_lock = threading.RLock()
        self.reshade = ReShade(self)
        self.renodx = RenoDX(self)
        self.profiles = Profiles(self)
        self.sessions: dict[str, dict] = {}   # game key -> {started, status} while RAT saw it start

    def _cached(self, name: str, folder: str, fn, ttl: float = 90.0):
        """Folder scans are slow on big games: cached briefly, cleared whenever RAT changes files."""
        k = f"{name}:{folder}"
        hit = self._scan_cache.get(k)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
        v = fn()
        self._scan_cache[k] = (time.time(), v)
        return v

    def invalidate(self) -> None:
        self._scan_cache = {k: v for k, v in self._scan_cache.items() if k.startswith("profile:")}

    # --- settings -------------------------------------------------------------------------------------------------

    @property
    def settings_path(self) -> Path:
        return Paths.settings_dir / "settings.json"

    def settings(self) -> dict:
        with self._settings_lock:
            s = read_json(self.settings_path, {})
        s.setdefault("prefs", {})
        for k, v in DEFAULT_PREFS.items():
            s["prefs"].setdefault(k, v)
        s.setdefault("custom_sources", {})
        s.setdefault("custom_games", [])
        s.setdefault("overrides", {})
        s.setdefault("choices", {})
        return s

    def save(self, s: dict) -> None:
        with self._settings_lock:
            write_json(self.settings_path, s)

    def _update_settings(self, fn) -> dict:
        """Read-modify-write of settings.json under a lock (backend calls run in parallel threads)."""
        with self._settings_lock:
            s = self.settings()
            fn(s)
            self.save(s)
            return s

    def pref(self, key: str):
        return self.settings()["prefs"].get(key, DEFAULT_PREFS.get(key))

    def set_pref(self, key: str, value) -> dict:
        if key not in DEFAULT_PREFS:
            raise UserError(f"Unknown setting {key}")
        d = DEFAULT_PREFS[key]
        if isinstance(d, bool):
            value = bool(value)
        elif isinstance(d, int):
            try:
                value = max(0, min(10000, int(float(value))))
            except (TypeError, ValueError):
                raise UserError(f"{key}: a number is expected")
        else:
            value = str(value)[:40]
        return self._update_settings(lambda s: s["prefs"].__setitem__(key, value))["prefs"]

    def game_overrides(self, key: str) -> dict:
        return dict(self.settings()["overrides"].get(key, {}))

    # --- environment ----------------------------------------------------------------------------------------------

    def gpu(self) -> dict:
        if self._gpu is None:
            try:
                self._gpu = gpu_mod.detect()
            except Exception as e:   # never fatal
                log.warning("GPU detection failed: %s", e)
                self._gpu = {"vendor": "unknown", "name": "Unknown GPU", "family": "unknown", "gfx": None,
                             "steam_deck": False, "dlss": False, "xmx": False, "fsr4_native": False}
        return self._gpu

    def sources(self) -> dict[str, dict]:
        """Official add-ons plus imported forks: {source_id: {id, title, kind, repo?, ...}}"""
        out = {}
        for a in A.ADDONS.values():
            out[a["id"]] = {"id": a["id"], "title": a["title"], "subtitle": a["subtitle"], "kind": a["kind"],
                            "repo": a["repo"], "official": True, "family": a.get("family"),
                            "author": a.get("author_short")}
        for sid, c in self.settings()["custom_sources"].items():
            out[sid] = dict(c, official=False, family="fork")
        return out

    def source(self, sid: str) -> dict:
        s = self.sources().get(sid)
        if not s:
            raise UserError("Unknown add-on source")
        return s

    def packages_for(self, sid: str) -> list[dict]:
        pk = [p for p in sources.list_packages() if p.get("source_id") == sid]
        cap = A.ADDONS.get(sid, {}).get("max_major") if self.sources().get(sid, {}).get("official") else None
        if cap is not None:
            # versions past the pinned line (HelixSR 2.x) are wiped from the store: never offered, never built
            for p in [p for p in pk if sources.above_major(p.get("version"), cap)]:
                log.info("removing %s %s: past the supported v%d.x line", sid, p.get("version"), cap)
                try:
                    sources.remove_package(p["id"])
                except Exception as e:
                    log.warning("could not remove %s: %s", p.get("id"), e)
            pk = [p for p in pk if not sources.above_major(p.get("version"), cap)]
        return sorted(pk, key=lambda p: p.get("stored", 0), reverse=True)

    @staticmethod
    def addon_of(rec: dict | None, kind: str | None = None) -> dict:
        rec = rec or {}
        return A.addon_for(rec.get("addon") or rec.get("source_id"), rec.get("kind") or kind)

    def addon_for_source(self, sid: str) -> dict:
        src = self.sources().get(sid) or {}
        return A.addon_for(sid if src.get("official") else None, src.get("kind") or "optiscaler")

    # --- games ----------------------------------------------------------------------------------------------------

    def games(self, refresh: bool = False) -> list[dict]:
        if refresh or self._games is None or time.time() - self._games_t > 120:
            g = steam.games()
            for c in self.settings()["custom_games"]:
                if Path(c["folder"]).is_dir():
                    g.append({"appid": None, "name": c["name"], "folder": c["folder"], "custom": True,
                              "key": c["key"], "fully_installed": True})
            for x in g:
                x.setdefault("key", x["appid"])
            self._games = g
            self._games_t = time.time()
        out = []
        for g in self._games:
            rec = self.record_for(g)
            out.append(dict(g, installed=rec.get("title") if rec else None,
                            active=rec.get("active") if rec else None))
        return out

    def game(self, key: str) -> dict:
        for g in self._games or self.games():
            if g["key"] == key:
                return g
        for g in self.games(refresh=True):
            if g["key"] == key:
                return g
        raise UserError("Game not found (was it uninstalled?)")

    def game_by_appid(self, appid) -> dict | None:
        for g in self._games or self.games():
            if g.get("appid") and str(g["appid"]) == str(appid):
                return g
        return None

    def add_custom_game(self, folder: str) -> dict:
        p = Path(folder)
        if p.is_file():
            p = p.parent
        if not p.is_dir():
            raise UserError("Pick the game's folder (or its .exe)")
        key = game_key_for_folder(str(p))

        def fn(s):
            if not any(c["key"] == key for c in s["custom_games"]):
                s["custom_games"].append({"key": key, "name": p.name, "folder": str(p)})
        self._update_settings(fn)
        self._games = None
        return {"key": key}

    def remove_custom_game(self, key: str) -> None:
        self._update_settings(lambda s: s.__setitem__("custom_games",
                                                      [c for c in s["custom_games"] if c["key"] != key]))
        self._games = None

    def exe_dir(self, g: dict) -> tuple[Path | None, list[dict]]:
        o = self.settings()["overrides"].get(g["key"], {})
        ov = o.get("exe_dir")
        cands = self._cached("exe", g["folder"], lambda: steam.exe_candidates(Path(g["folder"])))
        known = o.get("record_dir")
        if known and rec_file(Path(known)).exists():
            return Path(known), cands
        if ov and Path(ov).is_dir():
            return Path(ov), cands
        rec = self._find_record_dir(Path(g["folder"]), cands)
        if rec:
            return rec, cands
        return (Path(cands[0]["dir"]) if cands else None), cands

    def exe_name(self, g: dict, exe: Path | None, cands: list[dict]) -> str | None:
        if not exe:
            return None
        for c in cands:
            if Path(c["dir"]) == exe:
                return c["exe"]
        try:
            exes = sorted((p for p in exe.iterdir() if p.suffix.lower() == ".exe"), key=lambda p: -p.stat().st_size)
            return exes[0].name if exes else None
        except OSError:
            return None

    def set_exe_dir(self, key: str, path: str) -> None:
        g = self.game(key)
        p = Path(path)
        if p.is_file():
            p = p.parent
        if not p.is_dir() or not is_within(p, Path(g["folder"])):
            raise UserError("Pick a folder inside the game's install folder")
        self._update_settings(lambda s: s["overrides"].setdefault(key, {}).__setitem__("exe_dir", str(p)))
        self.invalidate()

    def _find_record_dir(self, folder: Path, cands: list[dict]) -> Path | None:
        for c in cands:
            d = Path(c["dir"])
            if (d / REC_DIR).is_dir():
                return d
        return None

    def record_for(self, g: dict) -> dict | None:
        """The install record of a game, looked up cheaply (override, then previously known record dir)."""
        s = self.settings()
        cand = []
        ov = s["overrides"].get(g["key"], {}).get("exe_dir")
        if ov:
            cand.append(Path(ov))
        known = s["overrides"].get(g["key"], {}).get("record_dir")
        if known:
            cand.append(Path(known))
        for d in cand:
            r = read_json(rec_file(d), None)
            if r:
                return r
        return None

    def _remember_record_dir(self, key: str, d: Path | None) -> None:
        cur = self.settings()["overrides"].get(key, {}).get("record_dir")
        if (str(d) if d else None) == cur:
            return

        def fn(s):
            o = s["overrides"].setdefault(key, {})
            if d:
                o["record_dir"] = str(d)
            else:
                o.pop("record_dir", None)
        self._update_settings(fn)

    def game_profile(self, g: dict) -> dict:
        """Architecture, graphics API and engine of the game's main executable (cached for a while)."""
        exe, cands = self.exe_dir(g)
        name = self.exe_name(g, exe, cands)
        return self._cached("profile", f"{g['folder']}|{exe}|{name}",
                            lambda: dict(peinfo.game_profile(g["folder"], exe, name), exe=name,
                                         exe_dir=str(exe) if exe else None), ttl=3600)

    # --- status ---------------------------------------------------------------------------------------------------

    def choice(self, key: str) -> dict:
        return self.settings()["choices"].get(key, {})

    def set_choice(self, key: str, source_id: str | None = None, upscaler: str | None = None,
                   option: list | None = None) -> dict:
        out: dict = {}

        def fn(s):
            c = s["choices"].setdefault(key, {})
            if source_id is not None:
                if c.get("source_id") and c.get("source_id") != source_id:
                    c["upscaler"] = None
                    c["options"] = {}
                c["source_id"] = source_id
            if upscaler is not None:
                c["upscaler"] = upscaler
            if option is not None:
                c.setdefault("options", {})[option[0]] = option[1]
            out.update(c)
        self._update_settings(fn)
        return out

    def detail(self, key: str) -> dict:
        g = self.game(key)
        exe, cands = self.exe_dir(g)
        rec = read_json(rec_file(exe), None) if exe else None
        if rec:
            self._remember_record_dir(key, exe)
        scan = self._cached("scan", g["folder"], lambda: steam.scan_game(Path(g["folder"])))
        gpu = self.gpu()
        ch = self.choice(key)
        installed_sid = (rec or {}).get("source_id")
        sid = ch.get("source_id") or installed_sid or self._default_source(gpu)
        if sid not in self.sources():
            sid = installed_sid if installed_sid in self.sources() else "optiscaler"
        src = self.sources()[sid]
        kind = src["kind"] or "optiscaler"
        addon = self.addon_for_source(sid)
        status = self._status(rec, exe) if rec else {"state": "not_installed", "text": "Not installed"}
        live = self._editing_installed(rec, ch)
        cur = rec if live else None
        route = (cur or {}).get("route")
        upscaler = (cur or {}).get("upscaler") if cur else ch.get("upscaler")
        if cur and kind == "d4r" and exe and upscaler in ("K", "M", "L", "E", "F", "game", None):
            model = self._ini_get(cur, exe, A.D4R_INI, "DLSS", "Model")
            if model and cur.get("active", True):
                upscaler = model if model in ("K", "M", "L", "E", "F") else ("game" if model == "game" else upscaler)
        if upscaler not in {u["id"] for u in A.upscalers_for(addon, gpu)}:
            upscaler = A.default_upscaler(addon, gpu)
        up = A.upscaler(addon, upscaler) or {}
        opt_values = self._option_values(cur, exe, addon, ch)
        tool = steam.current_compat_tool(g["appid"]) if g.get("appid") else None
        warnings = []
        if scan["anticheat"]:
            warnings.append("Anti-cheat found (" + ", ".join(scan["anticheat"]) + "): do not use injectors in online "
                            "games." + (" HelixSR's FSR drop-in is the only safer route." if kind == "helixsr" else ""))
        ext = self.external(g)
        if ext:
            warnings.append("An injector RAT did not install is in this game (" +
                            ", ".join(f"{x['kind']} in {x['rel_dir']}" for x in ext) + "). Remove & wipe it below "
                            "before injecting, so the two do not fight.")
        if not g.get("fully_installed", True):
            warnings.append("Steam has not finished installing or updating this game.")
        if kind == "d4r" and rec and live and not (rec.get("fg_fallback") or {}).get("groups"):
            warnings.append("d4r's bundled OptiScaler ships no frame generation files. The FG tab can add them from the "
                            "matching official OptiScaler release (FG fallback).")
        if up.get("path") == "native" and kind == "optiscaler" and not gpu.get("dlss"):
            warnings.append("Native DLSS through OptiScaler needs an NVIDIA RTX GPU. On AMD pick d4r or HelixSR for a "
                            "native DLSS network, or a spoofed FSR / XeSS path.")
        running_pid = self.running_pid(g)
        prof = self.game_profile(g)
        return {
            "game": dict(g, exe_dir=str(exe) if exe else None, exe_candidates=cands),
            "scan": scan, "record": rec, "status": status, "source_id": sid, "installed_source_id": installed_sid,
            "editing_installed": live, "kind": kind, "addon_id": addon["id"], "route": route,
            "upscaler": upscaler, "path": up.get("path"), "paths": A.paths_of(addon, route, gpu),
            "upscalers": [u["id"] for u in A.upscalers_for(addon, gpu)],
            "options": opt_values,
            "gpu_support": A.gpu_support(addon, gpu), "proton": addon["proton"], "current_tool": tool,
            "warnings": warnings, "running": bool(running_pid),
            "launch": self.wanted_launch(rec, key) if rec else None,
            "in_game": self._in_game(addon, route),
            "external": [{k: x[k] for k in ("kind", "rel_dir", "files", "dirs", "shared")} |
                         {"restore": len(x["restore"])} for x in ext],
            "tunable": bool(rec) and live,
            "preserve_swapchain": SWAPCHAIN_ENV[0] in self.extra_env(key),
            "profile": prof,
            "session": self.launch_status(key) if running_pid else None,
            "has_clipboard": bool(self.settings().get("clipboard")),
        }

    @staticmethod
    def _editing_installed(rec: dict | None, ch: dict) -> bool:
        """True when the add-on picked in the UI is the one installed in the game (settings then go to its files)."""
        return bool(rec) and (not ch.get("source_id") or ch.get("source_id") == rec.get("source_id"))

    def _default_source(self, gpu: dict) -> str:
        fam = gpu.get("family")
        if fam in ("RDNA3", "RDNA4"):
            return "d4r"
        if fam in ("RDNA1", "RDNA2", "RDNA3.5"):
            return "helixsr"
        return "optiscaler"   # NVIDIA (native DLSS), Intel (XeSS) and everything else

    @staticmethod
    def _in_game(addon: dict, route: str | None) -> str:
        t = addon.get("in_game", "")
        if isinstance(t, dict):
            return t.get(route or "", "") or " / ".join(t.values())
        return t

    def _status(self, rec: dict, exe: Path) -> dict:
        missing = [r for r in rec.get("files", []) if not (exe / r).exists() and not (exe / (r + OFF)).exists()]
        version = rec.get("package_version", "?")
        title = rec.get("title", rec.get("kind"))
        if missing:
            return {"state": "broken", "text": f"{title} {version} · {len(missing)} files missing (game update or "
                                               "file verification?) · Repair", "missing": missing[:5]}
        if not rec.get("active", True):
            return {"state": "inactive", "text": f"{title} {version} · Installed · Inactive"}
        return {"state": "active", "text": f"{title} {version} · Installed · Active"}

    # --- running game check ---------------------------------------------------------------------------------------

    def running_pid(self, g: dict) -> int | None:
        # both spellings: a library reached through a symlink (SD card, /run/media) shows its unresolved path in the
        # process command line
        folders = {os.path.realpath(g["folder"]), os.path.normpath(g["folder"])}
        wins = {"z:" + f.replace("/", "\\").lower() for f in folders}
        try:
            pids = [p for p in os.listdir("/proc") if p.isdigit()]
        except OSError:
            return None
        best = None
        for pid in pids:
            try:
                with open(f"/proc/{pid}/cmdline", "rb") as f:
                    cmd = f.read(4096).decode("utf-8", "replace")
            except OSError:
                continue
            low = cmd.lower()
            if "helix_bridge" in low or "py_modules/rat" in low:
                continue
            args = cmd.split("\0")
            if any(a == f or a.startswith(f + "/") for a in args for f in folders) or any(
                    a.lower() == w or a.lower().startswith(w + "\\") for a in args for w in wins):
                # the Windows game process itself (an .exe) beats Proton's wrappers
                if any(a.lower().endswith(".exe") for a in args):
                    return int(pid)
                best = best or int(pid)
        return best

    def is_running(self, g: dict) -> bool:
        return self.running_pid(g) is not None

    # --- configuration files --------------------------------------------------------------------------------------

    def config_files(self, rec: dict, exe: Path) -> list[Path]:
        kind = rec.get("kind")
        out: list[Path] = []
        if kind == "d4r":
            out = [exe / "OptiScaler.ini", exe / "d4r" / "d4r.ini"]
        elif kind == "optiscaler":
            out = [exe / "OptiScaler.ini", exe / "fakenvapi.ini", exe / "OptiScaler" / "fakenvapi.ini"]
        elif kind == "helixsr":
            if rec.get("route") == "scaler":
                out = [exe / "OptiScaler.ini", exe / "HelixSR" / "helixsr.ini"]
            else:
                out = [Path(d) / "helixsr.ini" for d in rec.get("helix_dirs", [])]
        return out

    def _resolve_file(self, rec: dict, exe: Path, logical: str) -> list[Path]:
        if logical == A.OPTI:
            return [exe / "OptiScaler.ini"]
        if logical == A.D4R_INI:
            return [exe / "d4r" / "d4r.ini"]
        if logical == A.HELIX_INI:
            return [Path(d) / "helixsr.ini" for d in rec.get("helix_dirs", [])]
        return [exe / logical]

    def opti_doc(self, rec: dict | None, exe: Path | None) -> ini.IniDoc | None:
        if not rec or not exe or (rec.get("kind") == "helixsr" and rec.get("route") != "scaler"):
            return None
        p = exe / "OptiScaler.ini"
        return ini.read(p) if p.exists() else None

    def _ini_get(self, rec: dict, exe: Path, logical: str, section: str, key: str) -> str | None:
        for p in self._resolve_file(rec, exe, logical):
            if p.exists():
                return ini.read(p).get(section, key)
        return None

    def _option_values(self, rec: dict | None, exe: Path | None, addon: dict, ch: dict) -> dict:
        saved = {**((rec or {}).get("options") or {}), **((rec or {}).get("pending") or {})} if rec else \
            (ch.get("options") or {})
        out = {}
        for o in addon["options"]:
            v = saved.get(o["id"], o.get("default"))
            if rec and exe and not o.get("meta") and o.get("file"):
                raw = self._ini_get(rec, exe, o["file"], o["section"], o["key"])
                if raw is not None:
                    if o["type"] == "toggle":
                        r = raw.lower()
                        on, off = str(o["on"]).lower(), str(o["off"]).lower()
                        if r == on or (r in ("true", "on", "1") and off != r):
                            v = True
                        elif r == off or r in ("false", "off", "0"):
                            v = False
                        else:   # auto / empty: what the add-on does by default
                            v = bool(o.get("default"))
                    elif o["type"] == "slider":
                        try:
                            v = float(raw)
                        except ValueError:
                            v = o.get("default")
                    else:
                        v = raw if raw != "" else o.get("default")
            out[o["id"]] = v
        return out

    @staticmethod
    def resolve_value(doc: ini.IniDoc, section: str, key: str, value, kind: str = "optiscaler") -> str:
        """value may be a list of candidates: the first one the INI's comment documents for this key wins."""
        if not isinstance(value, (list, tuple)):
            return str(value)
        entry = next((e for e in doc.entries() if e["section"].lower() == section.lower()
                      and e["key"].lower() == key.lower()), None)
        if entry:
            f = inischema.field("optiscaler" if kind != "d4r" else "optiscaler", entry)
            allowed = {c[0].lower() for c in f.get("choices", [])}
            for v in value:
                if v.lower() in allowed:
                    return v
        return str(value[0])

    def _apply_settings(self, rec: dict, exe: Path, job: Job | None = None) -> None:
        """Writes the record's upscaler, options and RAT's integration keys into the add-on's INI files."""
        kind = rec["kind"]
        addon = self.addon_of(rec)
        fam = self.gpu().get("family", "unknown")
        edits: dict[str, list[tuple[str, str, object]]] = {}
        if rec.get("active", True) and rec.get("upscaler") != "none":
            for f, sec, key, val in A.upscaler_edits(kind, rec.get("upscaler") or "", fam, rec.get("route")):
                edits.setdefault(f, []).append((sec, key, val))
        opts = rec.get("options", {})
        for o in addon["options"]:
            if o.get("meta") or not o.get("file") or o["id"] not in opts:
                continue
            if not A.option_visible(o, rec.get("route"), fam) and o.get("route"):
                continue
            v = opts[o["id"]]
            if o["type"] == "toggle":
                val = o["on"] if v else o["off"]
            elif o["type"] == "slider":
                val = f"{float(v):.2f}".rstrip("0").rstrip(".") if v is not None else str(o.get("default"))
                if "." not in val:
                    val += ".0"
            else:
                val = str(v)
            edits.setdefault(o["file"], []).append((o["section"], o["key"], val))
        if kind in ("optiscaler", "d4r") or (kind == "helixsr" and rec.get("route") == "scaler"):
            rsr = self.reshade.record(exe) or {}
            chain = rsr.get("enabled", True) and rsr.get("proxy", "").lower() in ("reshade64.dll", "reshade32.dll")
            edits.setdefault(A.OPTI, []).append(("Plugins", "LoadReshade", "true" if chain else "auto"))
            # RenoDX (a ReShade add-on, like Luma) needs the D3D12 device created before ReShade is loaded
            hdr = chain and bool(self.renodx.record(exe))
            edits[A.OPTI].append(("Hotfix", "CreateD3D12DeviceForLuma", "true" if hdr else "auto"))
            if chain:
                edits[A.OPTI] += self._overlay_key_edits(exe)
        for logical, lst in edits.items():
            for p in self._resolve_file(rec, exe, logical):
                if not p.exists() and logical != A.HELIX_INI:
                    continue
                doc = ini.read(p) if p.exists() else ini.IniDoc("")
                have = doc.to_dict()
                changed = False
                for sec, key, val in lst:
                    forced = (sec.lower(), key.lower()) == ("plugins", "loadreshade") and val == "true"
                    if (sec.lower(), key.lower()) in A.OPTIONAL_KEYS and key.lower() not in have.get(sec.lower(), {}) \
                            and not forced:   # LoadReshade is written even when an add-on's .ini omits it (d4r)
                        continue
                    changed |= doc.set(sec, key, self.resolve_value(doc, sec, key, val, kind))
                if changed:
                    ini.write(p, doc)
                    if job:
                        job.log(f"Updated {p.name}")

    # --- launch options -------------------------------------------------------------------------------------------

    def extra_env(self, key: str | None) -> dict[str, str]:
        """Launch variables RAT manages for a game apart from the add-on (Preserve Swapchain Image Count)."""
        if not key:
            return {}
        from .profiles import safe_env
        return safe_env(self.settings()["overrides"].get(key, {}).get("extra_env"))

    def set_preserve_swapchain(self, key: str, on: bool) -> dict:
        """Per-game Preserve Swapchain Image Count toggle (written to the launch options, applies at next start)."""
        self.game(key)

        def fn(s):
            ex = s["overrides"].setdefault(key, {}).setdefault("extra_env", {})
            if on:
                ex[SWAPCHAIN_ENV[0]] = SWAPCHAIN_ENV[1]
            else:
                ex.pop(SWAPCHAIN_ENV[0], None)
        self._update_settings(fn)
        return self.detail(key)

    def wanted_launch(self, rec: dict | None, key: str | None = None, without: str | None = None) -> dict:
        """Every launch variable and DLL override RAT wants for a game: the injector's, ReShade's, HDR's and
        the game's extra variables. without ("injector", "reshade", "renodx") leaves one part out: what the launch
        options become once it is uninstalled."""
        env: dict[str, str] = {}
        dlls: dict[str, str] = {}
        if without == "injector":
            rec = None
        if rec and rec.get("active", True):
            kind = rec["kind"]
            opts = rec.get("options", {})
            if kind == "d4r":
                env.update(A.ADDONS["d4r"]["launch"]["env"])
                if opts.get("rr_launch"):
                    dlls.update({"icuuc": "n,b", "icuin": "n,b", "icudt": "n,b", "d3d12": "n,b", "d3d12core": "n,b"})
            elif kind == "optiscaler":
                proxy = rec.get("proxy") or "dxgi.dll"
                dlls[Path(proxy).stem.lower()] = "n,b"
                if opts.get("force_nvapi"):
                    env["PROTON_FORCE_NVAPI"] = "1"
            elif kind == "helixsr":
                proxy = rec.get("proxy")
                if rec.get("route") == "scaler" and proxy and proxy.lower() != "dxgi.dll":
                    dlls[Path(proxy).stem.lower()] = "n,b"
        if key:
            parts = []
            hdr = self.renodx.launch(key)
            # ReShade stays while RenoDX uses it, so removing ReShade alone changes nothing then
            if without != "reshade" or hdr:
                parts.append(self.reshade.launch(key))
            if without != "renodx":
                parts.append(hdr)
            for part in parts:
                env.update(part.get("env", {}))
                dlls.update(part.get("dlls", {}))
        env.update(self.extra_env(key))
        return {"env": env, "dlls": dlls, "text": launchopts.describe(env, dlls) if (env or dlls) else ""}

    def launch_plan(self, key: str, current: str | None, strip_foreign: bool = False) -> dict:
        """What the game's launch options should become. current is the live value from the Steam client.
        strip_foreign also removes overrides another injector's setup left (WINEDLLOVERRIDES dxgi=n,b, GE-Proton's
        PROTON_USE_OPTISCALER...), after Remove & wipe."""
        g = self.game(key)
        if current is None and g.get("appid"):
            current = steam.launch_options_on_disk(g["appid"]) or ""
        current = current or ""
        base, notes = launchopts.clean(current)   # duplicates and repeated wrappers out before RAT merges its part
        rec = self.record_for(g)
        want = self.wanted_launch(rec, key)
        new = self._merge_launch(key, base, want, installed=bool(rec), strip_foreign=strip_foreign)
        new, more = launchopts.clean(new)   # and nothing RAT added may duplicate what was there
        return {"current": current, "new": new, "changed": new.strip() != current.strip(), "ours": want["text"],
                "can_apply": bool(g.get("appid")), "cleaned": notes + more}

    def _launch_state(self, key: str) -> tuple[dict, dict]:
        """(what RAT owns in the launch options, the user's own values of variables RAT also uses)."""
        ov = self.settings()["overrides"].get(key, {})
        owned = ov.get("launch_owned") or {"env": [], "dlls": []}
        user = ov.get("launch_user") or {"env": {}, "dlls": {}}
        return owned, {"env": dict(user.get("env") or {}), "dlls": dict(user.get("dlls") or {})}

    def _merge_launch(self, key: str, base: str, want: dict, installed: bool, strip_foreign: bool = False) -> str:
        """RAT's wanted variables merged into base. Variables the user had before RAT used them are never removed:
        once RAT stops needing one, it gets the user's own value back (native HDR games keep PROTON_ENABLE_HDR /
        DXVK_HDR after ReShade or RenoDX goes)."""
        owned, user = self._launch_state(key)
        present = launchopts.parse(base)
        have_env = {k for k, _ in present["env"]}
        have_dlls = {n.lower() for k, v in present["env"] if k == "WINEDLLOVERRIDES" for n, _ in launchopts.parse_dll_overrides(v)}
        env = {k: v for k, v in user["env"].items() if k not in want["env"] and k in have_env}
        env.update(want["env"])
        dlls = {k: v for k, v in user["dlls"].items() if k.lower() not in {d.lower() for d in want["dlls"]} and k.lower() in have_dlls}
        dlls.update(want["dlls"])
        own_env = set(owned["env"]) - set(user["env"])
        own_dlls = {d.lower() for d in owned["dlls"]} - {d.lower() for d in user["dlls"]}
        if strip_foreign:
            return launchopts.merge(base, env, dict(want["dlls"]), remove_conflicts=True,
                                    owned_env=(set(launchopts.INJECTOR_ENV) | own_env) - set(user["env"]),
                                    owned_dlls=None)   # Remove & wipe: every injector DLL override goes
        return launchopts.merge(base, env, dlls, remove_conflicts=installed, owned_env=own_env, owned_dlls=own_dlls)

    def launch_applied(self, key: str, before: str | None = None) -> None:
        """The frontend applied the plan: remember what RAT owns now, so a later change removes only that. before
        (the launch options as they were) tells which of RAT's variables the user already had: those stay theirs."""
        g = self.game(key)
        want = self.wanted_launch(self.record_for(g), key)
        owned, user = self._launch_state(key)
        if before:
            p = launchopts.parse(before)
            prev_env = set(owned["env"])
            prev_dlls = {d.lower() for d in owned["dlls"]}
            for k, v in p["env"]:
                if k == "WINEDLLOVERRIDES":
                    for n, mode in launchopts.parse_dll_overrides(v):
                        if n.lower() in {d.lower() for d in want["dlls"]} and n.lower() not in prev_dlls:
                            user["dlls"].setdefault(n.lower(), mode)
                elif k in want["env"] and k not in prev_env:
                    user["env"].setdefault(k, launchopts.unquote(v))

        def fn(s):
            o = s["overrides"].setdefault(key, {})
            o["launch_owned"] = {"env": sorted(k for k in want["env"] if k not in user["env"]),
                                 "dlls": sorted(d for d in want["dlls"] if d.lower() not in user["dlls"])}
            if user["env"] or user["dlls"]:
                o["launch_user"] = user
        self._update_settings(fn)

    def launch_removal(self, key: str, part: str, current: str | None) -> dict:
        """What uninstalling a part (injector, reshade, renodx) does to the game's launch options: the arguments it
        removes, for the uninstall dialog."""
        if part not in ("injector", "reshade", "renodx"):
            raise UserError("Unknown part")
        g = self.game(key)
        if current is None and g.get("appid"):
            current = steam.launch_options_on_disk(g["appid"]) or ""
        base, _ = launchopts.clean(current or "")
        rec = self.record_for(g)
        after = self.wanted_launch(rec, key, without=part)
        new, _ = launchopts.clean(self._merge_launch(key, base, after, installed=bool(rec) and part != "injector"))
        p_old, p_new = launchopts.parse(base), launchopts.parse(new)
        old_env = {k: v for k, v in p_old["env"] if k != "WINEDLLOVERRIDES"}
        new_env = {k: v for k, v in p_new["env"] if k != "WINEDLLOVERRIDES"}
        dl = lambda p: {n.lower(): m for k, v in p["env"] if k == "WINEDLLOVERRIDES" for n, m in launchopts.parse_dll_overrides(v)}
        old_d, new_d = dl(p_old), dl(p_new)
        removed = [f"{k}={launchopts.unquote(v)}" for k, v in old_env.items() if new_env.get(k) != v]
        removed += [f"{n}={m}" for n, m in old_d.items() if new_d.get(n) != m]
        return {"current": base, "after": new, "removed": removed, "changed": new.strip() != base.strip()}

    def launch_keep(self, key: str, part: str, current: str | None) -> dict:
        """Keep the launch arguments a part's uninstall would remove: they become the user's own (never removed by
        RAT), for this game only."""
        r = self.launch_removal(key, part, current)
        p = launchopts.parse(r["current"])
        owned, user = self._launch_state(key)
        gone = set(r["removed"])
        for k, v in p["env"]:
            if k == "WINEDLLOVERRIDES":
                for n, m in launchopts.parse_dll_overrides(v):
                    if f"{n.lower()}={m}" in gone:
                        user["dlls"][n.lower()] = m
            elif f"{k}={launchopts.unquote(v)}" in gone:
                user["env"][k] = launchopts.unquote(v)

        def fn(s):
            o = s["overrides"].setdefault(key, {})
            lo = o.get("launch_owned") or {"env": [], "dlls": []}
            o["launch_owned"] = {"env": [k for k in lo.get("env", []) if k not in user["env"]],
                                 "dlls": [d for d in lo.get("dlls", []) if d.lower() not in user["dlls"]]}
            o["launch_user"] = user
        self._update_settings(fn)
        return {"kept": r["removed"]}

    # --- downloads ------------------------------------------------------------------------------------------------

    def _release(self, sid: str, force: bool = False) -> dict:
        src = self.source(sid)
        a = A.ADDONS.get(sid) if src.get("official") else None
        return sources.latest(src["repo"], (a or {}).get("asset") or src.get("pattern"),
                              (a or {}).get("asset_fallback"), (a or {}).get("asset_exclude"),
                              prerelease=self.pref("include_prerelease"), force=force,
                              max_major=(a or {}).get("max_major"))

    def check_updates(self, sid: str, force: bool = False) -> dict:
        src = self.source(sid)
        have = self.packages_for(sid)
        out = {"source_id": sid, "installed": [p.get("version") for p in have], "latest": None, "update": False,
               "can_check": bool(src.get("repo"))}
        if not src.get("repo"):
            return out
        rel = self._release(sid, force)
        out["latest"] = {"tag": rel["tag"], "name": rel["name"], "published": rel["published"], "page": rel["page"],
                         "asset": rel["asset"]["name"], "size": rel["asset"].get("size"), "notes": rel["notes"][:600]}
        out["update"] = rel["tag"] not in out["installed"]
        return out

    def _download_source(self, sid: str, job: Job, lo: float = 0.0, hi: float = 0.6) -> dict:
        src = self.source(sid)
        if not src.get("repo") and not src.get("url"):
            have = self.packages_for(sid)
            if have:
                return have[0]
            raise UserError("This add-on was imported from a file: import the new version the same way.")
        job.stage("fetch", f"Checking {src['title']} releases...", lo)
        if src.get("repo"):
            rel = self._release(sid, force=True)
            for p in self.packages_for(sid):
                if p.get("version") == rel["tag"] and Path(p["path"]).is_dir():
                    job.update(f"{src['title']} {rel['tag']} is already downloaded", hi)
                    return p
            url, name, version, size = rel["asset"]["url"], rel["asset"]["name"], rel["tag"], rel["asset"].get("size")
            sha = sources.expected_sha256(rel)
            if not sha:
                job.log(f"{name}: the release publishes no usable SHA-256, so the download is not verified")
        else:
            url, name, version, size, sha = src["url"], src.get("name") or "package.zip", src.get("version") or "link", None, None
        dest = Paths.downloads() / name
        span = hi - lo
        job.stage("download", f"Downloading {name}...", lo)
        sources.download(url, dest, progress=lambda f, t: job.sub(f, lo, lo + span * 0.7, f"Downloading {name}: {t}"),
                         should_cancel=job.cancelled, size_hint=size)
        if sha:
            job.stage("verify", "Verifying checksum...", lo + span * 0.72)
            if sha256_of(dest) != sha:
                dest.unlink(missing_ok=True)
                raise UserError("Checksum mismatch: the download is corrupt or was replaced. Nothing was installed.")
        job.stage("extract", f"Extracting {name}...", lo + span * 0.75)
        meta = sources.store_package(dest, {"source_id": sid, "version": version, "asset": name, "url": url,
                                            "sha256": sha, "kind": src.get("kind") if not src.get("official") else
                                            A.ADDONS[sid]["kind"], "title": src["title"]}, job.cancelled)
        dest.unlink(missing_ok=True)
        if meta["kind"] != src["kind"]:
            log.warning("package kind %s differs from source kind %s", meta["kind"], src["kind"])
        job.update(f"{src['title']} {version} downloaded", hi)
        return meta

    def start_download(self, sid: str) -> dict:
        return self.jobs.start(f"Download {self.source(sid)['title']}",
                               lambda job: self._download_source(sid, job, 0.0, 1.0), stages=STAGES_DOWNLOAD)

    def import_link(self, url: str) -> dict:
        def run(job: Job):
            job.stage("fetch", "Resolving link...")
            r = sources.resolve_link(url, self.pref("include_prerelease"))
            if r["kind"] == "github":
                rel = r["release"]
                sid = "custom-" + hashlib.sha1(r["repo"].lower().encode()).hexdigest()[:8]
                entry = {"id": sid, "title": r["repo"].split("/")[1], "subtitle": f"Fork: {r['repo']}",
                         "repo": r["repo"], "pattern": re.escape(rel["asset"]["name"]).replace(re.escape(rel["tag"]),
                                                                                                ".+") if rel["tag"] in rel["asset"]["name"] else None,
                         "origin": url, "kind": None, "author": r["repo"].split("/")[0]}
            else:
                sid = "custom-" + hashlib.sha1(r["url"].encode()).hexdigest()[:8]
                entry = {"id": sid, "title": Path(r["name"]).stem[:40], "subtitle": "Direct link", "url": r["url"],
                         "name": r["name"], "version": r.get("tag") or Path(r["name"]).stem, "origin": url,
                         "kind": None, "repo": None, "author": (r.get("repo") or "").split("/")[0] or None}
            previous = self.settings()["custom_sources"].get(sid)
            self._update_settings(lambda s: s["custom_sources"].__setitem__(sid, entry))
            try:
                meta = self._download_source(sid, job, 0.0, 1.0)
            except Exception:
                # a failed re-import puts the existing source back instead of dropping it (its packages stay usable)
                if previous:
                    self._update_settings(lambda s: s["custom_sources"].__setitem__(sid, previous))
                else:
                    self._update_settings(lambda s: s["custom_sources"].pop(sid, None))
                raise

            def fin(s):
                s["custom_sources"][sid]["kind"] = meta["kind"]
                s["custom_sources"][sid]["subtitle"] = f"{A.KIND_TITLES[meta['kind']]} · {entry['subtitle']}"
            self._update_settings(fin)
            job.update(f"Added {entry['title']} ({A.KIND_TITLES[meta['kind']]})")
            return {"source_id": sid}
        return self.jobs.start("Import from link", run, stages=STAGES_DOWNLOAD)

    def import_file(self, path: str) -> dict:
        p = Path(path)

        def run(job: Job):
            if not p.is_file():
                raise UserError("File not found")
            if not p.name.lower().endswith(sources.ARCHIVE_EXT):
                raise UserError("Pick a .zip, .7z or .tar archive")
            sid = "custom-" + hashlib.sha1(str(p.resolve()).encode()).hexdigest()[:8]
            job.stage("extract", f"Extracting {p.name}...")
            meta = sources.store_package(p, {"source_id": sid, "version": p.stem, "asset": p.name,
                                             "url": None, "sha256": None, "title": p.stem}, job.cancelled)
            self._update_settings(lambda s: s["custom_sources"].__setitem__(sid, {
                "id": sid, "title": p.stem[:40], "kind": meta["kind"], "origin": str(p),
                "subtitle": f"{A.KIND_TITLES[meta['kind']]} · local file", "repo": None}))
            job.update(f"Imported {p.name} ({A.KIND_TITLES[meta['kind']]})")
            return {"source_id": sid}
        return self.jobs.start(f"Import {p.name}", run, stages=["extract"])

    def remove_source(self, sid: str) -> None:
        for p in self.packages_for(sid):
            sources.remove_package(p["id"])
        self._update_settings(lambda s: s["custom_sources"].pop(sid, None))

    # --- HelixSR preparation ---------------------------------------------------------------------------------------

    def _python(self) -> str:
        for c in ("/usr/bin/python3", which("python3"),
                  str(Paths.user_home / ".local/share/HelixSR/python/bin/python3")):
            if c and Path(c).exists():
                return c
        raise UserError("python3 was not found on this system")

    def _bridge(self, args: list[str], job: Job | None = None, timeout: float = 600) -> dict:
        bridge = Path(__file__).with_name("helix_bridge.py")
        res: dict = {}

        def line(l: str):
            nonlocal res
            if l.startswith("@@RAT@@ "):
                res = json.loads(l[len("@@RAT@@ "):])
            elif job:
                job.log(l.replace("[helixsr-setup] ", ""))
        code, tail = run_streaming([self._python(), "-I", str(bridge)] + args, on_line=line, timeout=timeout,
                                   should_cancel=job.cancelled if job else None)
        if not res:
            raise UserError("HelixSR's installer failed: " + (tail[-1] if tail else f"exit {code}"))
        return res

    def helix_ready(self, pkg: dict) -> bool:
        if pkg.get("prepared"):
            return True
        try:
            return bool(self._bridge(["ready", pkg["path"]], timeout=60).get("ready"))
        except Exception:
            return False

    def _prepare_helix(self, pkg: dict, job: Job) -> None:
        if self.helix_ready(pkg):
            return
        script = Path(pkg["path"]) / "helixsr-setup.sh"
        if not script.exists():
            raise UserError("helixsr-setup.sh is missing from this HelixSR package")
        bash = which("bash") or "/bin/bash"
        job.stage("compile", "Building HelixSR's network on this device (a few minutes, once per version)...", 0.62)
        steps = re.compile(r"(downloading|building|weights|tables|shader|python|network|compil)", re.I)

        def on_line(l: str):
            clean = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", l).replace("[helixsr-setup] ", "").strip()
            if not clean:
                return
            job.log(clean)
            if steps.search(clean):
                job.message = clean[:200]
            m = re.search(r"(\d{1,3})\s*%", clean)
            if m:
                job.sub(int(m.group(1)) / 100.0, 0.62, 0.85)
        code, tail = run_streaming([bash, str(script), "--yes"], cwd=pkg["path"], on_line=on_line, timeout=3600,
                                   should_cancel=job.cancelled)
        if not self.helix_ready(pkg):
            clean = [re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", t).replace("[helixsr-setup] ", "").strip() for t in tail]
            clean = [t for t in clean if t]
            why = [t for t in clean if re.search(r"(?i)error|fail|could not|cannot|not found|denied|timed? ?out|"
                                                r"no space|http|download", t)]
            detail = " / ".join((why or clean)[-2:]) or f"exit code {code}"
            if any("helixsr-fg" in t or "was not listed" in t for t in clean):
                detail += (". This HelixSR build adds a frame-generation step (NVIDIA driver download, per-game setup) "
                           "that RAT can't run unattended: use a HelixSR 1.x package")
            raise UserError(f"HelixSR {pkg.get('version')}'s one-time network build failed: {detail}. Its log is in the "
                            "Decky plugin log. Check the network and free space, then press Inject again.")
        pkg["prepared"] = True
        sources.save_package_meta(pkg)
        job.update("HelixSR network ready", 0.85)

    # --- install / uninstall --------------------------------------------------------------------------------------

    def _check_installable(self, g: dict, addon: dict, force: bool) -> None:
        if self.is_running(g):
            raise UserError("Close the game first.")
        sup = A.gpu_support(addon, self.gpu())
        if sup["level"] == "unsupported" and not self.pref("allow_unsupported_gpu"):
            raise UserError(f"{addon['title']} does not support this GPU ({self.gpu().get('name')}). "
                            f"{addon['gpu']['note']} (Settings → Allow unsupported GPUs to try anyway.)")

    def start_install(self, key: str, force: bool = False) -> dict:
        g = self.game(key)
        ch = self.choice(key)
        # same order as detail(): what the panel shows is what gets installed
        sid = ch.get("source_id") or (self.record_for(g) or {}).get("source_id") or self._default_source(self.gpu())
        if sid not in self.sources():
            sid = "optiscaler"
        src = self.source(sid)
        self._check_installable(g, self.addon_for_source(sid), force)
        self.invalidate()
        return self.jobs.start(f"Install {src['title']}", lambda job: self._install(g, sid, job, force), game=key,
                               stages=STAGES_INSTALL)

    def start_update(self, key: str) -> dict:
        g = self.game(key)
        rec = self.record_for(g) or {}
        sid = rec.get("source_id")
        if not sid:
            raise UserError("Nothing installed to update")
        src = self.source(sid)
        if self.is_running(g):
            raise UserError("Close the game first.")
        self.invalidate()
        return self.jobs.start(f"Update {src['title']}", lambda job: self._install(g, sid, job, True, update=True),
                               game=key, stages=STAGES_INSTALL)

    def start_uninstall(self, key: str) -> dict:
        g = self.game(key)
        if self.is_running(g):
            raise UserError("Close the game first.")
        self.invalidate()
        return self.jobs.start("Uninstall", lambda job: self._uninstall(g, job), game=key, stages=STAGES_REMOVE)

    @contextlib.contextmanager
    def _reshade_detached(self, g: dict, job: Job | None = None):
        """ReShade's proxy DLL steps aside while the injector's files change, then takes the right name again
        (dxgi.dll on its own, or ReShade64.dll loaded by OptiScaler when the injector holds dxgi.dll)."""
        info = self.reshade.detach(g)
        try:
            yield
        finally:
            if info:
                try:
                    self.reshade.relink(g, job, info.get("_dir"))
                except Exception as e:   # never hides the injector's own error; ReShade is re-linked next time
                    log.warning("ReShade relink failed: %s", e)
                    if job:
                        job.log(f"ReShade could not be re-linked ({e}): reinstall it on the ReShade section")

    def _install(self, g: dict, sid: str, job: Job, force: bool, update: bool = False) -> dict:
        src = self.source(sid)
        pkg = None
        if update or not self.packages_for(sid):
            pkg = self._download_source(sid, job)
        pkg = pkg or self.packages_for(sid)[0]
        kind = pkg["kind"]
        addon = A.addon_for(sid if src.get("official") else None, kind)
        exe, _ = self.exe_dir(g)
        if not exe:
            raise UserError("No game .exe found. Pick the executable folder under Settings → Executable folder.")
        old = read_json(rec_file(exe), None)
        same = bool(old) and old.get("source_id") == sid and old.get("kind") == kind
        ch = self.choice(g["key"])
        options: dict = {}
        if same:
            options.update(old.get("options", {}))
            # what the INI files say now (the user may have changed them in-game or in the Tune tab)
            for oid, v in self._option_values(old, exe, addon, {}).items():
                o = next((x for x in addon["options"] if x["id"] == oid), None)
                if o and not o.get("meta") and o.get("file"):
                    options[oid] = v
            options.update(old.get("pending", {}))
        if not update:
            options.update(ch.get("options", {}))
        for o in addon["options"]:
            options.setdefault(o["id"], o.get("default"))
        ids = {u["id"] for u in A.upscalers_for(addon, self.gpu())}
        if update:
            upscaler = old.get("upscaler") if same else None
        else:
            upscaler = ch.get("upscaler") or (old.get("upscaler") if same else None)
        if not upscaler or upscaler not in ids:
            upscaler = A.default_upscaler(addon, self.gpu())
        was_active = old.get("active", True) if same else True
        if kind == "helixsr":
            # the network build can fail (no Proton, no network): do it before touching the game. When it fails, a
            # HelixSR version whose network was built before keeps the game working.
            try:
                self._prepare_helix(pkg, job)
            except UserError as e:
                ready = [p for p in self.packages_for(sid) if p["id"] != pkg["id"] and Path(p.get("path", "")).is_dir()
                         and self.helix_ready(p)]
                if not ready:
                    raise
                job.log(str(e))
                pkg = ready[0]
                job.log(f"Using HelixSR {pkg.get('version')} instead: its network is already built on this device")
        # the user's INI files, carried into the new version when the add-on stays the same kind
        saved_ini: dict[str, str] = {}
        if old and old.get("kind") == kind:
            for p in self.config_files(old, exe):
                if p.exists():
                    saved_ini[p.relative_to(exe).as_posix() if is_within(p, exe) else str(p)] = \
                        p.read_text(encoding="utf-8", errors="replace")
        keep_fg = (old or {}).get("fg_fallback") if kind == "d4r" and same else None
        return self._install_files_phase(g, sid, src, pkg, kind, addon, exe, old, options, upscaler, was_active,
                                         saved_ini, keep_fg, job)

    def _install_files_phase(self, g, sid, src, pkg, kind, addon, exe, old, options, upscaler, was_active,
                             saved_ini, keep_fg, job) -> dict:
        with self._reshade_detached(g, job):
            job.stage("install", f"Installing {src['title']} into {g['name']}...", 0.86)
            if old:
                job.update(f"Removing {old.get('title')} {old.get('package_version', '')} first...")
                nxt = (options.get("route") or "auto") if kind == "helixsr" else None
                self._uninstall_files(g, exe, old, job, keep_launch=True, next_route=nxt)
            if kind == "helixsr":
                rec = self._install_helix(g, exe, pkg, options, job)
                exe = Path(rec["exe_dir"])
            else:
                rec = self._install_files(g, exe, pkg, kind, options, job)
            rec.update(source_id=sid, addon=addon["id"], title=src["title"], package_id=pkg["id"],
                       package_version=pkg.get("version"), upscaler=upscaler, options=options, active=True,
                       appid=g.get("appid"), game_folder=g["folder"], author=addon.get("author_short") or src.get("author"),
                       installed=(old or {}).get("installed") or time.time(), updated=time.time())
            job.stage("configure", "Applying your settings...", 0.95)
            # carry the user's settings over, then apply RAT's choices on top
            for rel, text in saved_ini.items():
                p = Path(rel) if os.path.isabs(rel) else exe / rel
                if p.exists():
                    locked = {k for f, keys in addon.get("locked", {}).items() if Path(f).name == p.name for k in keys}
                    merged = ini.merge_user_values(p.read_text(encoding="utf-8", errors="replace"), text, locked)
                    p.write_text(merged, encoding="utf-8", newline="")
                    job.log(f"Kept your settings in {p.name}")
            self._write_record(exe, rec)
            self._remember_record_dir(g["key"], exe)
            if (old or {}).get("splash_user") and old.get("kind") == kind:
                rec["splash_user"] = True   # the user set the splash in the Tune tab: their merged value stays
            else:
                self._splash_on(rec, exe, job)
            self._apply_settings(rec, exe, job)
            if keep_fg and keep_fg.get("groups"):
                # the FG fallback files lived in d4r's folder, which the reinstall replaced: put them back
                try:
                    d4rfg.place(self, g, exe, rec, keep_fg["groups"], job, quiet=True)
                except Exception as e:   # never fails the injector install
                    job.log(f"Frame generation fallback not restored ({e}): add it again on the FG tab")
            if kind == "d4r" and "install.sh" in rec.get("files", []):
                job.stage("compile", "Running d4r's install.sh...", 0.96)
                self._run_d4r_installer(exe, rec, options, job)
            if upscaler == "none" or not was_active:
                self._set_active(exe, rec, False, job)
            self.sync_engine_fixes(g, rec, exe)   # the reinstall took Engine.ini fixes out; the merged .ini may need them
        self.set_choice(g["key"], source_id=sid, upscaler=upscaler)
        pending = self.settings()["overrides"].get(g["key"], {}).get("pending_profile")
        if pending:
            try:
                self.profiles.apply_ini(g["key"], pending, job)
            except Exception as e:   # a pasted profile never breaks an install
                job.log(f"Pasted profile: {e}")
            self._update_settings(lambda s: s["overrides"].get(g["key"], {}).pop("pending_profile", None))
        self.invalidate()
        job.update(f"{src['title']} {pkg.get('version')} installed. {self._in_game(addon, rec.get('route'))}", 1.0)
        return {"launch": self.wanted_launch(rec, g["key"])}

    @staticmethod
    def _key_code(v: str | None, default: int) -> int:
        v = (v or "").split(",")[0].strip().lower()
        if v in ("", "auto"):
            return default
        try:
            return int(v, 16) if v.startswith("0x") else int(v)
        except ValueError:
            return default

    def _overlay_key_edits(self, exe: Path) -> list[tuple[str, str, str]]:
        """With ReShade loaded through OptiScaler both menus share the game's input: when OptiScaler's menu key is
        ReShade's overlay key, OptiScaler takes it and ReShade's overlay never opens. OptiScaler moves to another key."""
        r, o = exe / "ReShade.ini", exe / "OptiScaler.ini"
        if not r.is_file() or not o.is_file():
            return []
        rs_key = self._key_code(ini.read(r).get("INPUT", "KeyOverlay"), 0x24)
        op_key = self._key_code(ini.read(o).get("Menu", "ShortcutKey"), 0x2D)
        if rs_key != op_key:
            return []
        new = 0x2D if rs_key != 0x2D else 0x23   # Insert, or End when ReShade itself uses Insert
        log.info("OptiScaler's menu key %#x clashed with ReShade's overlay key: OptiScaler moved to %#x", op_key, new)
        return [("Menu", "ShortcutKey", f"0x{new:02X}")]

    def _splash_on(self, rec: dict, exe: Path, job: Job | None = None) -> None:
        """Every add-on starts with its startup splash shown: OptiScaler's [Menu] DisableSplash=false, and any other
        on / off splash key an add-on's .ini has. Keys that are not on / off (durations, paths) are left alone."""
        offs, ons = ("false", "0", "off"), ("true", "1", "on")
        for p in self.config_files(rec, exe):
            if not p.is_file():
                continue
            edits = []
            for e in ini.read(p).entries():
                k, val = e["key"].lower(), e["value"].strip().lower()
                if "splash" not in k or val not in offs + ons + ("auto", ""):
                    continue
                negative = k.startswith(("disable", "hide", "no", "skip"))
                if val in ("auto", ""):
                    want = "false" if negative else "true"
                else:
                    pair = (offs[ons.index(val)] if val in ons else val) if negative else \
                        (ons[offs.index(val)] if val in offs else val)
                    want = pair
                if want != val:
                    edits.append((e["section"], e["key"], want))
            if edits and ini.edit_file(p, edits) and job:
                job.log(f"Splash screen on in {p.name}")

    def _write_record(self, exe: Path, rec: dict) -> None:
        write_json(rec_file(exe), rec)

    def _package_files(self, pkg: dict, kind: str, options: dict) -> list[tuple[Path, str]]:
        root = Path(pkg["path"])
        addon = A.ADDONS[kind]
        skip = [re.compile(p) for p in addon.get("skip_files", [])]
        out = []
        for p in sorted(root.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(root).as_posix()
            if rel in sources.META_NAMES or any(s.search(rel) for s in skip):
                continue
            if kind == "optiscaler":
                base = rel.rsplit("/", 1)[-1].lower()
                if base.startswith("fakenvapi") and not options.get("fakenvapi"):
                    continue
                if rel == "OptiScaler.dll":
                    rel = options.get("proxy") or "dxgi.dll"
                    if rel not in A.PROXY_NAMES:
                        rel = "dxgi.dll"
            out.append((p, rel))
        return out

    def _install_files(self, g: dict, exe: Path, pkg: dict, kind: str, options: dict, job: Job) -> dict:
        files = self._package_files(pkg, kind, options)
        if not files:
            raise UserError("The package is empty")
        rec = {"version": 2, "kind": kind, "exe_dir": str(exe), "files": [], "backups": {}, "created_dirs": [],
               # the same fallback as _package_files: the record names the file really installed
               "proxy": ((options.get("proxy") if options.get("proxy") in A.PROXY_NAMES else "dxgi.dll")
                         if kind == "optiscaler" else ("dxgi.dll" if kind == "d4r" else None)),
               "route": None}
        backup_root = exe / REC_DIR / "backup"
        # top-level folders that do not exist yet: removed whole on uninstall (logs and caches inside included)
        tops = {rel.split("/")[0] for _, rel in files if "/" in rel}
        rec["created_dirs"] = sorted(t for t in tops if not (exe / t).exists())
        total = len(files)
        try:
            for i, (src, rel) in enumerate(files):
                if job.cancelled():
                    raise UserError("Cancelled")
                dst = exe / rel
                replaced = False
                actual = ci_path(dst)   # the game's file may differ in case only (Wine treats them as one DLL)
                if actual.exists() or actual.is_symlink():
                    arel = actual.relative_to(exe).as_posix()
                    b = backup_root / arel
                    b.parent.mkdir(parents=True, exist_ok=True)
                    if not b.exists():
                        os.replace(actual, b)
                        job.log(f"Backed up the game's {arel}")
                    else:
                        # an older backup (from an interrupted install, or a second entry for the same file) is the
                        # game's original: keep pointing at it so uninstall still restores it
                        actual.unlink()
                    rec["backups"][arel] = b.relative_to(exe).as_posix()   # restored under the game's own spelling
                    replaced = True
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                rec["files"].append(rel)
                # binaries get a hash so uninstall notices when a game update replaced or added that file (config
                # files are left out: RAT edits those after install, a changed hash there is expected)
                if replaced or Path(rel).suffix.lower() in HASHED_SUFFIXES:
                    rec.setdefault("hashes", {})[rel] = sha256_of(dst)
                if i % 10 == 0:
                    job.sub(i / total, 0.86, 0.94, f"Copying files ({i + 1}/{total})...")
        finally:
            self._write_record(exe, rec)   # whatever got in is recorded, so it can be removed again
        return rec

    def _top_files(self, exe: Path, rec: dict) -> set[str]:
        """Files below exe that are not in folders RAT created (those are removed whole)."""
        out = set()
        skip = {REC_DIR} | set(rec.get("created_dirs", []))
        for d, files in steam.walk(exe, depth=3, skip=set()):
            rel_d = d.relative_to(exe)
            if rel_d.parts and rel_d.parts[0] in skip:
                continue
            for f in files:
                out.add((rel_d / f).as_posix())
        return out

    def _run_d4r_installer(self, exe: Path, rec: dict, options: dict, job: Job) -> None:
        script = exe / "install.sh"
        if not script.exists():
            return   # releases that bundle NVIDIA's files have no installer step
        before = self._top_files(exe, rec)
        args = ["--yes"]
        help_text = ""
        try:
            _, tail = run_streaming([which("bash") or "/bin/bash", str(script), "--help"], cwd=exe, timeout=30)
            help_text = "\n".join(tail)
        except Exception:
            pass
        if "--no-engine" in help_text and not options.get("build_engine"):
            args.append("--no-engine")
        job.update("Running d4r's install.sh (downloads NVIDIA's DLLs once, ~1 GB)...", -1)
        code, tail = run_streaming([which("bash") or "/bin/bash", str(script)] + args, cwd=exe,
                                   on_line=lambda l: (job.log(l), setattr(job, "message", l[:200])), timeout=7200,
                                   should_cancel=job.cancelled)
        new = sorted(self._top_files(exe, rec) - before - set(rec.get("files", [])))
        if new:   # files the installer added outside the d4r folder: removed with the rest on uninstall
            rec["files"] = rec.get("files", []) + new
        # install.sh may have rewritten files RAT copied in: re-hash so uninstall still recognises them as RAT's
        hashes = rec.setdefault("hashes", {})
        for rel in rec.get("files", []):
            p = exe / rel
            if (rel in hashes or Path(rel).suffix.lower() in HASHED_SUFFIXES) and p.is_file():
                hashes[rel] = sha256_of(p)
        self._write_record(exe, rec)
        if code != 0:
            raise UserError("d4r's install.sh failed: " + (tail[-1] if tail else f"exit {code}") +
                            " (the files are installed; run Update to retry)")

    def _install_helix(self, g: dict, exe: Path, pkg: dict, options: dict, job: Job) -> dict:
        route = options.get("route") or "auto"
        job.update("Installing HelixSR into the game...", 0.9)
        res = self._bridge(["install", pkg["path"], g["folder"], route], job)
        if not res.get("ok"):
            raise UserError(res.get("error") or "HelixSR could not be installed here")
        route = res.get("current") or route
        target = Path(res.get("target") or exe)
        helix_dirs = sorted({str(Path(t).parent) for t in res.get("fsr_targets", [])}) if route == "direct" else []
        return {"version": 2, "kind": "helixsr", "exe_dir": str(target if route == "scaler" else exe),
                "files": [], "backups": {}, "created_dirs": [], "route": route, "proxy": res.get("proxy"),
                "helix_dirs": helix_dirs, "fsr_targets": res.get("fsr_targets", [])}

    def _uninstall(self, g: dict, job: Job) -> dict:
        exe, _ = self.exe_dir(g)
        rec = read_json(rec_file(exe), None) if exe else None
        if not rec:
            raise UserError("RAT has not installed an injector in this game")
        with self._reshade_detached(g, job):
            job.stage("remove", f"Removing {rec.get('title')}...", 0.2)
            self._uninstall_files(g, exe, rec, job)
        self._remember_record_dir(g["key"], None if not (exe / REC_DIR).is_dir() else exe)
        self.invalidate()
        job.stage("configure", "Cleaning up launch options...", 0.95)
        job.update(f"{rec.get('title')} removed; the game's own files are back.", 1.0)
        return {"launch": self.wanted_launch(None, g["key"])}

    def _uninstall_files(self, g: dict, exe: Path, rec: dict, job: Job, keep_launch: bool = False,
                         next_route: str | None = None) -> None:
        if not rec.get("active", True):
            self._set_active(exe, rec, True, job, write=False)
        if rec.get("engine_ini"):   # Engine.ini lines RAT wrote for a frame generation method
            try:
                ueconfig.set_dilation_off(g, self.game_profile(g), False, rec["engine_ini"])
            except OSError:
                pass
        for rel in (rec.get("fg_fallback") or {}).get("files", []):   # d4r's FG fallback files
            if _rel_ok(rel):
                (exe / rel).unlink(missing_ok=True)
        if rec.get("kind") == "helixsr":
            pkg = sources.get_package(rec.get("package_id", "")) or {}
            path = pkg.get("path")
            if not path or not Path(path).exists():
                # any HelixSR package can remove any HelixSR install (it reads the game's own records)
                others = [p for p in sources.list_packages() if p.get("kind") == "helixsr"]
                path = others[0]["path"] if others else None
            if not path:
                raise UserError("No HelixSR package left to remove it with. Download HelixSR again, then uninstall.")
            res = self._bridge(["uninstall", path, g["folder"]], job)
            if not res.get("ok"):
                raise UserError(res.get("error") or "HelixSR could not be removed")
            # HelixSR keeps a helixsr.ini the user changed; it is only kept while the drop-in comes back
            if next_route not in ("direct", "auto"):
                for d in rec.get("helix_dirs", []):
                    for n in ("helixsr.ini", "helixsr.log"):
                        f = Path(d) / n
                        if f.is_file() and not (Path(d) / "helixsr-install.json").exists():
                            f.unlink()
        else:
            hashes = rec.get("hashes", {})
            game_updated = set()
            for rel in reversed(rec.get("files", [])):
                if not _rel_ok(rel):
                    continue
                p = exe / rel
                if rel in hashes and p.is_file() and sha256_of(p) != hashes[rel]:
                    # a game update or file verification put the game's own (newer) file here: keep it
                    game_updated.add(rel)
                    job.log(f"Kept the game's updated {rel}")
                    continue
                if p.is_file() or p.is_symlink():
                    p.unlink()
            for rel in RUNTIME_FILES.get(rec.get("kind"), []):
                (exe / rel).unlink(missing_ok=True)
            for d in rec.get("created_dirs", []):
                if _rel_ok(d) and (exe / d).is_dir():
                    shutil.rmtree(exe / d, ignore_errors=True)
            # empty folders the install made inside existing ones
            for rel in sorted({str(Path(r).parent) for r in rec.get("files", []) if "/" in r}, key=len, reverse=True):
                d = exe / rel
                try:
                    if d.is_dir() and not any(d.iterdir()):
                        d.rmdir()
                except OSError:
                    pass
            for rel, b in rec.get("backups", {}).items():
                src = exe / b
                if rel.lower() in {u.lower() for u in game_updated}:
                    continue   # the backup is older than what the game has now
                if src.exists() and _rel_ok(rel):
                    (exe / rel).parent.mkdir(parents=True, exist_ok=True)
                    os.replace(src, exe / rel)
                    job.log(f"Restored the game's {rel}")
        # only the injector's part of the record folder: ReShade / HDR records stay
        shutil.rmtree(exe / REC_DIR / "backup", ignore_errors=True)
        rec_file(exe).unlink(missing_ok=True)
        cleanup(exe)

    # --- activation and live settings -----------------------------------------------------------------------------

    def _deactivate_paths(self, rec: dict, exe: Path) -> list[Path]:
        kind = rec["kind"]
        if kind == "d4r":
            # only names d4r really put there: a game's own dxgi.dll / d3d12.dll must never vanish while d4r is off
            own = {f.lower() for f in rec.get("files", [])}
            return [exe / n for n in A.ADDONS["d4r"]["deactivate"] if n.lower() in own]
        if kind == "optiscaler":
            return [exe / (rec.get("proxy") or "dxgi.dll")]
        if kind == "helixsr":
            if rec.get("route") == "scaler":
                return [exe / (rec.get("proxy") or "dxgi.dll")]
            return [Path(t) for t in rec.get("fsr_targets", [])]
        return []

    def _set_active(self, exe: Path, rec: dict, active: bool, job: Job | None = None, write: bool = True) -> None:
        helix_direct = rec["kind"] == "helixsr" and rec.get("route") != "scaler"
        for p in self._deactivate_paths(rec, exe):
            off = p.with_name(p.name + OFF)
            if active:
                if off.exists():
                    os.replace(off, p)
            else:
                if p.exists() and not off.exists():
                    os.replace(p, off)
                    if helix_direct:   # the game's own FSR DLL back in place while HelixSR is off
                        orig = p.with_name(p.stem + ".original.dll")
                        if orig.exists():
                            shutil.copy2(orig, p)
        rec["active"] = active
        if active and rec.get("upscaler") == "none":
            rec["upscaler"] = A.default_upscaler(self.addon_of(rec), self.gpu())
        if write:
            self._write_record(exe, rec)
        if job:
            job.log("Injection " + ("enabled" if active else "disabled"))

    def _load(self, key: str, need: bool = True) -> tuple[dict, Path | None, dict | None]:
        g = self.game(key)
        exe, _ = self.exe_dir(g)
        rec = read_json(rec_file(exe), None) if exe else None
        if need and not rec:
            raise UserError("Not installed")
        return g, exe, rec

    def set_active(self, key: str, active: bool) -> dict:
        g, exe, rec = self._load(key)
        if self.is_running(g):
            raise UserError("Close the game first.")
        with self._reshade_detached(g):
            self._set_active(exe, rec, bool(active))
            self._apply_settings(rec, exe)
        self.sync_engine_fixes(g, rec, exe)
        self.set_choice(key, upscaler=rec.get("upscaler"))
        return self.detail(key)

    def set_upscaler(self, key: str, upscaler: str) -> dict:
        g, exe, rec = self._load(key, need=False)
        if not self._editing_installed(rec, self.choice(key)):
            self.set_choice(key, upscaler=upscaler)
            return self.detail(key)
        addon = self.addon_of(rec)
        if upscaler not in {u["id"] for u in addon["upscalers"]}:
            raise UserError("Unknown upscaler for this add-on")
        if not A.usable(A.upscaler(addon, upscaler), self.gpu()):
            raise UserError("That upscaler needs an NVIDIA RTX GPU")
        if self.is_running(g):
            raise UserError("Close the game first: settings are read when it starts.")
        with self._reshade_detached(g):
            if upscaler == "none":
                rec["upscaler"] = "none"
                self._set_active(exe, rec, False)
            else:
                rec["upscaler"] = upscaler
                if not rec.get("active", True):
                    self._set_active(exe, rec, True, write=False)
                    rec["upscaler"] = upscaler
                self._write_record(exe, rec)
                self._apply_settings(rec, exe)
        self.set_choice(key, upscaler=upscaler)
        return self.detail(key)

    def set_mode(self, key: str, path: str) -> dict:
        """Native DLSS / spoofed FSR / spoofed XeSS: picks the path's best upscaler and the spoofing it needs."""
        if path not in A.PATHS:
            raise UserError("Unknown upscaling mode")
        g, exe, rec = self._load(key, need=False)
        live = self._editing_installed(rec, self.choice(key))
        if live:
            addon = self.addon_of(rec)
            route = rec.get("route")
        else:
            sid = self.choice(key).get("source_id") or (rec or {}).get("source_id") or self._default_source(self.gpu())
            addon = self.addon_for_source(sid)
            route = None
        up = A.default_for_path(addon, path, self.gpu(), route)
        if not up:
            raise UserError(f"{addon['title']} has no {A.PATHS[path]} mode" +
                            (" with this install method" if route else ""))
        if any(o["id"] == "spoof" for o in addon["options"]):
            want_spoof = path != "native" or not self.gpu().get("dlss")
            if live:
                rec.setdefault("options", {})["spoof"] = want_spoof
                self._write_record(exe, rec)
            else:
                self.set_choice(key, option=["spoof", want_spoof])
        return self.set_upscaler(key, up)

    def set_option(self, key: str, opt_id: str, value) -> dict:
        g, exe, rec = self._load(key, need=False)
        if not self._editing_installed(rec, self.choice(key)):
            self.set_choice(key, option=[opt_id, value])
            return self.detail(key)
        addon = self.addon_of(rec)
        opt = next((o for o in addon["options"] if o["id"] == opt_id), None)
        if not opt:
            raise UserError("Unknown option")
        if self.is_running(g):
            raise UserError("Close the game first: settings are read when it starts.")
        allowed = [v[0] for v in opt.get("values") or [] if isinstance(v, (list, tuple)) and v]
        if allowed and str(value) not in {str(a) for a in allowed}:
            raise UserError(f"{value!r} is not a valid value for {opt.get('label') or opt_id}")
        if opt.get("meta") and opt_id in ("proxy", "route", "fakenvapi", "build_engine"):
            # needs a reinstall: remembered for the next install/update
            self.set_choice(key, option=[opt_id, value])
            rec.setdefault("pending", {})[opt_id] = value
            self._write_record(exe, rec)
            return dict(self.detail(key), needs_reinstall=True)
        rec.setdefault("options", {})[opt_id] = value
        self._write_record(exe, rec)
        self._apply_settings(rec, exe)
        self.set_choice(key, option=[opt_id, value])
        return self.detail(key)

    # --- advanced view --------------------------------------------------------------------------------------------

    def read_configs(self, key: str) -> list[dict]:
        g = self.game(key)
        exe, _ = self.exe_dir(g)
        rec = read_json(rec_file(exe), None) if exe else None
        paths: list[Path] = []
        if rec:
            paths += list(self.config_files(rec, exe))
            for l in self.addon_of(rec).get("logs", []):
                paths.append(exe / l)
            for d in rec.get("helix_dirs", []):
                paths.append(Path(d) / "helixsr.log")
        if exe:
            paths += [exe / "ReShade.ini", exe / "ReShade.log", exe / "ReShadePreset.ini"]
        out = []
        seen = set()
        for p in paths:
            if str(p) in seen:
                continue
            seen.add(str(p))
            if not p.exists():
                continue
            try:
                data = p.read_bytes()
            except OSError:
                continue
            text = data[-200_000:].decode("utf-8", "replace")
            out.append({"name": p.name, "path": str(p), "size": len(data), "content": text,
                        "kind": "log" if p.suffix.lower() == ".log" else "ini",
                        "truncated": len(data) > 200_000, "modified": p.stat().st_mtime})
        return out

    def write_config_value(self, key: str, path: str, section: str, k: str, value: str) -> list[dict]:
        g = self.game(key)
        exe, _ = self.exe_dir(g)
        rec = read_json(rec_file(exe), None) if exe else None
        allowed = {str(p) for p in self.config_files(rec, exe)} if rec else set()
        if exe:
            allowed |= {str(exe / "ReShade.ini")}
        if path not in allowed:
            raise UserError("Only the add-on's own INI files and ReShade.ini can be edited here")
        if self.is_running(g):
            raise UserError("Close the game first.")
        ini.edit_file(Path(path), [(section, k, value)])
        return self.read_configs(key)

    # --- injectors RAT did not install --------------------------------------------------------------------------------

    def _own_dirs(self, g: dict) -> list[str]:
        """Folders that hold RAT's own install in this game (its record folder, HelixSR drop-in folders)."""
        rec = self.record_for(g) or {}
        out = [rec.get("exe_dir")] if rec.get("exe_dir") else []
        out += list(rec.get("helix_dirs") or [])
        out += [str(Path(t).parent) for t in rec.get("fsr_targets") or []]
        return [d for d in out if d]

    def external(self, g: dict) -> list[dict]:
        return self._cached("external", g["folder"], lambda: ext_mod.detect(g["folder"], self._own_dirs(g)))

    def start_wipe_external(self, key: str) -> dict:
        g = self.game(key)
        if self.is_running(g):
            raise UserError("Close the game first.")
        items = ext_mod.detect(g["folder"], self._own_dirs(g))
        if not items:
            raise UserError("No external injector found in this game")
        self.invalidate()
        return self.jobs.start("Remove & wipe", lambda job: self._wipe(g, items, job), game=key, stages=STAGES_REMOVE)

    def _wipe(self, g: dict, items: list[dict], job: Job) -> dict:
        removed = restored = 0
        kept: list[str] = []
        own = self.record_for(g) or {}
        job.stage("remove", "Removing the external injector...", 0.05)
        if any(x["helix"] for x in items) and own.get("kind") != "helixsr":   # never RAT's own HelixSR install
            pk = [p for p in sources.list_packages() if p.get("kind") == "helixsr" and Path(p.get("path", "")).is_dir()]
            if pk:
                job.update("Running HelixSR's own uninstaller...", 0.1)
                try:
                    self._bridge(["uninstall", pk[0]["path"], g["folder"]], job, timeout=300)
                except Exception as e:   # not HelixSR's own install, or no record: cleaned up by hand below
                    job.log(f"HelixSR's uninstaller: {e}")
                items = ext_mod.detect(g["folder"], self._own_dirs(g))
        for i, x in enumerate(items):
            if job.cancelled():
                raise UserError("Cancelled")
            job.update(f"Removing {x['kind']} from {x['rel_dir']}...", 0.2 + 0.7 * i / max(1, len(items)))
            r = ext_mod.remove(x, job.log)
            removed += r["removed"]
            restored += r["restored"]
            kept += [f"{x['rel_dir']}/{k}" if x["rel_dir"] != "." else k for k in r["kept"]]
        self.invalidate()
        left = ext_mod.detect(g["folder"], self._own_dirs(g))
        job.stage("configure", "Cleaning up launch options...", 0.95)
        msg = f"Removed {removed} item{'s' if removed != 1 else ''}"
        if restored:
            msg += f", restored {restored} game file{'s' if restored != 1 else ''}"
        if kept:
            msg += f". Kept {len(kept)} runtime DLL{'s' if len(kept) != 1 else ''} the game may own: verify the game " \
                   "files in Steam if it misbehaves"
        if left:
            msg += ". Some files could not be removed: " + ", ".join(x["rel_dir"] for x in left)
        job.update(msg + ".", 1.0)
        return {"launch": self.wanted_launch(self.record_for(g), g["key"]), "strip": True, "removed": removed,
                "restored": restored, "kept": kept, "left": len(left)}

    # --- injector settings (INI editor) -----------------------------------------------------------------------------

    INI_IDS = {"optiscaler.ini": "optiscaler", "d4r.ini": "d4r", "helixsr.ini": "helixsr", "fakenvapi.ini": "fakenvapi"}

    def _ini_groups(self, rec: dict, exe: Path) -> dict[str, dict]:
        groups: dict[str, dict] = {}
        for p in self.config_files(rec, exe):
            fid = self.INI_IDS.get(p.name.lower())
            if fid:
                groups.setdefault(fid, {"id": fid, "name": p.name, "paths": []})["paths"].append(p)
        return {k: v for k, v in groups.items() if any(p.exists() for p in v["paths"])}

    def _locked_keys(self, rec: dict, fid: str) -> dict[tuple[str, str], str]:
        """Keys the editor shows read-only: the Upscaler picker's and the ones d4r needs to run."""
        out: dict[tuple[str, str], str] = {}
        kind = rec["kind"]
        addon = self.addon_of(rec)
        fam = self.gpu().get("family", "unknown")
        for up in addon["upscalers"]:
            for f, sec, key, _ in A.upscaler_edits(kind, up["id"], fam, rec.get("route")):
                if self.INI_IDS.get(Path(f).name.lower()) == fid:
                    out[(sec.lower(), key.lower())] = "Set by RAT's Upscaler picker (Upscale tab)"
        for f, keys in addon.get("locked", {}).items():
            if self.INI_IDS.get(Path(f).name.lower()) == fid:
                for sk in keys:
                    out[sk] = f"Managed by {addon['title']}: needed for it to work"
        if fid == "optiscaler":
            out[("plugins", "loadreshade")] = "Managed by RAT's ReShade section (ReShade loaded through OptiScaler)"
        return out

    def _editor(self, key: str) -> tuple[dict, Path, dict, dict[str, dict]]:
        g, exe, rec = self._load(key)
        return g, exe, rec, self._ini_groups(rec, exe)

    def ini_files(self, key: str) -> dict:
        g, exe, rec, groups = self._editor(key)
        files = []
        for fid, grp in groups.items():
            p = next(p for p in grp["paths"] if p.exists())
            files.append({"id": fid, "name": grp["name"], "path": str(p), "copies": len(grp["paths"]),
                          "sections": inischema.sections(ini.read(p))})
        order = ["optiscaler", "d4r", "helixsr", "fakenvapi"]
        files.sort(key=lambda f: order.index(f["id"]) if f["id"] in order else 9)
        return {"files": files, "running": self.is_running(g)}

    def ini_essentials(self, key: str) -> list[dict]:
        """The TUNE tab's short list: the keys most people change, grouped, from the files this game has. Frame
        generation (FG tab) and the upscaler choice (UPSCALE tab) are left out; everything else is under Show advanced."""
        g, exe, rec, groups = self._editor(key)
        out = []
        for fid, grp in groups.items():
            p = next(p for p in grp["paths"] if p.exists())
            doc = ini.read(p)
            locked = self._locked_keys(rec, fid)
            for title, keys in ESSENTIALS.get(fid, []):
                fields = inischema.fields(fid, doc, only=keys, locked=locked)
                if fields:
                    out.append({"title": title, "file": fid, "fields": fields})
        return out

    def ini_section(self, key: str, fid: str, section: str) -> list[dict]:
        g, exe, rec, groups = self._editor(key)
        grp = groups.get(fid)
        if not grp:
            raise UserError("That settings file is not in this game")
        p = next(p for p in grp["paths"] if p.exists())
        return inischema.fields(fid, ini.read(p), section=section, locked=self._locked_keys(rec, fid))

    def save_ini(self, key: str, fid: str, edits: list) -> dict:
        """Writes staged [section, key, value] edits into the add-on's INI (every copy, for HelixSR's drop-in)."""
        g, exe, rec, groups = self._editor(key)
        grp = groups.get(fid)
        if not grp:
            raise UserError("That settings file is not in this game")
        if self.is_running(g):
            raise UserError("Close the game first: the injector reads its settings when the game starts.")
        locked = self._locked_keys(rec, fid)
        clean: list[tuple[str, str, str]] = []
        for e in edits or []:
            if not isinstance(e, (list, tuple)) or len(e) != 3:
                raise UserError("Bad edit")
            sec, k, v = (str(x).strip() for x in e)
            one_line = all(len(("x" + t + "x").splitlines()) == 1 for t in (sec, k, v))
            if not sec or not k or not one_line or any(c in sec for c in "[]") or any(c in k for c in "[]=;#"):
                raise UserError(f"Invalid value for {k or 'a key'}")
            if (sec.lower(), k.lower()) in locked:
                raise UserError(f"{k}: {locked[(sec.lower(), k.lower())]}")
            clean.append((sec, k, v))
        changed = 0
        for p in grp["paths"]:
            if p.exists() and ini.edit_file(p, clean):
                changed += 1
        if any("splash" in k.lower() for _, k, _ in clean) and not rec.get("splash_user"):
            rec["splash_user"] = True   # kept across reinstalls from now on
            self._write_record(exe, rec)
        self._sync_record_options(rec, exe)
        note = self.sync_engine_fixes(g, rec, exe) if fid == "optiscaler" else None
        self.invalidate()
        return {"saved": len(clean), "files": changed, "note": note}

    def sync_engine_fixes(self, g: dict, rec: dict, exe: Path) -> str | None:
        """Engine-side fixes the current frame generation method needs, applied to every OptiScaler-based add-on
        (OptiScaler, d4r, HelixSR Scaler): OptiFG → XeFG on Unreal needs dilated motion vectors off (Engine.ini)."""
        doc = self.opti_doc(rec, exe)
        want = False
        if doc is not None and rec.get("active", True):
            inp = framegen._norm("FrameGen", "FGInput", doc.get("FrameGen", "FGInput"))
            outp = framegen._norm("FrameGen", "FGOutput", doc.get("FrameGen", "FGOutput"))
            want = inp == "upscaler" and outp == "xefg"
        owned = rec.get("engine_ini") or []
        if not want and not owned:
            return None
        try:
            new, note = ueconfig.set_dilation_off(g, self.game_profile(g), want, owned)
        except OSError as e:
            log.warning("Engine.ini: %s", e)
            return None
        if new != owned:
            rec["engine_ini"] = new
            self._write_record(exe, rec)
        return note

    def _sync_record_options(self, rec: dict, exe: Path) -> None:
        """Options the Upscale tab shows (sharpening, spoofing...) follow what the INI now says, so a later toggle
        there does not write an older value back."""
        addon = self.addon_of(rec)
        vals = self._option_values(rec, exe, addon, {})
        opts = rec.setdefault("options", {})
        changed = False
        for o in addon["options"]:
            if o.get("meta") or not o.get("file"):
                continue
            if opts.get(o["id"]) != vals.get(o["id"]):
                opts[o["id"]] = vals.get(o["id"])
                changed = True
        if changed:
            self._write_record(exe, rec)

    def frame_gen(self, key: str) -> dict:
        g, exe, rec = self._load(key, need=False)
        if not rec:
            res = framegen.analyze(None, None, "", None, {})
        else:
            kind = rec["kind"]
            doc = self.opti_doc(rec, exe)
            scan = self._cached("scan", g["folder"], lambda: steam.scan_game(Path(g["folder"])))
            env = {"vendor": self.gpu().get("vendor"), "fallback": d4rfg.state(exe, rec) if kind == "d4r" else None}
            res = framegen.analyze(doc, exe, kind, rec.get("route"), scan["native"],
                                   self._locked_keys(rec, "optiscaler"), env)
            res["file_id"] = "optiscaler"
        res["running"] = self.is_running(g)
        return res

    def start_fg_fallback(self, key: str, groups: list[str]) -> dict:
        """d4r: adds frame generation libraries from the matching official OptiScaler release (FG fallback)."""
        g, exe, rec = self._load(key)
        if rec.get("kind") != "d4r":
            raise UserError("The frame generation fallback is only for d4r")
        if self.is_running(g):
            raise UserError("Close the game first.")
        self.invalidate()

        def run(job: Job):
            fb = d4rfg.place(self, g, exe, rec, groups, job)
            self.invalidate()
            n = len(fb["files"])
            job.update(f"Frame generation fallback ready: {n} file{'s' if n != 1 else ''} from OptiScaler "
                       f"{fb['donor']} (d4r bundles {fb['bundled']}). Pick a method on the FG tab.", 1.0)
            return fb
        return self.jobs.start("Add frame generation to d4r", run, game=key, stages=STAGES_INSTALL)

    def remove_fg_fallback(self, key: str) -> dict:
        g, exe, rec = self._load(key)
        if rec.get("kind") != "d4r":
            raise UserError("The frame generation fallback is only for d4r")
        if self.is_running(g):
            raise UserError("Close the game first.")
        d4rfg.remove(exe, rec)
        self.invalidate()
        return self.frame_gen(key)

    # --- launch status (banner in the panel, HUD toast when a game starts) --------------------------------------------

    def launch_status(self, key: str) -> dict:
        """What runs in the game: injector, upscaler (native or spoofed, name, version, model, API), frame
        generation, ReShade and HDR. Used for the in-panel banner and the boot HUD."""
        g = self.game(key)
        exe, _ = self.exe_dir(g)
        rec = read_json(rec_file(exe), None) if exe else None
        pid = self.running_pid(g)
        prof = self.game_profile(g)
        live_api = peinfo.running_api(pid) if pid else None
        api = live_api or prof.get("api")
        out: dict = {"game": g["name"], "key": key, "running": bool(pid), "api": api, "api_live": bool(live_api),
                     "product": PRODUCT, "version": VERSION, "appid": g.get("appid")}
        if rec and rec.get("active", True) and rec.get("upscaler") != "none":
            addon = self.addon_of(rec)
            up = A.upscaler(addon, rec.get("upscaler")) or {}
            path = up.get("path", "auto")
            model = up.get("model", "")
            name = up.get("tech") or up.get("label", "")
            lib_version = None
            doc = self.opti_doc(rec, exe)
            if rec["kind"] == "d4r" and path == "native":
                lib_version = peinfo_version(exe / "d4r" / "nvngx_dlss.dll")
            elif path == "fsr" and doc is not None:
                idx = (doc.get("FSR", "UpscalerIndex") or "auto").strip()
                entry = next((e for e in doc.entries() if e["section"].lower() == "fsr"
                              and e["key"].lower() == "upscalerindex"), None)
                if entry and idx != "auto":
                    f = inischema.field("optiscaler", entry)
                    lab = next((c[1] for c in f["choices"] if c[0] == idx), "")
                    m = re.search(r"FSR\s*([\d.]+)", lab)
                    lib_version = m.group(1) if m else None
            elif path == "xess" and exe:
                for d in framegen.search_dirs(exe, doc):
                    v = peinfo_version(d / "libxess.dll")
                    if v:
                        lib_version = v
                        break
            elif path == "native" and rec["kind"] == "optiscaler":
                scan = self._cached("scan", g["folder"], lambda: steam.scan_game(Path(g["folder"])))
                dl = (scan["native"].get("DLSS") or [None])[0]
                lib_version = peinfo_version(Path(dl)) if dl else None
            mode = {"native": "Native", "fsr": "Spoofed", "xess": "Spoofed"}.get(path, "Auto")
            out["injector"] = {"title": rec.get("title"), "author": rec.get("author") or addon.get("author_short"),
                               "version": rec.get("package_version"), "kind": rec["kind"]}
            out["upscaler"] = {"mode": mode, "path": path, "name": name, "label": up.get("label"), "model": model,
                               "version": lib_version, "family": up.get("family"), "api": api}
            env = {"vendor": self.gpu().get("vendor"),
                   "fallback": d4rfg.state(exe, rec) if rec["kind"] == "d4r" else None}
            fg = framegen.analyze(doc, exe, rec["kind"], rec.get("route"), {}, None, env) if doc is not None else None
            if fg and fg.get("available") and fg.get("current") not in (None, "off"):
                m = next((x for x in fg["methods"] if x["id"] == fg["current"]), None)
                out["fg"] = {"active": True, "provider": "OptiScaler", "method": m["label"] if m else "Custom"}
        else:
            out["injector"] = None
            out["upscaler"] = {"mode": "Off", "path": "none", "name": "Game's own", "api": api}
        out.setdefault("fg", {"active": False})
        out["reshade"] = self.reshade.status(key)
        out["hdr"] = self.renodx.status(key)
        inj = out.get("injector")
        out["header"] = f"{PRODUCT} {VERSION}" + (f" (Running {inj['title']} by {inj['author']})" if inj else "")
        return out

    def game_started(self, appid) -> dict | None:
        """Called by the frontend when Steam starts a game: applies per-session effects (gamescope ReShade) and returns
        the status for the boot banner."""
        g = self.game_by_appid(appid)
        if not g:
            return None
        key = g["key"]
        exe = self.reshade.game_dir(g)
        managed = bool(self.record_for(g) or self.reshade.record(exe) or self.renodx.record(exe)
                       or (self.game_overrides(key).get("compositor") or {}).get("effect"))
        if not managed:
            return None   # a game RAT does nothing in: no banner, no polling
        self.sessions[g["key"]] = {"started": time.time()}
        try:
            self.reshade.session_start(g["key"])
        except Exception as e:
            log.warning("gamescope effect: %s", e)
        st = self.launch_status(g["key"])
        st["show"] = bool(self.pref("boot_banner"))
        return st

    def game_stopped(self, appid) -> None:
        g = self.game_by_appid(appid)
        if g:
            self.sessions.pop(g["key"], None)
            try:
                self.reshade.session_stop(g["key"])
            except Exception as e:
                log.warning("gamescope effect: %s", e)

    # --- overview -------------------------------------------------------------------------------------------------

    def overview(self) -> dict:
        s = self.settings()
        srcs = []
        for sid, src in self.sources().items():
            pk = self.packages_for(sid)
            srcs.append(dict(src, packages=[{"id": p["id"], "version": p.get("version"),
                                              "prepared": p.get("prepared", False)} for p in pk]))
        browse = str(Paths.user_home)
        for m in (Path("/run/media") / Paths.user_home.name, Path("/run/media")):
            try:
                if m.is_dir() and any(m.iterdir()):
                    browse = str(m)
                    break
            except OSError:
                continue
        return {"gpu": self.gpu(), "prefs": s["prefs"], "sources": srcs, "catalog": A.public_catalog(),
                "job": self.jobs.get(), "browse_root": browse, "home": str(Paths.user_home),
                "product": PRODUCT, "version": VERSION, "paths": A.PATHS, "exports": str(Paths.user_home / "RAT")}


def peinfo_version(path: Path) -> str | None:
    """FileVersion from a DLL's version resource (UTF-16 string table), e.g. '310.7.0.0'."""
    try:
        if not path or not Path(path).is_file():
            return None
        size = Path(path).stat().st_size
        with open(path, "rb") as f:
            f.seek(max(0, size - (4 << 20)))
            data = f.read()
        key = "FileVersion".encode("utf-16-le") + b"\0\0"
        i = data.rfind(key)
        if i < 0:
            return None
        j = i + len(key)
        while j < len(data) - 1 and data[j:j + 2] == b"\0\0":
            j += 2
        end = data.find(b"\0\0", j)
        while end > 0 and (end - j) % 2:
            end = data.find(b"\0\0", end + 1)
        s = data[j:end].decode("utf-16-le", "ignore").strip()
        m = re.match(r"[\d]+(?:[.,]\s*\d+)*", s)
        return m.group(0).replace(",", ".").replace(" ", "") if m else None
    except OSError:
        return None
