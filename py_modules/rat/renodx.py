"""RenoDX HDR for Proton games: official per-game mods matched from RenoDX's own index, community forks from the wiki's
mod list, engine-wide "auto profiles" when no game mod exists, and custom mods from a file or link.

Sources (https://github.com/clshortfuse/renodx, MIT):
* games-index.json, published with every snapshot build (renodx.com / the "snapshot" release): game titles, aliases,
  Steam app ids, mod status and the built .addon64 / .addon32 files.
* The wiki's Mods page (raw markdown): every mod including fork-hosted ones, plus the per-engine lists of games known to
  work with the multi-game mods (Unreal Engine Extended, Unity Engine, Source Engine).

A RenoDX mod is a ReShade add-on, so the two are one dependency in both directions: enabling HDR always (re)installs,
verifies and links ReShade with full add-on support next to the game's real executable (Unreal: Binaries/Win64 or
WinGDK, never the bootstrapper), and installing ReShade re-links RenoDX when the game has it or a pasted profile asked
for it. Settings live in ReShade.ini ([renodx-preset1] per preset, [renodx] global); renodx_auto.py fills in what the
mod needs for this engine and pipeline (swap chain, resource upgrades, brightness) so the game does not go black.

Under Proton the game must see an HDR display: DXVK_HDR=1 (DXVK only reports the HDR10 colour space with it, and RenoDX
reads the peak brightness from there), PROTON_ENABLE_HDR=1, ENABLE_HDR_WSI=1 and ENABLE_GAMESCOPE_WSI=1, the set
reported working in Game Mode on SteamOS. HDR itself must be on in SteamOS (Settings → Display).
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
from pathlib import Path

from . import ini, renodx_auto, sources
from .jobs import Job
from .records import read_extra, remove_extra, write_extra
from .util import Paths, UserError, log, norm_title, place_file, slug

INDEX_URLS = ["https://renodx.com/games-index.json",
              "https://github.com/clshortfuse/renodx/releases/download/snapshot/games-index.json"]
ARTIFACT_BASES = ["https://renodx.com/", "https://github.com/clshortfuse/renodx/releases/download/snapshot/"]
WIKI_URL = "https://raw.githubusercontent.com/wiki/clshortfuse/renodx/Mods.md"
SNAP = "https://github.com/clshortfuse/renodx/releases/download/snapshot/"
HDR_ENV = {"DXVK_HDR": "1", "PROTON_ENABLE_HDR": "1", "ENABLE_HDR_WSI": "1", "ENABLE_GAMESCOPE_WSI": "1"}
OFF = ".rat-off"

# Multi-game mods used for auto profiles (wiki "Multi-Game Mods", RenoDX snapshot)
GENERIC: dict[str, dict] = {
    "ue-extended": {"title": "Unreal Engine Extended", "maintainer": "marat569", "engine": "unreal",
                    "wiki": "unreal engine extended",
                    "urls": {"x64": ["https://marat569.github.io/renodx/renodx-ue-extended.addon64",
                                     "https://github.com/marat569/renodx/releases/download/snapshot/renodx-ue-extended.addon64"]},
                    "notes": "Works on most UE4 / UE5 games. If the game has native HDR, turn it on in the game and set "
                             "Upgrade Path: Off in RenoDX's menu; otherwise keep Upgrade Path: On."},
    "unrealengine": {"title": "Unreal Engine (legacy)", "maintainer": "ShortFuse", "engine": "unreal",
                     "urls": {"x64": [SNAP + "renodx-unrealengine.addon64",
                                      "https://clshortfuse.github.io/renodx/renodx-unrealengine.addon64"]},
                     "notes": "The original Unreal mod. Try it when Unreal Engine Extended misbehaves."},
    "unityengine": {"title": "Unity Engine", "maintainer": "Voosh", "engine": "unity", "wiki": "unity engine",
                    "urls": {"x64": ["https://notvoosh.github.io/renodx-unity/renodx-unityengine.addon64",
                                     "https://github.com/notvoosh/renodx-unity/releases/download/snapshot/renodx-unityengine.addon64"],
                             "x86": ["https://notvoosh.github.io/renodx-unity/renodx-unityengine.addon32",
                                     "https://github.com/notvoosh/renodx-unity/releases/download/snapshot/renodx-unityengine.addon32"]},
                    "notes": "Avoid exclusive fullscreen. If colours stay limited, switch Settings Mode to Advanced and "
                             "upgrade R11G11B10_FLOAT, then restart the game."},
    "sourceengine": {"title": "Source Engine", "maintainer": "CommandGenius", "engine": "source", "wiki": "source engine",
                     "urls": {"x86": ["https://github.com/CommandGenius/renodx/releases/download/snapshot/renodx-sourceengine.addon32"]},
                     "env": {"DXVK_CONFIG": "d3d9.deferSurfaceCreation=True"},
                     "notes": "Source 1 (DirectX 9 through DXVK). VAC games need -insecure in the launch options."},
    "generic": {"title": "Generic (DirectX 11 / 12)", "maintainer": "ShortFuse", "engine": "other",
                "urls": {"x64": [SNAP + "renodx-generic.addon64"], "x86": [SNAP + "renodx-generic.addon32"]},
                "notes": "Engine-agnostic SDR → HDR upgrade. Results vary per game; adjust the tone mapper in RenoDX's "
                         "menu."},
}
# Parameter templates for auto profiles (keys shared by the generic, Unreal and Unity mods)
TEMPLATES = {
    "unreal": {"ToneMapType": "1", "GammaCorrection": "1"},
    "unity": {},
    "source": {},
    "other": {"GammaCorrection": "1"},
}
SETTING_KEYS = [
    {"key": "ToneMapPeakNits", "label": "Peak brightness", "min": 300, "max": 4000, "step": 10, "unit": "nits",
     "desc": "Your display's peak HDR brightness."},
    {"key": "ToneMapGameNits", "label": "Game brightness (paper white)", "min": 80, "max": 500, "step": 1,
     "unit": "nits", "desc": "Mid-grey / paper white of the game image. 203 is the HDR reference."},
    {"key": "ToneMapUINits", "label": "UI brightness", "min": 80, "max": 500, "step": 1, "unit": "nits",
     "desc": "Brightness of menus and HUD."},
]
# Settings RenoDX mods share (src/templates/settings.hpp and the generic / Unreal / Unity mods). Key names and choice
# orders differ a little between mods, so only keys the game's ReShade.ini already holds are shown (RenoDX writes all
# of its keys there the first time it runs), plus the brightness keys RAT prefills.
KNOWN_SETTINGS: dict[str, dict] = {
    "ToneMapType": {"label": "Tone mapper", "kind": "choice", "choices": ["Vanilla", "None", "ACES", "RenoDRT"],
                    "desc": "How HDR highlights are rolled off. RenoDRT is RenoDX's own (choice order varies per mod)."},
    "ToneMapPeakNits": {"label": "Peak brightness", "min": 300, "max": 4000, "step": 10, "unit": "nits",
                        "desc": "Your display's peak HDR brightness."},
    "ToneMapGameNits": {"label": "Game brightness (paper white)", "min": 80, "max": 500, "step": 1, "unit": "nits",
                        "desc": "Mid-grey / paper white of the game image. 203 is the HDR reference."},
    "ToneMapUINits": {"label": "UI brightness", "min": 80, "max": 500, "step": 1, "unit": "nits",
                      "desc": "Brightness of menus and HUD."},
    "GammaCorrection": {"label": "Gamma correction", "kind": "choice", "choices": ["Off", "2.2", "BT.1886"],
                        "desc": "Emulates the SDR gamma curve the game was graded for."},
    "ToneMapGammaCorrection": {"label": "Gamma correction", "kind": "choice", "choices": ["Off", "2.2", "BT.1886"],
                               "desc": "Emulates the SDR gamma curve the game was graded for."},
    "ToneMapHueCorrection": {"label": "Hue correction", "min": 0, "max": 100, "step": 1, "unit": "%",
                             "desc": "Keeps highlight hues from shifting."},
    "ToneMapHueShift": {"label": "Hue shift", "min": 0, "max": 100, "step": 1, "unit": "%", "desc": ""},
    "ToneMapWhiteClip": {"label": "White clip", "min": 1, "max": 100, "step": 1, "desc": ""},
    "ColorGradeExposure": {"label": "Exposure", "min": 0, "max": 2, "step": 0.01, "desc": "1 = unchanged."},
    "ColorGradeHighlights": {"label": "Highlights", "min": 0, "max": 100, "step": 1, "desc": "50 = unchanged."},
    "ColorGradeShadows": {"label": "Shadows", "min": 0, "max": 100, "step": 1, "desc": "50 = unchanged."},
    "ColorGradeContrast": {"label": "Contrast", "min": 0, "max": 100, "step": 1, "desc": "50 = unchanged."},
    "ColorGradeSaturation": {"label": "Saturation", "min": 0, "max": 100, "step": 1, "desc": "50 = unchanged."},
    "ColorGradeHighlightSaturation": {"label": "Highlight saturation", "min": 0, "max": 100, "step": 1, "desc": ""},
    "ColorGradeBlowout": {"label": "Blowout", "min": 0, "max": 100, "step": 1, "desc": ""},
    "ColorGradeFlare": {"label": "Flare", "min": 0, "max": 100, "step": 1, "desc": ""},
    "ColorGradeLUTStrength": {"label": "LUT strength", "min": 0, "max": 100, "step": 1, "desc": ""},
    "ColorGradeLUTScaling": {"label": "LUT scaling", "min": 0, "max": 100, "step": 1, "desc": ""},
    "FxBloom": {"label": "Bloom", "min": 0, "max": 100, "step": 1, "desc": ""},
    "FxVignette": {"label": "Vignette", "min": 0, "max": 100, "step": 1, "desc": ""},
    "FxGrainStrength": {"label": "Film grain", "min": 0, "max": 100, "step": 1, "desc": ""},
    "FxLensFlare": {"label": "Lens flare", "min": 0, "max": 100, "step": 1, "desc": ""},
    "FxChromaticAberration": {"label": "Chromatic aberration", "min": 0, "max": 100, "step": 1, "desc": ""},
    "FxMotionBlur": {"label": "Motion blur", "min": 0, "max": 100, "step": 1, "desc": ""},
    "SettingsMode": {"label": "Settings mode", "kind": "choice", "choices": ["Simple", "Intermediate", "Advanced"],
                     "desc": "Advanced unlocks the resource upgrade sliders in RenoDX's menu."},
    "ForceBorderless": {"label": "Force borderless", "kind": "choice", "choices": ["Disabled", "Enabled"],
                        "desc": "Exclusive fullscreen goes black under Proton / gamescope: keep it enabled."},
    "SwapChainForceBorderless": {"label": "Force borderless", "kind": "choice", "choices": ["Disabled", "Enabled"],
                                 "desc": "Exclusive fullscreen goes black under Proton / gamescope: keep it enabled."},
    "PreventFullscreen": {"label": "Prevent fullscreen", "kind": "choice", "choices": ["Disabled", "Enabled"],
                          "desc": "Blocks exclusive fullscreen switches."},
    "SwapChainPreventFullscreen": {"label": "Prevent fullscreen", "kind": "choice", "choices": ["Disabled", "Enabled"],
                                   "desc": "Blocks exclusive fullscreen switches."},
    "Upgrade_UseSCRGB": {"label": "Swap chain format", "kind": "choice", "choices": ["HDR10", "scRGB"],
                         "desc": "HDR10 is what gamescope presents directly."},
    "SwapChainEncoding": {"label": "Swap chain encoding", "kind": "choice",
                          "choices": ["None", "SRGB", "2.2", "2.4", "HDR10", "scRGB"], "desc": "HDR10 under gamescope."},
    "Upgrade_SwapChainCompatibility": {"label": "Swap chain compatibility mode", "kind": "choice",
                                       "choices": ["Off", "On"],
                                       "desc": "Lets other add-ons (OptiScaler, frame generation) read the swap chain."},
    "Upgrade_CopyDestinations": {"label": "Upgrade copy destinations", "kind": "choice", "choices": ["Off", "On"],
                                 "desc": "Also upgrades texture copy targets."},
}
for _f in renodx_auto.FORMATS:
    KNOWN_SETTINGS[f"Upgrade_{_f}"] = {"label": f"Upgrade {_f}", "kind": "choice",
                                       "choices": ["Off", "Output size", "Output ratio", "Any size"],
                                       "desc": "Resource upgrade to RGBA16F. Output size is the safe choice."}
ALWAYS = ["ToneMapPeakNits", "ToneMapGameNits", "ToneMapUINits"]
STATUS_WORDS = {"white_check_mark": "working", "construction": "in progress"}


def _label_of(key: str) -> str:
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", key)


def settings_view(values: dict[str, str], peak: int) -> list[dict]:
    """Every RenoDX setting to show: the brightness keys, then the known keys the game's ReShade.ini holds, then any
    other numeric key RenoDX wrote (with a range guessed from its value)."""
    out = []
    keys = list(ALWAYS) + [k for k in KNOWN_SETTINGS if k in values and k not in ALWAYS] + \
        sorted(k for k in values if k not in KNOWN_SETTINGS)
    for k in keys:
        raw = values.get(k)
        try:
            num = float(raw) if raw is not None else None
        except ValueError:
            continue   # text values are left to RenoDX's own menu
        spec = dict(KNOWN_SETTINGS.get(k) or {})
        if not spec:
            v = num or 0.0
            spec = {"label": _label_of(k), "min": 0, "max": 1 if v <= 1 else (100 if v <= 100 else max(4000, v * 2)),
                    "step": 0.01 if v <= 1 else (1 if v <= 100 else 10), "desc": ""}
        if num is None:
            num = float(peak if k == "ToneMapPeakNits" else 203)
        kind = spec.get("kind", "slider")
        if kind == "choice":
            spec.update(min=0, max=len(spec["choices"]) - 1, step=1)
        out.append({"key": k, "value": num, "kind": kind, "known": k in KNOWN_SETTINGS, "present": raw is not None,
                    **{x: spec.get(x) for x in ("label", "min", "max", "step", "unit", "desc", "choices")}})
    return out


def _clean_cell(c: str) -> str:
    c = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", c)
    c = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", c)
    return re.sub(r"\s+", " ", c.replace("&middot;", "")).strip()


def _status_of(cell: str) -> tuple[str | None, str | None]:
    m = re.search(r":(white_check_mark|construction):", cell)
    note = re.search(r'\(#\s*"([^"]+)"\)', cell)
    return (STATUS_WORDS.get(m.group(1)) if m else None), (note.group(1) if note else None)


def parse_wiki(text: str) -> dict:
    """{mods: [{name, maintainer, links, status, note}], engines: {section: {norm name: {status, note}}}}"""
    mods: list[dict] = []
    engines: dict[str, dict[str, dict]] = {}
    part = "intro"
    section = ""
    for line in text.splitlines():
        if line.startswith("# "):
            part = line[2:].strip().lower()
            continue
        if line.startswith("## Multi-Game"):
            part = "multi"
            continue
        if line.startswith("### "):
            section = _clean_cell(line[4:]).strip().lower()
            continue
        if not line.lstrip().startswith("|") or re.match(r"^\s*\|\s*:?-{3}", line):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if not cells or cells[0].lower() in ("name", ""):
            continue
        if part == "list" and len(cells) >= 4:
            links = re.findall(r"\]\((https?://[^)\s]+)\)", re.sub(r"!\[[^\]]*\]\([^)]*\)", "", cells[2]))
            st, note = _status_of(cells[3])
            mods.append({"name": _clean_cell(cells[0]), "maintainer": _clean_cell(cells[1]), "links": links,
                         "status": st, "note": note})
        elif part == "multi" and len(cells) >= 2:
            st, note = _status_of(cells[1])
            notes = _clean_cell(cells[2]) if len(cells) > 2 else ""
            engines.setdefault(section, {})[norm_title(_clean_cell(cells[0]))] = {
                "name": _clean_cell(cells[0]), "status": st, "note": note or notes or None}
    return {"mods": mods, "engines": engines}


def _arch_urls(links: list[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for u in links:
        low = u.lower().split("?")[0]
        if low.endswith(".addon64"):
            out.setdefault("x64", []).append(u)
        elif low.endswith(".addon32"):
            out.setdefault("x86", []).append(u)
    return out


class RenoDX:
    def __init__(self, mgr):
        self.m = mgr

    # --- catalog ------------------------------------------------------------------------------------------------

    def index(self, force: bool = False) -> list[dict]:
        for i, url in enumerate(INDEX_URLS):
            try:
                data = json.loads(sources.cached_text(f"renodx-index-{i}.json", url, ttl=6 * 3600, force=force))
                return data.get("games", []) if isinstance(data, dict) else data
            except (UserError, ValueError) as e:
                log.info("RenoDX index %s: %s", url, e)
        return []

    def wiki(self, force: bool = False) -> dict:
        try:
            return parse_wiki(sources.cached_text("renodx-mods.md", WIKI_URL, ttl=6 * 3600, force=force))
        except UserError as e:
            log.info("RenoDX wiki: %s", e)
            return {"mods": [], "engines": {}}

    def match(self, g: dict, force: bool = False) -> dict:
        """Mods for a game, best first: official (index, by Steam app id then title), forks (wiki), engine lists."""
        appid = str(g.get("appid") or "")
        key = norm_title(g["name"])
        prof = self.m.game_profile(g)
        arch = prof.get("arch") or "x64"
        official: list[dict] = []
        for game in self.index(force):
            ids = {str(game.get("steam_appid") or ""), str((game.get("deploy") or {}).get("steam_appid") or "")}
            titles = {norm_title(game.get("title", ""))} | {norm_title(a) for a in game.get("aliases") or []}
            if not ((appid and appid in ids) or (key and key in titles)):
                continue
            for mod in game.get("mods", []):
                if mod.get("category") not in ("game", "engine", None):
                    continue
                if any("superseded" in (n or "").lower() for n in mod.get("notes") or []):
                    continue
                urls: dict[str, list[str]] = {}
                for a in mod.get("artifacts") or []:
                    rel = (a.get("url") or a.get("path") or "").lstrip("./")
                    if not rel:
                        continue
                    urls.setdefault(a.get("arch") or "x64", []).extend(urllib.parse.urljoin(b, rel) for b in ARTIFACT_BASES)
                links = [u.get("url") for u in mod.get("urls") or [] if isinstance(u, dict) and u.get("url")]
                official.append({"id": f"official:{mod.get('id')}", "mod": mod.get("id"),
                                 "title": mod.get("title") or game.get("title"), "variant": mod.get("variant"),
                                 "status": mod.get("status") or "listed", "maintainers": mod.get("maintainers") or [],
                                 "source": "official", "urls": urls, "links": links,
                                 "api": (mod.get("deploy") or {}).get("api"),
                                 "proxy": (mod.get("deploy") or {}).get("reshade_preferred_name"),
                                 "notes": [n for n in mod.get("notes") or [] if n][:3],
                                 "downloadable": bool(urls.get(arch))})
        wk = self.wiki(force)
        forks: list[dict] = []
        for row in wk["mods"]:
            if norm_title(row["name"]) != key:
                continue
            urls = _arch_urls(row["links"])
            if any(u.startswith(ARTIFACT_BASES[0]) or "clshortfuse.github.io" in u for v in urls.values() for u in v) \
                    and official:
                continue   # already offered from the index
            forks.append({"id": f"fork:{slug(row['name'])}:{slug(row['maintainer'], 20)}", "title": row["name"],
                          "variant": None, "status": row["status"] or "listed", "maintainers": [row["maintainer"]],
                          "source": "fork", "urls": urls, "links": row["links"], "notes": [row["note"]] if row["note"]
                          else [], "downloadable": bool(urls.get(arch))})
        auto = self.auto_profile(g, prof, wk)
        return {"official": official, "forks": forks, "auto": auto, "arch": arch, "api": prof.get("api"),
                "engine": prof.get("engine"), "engine_version": prof.get("version")}

    def auto_profile(self, g: dict, prof: dict, wk: dict | None = None) -> dict:
        """The recommended multi-game mod and settings when no game-specific mod exists."""
        eng = prof.get("engine") or "other"
        api = prof.get("api")
        arch = prof.get("arch") or "x64"
        if api == "Vulkan":
            return {"supported": False, "reason": "Vulkan game: RenoDX needs ReShade's DirectX proxy, which a Vulkan "
                                                  "game cannot load under Proton."}
        mod_id = {"unreal": "ue-extended", "unity": "unityengine", "source": "sourceengine"}.get(eng, "generic")
        gm = GENERIC[mod_id]
        if arch not in gm["urls"]:
            alt = "generic"
            if arch not in GENERIC[alt]["urls"]:
                return {"supported": False, "reason": f"No {arch} build of the {gm['title']} mod."}
            mod_id, gm = alt, GENERIC[alt]
        known = None
        if wk and gm.get("wiki"):
            sec = next((s for s in wk["engines"] if s.startswith(gm["wiki"])), None)
            if sec:
                known = wk["engines"][sec].get(norm_title(g["name"]))
        settings = dict(TEMPLATES.get(eng, {}))
        settings.update({"ToneMapPeakNits": str(self.peak_nits()), "ToneMapGameNits": "203", "ToneMapUINits": "203"})
        why = {"unreal": f"Unreal Engine {prof.get('version') or ''}".strip(), "unity": "Unity", "source": "Source"}.get(
            eng, f"{api or 'DirectX'} game")
        return {"supported": True, "id": f"auto:{mod_id}", "mod": mod_id, "title": gm["title"],
                "maintainers": [gm["maintainer"]], "source": "auto", "urls": gm["urls"], "notes": [gm["notes"]],
                "reason": f"{why} detected" + (f" ({api})" if api and eng != "other" else ""),
                "known": known, "settings": settings, "env": gm.get("env", {}),
                "downloadable": arch in gm["urls"]}

    def peak_nits(self) -> int:
        v = int(self.m.pref("hdr_peak_nits") or 0)
        if v:
            return v
        return 1000   # Steam Deck OLED's gamescope panel profile, and a safe start on HDR TVs and monitors

    # --- per game -----------------------------------------------------------------------------------------------

    def record(self, exe: Path | None) -> dict | None:
        return read_extra(exe, "renodx")

    def game_state(self, key: str, force: bool = False) -> dict:
        g = self.m.game(key)
        exe = self.m.reshade.game_dir(g)
        rec = self.record(exe)
        running = self.m.is_running(g)
        if rec and not running and rec.get("auto") and exe and (exe / "ReShade.ini").exists():
            # RenoDX may have written its own sections since (first start): mirror RAT's values there too
            glob, pres = renodx_auto.sections_of(ini.read(exe / "ReShade.ini"))
            if len(glob) + len(pres) > len(rec.get("auto_sections") or []) and len(glob) + len(pres) > 2:
                rec["auto_sections"] = sorted(glob | pres)
                self._autoconfigure(g, exe, rec)
        cands = self.match(g, force)
        values = self._values(exe) if rec else {}
        ctx = renodx_auto.context(self.m, g, exe, rec) if exe else None
        return {"installed": bool(rec), "record": rec, "enabled": bool(rec) and rec.get("enabled", True),
                "candidates": cands, "settings": values, "setting_keys": SETTING_KEYS,
                "params": settings_view(values, self.peak_nits()) if rec else [],
                "peak_nits": self.peak_nits(), "reshade": self.m.reshade.status(key),
                "running": running, "env": HDR_ENV, "context": ctx, "dir": str(exe) if exe else None,
                "pending": self.pending(key),
                "auto": {"report": (rec or {}).get("auto_report") or [], "advice": (rec or {}).get("auto_advice") or [],
                         "at": (rec or {}).get("auto_at")} if rec else None}

    @staticmethod
    def _values(exe: Path | None) -> dict[str, str]:
        if not exe or not (exe / "ReShade.ini").exists():
            return {}
        out: dict[str, str] = {}
        for e in ini.read(exe / "ReShade.ini").entries():
            if e["section"].lower() in ("renodx-preset1", "renodx"):
                out.setdefault(e["key"], e["value"])
        return out

    def browse_all(self, force: bool = False) -> list[dict]:
        """Every downloadable RenoDX mod, for any game, so a mod can be tried on a title it is not listed for (another
        edition, a game on the same engine): RenoDX's index, the wiki's fork-hosted mods and the multi-game mods."""
        out: list[dict] = []
        seen_urls: set[str] = set()
        for game in self.index(force):
            for mod in game.get("mods", []):
                urls: dict[str, list[str]] = {}
                for a in mod.get("artifacts") or []:
                    rel = (a.get("url") or a.get("path") or "").lstrip("./")
                    if rel:
                        urls.setdefault(a.get("arch") or "x64", []).extend(urllib.parse.urljoin(b, rel) for b in ARTIFACT_BASES)
                if not urls:
                    continue
                seen_urls |= {u for v in urls.values() for u in v}
                out.append({"id": f"any:index:{mod.get('id')}", "title": mod.get("title") or game.get("title"),
                            "game": game.get("title"), "variant": mod.get("variant"),
                            "status": mod.get("status") or "listed", "maintainers": mod.get("maintainers") or [],
                            "source": "official", "urls": urls, "notes": [], "downloadable": True})
        for row in self.wiki(force)["mods"]:
            urls = _arch_urls(row["links"])
            if not urls or any(u in seen_urls for v in urls.values() for u in v):
                continue
            out.append({"id": f"any:wiki:{slug(row['name'])}:{slug(row['maintainer'], 20)}", "title": row["name"],
                        "game": row["name"], "variant": None, "status": row["status"] or "listed",
                        "maintainers": [row["maintainer"]], "source": "fork", "urls": urls,
                        "notes": [row["note"]] if row["note"] else [], "downloadable": True})
        for mid, gm in GENERIC.items():
            out.append({"id": f"any:generic:{mid}", "title": gm["title"], "game": "Multi-game", "variant": None,
                        "status": "listed", "maintainers": [gm["maintainer"]], "source": "auto", "urls": gm["urls"],
                        "notes": [gm["notes"]], "env": gm.get("env", {}), "downloadable": True})
        out.sort(key=lambda x: (x["title"] or "").upper())
        return out

    def _download(self, urls: list[str], name: str, job: Job, lo: float, hi: float) -> Path:
        dest = Paths.data("renodx", "mods") / name
        last = None
        for u in urls:
            if not u.lower().startswith("https://"):
                continue
            try:
                job.stage("download", f"Downloading {name}...", lo)
                sources.download(u, dest, progress=lambda f, t: job.sub(f, lo, hi, f"Downloading {name}: {t}"),
                                 should_cancel=job.cancelled)
                if dest.stat().st_size < 16 * 1024 or dest.read_bytes()[:2] != b"MZ":
                    raise UserError(f"{name} is not a ReShade add-on (unexpected download)")
                return dest
            except UserError as e:
                last = e
                job.log(f"{u}: {e}")
        raise last or UserError("No download link for this mod")

    def start_install(self, key: str, choice_id: str, url: str | None = None, path: str | None = None) -> dict:
        g = self.m.game(key)
        if self.m.is_running(g):
            raise UserError("Close the game first.")
        return self.m.jobs.start("Enable HDR (RenoDX + ReShade)", lambda job: self._install(g, choice_id, job, url, path),
                                 game=key, stages=["fetch", "download", "verify", "extract", "install", "configure"])

    def _install(self, g: dict, choice_id: str, job: Job, url: str | None, path: str | None,
                 with_reshade: bool = True, resolved: dict | None = None, settings: dict | None = None,
                 lo: float = 0.0, hi: float = 1.0) -> dict:
        span = hi - lo

        def at(x: float) -> float:
            return lo + span * x
        prof = self.m.game_profile(g)
        arch = prof.get("arch") or "x64"
        job.stage("fetch", "Matching RenoDX mods...", at(0.02))
        if choice_id == "custom":
            if path:
                src = Path(path)
                if not src.is_file() or src.suffix.lower() not in (".addon64", ".addon32"):
                    raise UserError("Pick a RenoDX .addon64 / .addon32 file")
                choice = {"id": "custom", "title": src.stem, "source": "custom", "maintainers": [], "urls": {},
                          "file": src}
            elif url:
                if not re.match(r"^https://\S+\.addon(64|32)$", url.strip(), re.I):
                    raise UserError("The link must be an https:// address ending in .addon64 or .addon32")
                a = "x86" if url.lower().endswith(".addon32") else "x64"
                choice = {"id": "custom", "title": Path(urllib.parse.urlparse(url).path).stem, "source": "custom",
                          "maintainers": [], "urls": {a: [url.strip()]}}
            else:
                raise UserError("Pick a file or paste a link")
        elif choice_id == "resolved" and resolved:
            choice = resolved
        elif choice_id.startswith("any:"):
            choice = next((c for c in self.browse_all() if c["id"] == choice_id), None)
            if not choice:
                raise UserError("That mod is no longer in RenoDX's lists")
            choice = dict(choice, settings={"ToneMapPeakNits": str(self.peak_nits()), "ToneMapGameNits": "203",
                                            "ToneMapUINits": "203"})
        else:
            m = self.match(g)
            pool = m["official"] + m["forks"] + ([m["auto"]] if m["auto"].get("supported") else [])
            choice = next((c for c in pool if c["id"] == choice_id), None)
            if not choice:
                raise UserError("That mod is not available for this game any more")
        # ReShade with add-on support first (RenoDX is a ReShade add-on). Always run, not only when no record exists:
        # it downloads a missing binary, moves a setup left in the wrong folder (Unreal bootstrapper), relinks a DLL a
        # game update or file verification removed, and switches ReShade back on.
        if with_reshade:
            from .reshade import PROXY_CHOICES
            rs0 = self.m.reshade.record(self.m.reshade.game_dir(g)) or {}
            pref = rs0.get("proxy_pref") or (choice.get("proxy") if choice.get("proxy") in PROXY_CHOICES else "auto")
            was_off = bool(rs0) and not rs0.get("enabled", True)
            self.m.reshade.install_game(g, job, addon=True, proxy_pref=pref, by="hdr", lo=at(0.05), hi=at(0.55))
            if was_off:
                job.log("ReShade was switched off: switched back on for RenoDX")
        exe = self.m.reshade.game_dir(g)
        rs = self.m.reshade.record(exe)
        if not rs or not rs.get("addon", True):
            raise UserError("ReShade with full add-on support could not be set up in this game, so RenoDX cannot load. "
                            "Check the ReShade section.")
        if choice.get("file"):
            mod_file = choice["file"]
        else:
            urls = choice["urls"].get(arch) or []
            if not urls:
                raise UserError(f"This mod has no {arch} build to download" +
                                (" (Nexus Mods only: download it there and add it as a custom mod)" if choice.get("links")
                                 else ""))
            name = Path(urllib.parse.urlparse(urls[0]).path).name
            mod_file = self._download(urls, name, job, at(0.6), at(0.85))
        job.stage("install", f"Installing {choice['title']}...", at(0.88))
        old = self.record(exe)
        if old and old.get("file"):
            for n in (old["file"], old["file"] + OFF):
                (exe / n).unlink(missing_ok=True)
        dst = place_file(mod_file, exe / mod_file.name)
        rec = {"version": 1, "id": choice["id"], "mod": choice.get("mod"), "title": choice["title"],
               "source": choice["source"], "maintainers": choice.get("maintainers", []), "file": dst.name, "arch": arch,
               "status": choice.get("status"), "enabled": True, "env": choice.get("env", {}),
               "urls": {k: [u for u in v if u.lower().startswith("https://")] for k, v in (choice.get("urls") or {}).items()},
               "notes": self._notes_of(g, choice),
               "auto": (old or {}).get("auto") or {},
               "installed": (old or {}).get("installed") or time.time(), "updated": time.time()}
        write_extra(exe, "renodx", rec)
        job.stage("configure", "Configuring RenoDX for this engine and pipeline...", at(0.94))
        self._autoconfigure(g, exe, rec, choice, job=job)
        if settings:   # a pasted profile's own brightness values go on top
            ini.edit_file(exe / "ReShade.ini", [(renodx_auto.PRESET, k, str(v)) for k, v in settings.items()
                                                if re.fullmatch(r"[A-Za-z]\w{0,60}", k)
                                                and re.fullmatch(r"-?\d+(\.\d+)?", str(v))])
        self.set_pending(g["key"], None)
        self._sync_injector(g)
        self.m.invalidate()
        job.update(f"HDR ready: {choice['title']} with ReShade {rs.get('reshade_version')}. Turn HDR on in SteamOS, "
                   "start the game, Home opens RenoDX.", hi)
        return {"launch": self.m.wanted_launch(self.m.record_for(g), g["key"])}

    def _sync_injector(self, g: dict) -> None:
        """OptiScaler's ReShade-related keys follow RenoDX (LoadReshade, the D3D12-device-first hotfix)."""
        rec = self.m.record_for(g)
        if rec and rec.get("exe_dir") and Path(rec["exe_dir"]).is_dir():
            self.m._apply_settings(rec, Path(rec["exe_dir"]))

    # --- automatic configuration ----------------------------------------------------------------------------------

    def _notes_of(self, g: dict, choice: dict) -> list[str]:
        """Every note the profile repositories give for this mod and game: index notes, the wiki row, the wiki's
        engine list entry for this game, and the multi-game mod's own advice."""
        notes = [n for n in choice.get("notes") or [] if n]
        known = choice.get("known") or {}
        if known.get("note"):
            notes.append(known["note"])
        mod = (choice.get("mod") or "").split(":")[-1]
        if mod in GENERIC and GENERIC[mod].get("notes") not in notes:
            notes.append(GENERIC[mod]["notes"])
        return list(dict.fromkeys(notes))[:8]

    def _autoconfigure(self, g: dict, exe: Path, rec: dict, choice: dict | None = None, force: bool = False,
                       job: Job | None = None) -> dict:
        choice = choice or {"id": rec.get("id"), "mod": rec.get("mod"), "source": rec.get("source")}
        ctx = renodx_auto.context(self.m, g, exe, choice)
        pl = renodx_auto.plan(ctx, choice, rec.get("notes") or [])
        owned, report = renodx_auto.apply(exe / "ReShade.ini", pl["settings"], rec.get("auto"), force)
        rec.update(auto=owned, auto_report=report, auto_advice=pl["advice"], auto_context=ctx, auto_at=time.time())
        write_extra(exe, "renodx", rec)
        if job:
            job.log(f"RenoDX auto-configuration: {len(owned)} settings for {ctx['engine']} "
                    f"{ctx.get('engine_version') or ''} / {ctx.get('api') or '?'}"
                    + (f", {sum(1 for r in report if r['kept'])} kept as you set them" if any(r["kept"] for r in report)
                       else ""))
            for a in pl["advice"]:
                job.log(f"Note: {a}")
        return rec

    def autoconfigure(self, key: str, force: bool = False) -> dict:
        """Re-applies the automatic configuration (force: also over values changed in RenoDX's menu)."""
        g = self.m.game(key)
        exe = self.m.reshade.game_dir(g)
        rec = self.record(exe)
        if not rec:
            raise UserError("RenoDX is not installed in this game")
        self._autoconfigure(g, exe, rec, force=bool(force))
        return dict(self.game_state(key), applied="next" if self.m.is_running(g) else None)

    # --- the ReShade side of the dependency ---------------------------------------------------------------------------

    def pending(self, key: str) -> dict | None:
        """An HDR mod a pasted / imported profile asked for, installed together with ReShade."""
        try:
            return self.m.settings()["overrides"].get(key, {}).get("pending_hdr")
        except Exception:
            return None

    def set_pending(self, key: str, spec: dict | None) -> None:
        def fn(s):
            o = s["overrides"].setdefault(key, {})
            if spec:
                o["pending_hdr"] = spec
            else:
                o.pop("pending_hdr", None)
        if spec or self.pending(key):
            self.m._update_settings(fn)

    def resolve_choice(self, g: dict, wanted: str | None) -> dict | None:
        """The mod a profile names, found for this game: its own match, the same mod from RenoDX's full list (a
        profile from another game), or this game's auto profile. None when nothing fits."""
        m = self.match(g)
        pool = m["official"] + m["forks"] + ([m["auto"]] if m["auto"].get("supported") else [])
        if wanted:
            hit = next((c for c in pool if c["id"] == wanted), None)
            if hit:
                return hit
            kind, _, rest = wanted.partition(":")
            if kind in ("official", "fork", "any"):
                alls = self.browse_all()
                want_any = {"official": f"any:index:{rest}", "any": wanted}.get(kind)
                hit = next((c for c in alls if c["id"] == want_any), None)
                if not hit and kind == "fork":
                    title = rest.split(":")[0]
                    hit = next((c for c in alls if c["id"].startswith(f"any:wiki:{title}:")), None)
                if hit:
                    return dict(hit, settings={})
        return m["auto"] if m["auto"].get("supported") else None

    def ensure_linked(self, g: dict, job: Job, lo: float = 0.9, hi: float = 1.0) -> str | None:
        """After ReShade was (re)installed: RenoDX's add-on is put back next to it when the game has RenoDX (from RAT's
        download cache, or downloaded again), or installed when a profile asked for it. Returns a log line."""
        exe = self.m.reshade.game_dir(g)
        rec = self.record(exe)
        if rec:
            f = exe / rec["file"]
            if rec.get("enabled", True) and not f.exists():
                off = exe / (rec["file"] + OFF)
                cached = Paths.data("renodx", "mods") / rec["file"]
                if off.exists():
                    os.replace(off, f)
                elif cached.is_file():
                    place_file(cached, f)
                elif (rec.get("urls") or {}).get(rec.get("arch") or "x64"):
                    self._download(rec["urls"][rec.get("arch") or "x64"], rec["file"], job, lo, hi)
                    place_file(Paths.data("renodx", "mods") / rec["file"], f)
                else:
                    return f"RenoDX's {rec['file']} is missing and RAT has no copy: enable HDR again on the HDR section."
                self._autoconfigure(g, exe, rec, job=job)
                return f"RenoDX ({rec['title']}) linked again next to ReShade"
            self._autoconfigure(g, exe, rec, job=job)
            return None
        spec = self.pending(g["key"])
        if spec:
            choice = self.resolve_choice(g, spec.get("id"))
            if not choice:
                self.set_pending(g["key"], None)
                return "The profile's HDR mod has no match for this game: pick one on the HDR section."
            self._install(g, "resolved", job, None, None, with_reshade=False, resolved=choice,
                          settings=spec.get("settings"), lo=lo, hi=hi)
            return f"RenoDX ({choice['title']}) installed with ReShade, as the profile asks"
        return None

    def move(self, old: Path, new: Path, job: Job | None = None) -> None:
        """Moves RenoDX's add-on and record from a stray folder to where ReShade now is."""
        rec = self.record(old)
        if not rec or not new or old == new:
            return
        adopt = not self.record(new)   # the new folder has its own RenoDX already: the stray copy just goes
        for n in (rec["file"], rec["file"] + OFF):
            src = old / n
            if src.exists():
                if adopt and not (new / n).exists():
                    os.replace(src, new / n)
                else:
                    src.unlink()
        remove_extra(old, "renodx")
        if adopt:
            write_extra(new, "renodx", rec)

    def set_enabled(self, key: str, on: bool) -> dict:
        g = self.m.game(key)
        if self.m.is_running(g):
            raise UserError("Close the game first.")
        exe = self.m.reshade.game_dir(g)
        rec = self.record(exe)
        if not rec:
            raise UserError("RenoDX is not installed in this game")
        f, off = exe / rec["file"], exe / (rec["file"] + OFF)
        if on and off.exists():
            os.replace(off, f)
        elif not on and f.exists():
            os.replace(f, off)
        rec["enabled"] = on
        write_extra(exe, "renodx", rec)
        return self.game_state(key)

    def set_setting(self, key: str, name: str, value) -> dict:
        g = self.m.game(key)
        exe = self.m.reshade.game_dir(g)
        if not self.record(exe):
            raise UserError("RenoDX is not installed in this game")
        if name not in KNOWN_SETTINGS and name not in self._values(exe):
            raise UserError("Unknown RenoDX setting")
        sec = renodx_auto.GLOBAL if name in renodx_auto.GLOBAL_KEYS else renodx_auto.PRESET
        doc = ini.read(exe / "ReShade.ini") if (exe / "ReShade.ini").exists() else ini.IniDoc("")
        glob, pres = renodx_auto.sections_of(doc)
        v = _fmt_value(name, value)
        ini.edit_file(exe / "ReShade.ini", [(s, name, v) for s in sorted(glob if sec == renodx_auto.GLOBAL else pres)])
        rec = self.record(exe)
        if rec and name in (rec.get("auto") or {}) and rec["auto"][name] != v:   # the user's value from now on
            rec["auto"].pop(name, None)
            write_extra(exe, "renodx", rec)
        # RenoDX reads its settings when the game loads it: while it runs, the new value is kept for the next start
        # (ReShade does not save over a ReShade.ini that changed on disk, so the value survives the session)
        return dict(self.game_state(key), applied="next" if self.m.is_running(g) else None)

    def export_settings(self, key: str) -> dict:
        """Saves the game's RenoDX settings to ~/RAT/renodx/<game>.ini (a [renodx-preset1] section)."""
        g = self.m.game(key)
        exe = self.m.reshade.game_dir(g)
        vals = self._values(exe)
        if not vals:
            raise UserError("No RenoDX settings yet: start the game once or change a setting first")
        d = Paths.exports() / "renodx"
        d.mkdir(parents=True, exist_ok=True)
        f = d / f"{slug(g['name'])}.ini"
        lines = [f"; RenoDX settings exported by RAT from {g['name']}", "[renodx-preset1]"]
        lines += [f"{k}={v}" for k, v in vals.items()]
        f.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return {"path": str(f), "count": len(vals)}

    def import_settings(self, key: str, path: str) -> dict:
        """Applies an exported RenoDX .ini (numeric values only) to the game."""
        g = self.m.game(key)
        exe = self.m.reshade.game_dir(g)
        if not self.record(exe):
            raise UserError("RenoDX is not installed in this game")
        p = Path(path)
        if not p.is_file() or p.stat().st_size > 1 << 20:
            raise UserError("Pick a RenoDX settings .ini")
        doc = ini.read(p)
        edits = []
        for e in doc.entries():
            if not e["section"].lower().startswith("renodx"):
                continue
            if re.fullmatch(r"[A-Za-z][\w]{0,60}", e["key"]) and re.fullmatch(r"-?\d+(\.\d+)?", e["value"].strip()):
                edits.append(("renodx-preset1", e["key"], e["value"].strip()))
        if not edits:
            raise UserError("No RenoDX settings found in that file")
        ini.edit_file(exe / "ReShade.ini", edits)
        return dict(self.game_state(key), imported=len(edits))

    def uninstall(self, key: str) -> dict:
        g = self.m.game(key)
        if self.m.is_running(g):
            raise UserError("Close the game first.")
        exe = self.m.reshade.game_dir(g)
        rec = self.record(exe)
        if rec:
            for n in (rec["file"], rec["file"] + OFF):
                (exe / n).unlink(missing_ok=True)
            remove_extra(exe, "renodx")
        self.m.reshade.uninstall_game(key, by="hdr")
        if exe:
            strip_settings(exe / "ReShade.ini")   # ReShade may stay for its own effects: RenoDX's sections go with it
        self._sync_injector(g)
        self.m.invalidate()
        return self.game_state(key)

    def launch(self, key: str) -> dict:
        try:
            g = self.m.game(key)
        except UserError:
            return {}
        exe = self.m.reshade.game_dir(g)
        rec = self.record(exe)
        if not rec or not rec.get("enabled", True) or not self._reshade_on(exe):
            return {}
        return {"env": dict(HDR_ENV, **(rec.get("env") or {})), "dlls": {}}

    def _reshade_on(self, exe: Path | None) -> bool:
        """RenoDX runs inside ReShade: with ReShade switched off (its master switch), HDR is paused."""
        rs = self.m.reshade.record(exe) if exe else None
        return bool(rs) and rs.get("enabled", True)

    def status(self, key: str) -> dict:
        try:
            g = self.m.game(key)
        except UserError:
            return {"active": False}
        exe = self.m.reshade.game_dir(g)
        rec = self.record(exe)
        if not rec or not rec.get("enabled", True):
            return {"active": False}
        if not self._reshade_on(exe):
            return {"active": False, "paused": True, "title": rec.get("title"), "source": rec.get("source")}
        return {"active": True, "title": rec.get("title"), "source": rec.get("source")}


def strip_settings(path: Path) -> bool:
    """Removes RenoDX's own sections ([renodx], [renodx-preset1], [renodx-<mod>]...) from a ReShade.ini that stays."""
    if not path.is_file():
        return False
    out, skip, changed = [], False, False
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True):
        t = line.strip()
        if t.startswith("[") and t.endswith("]"):
            skip = t[1:-1].strip().lower().startswith("renodx")
            changed |= skip
        if not skip:
            out.append(line)
    if changed:
        path.write_text("".join(out), encoding="utf-8")
    return changed


def _fmt_value(name: str, value) -> str:
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise UserError("A number is expected")
    spec = KNOWN_SETTINGS.get(name, {})
    if spec.get("kind") == "choice" or float(spec.get("step", 0.01)) >= 1 and v.is_integer():
        return str(int(round(v)))
    return f"{v:.3f}".rstrip("0").rstrip(".")
