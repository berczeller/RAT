"""ReShade for Proton games, managed from Game Mode: Linux-friendly install of the official ReShade binaries, the
official shader packages, per-game presets and a gamescope compositor mode.

How it works (studied from LetMeReShade by itsOwen / Cliffback, kevinlekiller's reshade-steam-proton and Reshadeck by
safijari; no code is copied):

* Binaries come straight from reshade.me at runtime ("do not share the binaries": RAT never bundles them). The setup
  .exe carries a ZIP with ReShade64.dll / ReShade32.dll; Python's zipfile reads it, 7-Zip is the fallback. The add-on
  build ("full add-on support") is the default because RenoDX needs it.
* d3dcompiler_47.dll (needed by ReShade's effect compiler under Wine) is taken from Mozilla's signed Firefox 62.0.3
  installer for the right architecture, checked against a known SHA-256, as winetricks does.
* Shader packages are only offered from ReShade's own package list (crosire/reshade-shaders, branch "list"), are
  downloaded from their GitHub repositories and extracted with path checks, keeping only shader / texture files.
* Per game, ReShade is copied next to the game's .exe as the proxy its graphics API loads (dxgi.dll for DX10-12,
  d3d9.dll, d3d8.dll, opengl32.dll). When an OptiScaler-based injector already owns dxgi.dll, ReShade becomes
  ReShade64.dll and OptiScaler loads it ([Plugins] LoadReshade=true), so both run.
* Compositor mode (Vulkan games, or no injection at all): one effect applied by gamescope itself through the
  GAMESCOPE_RESHADE_EFFECT root window property, like Reshadeck. No depth buffer, one effect at a time.
"""
from __future__ import annotations

import configparser
import os
import re
import shutil
import subprocess
import time
import zipfile
from pathlib import Path

from . import ini, liveinput, sources
from .jobs import Job
from .records import REC_DIR, read_extra, remove_extra, write_extra
from .util import (Paths, UserError, ci_path, clean_env, log, place_file, read_json, run_streaming, safe_rel, sha256_of, slug,
                   which, write_json)

# .ini files next to the exe that are never ReShade presets
NOT_PRESETS = {"reshade.ini", "optiscaler.ini", "fakenvapi.ini", "helixsr.ini", "nvngx.ini"}
PACKAGE_LIST_URL = "https://raw.githubusercontent.com/crosire/reshade-shaders/list/EffectPackages.ini"
RESHADE_SITE = "https://reshade.me"
SETUP_URL = "https://reshade.me/downloads/ReShade_Setup_{v}{addon}.exe"
PINNED = ["6.8.0", "6.7.3"]   # 6.7.3: reported stable on Steam Deck when 6.8.0 did not load for some
KNOWN_SHA256 = {
    "ReShade_Setup_6.8.0_Addon.exe": "afe4c8f13048306307983b8b3d41d5bf00a86820440b0e57dea10950e1176445",
    "ReShade_Setup_6.8.0.exe": "207aea16205fbf952bc8fe1879966672454cf04002e7ad34237c7990a5b3c0b4",
}
D3DCOMPILER = {
    "x64": ("https://download-installer.cdn.mozilla.net/pub/firefox/releases/62.0.3/win64/ach/Firefox%20Setup%2062.0.3.exe",
            "721977f36c008af2b637aedd3f1b529f3cfed6feb10f68ebe17469acb1934986"),
    "x86": ("https://download-installer.cdn.mozilla.net/pub/firefox/releases/62.0.3/win32/ach/Firefox%20Setup%2062.0.3.exe",
            "d6edb4ff0a713f417ebd19baedfe07527c6e45e84a6c73ed8c66a33377cc0aca"),
}
PROXY_FOR_API = {"DX12": "dxgi.dll", "DX11": "dxgi.dll", "DX10": "dxgi.dll", "DXGI": "dxgi.dll", "DX9": "d3d9.dll",
                 "DX8": "d3d8.dll", "OpenGL": "opengl32.dll"}
PROXY_CHOICES = ["auto", "dxgi.dll", "d3d11.dll", "d3d9.dll", "d3d8.dll", "opengl32.dll"]
SHADER_EXT = {".fx", ".fxh", ".h", ".png", ".jpg", ".jpeg", ".dds", ".bmp", ".tga", ".cube"}
CHAIN_KINDS = ("optiscaler", "d4r")
GAMESCOPE_DIR = ".local/share/gamescope/reshade"


def _wine_path(p: Path) -> str:
    return "Z:" + str(p).replace("/", "\\")


# --- shader parameters ------------------------------------------------------------------------------------------------

_UNIFORM = re.compile(r"\buniform\s+(bool|int|uint|float)([234])?\s+([A-Za-z_]\w*)\s*(<(.*?)>)?\s*(=\s*([^;]*))?;", re.S)
_ANN = re.compile(r"\b(\w+)\s*=\s*(\"(?:[^\"\\]|\\.)*\"|[^;]+?)\s*;", re.S)


def _strip_comments(text: str) -> str:
    """Removes // and /* */ comments outside string literals (ui_items strings contain \0 escapes)."""
    out, i, n, q = [], 0, len(text), False
    while i < n:
        c = text[i]
        if q:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if c == '"':
                q = False
            i += 1
            continue
        if c == '"':
            q = True
            out.append(c)
            i += 1
        elif text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _num(v: str | None, default: float | None = None) -> float | None:
    try:
        return float(str(v).strip().rstrip("fF"))
    except (TypeError, ValueError):
        return default


def _values(init: str | None, comps: int, kind: str) -> list[float]:
    """'0.5' / 'float3(1.0, 0.5, 0)' / 'true' / '{1, 2}' -> [values]."""
    if init is None:
        return [0.0] * comps
    body = init.strip()
    m = re.match(r"^[A-Za-z_]\w*\s*\((.*)\)$", body, re.S) or re.match(r"^\{(.*)\}$", body, re.S)
    parts = [x.strip() for x in (m.group(1) if m else body).split(",")]
    vals = []
    for x in parts:
        if x.lower() in ("true", "false"):
            vals.append(1.0 if x.lower() == "true" else 0.0)
        else:
            vals.append(_num(x, 0.0))
    if len(vals) == 1 and comps > 1:
        vals = vals * comps
    return (vals + [0.0] * comps)[:comps]


def parse_uniforms(text: str) -> list[dict]:
    """The user-tweakable uniforms of an effect file, with their UI annotations (label, range, step, combo items)."""
    out: list[dict] = []
    seen: set[str] = set()
    for m in _UNIFORM.finditer(_strip_comments(text)):
        base, comps, name, _, ann_text, _, init = m.groups()
        ann: dict[str, str] = {}
        for a in _ANN.finditer(ann_text or ""):
            v = a.group(2).strip()
            ann[a.group(1).lower()] = v[1:-1] if v.startswith('"') and v.endswith('"') else v
        if name in seen or "source" in ann or ann.get("hidden", "").lower() == "true" or ann.get("ui_type") == "none":
            continue
        seen.add(name)
        n = int(comps or 1)
        kind = "bool" if base == "bool" else ("int" if base in ("int", "uint") else "float")
        vals = _values(init, n, kind)
        items = None
        if "ui_items" in ann:
            items = [x for x in ann["ui_items"].replace("\\0", "\0").split("\0") if x != ""]
        lo = _num(ann.get("ui_min"))
        hi = _num(ann.get("ui_max"))
        if ann.get("ui_type") == "color":
            lo, hi = (0.0 if lo is None else lo), (1.0 if hi is None else hi)
        if items:
            lo, hi = 0.0, float(max(0, len(items) - 1))
        if kind == "bool":
            lo, hi = 0.0, 1.0
        if lo is None:
            lo = min(0.0, min(vals))
        if hi is None:
            hi = max(1.0 if kind == "float" else 10.0, max(vals) * 2 if max(vals) > 0 else 1.0)
        step = _num(ann.get("ui_step")) or (1.0 if kind in ("int", "bool") else round((hi - lo) / 100, 6) or 0.01)
        out.append({"name": name, "kind": kind, "components": n, "default": vals, "min": lo, "max": hi,
                    "step": step, "label": ann.get("ui_label") or name, "tooltip": ann.get("ui_tooltip", ""),
                    "category": ann.get("ui_category", ""), "items": items})
    return out


def _fmt(kind: str, vals: list[float]) -> str:
    if kind == "bool":
        return ",".join("1" if v else "0" for v in vals)
    if kind == "int":
        return ",".join(str(int(round(v))) for v in vals)
    return ",".join(f"{v:.6f}" for v in vals)


def _parse_stored(kind: str, raw: str, n: int) -> list[float] | None:
    try:
        vals = [float(x) for x in raw.split(",") if x.strip() != ""]
    except ValueError:
        return None
    return (vals + vals[-1:] * n)[:n] if vals else None


class _ParamsMixin:
    """Shader parameter editing on top of ReShade (kept apart for readability)."""

    def _fx_file(self, file: str) -> Path | None:
        file = Path(file).name
        for p in self.merged_shaders().rglob("*.fx"):
            if p.name.lower() == file.lower():
                return p
        return None

    def _defaults_file(self) -> Path:
        return self.root() / "param-defaults.json"

    def _active_preset(self, key: str) -> tuple[Path | None, str | None]:
        g = self.m.game(key)
        exe = self.game_dir(g)
        pr = self.presets(key)
        if not exe or not pr.get("active"):
            return None, None
        return exe / pr["active"], pr["active"]

    def effect_params(self, key: str, file: str) -> dict:
        fx = self._fx_file(file)
        if not fx:
            raise UserError(f"{file} is not in the installed shaders")
        params = parse_uniforms(fx.read_text(encoding="utf-8", errors="replace"))
        preset, name = self._active_preset(key)
        doc = ini.read(preset) if preset and preset.exists() else ini.IniDoc("")
        saved = read_json(self._defaults_file(), {}).get(fx.name, {})
        for p in params:
            raw = doc.get(fx.name, p["name"])
            cur = _parse_stored(p["kind"], raw, p["components"]) if raw is not None else None
            p["value"] = cur or p["default"]
            p["changed"] = cur is not None and cur != p["default"]
            p["saved_default"] = saved.get(p["name"])
        return {"file": fx.name, "preset": name, "params": params, "has_saved_default": bool(saved)}

    def set_param(self, key: str, file: str, name: str, values: list) -> dict:
        preset, _ = self._active_preset(key)
        if not preset:
            raise UserError("Pick or create a preset first")
        info = self.effect_params(key, file)
        p = next((x for x in info["params"] if x["name"] == name), None)
        if not p:
            raise UserError(f"{name} is not a parameter of {file}")
        try:
            vals = [float(v) for v in (values if isinstance(values, list) else [values])]
        except (TypeError, ValueError):
            raise UserError("A number is expected")
        vals = (vals + vals[-1:] * p["components"])[:p["components"]]
        vals = [min(p["max"], max(p["min"], v)) if p["kind"] != "bool" else (1.0 if v else 0.0) for v in vals]
        ini.edit_file(preset, [(info["file"], name, _fmt(p["kind"], vals))])
        return dict(self.effect_params(key, file), applied=self.live_apply(key))

    def save_param_defaults(self, key: str, file: str) -> dict:
        info = self.effect_params(key, file)
        allp = read_json(self._defaults_file(), {})
        allp[info["file"]] = {p["name"]: p["value"] for p in info["params"]}
        write_json(self._defaults_file(), allp)
        return self.effect_params(key, file)

    def restore_param_defaults(self, key: str, file: str) -> dict:
        """Your saved default when there is one, else the shader author's defaults."""
        preset, _ = self._active_preset(key)
        if not preset:
            raise UserError("Pick or create a preset first")
        info = self.effect_params(key, file)
        saved = read_json(self._defaults_file(), {}).get(info["file"])
        edits = []
        for p in info["params"]:
            vals = saved.get(p["name"]) if saved and p["name"] in saved else p["default"]
            edits.append((info["file"], p["name"], _fmt(p["kind"], vals)))
        if edits:
            ini.edit_file(preset, edits)
        return dict(self.effect_params(key, file), applied=self.live_apply(key))


class ReShade(_ParamsMixin):
    def __init__(self, mgr):
        self.m = mgr
        self._fx_cache: tuple[float, list[dict]] | None = None

    # --- folders ------------------------------------------------------------------------------------------------

    @staticmethod
    def root() -> Path:
        return Paths.data("reshade")

    def shaders_root(self) -> Path:
        return Paths.data("reshade", "shaders")

    def merged_shaders(self) -> Path:
        p = self.shaders_root() / "reshade-shaders" / "Shaders"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def merged_textures(self) -> Path:
        p = self.shaders_root() / "reshade-shaders" / "Textures"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def presets_library(self) -> Path:
        return Paths.data("reshade", "presets")

    # --- binaries -----------------------------------------------------------------------------------------------

    def latest_version(self, force: bool = False) -> str:
        """The newest version linked from reshade.me (cached), else the newest pinned one."""
        try:
            html = sources.cached_text("reshade-site.html", RESHADE_SITE, ttl=12 * 3600, force=force)
            m = re.search(r"/downloads/ReShade_Setup_(\d+\.\d+\.\d+)(?:_Addon)?\.exe", html)
            if m:
                return m.group(1)
        except UserError as e:
            log.info("reshade.me: %s", e)
        return PINNED[0]

    def wanted_version(self) -> str:
        v = str(self.m.pref("reshade_version") or "latest")
        return self.latest_version() if v == "latest" else v

    def binary_dir(self, version: str, addon: bool) -> Path:
        return self.root() / "bin" / f"{version}{'_addon' if addon else ''}"

    def installed_binaries(self) -> list[str]:
        d = self.root() / "bin"
        try:
            return sorted(p.name for p in d.iterdir() if (p / "ReShade64.dll").exists())
        except OSError:
            return []

    def ensure_binary(self, version: str, addon: bool, job: Job | None = None, lo: float = 0.0,
                      hi: float = 0.3) -> Path:
        d = self.binary_dir(version, addon)
        if (d / "ReShade64.dll").exists() and (d / "ReShade32.dll").exists():
            return d
        if not re.fullmatch(r"\d+\.\d+\.\d+", version):
            raise UserError(f"Unknown ReShade version {version}")
        name = f"ReShade_Setup_{version}{'_Addon' if addon else ''}.exe"
        url = SETUP_URL.format(v=version, addon="_Addon" if addon else "")
        dest = Paths.downloads() / name
        if job:
            job.stage("download", f"Downloading ReShade {version}{' (add-on build)' if addon else ''}...", lo)
        sources.download(url, dest, progress=(lambda f, t: job.sub(f, lo, hi, f"Downloading {name}: {t}")) if job
                         else None, should_cancel=job.cancelled if job else None)
        sha = KNOWN_SHA256.get(name)
        if sha:
            if job:
                job.stage("verify", "Verifying ReShade's checksum...")
            if sha256_of(dest) != sha:
                dest.unlink(missing_ok=True)
                raise UserError("ReShade's checksum does not match the published one. Nothing was installed.")
        if job:
            job.stage("extract", "Extracting ReShade...")
        tmp = d.with_name(d.name + ".new")
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        want = ("ReShade64.dll", "ReShade32.dll", "ReShade64.json", "ReShade32.json")
        try:
            with zipfile.ZipFile(dest) as z:   # the setup .exe ends with a ZIP archive
                for n in z.namelist():
                    if Path(n).name in want:
                        (tmp / Path(n).name).write_bytes(z.read(n))
        except zipfile.BadZipFile:
            tool = sources.seven_zip()
            if not tool:
                raise UserError("Could not open ReShade's setup file (7-Zip missing)")
            code, tail = run_streaming([tool, "e", "-y", f"-o{tmp}", str(dest)] + list(want), timeout=300)
            if code != 0:
                raise UserError("Could not extract ReShade: " + (tail[-1] if tail else f"7-Zip exit {code}"))
        if not (tmp / "ReShade64.dll").exists():
            shutil.rmtree(tmp, ignore_errors=True)
            raise UserError("The ReShade download does not contain ReShade64.dll")
        shutil.rmtree(d, ignore_errors=True)
        tmp.rename(d)
        dest.unlink(missing_ok=True)
        return d

    def ensure_d3dcompiler(self, arch: str, job: Job | None = None) -> Path | None:
        """d3dcompiler_47.dll for the game's architecture (from Mozilla's Firefox 62.0.3 installer, hash-checked)."""
        arch = "x86" if arch == "x86" else "x64"
        out = self.root() / f"d3dcompiler_47.{arch}.dll"
        if out.exists():
            return out
        url, sha = D3DCOMPILER[arch]
        dest = Paths.downloads() / f"firefox-62.0.3-{arch}.exe"
        if job:
            job.update(f"Downloading d3dcompiler_47.dll ({arch}) from Mozilla's Firefox installer...")
        try:
            sources.download(url, dest, should_cancel=job.cancelled if job else None)
            if sha256_of(dest) != sha:
                raise UserError("Firefox installer checksum mismatch")
            tool = sources.seven_zip()
            if not tool:
                raise UserError("7-Zip is missing")
            tmp = self.root() / f"d3dc-{arch}"
            shutil.rmtree(tmp, ignore_errors=True)
            code, tail = run_streaming([tool, "e", "-y", f"-o{tmp}", str(dest), "d3dcompiler_47.dll", "-r"], timeout=300)
            got = next(iter(tmp.rglob("d3dcompiler_47.dll")), None) if tmp.exists() else None
            if code != 0 or not got:
                raise UserError("d3dcompiler_47.dll was not found in the installer")
            shutil.copy2(got, out)
            shutil.rmtree(tmp, ignore_errors=True)
            return out
        except UserError as e:   # ReShade still works for many games with Wine's own compiler
            log.warning("d3dcompiler_47: %s", e)
            if job:
                job.log(f"d3dcompiler_47.dll skipped: {e}")
            return None
        finally:
            dest.unlink(missing_ok=True)

    # --- shader packages ----------------------------------------------------------------------------------------

    def _state_file(self) -> Path:
        return self.root() / "packages.json"

    def packages(self, force: bool = False) -> list[dict]:
        """ReShade's official package list with RAT's install state."""
        installed = read_json(self._state_file(), {})
        try:
            text = sources.cached_text("EffectPackages.ini", PACKAGE_LIST_URL, ttl=24 * 3600, force=force)
        except UserError:
            text = ""
        cp = configparser.ConfigParser(interpolation=None, strict=False)
        cp.optionxform = str
        try:
            cp.read_string(text)
        except configparser.Error:
            pass
        out = []
        for sec in cp.sections():
            p = cp[sec]
            url = p.get("DownloadUrl", "")
            if not re.match(r"^https://github\.com/[\w.-]+/[\w.-]+/archive/[\w./-]+\.zip$", url):
                continue   # only GitHub archives from the official list
            repo = p.get("RepositoryUrl", url)
            key = slug(re.sub(r"^https://github\.com/", "", repo), 80)
            out.append({"key": key, "name": p.get("PackageName", key), "description": p.get("PackageDescription", ""),
                        "url": url, "repo": repo, "install_path": p.get("InstallPath", ".\\reshade-shaders\\Shaders"),
                        "texture_path": p.get("TextureInstallPath", ".\\reshade-shaders\\Textures"),
                        "effects": [e for e in p.get("EffectFiles", "").split(",") if e],
                        "deny": [e for e in p.get("DenyEffectFiles", "").split(",") if e],
                        "required": p.get("Required", "0") == "1",
                        "installed": key in installed, "installed_at": installed.get(key, {}).get("installed_at")})
        for key, st in installed.items():   # installed earlier, gone from the list since
            if not any(x["key"] == key for x in out):
                out.append({"key": key, "name": st.get("name", key), "description": "No longer in ReShade's list",
                            "url": st.get("url", ""), "repo": "", "install_path": "", "texture_path": "",
                            "effects": [], "deny": [], "required": False, "installed": True,
                            "installed_at": st.get("installed_at")})
        return sorted(out, key=lambda x: (not x["required"], x["name"].lower()))

    def _target(self, rel: str) -> Path:
        clean = safe_rel(rel)
        if clean is None:
            raise UserError("Unsafe install path in the package list")
        p = (self.shaders_root() / clean).resolve()
        if not str(p).startswith(str(self.shaders_root().resolve())):
            raise UserError("Unsafe install path in the package list")
        return p

    def install_package(self, key: str, job: Job, lo: float = 0.0, hi: float = 1.0) -> int:
        pk = next((p for p in self.packages() if p["key"] == key), None)
        if not pk or not pk["url"]:
            raise UserError("Unknown shader package")
        name = f"shaders-{key}.zip"
        dest = Paths.downloads() / name
        job.stage("download", f"Downloading {pk['name']}...", lo)
        sources.download(pk["url"], dest, progress=lambda f, t: job.sub(f, lo, lo + (hi - lo) * 0.7,
                                                                        f"Downloading {pk['name']}: {t}"),
                         should_cancel=job.cancelled)
        job.stage("extract", f"Installing {pk['name']}...", lo + (hi - lo) * 0.75)
        shaders_to = self._target(pk["install_path"])
        textures_to = self._target(pk["texture_path"])
        deny = {d.lower() for d in pk["deny"]}
        files: list[str] = []
        with zipfile.ZipFile(dest) as z:
            names = [n for n in z.namelist() if not n.endswith("/")]
            # the shallowest "Shaders" / "Textures" folders of the archive, as ReShade's installer does
            def base_of(label: str) -> str | None:
                cands = [n for n in names if f"/{label.lower()}/" in "/" + n.lower()]
                if not cands:
                    return None
                best = min(cands, key=lambda n: n.lower().split("/").index(label.lower()))
                parts = best.split("/")
                i = [x.lower() for x in parts].index(label.lower())
                return "/".join(parts[:i + 1]) + "/"
            sbase, tbase = base_of("Shaders"), base_of("Textures")
            for n in names:
                ext = Path(n).suffix.lower()
                if ext not in SHADER_EXT:
                    continue
                if sbase and n.startswith(sbase):
                    rel, to = n[len(sbase):], shaders_to
                elif tbase and n.startswith(tbase):
                    rel, to = n[len(tbase):], textures_to
                elif not sbase and ext in (".fx", ".fxh"):
                    rel, to = Path(n).name, shaders_to
                else:
                    continue
                if Path(rel).name.lower() in deny:
                    continue
                target = (to / rel).resolve()
                if not str(target).startswith(str(self.shaders_root().resolve()) + os.sep):
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(z.read(n))
                files.append(target.relative_to(self.shaders_root()).as_posix())
        dest.unlink(missing_ok=True)
        st = read_json(self._state_file(), {})
        st[key] = {"name": pk["name"], "url": pk["url"], "files": sorted(set(files)), "installed_at": time.time()}
        write_json(self._state_file(), st)
        self._fx_cache = None
        job.update(f"{pk['name']}: {len(files)} files", hi)
        return len(files)

    def start_install_packages(self, keys: list[str]) -> dict:
        keys = [k for k in keys if k]

        def run(job: Job):
            n = 0
            for i, k in enumerate(keys):
                n += self.install_package(k, job, i / len(keys), (i + 1) / len(keys))
            return {"files": n}
        return self.m.jobs.start("Shader packages", run, stages=["download", "extract"])

    def remove_package(self, key: str) -> None:
        st = read_json(self._state_file(), {})
        mine = st.pop(key, None)
        if not mine:
            return
        others = {f for k, v in st.items() for f in v.get("files", [])}
        for f in mine.get("files", []):
            if f in others:
                continue   # shared headers (ReShade.fxh...) stay while another package uses them
            p = (self.shaders_root() / f)
            if p.is_file() and str(p.resolve()).startswith(str(self.shaders_root().resolve())):
                p.unlink()
        write_json(self._state_file(), st)
        self._fx_cache = None

    def effects(self) -> list[dict]:
        """Techniques in the installed shaders: [{id: 'Name@File.fx', technique, file, label, folder}]."""
        root = self.merged_shaders()
        try:
            stamp = max((p.stat().st_mtime for p in root.rglob("*.fx")), default=0.0)
        except OSError:
            stamp = 0.0
        if self._fx_cache and self._fx_cache[0] == stamp:
            return self._fx_cache[1]
        out = []
        tech = re.compile(r"^\s*technique\s+([A-Za-z_]\w*)\s*(<[^>]*>)?", re.M)
        label = re.compile(r'ui_label\s*=\s*"([^"]+)"')
        for p in sorted(root.rglob("*.fx")):
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for m in tech.finditer(text):
                lab = label.search(m.group(2) or "")
                out.append({"id": f"{m.group(1)}@{p.name}", "technique": m.group(1), "file": p.name,
                            "folder": p.parent.relative_to(root).as_posix() if p.parent != root else "",
                            "label": lab.group(1) if lab else m.group(1)})
        out.sort(key=lambda e: e["label"].lower())
        self._fx_cache = (stamp, out)
        return out

    # --- per game -----------------------------------------------------------------------------------------------

    def game_dir(self, g: dict) -> Path | None:
        """Where ReShade (and RenoDX with it) goes: the injector's folder when one is installed (HelixSR Scaler's target
        included), else the game's executable folder. Unreal games always get the folder of the real executable
        (<Project>/Binaries/Win64 or WinGDK), never the bootstrapper in the install root, unless the user picked the
        executable folder by hand."""
        rec = self.m.record_for(g)
        if rec and rec.get("exe_dir") and Path(rec["exe_dir"]).is_dir():
            return Path(rec["exe_dir"])
        exe, _ = self.m.exe_dir(g)
        if self.m.settings()["overrides"].get(g["key"], {}).get("exe_dir"):
            return exe
        try:
            prof = self.m.game_profile(g)
        except Exception:   # never fatal: the plain executable folder still works for most games
            return exe
        b = prof.get("binaries")
        if prof.get("engine") == "unreal" and b and Path(b).is_dir() and (not exe or Path(b) != exe):
            return Path(b)
        return exe

    def stray_dirs(self, g: dict) -> list[Path]:
        """Folders that hold a ReShade / RenoDX record of this game but are not where they belong any more (an Unreal
        bootstrapper folder picked before, an injector that moved)."""
        want = self.game_dir(g)
        out: list[Path] = []
        exe, cands = self.m.exe_dir(g)
        for d in [exe, Path(g["folder"])] + [Path(c["dir"]) for c in cands]:
            if d and want and d != want and d not in out and (read_extra(d, "reshade") or read_extra(d, "renodx")):
                out.append(d)
        return out

    def settle(self, g: dict, job: Job | None = None) -> Path | None:
        """Moves ReShade and RenoDX from a stray folder to the folder the game loads them from. Returns that folder."""
        want = self.game_dir(g)
        for old in self.stray_dirs(g):
            rs_old = read_extra(old, "reshade")
            if rs_old and not self.record(want):
                self.relink(g, job, where=str(old))
            elif rs_old:   # the right folder has ReShade already: the stray copy is removed, the game's files go back
                p = old / (rs_old.get("proxy") or "")
                if rs_old.get("proxy") and p.is_file() and rs_old.get("proxy_sha") and sha256_of(p) == rs_old["proxy_sha"]:
                    p.unlink()
                for name, b in (rs_old.get("backups") or {}).items():
                    bp = old / b
                    if safe_rel(b) and bp.exists() and not (old / name).exists():
                        os.replace(bp, old / name)
                if rs_old.get("d3dcompiler"):
                    (old / "d3dcompiler_47.dll").unlink(missing_ok=True)
                remove_extra(old, "reshade")
            self.m.renodx.move(old, want, job)
            if job:
                job.log(f"Moved ReShade / RenoDX from {old} to {want} (where the game loads them)")
        return want

    def record(self, exe: Path | None) -> dict | None:
        return read_extra(exe, "reshade")

    def _desired_proxy(self, g: dict, rs: dict) -> str:
        rec = self.m.record_for(g)
        inj_proxy = ((rec or {}).get("proxy") or ("dxgi.dll" if (rec or {}).get("kind") in CHAIN_KINDS else "")).lower()
        chainable = bool(rec) and rec.get("active", True) and (
            rec.get("kind") in CHAIN_KINDS or (rec.get("kind") == "helixsr" and rec.get("route") == "scaler"))
        want = rs.get("proxy_pref") or "auto"
        api_proxy = PROXY_FOR_API.get(rs.get("api") or "", "dxgi.dll") if want == "auto" else want
        if chainable and inj_proxy == api_proxy.lower() and rs.get("arch", "x64") == "x64":
            return "ReShade64.dll"
        return api_proxy

    def detach(self, g: dict) -> dict | None:
        exe = self.game_dir(g)
        rs = self.record(exe)
        if not rs or not exe:
            return None
        p = exe / rs.get("proxy", "")
        if rs.get("proxy") and p.is_file() and rs.get("proxy_sha") and sha256_of(p) == rs["proxy_sha"]:
            p.unlink()
            # the game's own file of that name goes back right away: whatever takes the slot next (an injector) then
            # backs it up itself, instead of ReShade later restoring it over that injector's DLL
            bk = (rs.get("backups") or {}).get(rs["proxy"])
            if bk and safe_rel(bk) and (exe / bk).is_file() and not p.exists():
                os.replace(exe / bk, p)
                rs["backups"].pop(rs["proxy"], None)
        rs["detached"] = True
        write_extra(exe, "reshade", rs)
        return dict(rs, _dir=str(exe))

    def relink(self, g: dict, job: Job | None = None, where: str | None = None) -> None:
        """Puts ReShade's DLL under the name the current setup needs (see the module docstring). `where` is the folder
        ReShade was detached from; when the injector now lives elsewhere (HelixSR Scaler's target), ReShade's record
        moves with it."""
        exe = self.game_dir(g)
        rs = self.record(exe)
        if where and exe and Path(where) != exe and not rs:
            old_dir = Path(where)
            rs = self.record(old_dir)
            if rs:   # follow the game: remove ReShade from the old folder, re-create the setup in the new one
                p = old_dir / (rs.get("proxy") or "")
                if rs.get("proxy") and p.is_file() and rs.get("proxy_sha") and sha256_of(p) == rs["proxy_sha"]:
                    p.unlink()
                for name, b in (rs.get("backups") or {}).items():   # the old folder gets the game's own files back
                    bp = old_dir / b
                    if safe_rel(b) and bp.exists() and not (old_dir / name).exists():
                        os.replace(bp, old_dir / name)
                for f in ("ReShade.ini", "ReShadePreset.ini", "d3dcompiler_47.dll"):
                    src = old_dir / f
                    if src.exists() and not (exe / f).exists():
                        shutil.move(str(src), str(exe / f))
                    elif f == "d3dcompiler_47.dll" and src.exists() and rs.get("d3dcompiler"):
                        src.unlink()
                rs = {k: v for k, v in rs.items() if k not in ("backups", "proxy_sha", "detached")}
                remove_extra(old_dir, "reshade")
                write_extra(exe, "reshade", rs)
        if not rs or not exe:
            return
        old = rs.get("proxy")
        want = self._desired_proxy(g, rs)
        if old and old != want and not rs.get("detached"):
            p = exe / old
            if p.is_file() and rs.get("proxy_sha") and sha256_of(p) == rs["proxy_sha"]:
                p.unlink()
        dst = exe / want
        if not rs.get("enabled", True):   # switched off: no proxy in the game, everything else stays
            for n in {want, old or ""} - {""}:
                q = exe / n
                if q.is_file() and rs.get("proxy_sha") and sha256_of(q) == rs["proxy_sha"]:
                    q.unlink()
                bk = (rs.get("backups") or {}).get(n)   # the game's own file of that name goes back while off
                if bk and safe_rel(bk) and (exe / bk).is_file() and not q.exists():
                    os.replace(exe / bk, q)
                    rs["backups"].pop(n, None)
            rs.update(proxy=want, detached=False, mode="chain" if want.lower().startswith("reshade") else "direct")
            write_extra(exe, "reshade", rs)
            rec = self.m.record_for(g)
            if rec and rec.get("exe_dir"):
                self.m._apply_settings(rec, Path(rec["exe_dir"]))
            return
        src = self.binary_dir(rs["reshade_version"], rs.get("addon", True)) / (
            "ReShade32.dll" if rs.get("arch") == "x86" else "ReShade64.dll")
        if not src.exists():
            src = self.ensure_binary(rs["reshade_version"], rs.get("addon", True), job)
            src = src / ("ReShade32.dll" if rs.get("arch") == "x86" else "ReShade64.dll")
        if dst.is_symlink() and not dst.exists():
            dst.unlink()   # a dangling link another tool left: nothing to keep
        cur = ci_path(dst)   # the game's file may differ in case only (Wine loads it all the same)
        if cur.is_symlink() and not cur.exists():
            cur.unlink()
        elif cur.exists() and not (rs.get("proxy_sha") and sha256_of(cur) == rs["proxy_sha"]):
            b = exe / REC_DIR / "reshade-backup" / want
            if want.lower() != "reshade64.dll":
                # a backup already there is older than this file (a game update or verify put it back): the current
                # file is the one to restore later, so it replaces the stale backup instead of being overwritten
                b.parent.mkdir(parents=True, exist_ok=True)
                os.replace(cur, b)
                rs.setdefault("backups", {})[want] = str(b.relative_to(exe))
                if job:
                    job.log(f"Backed up the game's {cur.name}")
        place_file(src, dst)
        rs.update(proxy=want, proxy_sha=sha256_of(dst), detached=False,
                  mode="chain" if want.lower().startswith("reshade") else "direct")
        write_extra(exe, "reshade", rs)
        rec = self.m.record_for(g)
        if rec and rec.get("exe_dir"):
            self.m._apply_settings(rec, Path(rec["exe_dir"]))   # LoadReshade follows the mode
        if job:
            job.log(f"ReShade loads as {want}" + (" (through OptiScaler)" if rs["mode"] == "chain" else ""))

    def _ensure_ini(self, exe: Path, rs: dict) -> None:
        p = exe / "ReShade.ini"
        doc = ini.read(p) if p.exists() else ini.IniDoc("")
        have = doc.to_dict()
        sh = _wine_path(self.merged_shaders()) + "\\**"
        tx = _wine_path(self.merged_textures()) + "\\**"
        doc.set("GENERAL", "EffectSearchPaths", sh)
        doc.set("GENERAL", "TextureSearchPaths", tx)
        defaults = [("GENERAL", "PresetPath", ".\\ReShadePreset.ini"), ("GENERAL", "IntermediateCachePath",
                                                                        "C:\\users\\steamuser\\Temp\\ReShade"),
                    ("GENERAL", "PerformanceMode", "0"), ("INPUT", "KeyOverlay", "36,0,0,0"),
                    ("OVERLAY", "TutorialProgress", "4")]
        for sec, k, v in defaults:
            if k.lower() not in have.get(sec.lower(), {}):
                doc.set(sec, k, v)
        self._shortcut_into(doc)
        ini.write(p, doc)
        preset = exe / "ReShadePreset.ini"
        if not preset.exists():
            preset.write_text("Techniques=\nTechniqueSorting=\n", encoding="utf-8")
            rs.setdefault("created", []).append("ReShadePreset.ini")

    def start_install_game(self, key: str, addon: bool = True, proxy_pref: str = "auto", by: str = "user") -> dict:
        g = self.m.game(key)
        if self.m.is_running(g):
            raise UserError("Close the game first.")
        return self.m.jobs.start("Install ReShade", lambda job: self.install_game(g, job, addon, proxy_pref, by),
                                 game=key, stages=["fetch", "download", "verify", "extract", "install", "configure"])

    def install_game(self, g: dict, job: Job, addon: bool = True, proxy_pref: str = "auto", by: str = "user",
                     lo: float = 0.0, hi: float = 1.0) -> dict:
        if proxy_pref not in PROXY_CHOICES:   # it becomes a file name in the game folder
            raise UserError(f"Unknown ReShade DLL name: {proxy_pref}")
        exe = self.settle(g, job)
        if not exe:
            raise UserError("No game .exe found. Pick the executable folder under Settings.")
        prof = self.m.game_profile(g)
        api = prof.get("api")
        # RenoDX is a ReShade add-on: when the game has it (or a profile asked for it), ReShade must be the add-on build
        hdr_wanted = bool(self.m.renodx.record(exe)) or bool(self.m.renodx.pending(g["key"]))
        if hdr_wanted and not addon:
            addon = True
            job.log("Full add-on support kept on: RenoDX HDR needs it")
        if api == "Vulkan" and proxy_pref == "auto":
            raise UserError("This is a Vulkan game: ReShade's DLL proxy cannot load in it under Proton. Use Compositor "
                            "mode (gamescope) on the ReShade section instead.")
        span = hi - lo
        job.stage("fetch", "Checking ReShade's latest version...", lo)
        version = self.wanted_version()
        cur = self.record(exe) or {}
        if cur and (cur.get("addon", True) or not addon) and cur.get("reshade_version") == version:
            addon = bool(cur.get("addon", True))
        arch = prof.get("arch") or "x64"
        self.ensure_binary(version, addon, job, lo + span * 0.05, lo + span * 0.4)
        st = read_json(self._state_file(), {})
        std = next((p for p in self.packages() if p["required"]), None)
        if std and std["key"] not in st:
            self.install_package(std["key"], job, lo + span * 0.4, lo + span * 0.6)
        job.stage("install", f"Installing ReShade {version} into {g['name']}...", lo + span * 0.65)
        d3dc = self.ensure_d3dcompiler(arch, job)
        rs = dict(cur)
        rs.update(version=1, reshade_version=version, addon=addon, arch=arch, api=api, proxy_pref=proxy_pref,
                  installed=cur.get("installed") or time.time(), enabled=True)
        rs["requested_by"] = sorted(set(cur.get("requested_by", [])) | {by} | ({"hdr"} if hdr_wanted else set()))
        dc = ci_path(exe / "d3dcompiler_47.dll")   # a game's own D3DCompiler_47.dll counts too
        # the game's own compiler (a real file) stays; a link another ReShade tool left (often dangling) is replaced
        if d3dc and (dc.is_symlink() or not dc.exists()):
            try:
                if dc.is_symlink() and dc.name != "d3dcompiler_47.dll":
                    dc.unlink()   # placed under the lowercase name, the one uninstall removes
                place_file(d3dc, exe / "d3dcompiler_47.dll")
                rs["d3dcompiler"] = True
            except (OSError, UserError) as e:   # ReShade then uses Wine's built-in compiler
                job.log(f"d3dcompiler_47.dll not placed ({e}); ReShade falls back to Wine's built-in compiler")
                rs["d3dcompiler"] = False
        write_extra(exe, "reshade", rs)
        self.relink(g, job)
        job.stage("configure", "Writing ReShade.ini...", lo + span * 0.9)
        rs = self.record(exe) or rs
        self._ensure_ini(exe, rs)
        write_extra(exe, "reshade", rs)
        self.verify_link(g, job)
        self.m.invalidate()
        if by != "hdr":   # the other direction: RenoDX follows ReShade when the game has it or a profile asks for it
            hdr = self.m.renodx.ensure_linked(g, job, lo=hi - (hi - lo) * 0.08, hi=hi)
            if hdr:
                job.log(hdr)
        job.update(f"ReShade {version} ready in {g['name']}. Home opens its overlay in game.", hi)
        return {"launch": self.m.wanted_launch(self.m.record_for(g), g["key"])}

    def verify_link(self, g: dict, job: Job | None = None) -> bool:
        """True when ReShade's DLL really is in the game under the name its record says (a game update, file
        verification or another tool can remove it); relinks it otherwise."""
        exe = self.game_dir(g)
        rs = self.record(exe)
        if not rs or not exe or not rs.get("enabled", True):
            return False
        p = exe / (rs.get("proxy") or "")
        ok = bool(rs.get("proxy")) and p.is_file() and not rs.get("detached") and \
            (not rs.get("proxy_sha") or sha256_of(p) == rs["proxy_sha"])
        if not ok or rs.get("proxy") != self._desired_proxy(g, rs):
            self.relink(g, job)
            if job:
                job.log("ReShade's DLL was missing or out of date in the game: linked again")
        return True

    def uninstall_game(self, key: str, by: str = "user") -> dict:
        g = self.m.game(key)
        if self.m.is_running(g):
            raise UserError("Close the game first.")
        exe = self.game_dir(g)
        rs = self.record(exe)
        if not rs:
            return {"removed": False}
        left = [r for r in rs.get("requested_by", ["user"]) if r != by]
        if left:
            rs["requested_by"] = left
            write_extra(exe, "reshade", rs)
            return {"removed": False, "kept_for": left}
        p = exe / rs.get("proxy", "")
        if rs.get("proxy") and p.is_file() and rs.get("proxy_sha") and sha256_of(p) == rs["proxy_sha"]:
            p.unlink()
        if rs.get("d3dcompiler"):
            (exe / "d3dcompiler_47.dll").unlink(missing_ok=True)
        for f in ("ReShade.log", "ReShade.ini"):
            (exe / f).unlink(missing_ok=True)
        for f in rs.get("created", []):   # an empty preset RAT made; presets with effects are the user's work
            p = exe / Path(f).name
            try:
                if p.is_file() and not [x for x in self._top_keys(p.read_text(encoding="utf-8", errors="replace"))
                                        .get("Techniques", "").split(",") if x]:
                    p.unlink()
            except OSError:
                pass
        kept_backup = False
        for name, b in (rs.get("backups") or {}).items():
            bp = exe / b
            if not (safe_rel(b) and bp.exists()):
                continue
            if (exe / name).exists():
                # another tool (an injector) uses that name now: never overwrite it, and keep the original around
                log(f"ReShade uninstall: {name} is in use, the game's original stays in {b}")
                kept_backup = True
                continue
            os.replace(bp, exe / name)
        if not kept_backup:
            shutil.rmtree(exe / REC_DIR / "reshade-backup", ignore_errors=True)
        remove_extra(exe, "reshade")   # presets stay: they are the user's work
        rec = self.m.record_for(g)
        if rec and rec.get("exe_dir"):
            self.m._apply_settings(rec, Path(rec["exe_dir"]))
        self.m.invalidate()
        return {"removed": True}

    def launch(self, key: str) -> dict:
        try:
            g = self.m.game(key)
        except UserError:
            return {}
        rs = self.record(self.game_dir(g))
        if not rs or not rs.get("enabled", True):
            return {}
        dlls = {}
        if rs.get("mode") == "direct" and rs.get("proxy"):
            dlls[Path(rs["proxy"]).stem.lower()] = "n,b"
        if rs.get("d3dcompiler"):
            dlls["d3dcompiler_47"] = "n"
        return {"env": {}, "dlls": dlls}

    def set_enabled(self, key: str, on: bool) -> dict:
        """Master switch: off removes ReShade's DLL (and its launch override) but keeps ReShade.ini, presets and
        shaders, so switching it back on is instant. RenoDX needs ReShade, so HDR pauses with it."""
        g = self.m.game(key)
        if self.m.is_running(g):
            raise UserError("Close the game first.")
        exe = self.game_dir(g)
        rs = self.record(exe)
        if not rs:
            raise UserError("Install ReShade in this game first")
        before = dict(rs)
        rs["enabled"] = bool(on)
        write_extra(exe, "reshade", rs)
        try:
            self.relink(g)
        except (OSError, UserError) as e:   # e.g. offline and ReShade's binary is gone: keep the old state
            write_extra(exe, "reshade", before)
            self.m.invalidate()
            raise UserError(f"Could not switch ReShade {'on' if on else 'off'}: {e}")
        self.m.invalidate()
        return self.game_state(key)

    # --- presets ------------------------------------------------------------------------------------------------

    @staticmethod
    def _top_keys(text: str) -> dict[str, str]:
        out = {}
        for line in text.splitlines():
            s = line.strip()
            if s.startswith("["):
                break
            if "=" in s and not s.startswith((";", "#")):
                k, v = s.split("=", 1)
                out[k.strip()] = v.strip()
        return out

    @staticmethod
    def _set_top_keys(text: str, values: dict[str, str]) -> str:
        lines = text.splitlines()
        first_sec = next((i for i, l in enumerate(lines) if l.strip().startswith("[")), len(lines))
        done = set()
        for i in range(first_sec):
            s = lines[i].strip()
            if "=" in s and not s.startswith((";", "#")):
                k = s.split("=", 1)[0].strip()
                if k in values:
                    lines[i] = f"{k}={values[k]}"
                    done.add(k)
        add = [f"{k}={v}" for k, v in values.items() if k not in done]
        lines[first_sec:first_sec] = add
        return "\n".join(lines) + "\n"

    def _is_preset(self, p: Path) -> bool:
        if p.suffix.lower() != ".ini" or p.name.lower() in NOT_PRESETS:
            return False
        try:
            head = p.read_text(encoding="utf-8", errors="replace")[:4096]
        except OSError:
            return False
        return "techniques=" in head.lower() or "preset" in p.name.lower()

    def presets(self, key: str) -> dict:
        g = self.m.game(key)
        exe = self.game_dir(g)
        active = None
        game: list[dict] = []
        if exe:
            p = exe / "ReShade.ini"
            if p.exists():
                v = ini.read(p).get("GENERAL", "PresetPath") or ""
                active = Path(v.replace("\\", "/")).name or None
            try:
                for f in sorted(exe.iterdir()):
                    if f.is_file() and self._is_preset(f):
                        t = self._top_keys(f.read_text(encoding="utf-8", errors="replace"))
                        n = len([x for x in t.get("Techniques", "").split(",") if x])
                        game.append({"name": f.name, "effects": n, "active": f.name == active})
            except OSError:
                pass
        lib = []
        for f in sorted(self.presets_library().glob("*.ini")):
            t = self._top_keys(f.read_text(encoding="utf-8", errors="replace"))
            lib.append({"name": f.name, "effects": len([x for x in t.get("Techniques", "").split(",") if x])})
        return {"active": active, "game": game, "library": lib, "exports": str(Paths.user_home / "RAT" / "presets")}

    def _preset_path(self, exe: Path, name: str) -> Path:
        name = Path(name).name
        if not name.lower().endswith(".ini") or name.lower() == "reshade.ini":
            raise UserError("Preset names end with .ini")
        p = exe / name
        # never delete or overwrite an injector's or the game's own .ini through the preset actions
        if name.lower() in NOT_PRESETS or (p.is_file() and p.stat().st_size > 0 and not self._is_preset(p)):
            raise UserError(f"{name} is not a ReShade preset")
        return p

    # --- live apply (see liveinput.py) ---------------------------------------------------------------------------

    @staticmethod
    def _shortcut_into(doc: ini.IniDoc) -> None:
        """Binds F24 as a ReShade preset shortcut to the active preset (kept next to any shortcuts of the user's)."""
        path = doc.get("GENERAL", "PresetPath") or ".\\ReShadePreset.ini"
        keys = [x.strip() for x in (doc.get("GENERAL", "PresetShortcutKeys") or "").split(",") if x.strip()]
        raw_paths = doc.get("GENERAL", "PresetShortcutPaths") or ""
        paths = [x.strip() for x in raw_paths.split(",")] if raw_paths.strip() else []
        groups = [keys[i:i + 4] for i in range(0, len(keys) - len(keys) % 4, 4)]
        idx = next((i for i, gk in enumerate(groups) if gk and gk[0] == str(liveinput.VK_F24)), None)
        if idx is None:
            groups.append([str(liveinput.VK_F24), "0", "0", "0"])
            paths = (paths + [""] * len(groups))[:len(groups) - 1] + [path]
        else:
            paths = (paths + [""] * len(groups))[:len(groups)]
            paths[idx] = path
        doc.set("GENERAL", "PresetShortcutKeys", ",".join(",".join(gk) for gk in groups))
        doc.set("GENERAL", "PresetShortcutPaths", ",".join(paths))

    def _set_preset_path(self, exe: Path, name: str) -> None:
        p = exe / "ReShade.ini"
        doc = ini.read(p) if p.exists() else ini.IniDoc("")
        doc.set("GENERAL", "PresetPath", ".\\" + name)
        self._shortcut_into(doc)
        ini.write(p, doc)

    def live_apply(self, key: str) -> str | None:
        """After a preset edit: 'live' when ReShade was told to re-read it in the running game, 'next' when it applies
        at the next start, None when the game is not running."""
        g = self.m.game(key)
        if not self.m.is_running(g):
            return None
        exe = self.game_dir(g)
        rs = self.record(exe) or {}
        if not rs.get("enabled", True) or rs.get("mode") == "compositor":
            return "next"
        cur = (ini.read(exe / "ReShade.ini").get("GENERAL", "PresetPath") or "") if exe else ""
        if rs.get("live_path") and cur and rs["live_path"].lower() != cur.lower():
            return "next"   # another preset was activated since the game started: ReShade still has the old one
        return "live" if liveinput.tap_f24() else "next"

    def preset_action(self, key: str, action: str, name: str = "", arg: str = "") -> dict:
        g = self.m.game(key)
        exe = self.game_dir(g)
        if not exe or not self.record(exe):
            raise UserError("Install ReShade in this game first")
        if action == "activate":
            p = self._preset_path(exe, name)
            if not p.exists():
                raise UserError("Preset not found")
            self._set_preset_path(exe, p.name)
        elif action == "new":
            p = self._preset_path(exe, (slug(name or "Preset", 40) or "preset") + ".ini")
            if p.exists():
                raise UserError("A preset with that name exists")
            p.write_text("Techniques=\nTechniqueSorting=\n", encoding="utf-8")
            self._set_preset_path(exe, p.name)
        elif action == "duplicate":
            p = self._preset_path(exe, name)
            n = p.with_name(p.stem + "-copy.ini")
            i = 2
            while n.exists():
                n = p.with_name(f"{p.stem}-copy{i}.ini")
                i += 1
            shutil.copy2(p, n)
        elif action == "delete":
            p = self._preset_path(exe, name)
            p.unlink(missing_ok=True)
        elif action == "save_library":
            p = self._preset_path(exe, name)
            shutil.copy2(p, self.presets_library() / f"{slug(g['name'], 30)}-{p.name}")
        elif action == "from_library":
            src = self.presets_library() / Path(name).name
            if not src.is_file():
                raise UserError("Preset not found in the library")
            dst = self._preset_path(exe, src.name)   # refuses to overwrite a non-preset .ini
            shutil.copy2(src, dst)
            self._set_preset_path(exe, dst.name)
        elif action == "delete_library":
            (self.presets_library() / Path(name).name).unlink(missing_ok=True)
        elif action == "export":
            p = self._preset_path(exe, name)
            d = Paths.exports() / "presets"
            d.mkdir(parents=True, exist_ok=True)
            out = d / f"{slug(g['name'], 30)}-{p.name}"
            shutil.copy2(p, out)
            return dict(self.presets(key), exported=str(out))
        elif action == "import":
            src = Path(arg)
            if not src.is_file() or src.suffix.lower() != ".ini" or src.stat().st_size > 2 << 20:
                raise UserError("Pick a ReShade preset (.ini)")
            if src.name.lower() == "reshade.ini":
                raise UserError("That is a ReShade.ini, not a preset")
            dst = self._preset_path(exe, src.name)   # refuses to overwrite a non-preset .ini
            shutil.copy2(src, dst)
        else:
            raise UserError("Unknown preset action")
        return self.presets(key)

    def preset_effects(self, key: str) -> dict:
        g = self.m.game(key)
        exe = self.game_dir(g)
        pr = self.presets(key)
        on: list[str] = []
        if exe and pr["active"] and (exe / pr["active"]).exists():
            t = self._top_keys((exe / pr["active"]).read_text(encoding="utf-8", errors="replace"))
            on = [x for x in t.get("Techniques", "").split(",") if x]
        fx = self.effects()
        return {"preset": pr["active"], "enabled": on, "effects": fx}

    def toggle_effect(self, key: str, effect_id: str, enabled: bool) -> dict:
        g = self.m.game(key)
        exe = self.game_dir(g)
        pr = self.presets(key)
        if not exe or not pr["active"]:
            raise UserError("Pick or create a preset first")
        p = exe / pr["active"]
        text = p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""
        t = self._top_keys(text)
        tech = [x for x in t.get("Techniques", "").split(",") if x]
        sort = [x for x in t.get("TechniqueSorting", "").split(",") if x]
        if enabled and effect_id not in tech:
            tech.append(effect_id)
        if not enabled:
            tech = [x for x in tech if x != effect_id]
        if effect_id not in sort:
            sort.append(effect_id)
        p.write_text(self._set_top_keys(text, {"Techniques": ",".join(tech), "TechniqueSorting": ",".join(sort)}),
                     encoding="utf-8")
        return dict(self.preset_effects(key), applied=self.live_apply(key))

    # --- compositor (gamescope) mode ----------------------------------------------------------------------------

    def _xprop(self, args: list[str]) -> bool:
        xprop = which("xprop")
        if not xprop:
            raise UserError("xprop was not found (it ships with SteamOS)")
        r = subprocess.run([xprop, "-root"] + args, env=clean_env({"DISPLAY": ":0"}), capture_output=True, timeout=10)
        return r.returncode == 0

    def _sync_gamescope(self) -> Path:
        dst = Paths.user_home / GAMESCOPE_DIR
        for sub, src in (("Shaders", self.merged_shaders()), ("Textures", self.merged_textures())):
            shutil.copytree(src, dst / sub, dirs_exist_ok=True)
        return dst

    def compositor(self, key: str) -> dict:
        o = self.m.game_overrides(key).get("compositor") or {}
        return {"effect": o.get("effect"), "effects": [e for e in self.effects()]}

    def set_compositor(self, key: str, effect: str | None, apply_now: bool = False) -> dict:
        if effect:
            fx = next((e for e in self.effects() if e["id"] == effect), None)
            if not fx:
                raise UserError("Unknown effect")
        self.m._update_settings(lambda s: s["overrides"].setdefault(key, {}).__setitem__(
            "compositor", {"effect": effect} if effect else {}))
        if apply_now:
            g = self.m.game(key)
            if self.m.is_running(g):
                self.session_start(key, snapshot=False) if effect else self.session_stop(key)
        return self.compositor(key)

    def _effect_path(self, effect_id: str) -> str | None:
        fx = next((e for e in self.effects() if e["id"] == effect_id), None)
        if not fx:
            return None
        return f"{fx['folder']}/{fx['file']}" if fx["folder"] else fx["file"]

    def session_start(self, key: str, snapshot: bool = True) -> None:
        g = self.m.game(key)
        exe = self.game_dir(g)
        rs = self.record(exe)
        if snapshot and rs and exe and (exe / "ReShade.ini").exists():   # the preset ReShade loads now: live target
            doc = ini.read(exe / "ReShade.ini")
            before = doc.text()
            self._shortcut_into(doc)   # bound to the preset this start loads
            if doc.text() != before:
                ini.write(exe / "ReShade.ini", doc)
            rs["live_path"] = doc.get("GENERAL", "PresetPath") or ""
            write_extra(exe, "reshade", rs)
        eff = (self.m.game_overrides(key).get("compositor") or {}).get("effect")
        if not eff:
            return
        rel = self._effect_path(eff)
        if not rel:
            return
        self._sync_gamescope()
        self._xprop(["-f", "GAMESCOPE_RESHADE_EFFECT", "8u", "-set", "GAMESCOPE_RESHADE_EFFECT", rel])

    def session_stop(self, key: str) -> None:
        if (self.m.game_overrides(key).get("compositor") or {}).get("effect"):
            self._xprop(["-remove", "GAMESCOPE_RESHADE_EFFECT"])

    # --- state for the tab and the HUD --------------------------------------------------------------------------

    def game_state(self, key: str) -> dict:
        g = self.m.game(key)
        exe = self.game_dir(g)
        rs = self.record(exe) or None
        prof = self.m.game_profile(g)
        st = read_json(self._state_file(), {})
        supported = prof.get("api") != "Vulkan"
        return {"installed": bool(rs), "record": rs, "api": prof.get("api"), "arch": prof.get("arch"),
                "supported": supported,
                "reason": None if supported else "Vulkan games cannot load ReShade's DLL proxy under Proton. "
                                                 "Use Compositor mode instead.",
                "versions": {"latest": self.latest_version(), "pinned": PINNED, "installed": self.installed_binaries(),
                             "wanted": self.m.pref("reshade_version")},
                "packages_installed": len(st), "proxy_choices": PROXY_CHOICES,
                "compositor": self.compositor(key)["effect"],
                "running": self.m.is_running(g), "runtime": self.runtime(g) if rs else None}

    def runtime(self, g: dict, pid: int | None = None) -> dict | None:
        """Whether ReShade and RenoDX are really loaded in the running game: the DLLs Wine mapped into the process
        (/proc/<pid>/maps), confirmed by ReShade.log written since the game started. None when the game is not
        running."""
        pid = pid or self.m.running_pid(g)
        if not pid:
            return None
        exe = self.game_dir(g)
        rs = self.record(exe) or {}
        hd = self.m.renodx.record(exe) or {}
        try:
            maps = Path(f"/proc/{pid}/maps").read_text(errors="replace")
            started = _process_start(pid)
        except OSError:
            return None
        mapped = {Path(line.split(None, 5)[5].strip()).name.lower() for line in maps.splitlines()
                  if len(line.split(None, 5)) == 6 and line.lower().rstrip().endswith((".dll", ".addon64", ".addon32"))}
        paths = {line.split(None, 5)[5].strip() for line in maps.splitlines() if len(line.split(None, 5)) == 6}
        proxy = exe / (rs.get("proxy") or "") if exe and rs.get("proxy") else None
        reshade = bool(proxy and str(proxy) in paths) or any(n in mapped for n in ("reshade64.dll", "reshade32.dll"))
        renodx = bool(hd.get("file") and exe and str(exe / hd["file"]) in paths)
        log_lines = []
        lp = exe / "ReShade.log" if exe else None
        try:
            if lp and lp.is_file() and lp.stat().st_mtime >= started - 5:
                log_lines = lp.read_text(encoding="utf-8", errors="replace").splitlines()[-400:]
        except OSError:
            pass
        text = "\n".join(log_lines).lower()
        if not reshade and "reshade" in text and ("initializ" in text or "running on" in text):
            reshade = True
        if not renodx and hd and re.search(r"(loaded|registered)[^\n]*add-?on[^\n]*renodx|renodx[^\n]*(loaded|registered)",
                                           text):
            renodx = True
        errors = [l.strip() for l in log_lines if re.search(r"\|\s*error\s*\||failed to", l, re.I)][-3:]
        key = (self._read_ini(exe).get("INPUT", "KeyOverlay") if exe else None) or "36,0,0,0"
        return {"reshade": reshade if rs else None, "renodx": renodx if hd else None,
                "overlay_key": _key_name(key), "errors": errors, "mode": rs.get("mode")}

    @staticmethod
    def _read_ini(exe: Path) -> ini.IniDoc:
        p = exe / "ReShade.ini"
        return ini.read(p) if p.is_file() else ini.IniDoc("")

    def status(self, key: str) -> dict:
        try:
            g = self.m.game(key)
        except UserError:
            return {"active": False}
        exe = self.game_dir(g)
        rs = self.record(exe)
        comp = (self.m.game_overrides(key).get("compositor") or {}).get("effect")
        if not rs or not rs.get("enabled", True):
            return {"active": bool(comp), "mode": "compositor" if comp else None, "effect": comp,
                    "paused": bool(rs)}
        pr = None
        n = 0
        try:
            pv = ini.read(exe / "ReShade.ini").get("GENERAL", "PresetPath") or ""
            pr = Path(pv.replace("\\", "/")).name or None
            if pr and (exe / pr).exists():
                t = self._top_keys((exe / pr).read_text(encoding="utf-8", errors="replace"))
                n = len([x for x in t.get("Techniques", "").split(",") if x])
        except OSError:
            pass
        return {"active": True, "mode": rs.get("mode"), "version": rs.get("reshade_version"), "preset": pr,
                "effects": n, "addon": rs.get("addon"), "runtime": self.runtime(g)}


_VK = {0x24: "Home", 0x2D: "Insert", 0x23: "End", 0x21: "Page Up", 0x22: "Page Down", 0x2E: "Delete", 0x13: "Pause",
       0x91: "Scroll Lock", 0x09: "Tab"}


def _key_name(v: str) -> str:
    """ReShade's KeyOverlay ("36,0,0,0" = key, ctrl, shift, alt) as a readable name."""
    parts = [p.strip() for p in (v or "36,0,0,0").split(",")]
    try:
        code = int(parts[0])
    except ValueError:
        return v
    name = _VK.get(code) or (f"F{code - 0x6F}" if 0x70 <= code <= 0x87 else chr(code) if 0x30 <= code <= 0x5A else
                             f"key {code}")
    mods = [m for m, on in zip(("Ctrl", "Shift", "Alt"), parts[1:4]) if on not in ("0", "")]
    return "+".join(mods + [name])


def _process_start(pid: int) -> float:
    """When a process started (epoch seconds): field 22 of /proc/<pid>/stat (clock ticks since boot) plus btime."""
    stat = Path(f"/proc/{pid}/stat").read_text()
    try:
        ticks = int(stat.rsplit(")", 1)[1].split()[19])
        btime = next(int(l.split()[1]) for l in Path("/proc/stat").read_text().splitlines() if l.startswith("btime"))
        return btime + ticks / os.sysconf("SC_CLK_TCK")
    except (ValueError, IndexError, StopIteration) as e:   # callers handle OSError only
        raise OSError(f"unreadable /proc/{pid}/stat: {e}") from e
