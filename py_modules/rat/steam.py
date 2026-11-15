"""Steam library discovery: installed games, their folders and executables, compatibility tools."""
from __future__ import annotations

import os
import re
from pathlib import Path

from . import vdf
from .util import Paths

# Not games: Proton, runtimes, redistributables
TOOL_APPIDS = {"228980", "1070560", "1391110", "1628350", "1493710", "2180100", "2348590", "250820", "1826330",
               "1161040", "1580130", "1887720", "2230260", "3658110"}
TOOL_NAME = re.compile(r"^(proton|steam linux runtime|steamworks common|steamvr|steam audio|"
                       r"proton easyanticheat runtime|proton battleye runtime)", re.I)

SKIP_EXE = re.compile(r"unins|setup|redist|crash|launcher|vc_?redist|dxsetup|easyanticheat|beservice|dotnet|"
                      r"helper|report|prereq|install|7z|ue4prereq|cefprocess|webhelper|start_protected_game|"
                      r"battleye|uploader|benchmark|server|editor", re.I)
SCAN_SKIP = {"_commonredist", "commonredist", "redist", "redistributables", "directx", "vcredist", "__installer",
             ".rat", "helixsr-scaler-backup", "shadercache", "steamworks shared"}
# looking for the main .exe: also skip big content folders and Unreal's own engine tree
EXE_SKIP = SCAN_SKIP | {"d4r", "engine", "content", "movies", "paks", "localization", "support", "optiscaler",
                        "d3d12_optiscaler", "licenses", "helixsr"}

# Upscaler libraries a game ships (what the game itself can offer, before any injection)
NATIVE_UPSCALERS = {
    "nvngx_dlss.dll": "DLSS",
    "nvngx_dlssd.dll": "DLSS RR",
    "nvngx_dlssg.dll": "DLSS FG",
    "amd_fidelityfx_upscaler_dx12.dll": "FSR 3.1+",
    "amd_fidelityfx_dx12.dll": "FSR 3.1",
    "amd_fidelityfx_vk.dll": "FSR 3.1 (Vulkan)",
    "ffx_fsr2_api_x64.dll": "FSR 2",
    "ffx_fsr2_api_dx12_x64.dll": "FSR 2",
    "libxess.dll": "XeSS",
    "libxess_fg.dll": "XeSS FG",
    "sl.interposer.dll": "Streamline",
}

ANTICHEAT = (
    ("EasyAntiCheat", ("easyanticheat", "start_protected_game.exe", "eac_launcher.exe")),
    ("BattlEye", ("battleye", "beservice", "beclient")),
    ("nProtect GameGuard", ("gameguard",)),
    ("XIGNCODE3", ("xigncode",)),
    ("EQU8", ("equ8",)),
    ("Anti-Cheat Expert", ("anticheatexpert", "ace-base")),
    ("EA Javelin", ("eaanticheat", "eaac.")),
    ("PunkBuster", ("pnkbstr", "punkbuster", "pbsvc")),
    ("Denuvo Anti-Cheat", ("denuvo-anti-cheat", "denuvoanticheat")),
    ("mhyprot", ("mhyprot",)),
    ("NetEase Anti-Cheat", ("neacsafe", "neac_")),
)


RECORD_DIRS = (".rat",)


def has_record_dir(d: Path) -> bool:
    """A folder where RAT installed an add-on."""
    return any((Path(d) / r).is_dir() for r in RECORD_DIRS)


def steam_roots(home: Path | None = None) -> list[Path]:
    home = Path(home or Paths.user_home)
    cands = [home / ".local/share/Steam", home / ".steam/steam", home / ".steam/root",
             home / ".var/app/com.valvesoftware.Steam/.local/share/Steam"]
    out: list[Path] = []
    for c in cands:
        try:
            r = c.resolve()
        except OSError:
            continue
        if (r / "steamapps").is_dir() and r not in out:
            out.append(r)
    return out


def library_paths(home: Path | None = None) -> list[Path]:
    out: list[Path] = []
    for root in steam_roots(home):
        for p in [root] + [Path(v["path"]) for v in
                           (vdf.get_ci(vdf.load(root / "steamapps/libraryfolders.vdf"), "libraryfolders") or {}).values()
                           if isinstance(v, dict) and v.get("path")]:
            try:
                p = p.resolve()
            except OSError:
                continue
            if (p / "steamapps").is_dir() and p not in out:
                out.append(p)
    return out


def games(home: Path | None = None) -> list[dict]:
    """Installed Steam games: [{appid, name, folder, library}] sorted by name."""
    out: dict[str, dict] = {}
    for lib in library_paths(home):
        apps = lib / "steamapps"
        for acf in apps.glob("appmanifest_*.acf"):
            st = vdf.get_ci(vdf.load(acf), "AppState") or {}
            appid = str(st.get("appid") or acf.stem.split("_")[-1])
            name = st.get("name") or f"App {appid}"
            installdir = st.get("installdir")
            if not installdir or appid in TOOL_APPIDS or TOOL_NAME.match(name):
                continue
            folder = apps / "common" / installdir
            if not folder.is_dir():
                continue
            try:
                flags = int(st.get("StateFlags") or 4)
            except ValueError:
                flags = 4
            out[appid] = {"appid": appid, "name": name, "folder": str(folder), "library": str(lib),
                          "fully_installed": bool(flags & 4), "custom": False}
    return sorted(out.values(), key=lambda g: g["name"].lower())


def walk(folder: Path, depth: int = 7, limit: int = 20000, skip: set[str] = SCAN_SKIP):
    """(dir, [file names]) pairs below folder, breadth first, skipping redistributable/content folders."""
    todo = [(Path(folder), 0)]
    seen = 0
    while todo:
        d, n = todo.pop(0)
        try:
            entries = list(os.scandir(d))
        except OSError:
            continue
        files = []
        for e in entries:
            try:
                if e.is_file(follow_symlinks=False) or e.is_symlink():
                    files.append(e.name)
                elif n < depth and e.is_dir(follow_symlinks=False) and e.name.lower() not in skip:
                    todo.append((Path(e.path), n + 1))
            except OSError:
                continue
        seen += len(files)
        yield d, files
        if seen > limit:
            return


def exe_candidates(folder: Path, max_items: int = 6) -> list[dict]:
    """Likely main executables, best first: [{dir, exe, score}]."""
    folder = Path(folder)
    scored, skipped = [], []
    for d, files in walk(folder, skip=EXE_SKIP):
        low = {f.lower() for f in files}
        for f in files:
            fl = f.lower()
            if not fl.endswith(".exe"):
                continue
            # helper names match anywhere in the name ("Observer.exe" contains "server"): those are only used when
            # nothing else is left, so a game whose exe happens to match is still found
            bucket = skipped if SKIP_EXE.search(f) else scored
            try:
                size = (d / f).stat().st_size
            except OSError:
                size = 0
            score = min(size / (1 << 20), 400.0)   # MB, capped
            rel_depth = len(d.relative_to(folder).parts)
            score -= rel_depth * 3
            if re.search(r"-(win64|wingdk|winrt)-shipping\.exe$", fl):
                score += 1000
            if d.name.lower() in ("win64", "wingdk", "winrt") and d.parent.name.lower() == "binaries":
                score += 200
                if d.parent.parent.name.lower() == "engine":
                    score -= 900   # Unreal's own tools
            # where an injector already lives wins: that is the folder the game loads it from
            if "optiscaler.ini" in low or "helixsr-scaler.files" in low or has_record_dir(d):
                score += 5000
            bucket.append({"dir": str(d), "exe": f, "score": round(score, 1)})
    if not scored:
        scored = skipped
    scored.sort(key=lambda c: -c["score"])
    out, dirs = [], set()
    for c in scored:
        if c["dir"] in dirs:
            continue
        dirs.add(c["dir"])
        out.append(c)
        if len(out) >= max_items:
            break
    return out


def scan_game(folder: Path) -> dict:
    """Native upscalers the game ships, anti-cheat, injectors already there (not made by RAT)."""
    folder = Path(folder)
    native: dict[str, list[str]] = {}
    anticheat: set[str] = set()
    foreign = []
    for d, files in walk(folder, depth=16, limit=60000):
        low = {f.lower(): f for f in files}
        rel_parts = [p.lower() for p in d.relative_to(folder).parts]
        if ".rat" in rel_parts or "helixsr" in rel_parts or "d4r" in rel_parts:
            continue
        for f in low:
            if f in NATIVE_UPSCALERS:
                native.setdefault(NATIVE_UPSCALERS[f], []).append(str(d / low[f]))
        for name, marks in ANTICHEAT:
            if any(f.startswith(m) for f in low for m in marks) or any(p.startswith(marks) for p in rel_parts):
                anticheat.add(name)
        if "optiscaler.ini" in low and not has_record_dir(d):
            foreign.append({"kind": "OptiScaler", "dir": str(d)})
        if "helixsr-scaler.files" in low and not has_record_dir(d):
            foreign.append({"kind": "HelixSR Scaler", "dir": str(d)})
    has_d3d12 = bool(native)  # a hint only: most modern upscaler-capable games are DX12
    return {"native": {k: v[:3] for k, v in sorted(native.items())}, "anticheat": sorted(anticheat),
            "foreign": foreign, "has_upscaler_dlls": has_d3d12}


def compat_tools(home: Path | None = None) -> list[dict]:
    """Installed compatibility tools: [{name (internal), display, kind}]. Official Protons and custom ones
    (GE-Proton, Proton-CachyOS, ...) in compatibilitytools.d."""
    out: dict[str, dict] = {}
    for root in steam_roots(home):
        for d in sorted((root / "compatibilitytools.d").glob("*")):
            cfg = vdf.load(d / "compatibilitytool.vdf")
            tools = vdf.get_ci(cfg, "compatibilitytools", "compat_tools") or {}
            for name, meta in tools.items():
                disp = (meta or {}).get("display_name", name) if isinstance(meta, dict) else name
                out[name] = {"name": name, "display": disp, "kind": "custom"}
            if not tools and d.is_dir():
                out.setdefault(d.name, {"name": d.name, "display": d.name, "kind": "custom"})
    for lib in library_paths(home):
        for d in sorted((lib / "steamapps/common").glob("Proton*")):
            if (d / "proton").exists():
                out.setdefault(d.name, {"name": d.name, "display": d.name, "kind": "official"})
    return sorted(out.values(), key=lambda t: t["display"].lower())


def current_compat_tool(appid: str, home: Path | None = None) -> str | None:
    for root in steam_roots(home):
        cfg = vdf.load(root / "config/config.vdf")
        mapping = vdf.get_ci(cfg, "InstallConfigStore", "Software", "Valve", "Steam", "CompatToolMapping") or {}
        entry = mapping.get(str(appid))
        if not (isinstance(entry, dict) and entry.get("name")):   # name "" means "use the default tool"
            entry = mapping.get("0")
        if isinstance(entry, dict) and entry.get("name"):
            return entry["name"]
    return None


def launch_options_on_disk(appid: str, home: Path | None = None) -> str | None:
    """LaunchOptions from localconfig.vdf (the newest user). Only a fallback: Steam rewrites this file on exit, so the
    frontend reads the live value from the Steam client instead."""
    best = None
    for root in steam_roots(home):
        for cfg in (root / "userdata").glob("*/config/localconfig.vdf"):
            try:
                mtime = cfg.stat().st_mtime
            except OSError:
                continue
            apps = vdf.get_ci(vdf.load(cfg), "UserLocalConfigStore", "Software", "Valve", "Steam", "apps") or {}
            app = apps.get(str(appid))
            if isinstance(app, dict) and (best is None or mtime > best[0]):
                best = (mtime, app.get("LaunchOptions", ""))
    return best[1] if best else None
