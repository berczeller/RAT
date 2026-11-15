"""Automatic RenoDX configuration: the settings a RenoDX mod needs so the game does not go black, applied when the mod
is downloaded or injected (and again on demand).

Three inputs:

* the profile's own metadata: notes from RenoDX's games-index.json and the wiki's Mods page (game rows and the
  multi-game engine lists), parsed for resource upgrades ("R10G10B10A2_UNORM → Output size"), swap chain advice
  (borderless, exclusive fullscreen, scRGB / HDR10, compatibility mode), copy destinations and in-game HDR advice;
* the runtime context: engine and version (Unreal 4 / 5.x, Unity, Source), graphics API (live when the game runs),
  architecture, the game's own HDR / fullscreen settings (Unreal's GameUserSettings.ini in the Proton prefix), the
  injector in front of ReShade (OptiScaler / d4r / HelixSR Scaler, frame generation), and the display's peak;
* engine rules: safe defaults per engine for blank / generic / auto-generated profiles.

Key names follow RenoDX's sources (src/utils/settings.hpp, src/games/generic, src/games/unrealengine):
  per-preset keys   [renodx-preset1]   tone mapping, colour grading
  global keys       [renodx]           SettingsMode, Upgrade_<FORMAT> (0 Off, 1 Output size, 2 Output ratio,
                                       3 Any size), Upgrade_CopyDestinations, Upgrade_SwapChainCompatibility,
                                       Upgrade_UseSCRGB (0 HDR10, 1 scRGB), ForceBorderless / PreventFullscreen (Unreal),
                                       SwapChainForceBorderless / SwapChainPreventFullscreen / SwapChainEncoding (generic)
A mod only reads the keys it defines, so writing both naming families is harmless. Mods that use another section name
are covered too: once RenoDX has written its sections, every renodx* section gets the same values.

The user's own changes win: a key is only written when it is missing or still holds the value RAT wrote last time,
unless a forced re-apply is asked for.
"""
from __future__ import annotations

import re
from pathlib import Path

from . import ini
from .util import log

PRESET = "renodx-preset1"
GLOBAL = "renodx"
UPGRADE = {"off": 0, "output size": 1, "output ratio": 2, "any size": 3, "any": 3}
FORMATS = ["R8G8B8A8_TYPELESS", "B8G8R8A8_TYPELESS", "R8G8B8A8_UNORM", "B8G8R8A8_UNORM", "R8G8B8A8_SNORM",
           "R8G8B8A8_UNORM_SRGB", "B8G8R8A8_UNORM_SRGB", "R10G10B10A2_TYPELESS", "R10G10B10A2_UNORM",
           "B10G10R10A2_UNORM", "R11G11B10_FLOAT", "R16G16B16A16_TYPELESS"]
GLOBAL_KEYS = {"SettingsMode", "Upgrade_CopyDestinations", "Upgrade_SwapChainCompatibility", "Upgrade_UseSCRGB",
               "ForceBorderless", "PreventFullscreen", "SwapChainForceBorderless", "SwapChainPreventFullscreen",
               "SwapChainEncoding", "SwapChainDeviceProxy"} | {f"Upgrade_{f}" for f in FORMATS}
CHAIN_KINDS = ("optiscaler", "d4r")

_FMT = re.compile(r"(?i)\b(" + "|".join(FORMATS) + r")\b(?:[^.\n;]{0,40}?\b(output size|output ratio|any size|off)\b)?")


# --- profile metadata ------------------------------------------------------------------------------------------------

def parse_notes(notes: list[str]) -> tuple[list[tuple[str, str, str]], list[str]]:
    """Settings a profile's notes ask for: ([(key, value, reason)], [advice the user has to act on in the game])."""
    out: list[tuple[str, str, str]] = []
    advice: list[str] = []
    for raw in notes:
        if not raw:
            continue
        n = re.sub(r"\s+", " ", raw)
        low = n.lower()
        for m in _FMT.finditer(n):
            kind = (m.group(2) or "output size").lower()
            out.append((f"Upgrade_{m.group(1).upper()}", str(UPGRADE[kind]), f"profile note: {m.group(0).strip()}"))
        if "copy destination" in low:
            out.append(("Upgrade_CopyDestinations", "1", "profile note: upgrade copy destinations"))
        if "compatibility mode" in low or "swap chain compatibility" in low:
            out.append(("Upgrade_SwapChainCompatibility", "1", "profile note: swap chain compatibility mode"))
        if "scrgb" in low:
            out += [("Upgrade_UseSCRGB", "1", "profile note: scRGB swap chain"),
                    ("SwapChainEncoding", "5", "profile note: scRGB swap chain")]
        if re.search(r"borderless|exclusive full ?screen|avoid full ?screen|windowed", low):
            out += [("ForceBorderless", "1", "profile note: borderless"),
                    ("SwapChainForceBorderless", "1", "profile note: borderless"),
                    ("PreventFullscreen", "1", "profile note: no exclusive fullscreen"),
                    ("SwapChainPreventFullscreen", "1", "profile note: no exclusive fullscreen")]
        if re.search(r"settings mode[^.]{0,20}advanced", low):
            out.append(("SettingsMode", "2", "profile note: Settings Mode Advanced"))
        if re.search(r"(in-?game|native|game'?s) hdr", low) or re.search(r"upgrade path", low):
            advice.append(n.strip())
        elif re.search(r"\b(dx11|dx12|directx 1[12]|-dx1[12]|vulkan)\b", low):
            advice.append(n.strip())
    return out, advice


# --- runtime context -------------------------------------------------------------------------------------------------

def _prefix(g: dict) -> Path | None:
    """The game's Proton prefix (steamapps/compatdata/<appid>/pfx)."""
    if not g.get("appid"):
        return None
    folder = Path(g["folder"])
    for up in folder.parents:
        if up.name.lower() == "steamapps":
            p = up / "compatdata" / str(g["appid"]) / "pfx"
            return p if p.is_dir() else None
    return None


def unreal_user_settings(g: dict, project: str | None) -> dict:
    """Unreal's GameUserSettings.ini in the Proton prefix: {hdr: bool|None, fullscreen: 0|1|2|None, path}."""
    out: dict = {"hdr": None, "fullscreen": None, "path": None}
    pfx = _prefix(g)
    if not pfx or not project:
        return out
    local = pfx / "drive_c" / "users" / "steamuser" / "AppData" / "Local"
    cands = []
    for base in (local / project, local / project.replace(" ", "")):
        cfg = base / "Saved" / "Config"
        if cfg.is_dir():
            cands += sorted(cfg.glob("Win*/GameUserSettings.ini"))
    for p in cands:
        try:
            d = ini.read(p)
        except OSError:
            continue
        out["path"] = str(p)
        for e in d.entries():
            k, v = e["key"].lower(), e["value"].strip().lower()
            if k == "busehdrdisplayoutput":
                out["hdr"] = v in ("true", "1")
            elif k in ("fullscreenmode", "lastconfirmedfullscreenmode") and v.isdigit() and out["fullscreen"] is None:
                out["fullscreen"] = int(v)
        break
    return out


def context(m, g: dict, exe: Path | None, choice: dict | None = None) -> dict:
    """Everything the rules look at, also shown on the HDR section."""
    prof = m.game_profile(g)
    pid = m.running_pid(g)
    api = prof.get("api")
    live = None
    if pid:
        from .peinfo import running_api
        live = running_api(pid)
    rec = m.record_for(g) or {}
    rs = m.reshade.record(exe) if exe else None
    inj = rec.get("kind") if rec.get("active", True) and rec.get("upscaler") != "none" else None
    fg = False
    if inj in CHAIN_KINDS or (inj == "helixsr" and rec.get("route") == "scaler"):
        p = Path(rec.get("exe_dir") or "") / "OptiScaler.ini"
        if p.is_file():
            d = ini.read(p)
            fg = (d.get("FrameGen", "Enabled") or "").strip().lower() == "true" or \
                (d.get("FrameGen", "FGType") or "nofg").strip().lower() not in ("nofg", "auto", "")
    eng = prof.get("engine") or "other"
    us = unreal_user_settings(g, prof.get("project")) if eng == "unreal" else {"hdr": None, "fullscreen": None}
    return {
        "engine": eng, "engine_version": prof.get("version_full") or prof.get("version"), "api": live or api,
        "api_live": bool(live), "arch": prof.get("arch") or "x64", "injector": inj, "frame_generation": fg,
        "chained": bool(rs) and (rs.get("mode") == "chain"), "game_hdr": us.get("hdr"),
        "game_fullscreen": us.get("fullscreen"), "peak_nits": m.renodx.peak_nits(),
        "input": "HDR (the game's own HDR output)" if us.get("hdr") else "SDR (sRGB / gamma 2.2)",
        "output": "HDR10 (PQ, BT.2020) through gamescope", "swap_chain": "R10G10B10A2 · HDR10",
        "mod": (choice or {}).get("id"), "source": (choice or {}).get("source"),
    }


# --- rules -----------------------------------------------------------------------------------------------------------

def plan(ctx: dict, choice: dict, notes: list[str]) -> dict:
    """{settings: [{key, value, section, reason}], advice: [..]} for a mod in this context."""
    rows: dict[str, tuple[str, str]] = {}

    def put(key: str, value, reason: str, force: bool = False):
        if force or key not in rows:
            rows[key] = (str(value), reason)

    eng, src = ctx["engine"], choice.get("source")
    mod = (choice.get("mod") or choice.get("id") or "").split(":")[-1]
    generic_profile = src in ("auto",) or str(choice.get("id", "")).startswith("any:generic:")
    # profile notes first: what the mod's maintainers say beats engine defaults
    parsed, advice = parse_notes(notes)
    for k, v, why in parsed:
        put(k, v, why)
    # the profile's own starting values (auto profiles' templates, a pasted profile)
    for k, v in (choice.get("settings") or {}).items():
        brightness = k in ("ToneMapPeakNits", "ToneMapGameNits", "ToneMapUINits")   # from the display, below
        if not brightness and re.fullmatch(r"[A-Za-z]\w{0,60}", str(k)) and re.fullmatch(r"-?\d+(\.\d+)?", str(v)):
            put(k, v, "the profile's starting value")
    # settings mode: Advanced shows the swap chain and resource upgrade sections in RenoDX's menu
    put("SettingsMode", 2, "Advanced, so swap chain and resource upgrades are visible and editable")
    # brightness from the display
    put("ToneMapPeakNits", ctx["peak_nits"], "your display's peak brightness (Settings → HDR peak)")
    put("ToneMapGameNits", 203, "HDR reference white (ITU-R BT.2408)")
    put("ToneMapUINits", 203, "HDR reference white for menus and HUD")
    # swap chain: gamescope composites HDR10; exclusive fullscreen under Proton is the classic black screen
    for k in ("ForceBorderless", "SwapChainForceBorderless"):
        put(k, 1, "borderless swap chain: exclusive fullscreen goes black under Proton / gamescope")
    for k in ("PreventFullscreen", "SwapChainPreventFullscreen"):
        put(k, 1, "blocks exclusive fullscreen switches (black screen on alt-tab or mode change)")
    if eng != "source":   # DX9 goes through DXVK's own path
        put("Upgrade_UseSCRGB", 0, "HDR10 swap chain: what gamescope and DXVK_HDR present without conversion")
        put("SwapChainEncoding", 4, "HDR10 (PQ) encoding to match the HDR10 swap chain")
    if ctx["injector"] or ctx["chained"] or ctx["frame_generation"]:
        put("Upgrade_SwapChainCompatibility", 1,
            f"{ctx['injector'] or 'another add-on'} reads the swap chain"
            + (" (frame generation)" if ctx["frame_generation"] else "") + ": compatibility mode keeps it readable")
    # resource upgrades: only for generic / auto profiles (game mods upgrade in code; their maintainers decide)
    if generic_profile or not src:
        if eng == "unreal":
            put("Upgrade_R10G10B10A2_UNORM", 1, f"Unreal Engine {ctx.get('engine_version') or ''}".strip() +
                ": the post-process chain ends in R10G10B10A2_UNORM; upgraded at output size so HDR survives "
                "(black screen otherwise)")
            if mod in ("unrealengine", "ue-extended"):
                put("ToneMapType", 1, "RenoDRT (Unreal mods: 0 Vanilla, 1 RenoDRT)")
            put("GammaCorrection", 1, "2.2: Unreal's SDR grade assumes a 2.2 display")
        elif eng == "unity":
            put("Upgrade_R11G11B10_FLOAT", 1, "Unity: HDR scene colour is R11G11B10_FLOAT; upgraded at output size")
            put("Upgrade_R8G8B8A8_TYPELESS", 1, "Unity: LDR targets are R8G8B8A8; upgraded at output size")
        elif eng == "source":
            pass   # DX9 / DXVK: the Source mod handles its own targets
        else:
            put("Upgrade_R8G8B8A8_TYPELESS", 1, "generic DX11 / DX12: the usual 8-bit intermediate before the swap chain")
            put("Upgrade_R10G10B10A2_UNORM", 1, "generic DX11 / DX12: 10-bit intermediates upgraded at output size")
            if mod == "generic":
                put("ToneMapType", 3, "RenoDRT (generic mod: 0 Vanilla, 1 None, 2 ACES, 3 RenoDRT)")
            put("GammaCorrection", 1, "2.2: most PC games are graded on 2.2 displays")
        for k, why in (("ColorGradeExposure", 1), ("ColorGradeHighlights", 50), ("ColorGradeShadows", 50),
                       ("ColorGradeContrast", 50), ("ColorGradeSaturation", 50),
                       ("ColorGradeHighlightSaturation", 50), ("ColorGradeBlowout", 0), ("ColorGradeFlare", 0)):
            put(k, why, "neutral custom colour grading (the game's own grade, untouched)")
    if ctx.get("game_hdr") and eng == "unreal" and generic_profile:
        advice.append("The game's own HDR is on (GameUserSettings.ini). For Unreal Engine Extended set Upgrade Path: "
                      "Off in RenoDX's menu, or switch the game's HDR off.")
    if ctx.get("game_fullscreen") == 0:
        advice.append("The game is set to exclusive fullscreen; RenoDX forces borderless, but switching the game to "
                      "Windowed Fullscreen in its menu avoids a black first frame.")
    if ctx.get("api") == "Vulkan":
        advice.append("Vulkan renderer: RenoDX's DirectX proxy will not load. Start the game in DX11 / DX12 (launch "
                      "option -dx12 or -d3d11 on Unreal) if it offers it.")
    settings = [{"key": k, "value": v, "section": GLOBAL if k in GLOBAL_KEYS else PRESET, "reason": why}
                for k, (v, why) in rows.items()]
    return {"settings": settings, "advice": list(dict.fromkeys(a for a in advice if a))}


# --- writing ---------------------------------------------------------------------------------------------------------

def _same(a, b) -> bool:
    """Equal as RenoDX reads them: 203, 203.0 and 203.000000 are the same value."""
    try:
        return abs(float(str(a).strip()) - float(str(b).strip())) < 1e-6
    except ValueError:
        return str(a).strip() == str(b).strip()


def sections_of(doc: ini.IniDoc) -> tuple[set[str], set[str]]:
    """(global sections, preset sections) RenoDX uses in this ReShade.ini, with the standard ones always included."""
    glob, pres = {GLOBAL}, {PRESET}
    for e in doc.entries():
        s = e["section"]
        if not s.lower().startswith("renodx"):
            continue
        if re.search(r"-preset1$", s, re.I):
            pres.add(s)
        elif not re.search(r"-preset\d$", s, re.I):
            glob.add(s)
    return glob, pres


def apply(rini: Path, settings: list[dict], previous: dict[str, str] | None = None, force: bool = False
          ) -> tuple[dict[str, str], list[dict]]:
    """Writes the plan into ReShade.ini. Returns (what RAT now owns {key: value}, [rows written / kept])."""
    doc = ini.read(rini) if rini.exists() else ini.IniDoc("")
    glob, pres = sections_of(doc)
    previous = previous or {}
    owned: dict[str, str] = {}
    report = []
    for row in settings:
        key, val = row["key"], str(row["value"])
        targets = glob if row["section"] == GLOBAL else pres
        kept = False
        for sec in sorted(targets):
            cur = doc.get(sec, key)
            mine = cur is not None and previous.get(key) is not None and _same(cur, previous[key])
            if cur is None or mine or force:
                doc.set(sec, key, val)
            else:
                kept = True   # the user changed it in RenoDX's menu: theirs
        if not kept:
            owned[key] = val
        report.append(dict(row, kept=kept))
    ini.write(rini, doc)
    log.info("RenoDX auto-configuration: %d settings (%d kept as the user set them)", len(owned),
             sum(1 for r in report if r["kept"]))
    return owned, report
