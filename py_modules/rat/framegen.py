"""Frame generation through OptiScaler's [FrameGen] settings (OptiScaler itself, HelixSR Scaler's OptiScaler, and d4r's
bundled OptiScaler once the FG fallback added the libraries). Methods are presets over FGInput / FGOutput /
FGNvngxReplacement as OptiScaler.ini documents them. Next to the method the tab shows:

  * a plain-language guide per method: what it does, which games and GPUs it works with, the spoofing it needs and the
    steps in the game;
  * the base framerate cap (written to [Framerate] FramerateLimit, converted from base to output frames);
  * a latency plan: which Reflex replacement runs for this GPU / add-on / method, the settings that apply to it (and
    only those), and a recommended combination.

Older OptiScaler releases (0.7) used a single [FrameGen] FGType = nofg | optifg | nukems key; both layouts are handled.
"""
from __future__ import annotations

from pathlib import Path

from . import inischema
from .ini import IniDoc
from .util import safe_rel

FFX_FG = [["amd_fidelityfx_dx12.dll"], ["amd_fidelityfx_loader_dx12.dll", "amd_fidelityfx_framegeneration_dx12.dll"]]
XEFG = [["libxess_fg.dll", "libxell.dll"]]
NUKEM = [["dlssg_to_fsr3_amd_is_better.dll"]]

# Each method: id, label, hint (one line), how (what it does), works (games / GPUs), spoof (what the game must be shown),
# steps (what to do in the game), edits {(section, key): value}, needs (alternative file sets), game (what it uses).
METHODS = [
    {"id": "off", "label": "Off", "hint": "No frame generation: every frame you see is rendered by the game.",
     "how": "OptiScaler's frame generation stays off. Use this to compare, or when a game misbehaves with FG.",
     "works": "Every game.", "spoof": "Nothing to spoof.", "steps": [],
     "edits": {("FrameGen", "Enabled"): "false", ("FrameGen", "FGInput"): "nofg", ("FrameGen", "FGOutput"): "nofg"}},
    {"id": "optifg_fsr", "label": "OptiFG → FSR FG (any game)",
     "hint": "Works in games without any frame generation of their own. Start here.",
     "how": "OptiScaler builds AMD FSR frame generation from what the upscaler already receives (colour, depth, motion "
            "vectors), so the game itself does not need to support frame generation.",
     "works": "DX12 games where OptiScaler runs the upscaler (the game offers DLSS, FSR 2+ or XeSS). Any GPU.",
     "spoof": "No spoofing needed for FG itself. On AMD / Intel keep 'DLSS inputs (spoof NVIDIA)' on only if the game "
              "hides its DLSS option otherwise.",
     "steps": ["Pick DLSS, FSR or XeSS as the upscaler in the game's graphics menu (OptiScaler swaps it).",
               "Leave the game's own frame generation off.",
               "If menus or the HUD smear, raise HUD Limit or turn on HUDFix Extended."],
     "edits": {("FrameGen", "Enabled"): "true", ("FrameGen", "FGInput"): "upscaler", ("FrameGen", "FGOutput"): "fsrfg",
               ("OptiFG", "HUDFix"): "true"},
     "needs": FFX_FG},
    {"id": "game_fsr", "label": "Game FSR 3.1 FG → FSR FG",
     "hint": "For games with their own FSR 3.1 frame generation toggle. Cleanest HUD.",
     "how": "The game feeds its own FSR 3.1 frame generation inputs (including a HUD-less image) and OptiScaler runs "
            "them through its FSR FG, so the UI is not interpolated.",
     "works": "Games that ship FSR 3.1 frame generation. Any GPU.",
     "spoof": "Nothing to spoof: FSR is vendor-neutral.",
     "steps": ["Turn on FSR / AMD frame generation in the game's graphics menu.",
               "Any upscaler works; FSR keeps it simplest."],
     "edits": {("FrameGen", "Enabled"): "true", ("FrameGen", "FGInput"): "fsrfg", ("FrameGen", "FGOutput"): "fsrfg"},
     "needs": FFX_FG, "game": "FSR 3.1"},
    {"id": "game_dlssg_fsr", "label": "Game DLSS FG → FSR FG",
     "hint": "For games with DLSS Frame Generation: the game's own FG inputs, output as FSR FG.",
     "how": "The game's DLSS Frame Generation (Streamline) calls are caught and answered with AMD FSR frame generation, "
            "so you get the game's own FG data (HUD-less image, Reflex markers) on any GPU.",
     "works": "Games that ship DLSS Frame Generation (nvngx_dlssg.dll / Streamline).",
     "spoof": "On AMD and Intel the game must think it runs on NVIDIA with Reflex: keep DXGI and Streamline spoofing on "
              "and use fakenvapi (Upscale tab → Install fakenvapi). NVIDIA GPUs need nothing extra.",
     "steps": ["Enable Reflex (or 'NVIDIA Reflex Low Latency') in the game.",
               "Turn on DLSS Frame Generation in the game's graphics menu.",
               "If the FG toggle stays greyed out, check the spoofing settings below."],
     "edits": {("FrameGen", "Enabled"): "true", ("FrameGen", "FGInput"): "dlssg", ("FrameGen", "FGOutput"): "fsrfg"},
     "needs": FFX_FG, "game": "DLSS FG"},
    {"id": "game_dlssg_nukems", "label": "Game DLSS FG → Nukem's dlssg-to-fsr3",
     "hint": "Nukem's DLSS FG replacement. You supply its DLL; RAT does not download it.",
     "how": "Nukem's dlssg-to-fsr3 replaces DLSS Frame Generation with FSR 3 FG at the driver level. Some games pace "
            "better with it than with OptiScaler's own FSR FG output.",
     "works": "Games that ship DLSS Frame Generation. Needs dlssg_to_fsr3_amd_is_better.dll next to OptiScaler.",
     "spoof": "On AMD and Intel: DXGI and Streamline spoofing on, plus fakenvapi for Reflex.",
     "steps": ["Copy dlssg_to_fsr3_amd_is_better.dll (from Nukem's release) next to the game's .exe.",
               "Enable Reflex, then DLSS Frame Generation in the game."],
     "edits": {("FrameGen", "Enabled"): "true", ("FrameGen", "FGInput"): "nvngxfg", ("FrameGen", "FGOutput"): "auto",
               ("FrameGen", "FGNvngxReplacement"): "Nukems"},
     "needs": NUKEM, "game": "DLSS FG"},
    {"id": "game_dlssg_nukems_out", "label": "Game DLSS FG → Nukem's dlssg-to-fsr3",
     "hint": "Nukem's DLSS FG replacement (OptiScaler 0.9 layout). You supply its DLL.",
     "how": "Nukem's dlssg-to-fsr3 replaces DLSS Frame Generation with FSR 3 FG, wired through OptiScaler's FG output.",
     "works": "Games that ship DLSS Frame Generation. Needs dlssg_to_fsr3_amd_is_better.dll next to OptiScaler.",
     "spoof": "On AMD and Intel: DXGI and Streamline spoofing on, plus fakenvapi for Reflex.",
     "steps": ["Copy dlssg_to_fsr3_amd_is_better.dll (from Nukem's release) next to the game's .exe.",
               "Enable Reflex, then DLSS Frame Generation in the game."],
     "edits": {("FrameGen", "Enabled"): "true", ("FrameGen", "FGInput"): "dlssg", ("FrameGen", "FGOutput"): "nukems"},
     "needs": NUKEM, "game": "DLSS FG"},
    {"id": "game_dlssg_ffx", "label": "Game DLSS FG → FSR FG (Nvngx, FSR 4 FG)",
     "hint": "Lower-level DLSS FG replacement backed by AMD's newest FFX frame generation.",
     "how": "Replaces the game's DLSS FG library (nvngx) itself with AMD's FFX frame generation (FSR 3 or FSR 4 FG, "
            "whichever the FFX libraries provide). Try it when 'Game DLSS FG → FSR FG' stutters.",
     "works": "Games that ship DLSS Frame Generation. Any GPU; FSR 4 FG needs RDNA4 or the libraries' fallback.",
     "spoof": "On AMD and Intel: DXGI and Streamline spoofing on, plus fakenvapi for Reflex.",
     "steps": ["Enable Reflex, then DLSS Frame Generation in the game."],
     "edits": {("FrameGen", "Enabled"): "true", ("FrameGen", "FGInput"): "nvngxfg", ("FrameGen", "FGOutput"): "auto",
               ("FrameGen", "FGNvngxReplacement"): "FFX"},
     "needs": FFX_FG, "game": "DLSS FG"},
    {"id": "optifg_xefg", "label": "OptiFG → XeFG (any game)",
     "hint": "Intel XeSS frame generation from the upscaler's inputs. Best on Intel Arc.",
     "how": "Like OptiFG → FSR FG, but the frames are generated by Intel's XeFG, with XeLL keeping latency down.",
     "works": "DX12 games where OptiScaler runs the upscaler. Made for Intel Arc; on AMD / NVIDIA it may refuse to "
              "start (try 'Ignore Init Checks').",
     "spoof": "No spoofing needed for FG itself.",
     "steps": ["Pick DLSS, FSR or XeSS as the upscaler in the game (Unreal: not the game's own XeSS).",
               "Leave the game's own frame generation off.",
               "If OptiScaler's menu says XeFG requires disabling (dilated) motion vectors: on Unreal games RAT already "
               "sets r.NGX.DLSS.DilateMotionVectors=0 in Engine.ini when you save; elsewhere use FSR or XeSS as the "
               "in-game upscaler instead of DLSS, or OptiFG → FSR FG.",
               "Restart the game once after enabling XeFG (OptiScaler's yellow restart note)."],
     "edits": {("FrameGen", "Enabled"): "true", ("FrameGen", "FGInput"): "upscaler", ("FrameGen", "FGOutput"): "xefg",
               ("OptiFG", "HUDFix"): "true", ("XeFG", "ForceBorderless"): "true"},
     "needs": XEFG},
    {"id": "game_xefg", "label": "Game XeFG → FSR FG",
     "hint": "For games with XeSS frame generation: its inputs, output as FSR FG.",
     "how": "The game's own XeSS frame generation inputs (HUD-less image included) are run through AMD FSR FG instead.",
     "works": "Games that ship XeSS frame generation (XeSS 2). Any GPU.",
     "spoof": "Nothing to spoof.",
     "steps": ["Turn on XeSS frame generation in the game's graphics menu."],
     "edits": {("FrameGen", "Enabled"): "true", ("FrameGen", "FGInput"): "xefg", ("FrameGen", "FGOutput"): "fsrfg"},
     "needs": FFX_FG, "game": "XeSS FG"},
]
LEGACY_METHODS = [
    {"id": "off", "label": "Off", "hint": "No frame generation.", "how": METHODS[0]["how"], "works": "Every game.",
     "spoof": "Nothing to spoof.", "steps": [], "edits": {("FrameGen", "FGType"): "nofg"}},
    {"id": "optifg", "label": "OptiFG (FSR 3 FG, any game)", "hint": "FSR 3 frame generation from upscaler inputs.",
     "how": METHODS[1]["how"], "works": METHODS[1]["works"], "spoof": METHODS[1]["spoof"], "steps": METHODS[1]["steps"],
     "edits": {("FrameGen", "FGType"): "optifg", ("OptiFG", "HUDFix"): "true"}, "needs": FFX_FG},
    {"id": "nukems", "label": "Nukem's dlssg-to-fsr3", "hint": "For games with DLSS Frame Generation.",
     "how": METHODS[4]["how"], "works": METHODS[4]["works"], "spoof": METHODS[4]["spoof"], "steps": METHODS[4]["steps"],
     "edits": {("FrameGen", "FGType"): "nukems"}, "needs": NUKEM, "game": "DLSS FG"},
]

# edits a method sets but the user may change without the method turning into "custom"
SOFT_EDITS = {("OptiFG", "HUDFix"), ("XeFG", "ForceBorderless")}

# the base framerate cap has its own control; latency keys have their own group
CORE = ["FrameGen.Enabled", "FrameGen.FGInput", "FrameGen.FGOutput", "FrameGen.FGNvngxReplacement", "FrameGen.FGType",
        "FrameGen.FTInput", "FrameGen.DrawUIOverFG", "FrameGen.DisableHudless", "FrameGen.HudCutoff",
        "Menu.FGShortcutKey"]
BY_OUTPUT = {
    "fsrfg": ("FSR frame generation", ["FSR.FGIndex", "FSRFG.AllowAsync", "FSRFG.FramePacingTuning",
                                       "FSRFG.FPTSafetyMarginInMs", "FSRFG.FPTVarianceFactor", "FSRFG.FPTHybridSpin",
                                       "FSRFG.UseMutexForSwapchain", "FSRFG.DebugTearLines", "FSRFG.EnableWatermark"]),
    "xefg": ("XeFG", ["XeFG.InterpolationCount", "XeFG.UIComposition", "XeFG.DepthInverted", "XeFG.JitteredMV",
                      "XeFG.HighResMV", "XeFG.ForceBorderless", "XeFG.IgnoreInitChecks", "XeFG.DebugView"]),
    "dlssg": ("DLSS FG (Streamline)", ["DLSSG.InterpolationCount", "DLSSG.OverrideInterpolationCount",
                                      "DLSSG.ForceDMFG", "DLSSG.OverrideForceDMFG", "DLSSG.FramerateTargetDMFG"]),
}
BY_INPUT = {
    "upscaler": ("OptiFG / HUDFix", ["OptiFG.HUDFix", "OptiFG.HUDLimit", "OptiFG.HUDFixExtended", "OptiFG.DisableHUDFix",
                                     "OptiFG.HUDFixImmediate", "OptiFG.HUDFixRelaxedResolutionCheck",
                                     "OptiFG.MakeDepthCopy", "OptiFG.MakeMVCopy", "OptiFG.ResourceFlip",
                                     "OptiFG.EnableDepthScale", "OptiFG.DepthScaleMax"]),
    "optifg": ("OptiFG / HUDFix", ["OptiFG.HUDFix", "OptiFG.HUDLimit", "OptiFG.HUDFixExtended", "OptiFG.MakeDepthCopy",
                                   "OptiFG.MakeMVCopy", "OptiFG.ResourceFlip", "OptiFG.EnableDepthScale"]),
    "nvngxfg": ("Nvngx FG", ["NvngxFG.MakeDepthCopy", "NvngxFG.DispatchFlags", "NvngxFG.DisableHudless",
                             "NvngxFG.ShowDebug", "DLSSG.InterpolationCount"]),
    "dlssg": ("DLSS FG input", ["DLSSG.InterpolationCount", "DLSSG.OverrideInterpolationCount"]),
    "xefg": ("XeFG input", ["XeFG.OverrideInterpolationCount", "XeFG.InterpolationCount"]),
    "fsrfg": ("FSR FG input", ["FSRFGInputs.SkipConfigForHudless", "FSRFGInputs.SkipDispatchForHudless"]),
}
SPOOFING = ["Spoofing.Dxgi", "Spoofing.StreamlineSpoofing", "Inputs.EnableDlssInputs", "Spoofing.SpoofedVendorId",
            "Spoofing.SpoofedDeviceId", "Spoofing.SpoofedGPUName", "Spoofing.SpoofHAGS", "Spoofing.Vulkan",
            "Spoofing.VulkanExtensionSpoofing", "Spoofing.DxgiVRAM"]
CAP_KEY = ("Framerate", "FramerateLimit")

# --- latency ---------------------------------------------------------------------------------------------------------
# Every latency key RAT knows, with a plain explanation. Which of them show depends on the plan (latency_plan).
LATENCY_KEYS = {
    "FrameGen.AllowedFrameAhead": "How many frames FG may run ahead of the game. 1 = lowest latency (recommended); 2-3 "
                                  "can smooth FG flickering on and off but add lag.",
    "fakenvapi.UseFakenvapi": "fakenvapi answers the game's NVIDIA Reflex calls with a latency tech your GPU has. "
                              "Needs fakenvapi installed (Upscale tab).",
    "fakenvapi.ForceReflex": "Many games only switch Reflex on together with DLSS FG. 'Force on' keeps the latency "
                             "reducer (and the framerate cap) working with every FG method.",
    "fakenvapi.ForceLatencyFlex": "Use LatencyFlex even when Anti-Lag / XeLL are available. Needed for the game's own "
                                  "FSR FG, which those cannot drive. No effect with XeFG.",
    "fakenvapi.LatencyFlexMode": "Conservative is safe everywhere. Aggressive cuts more latency but can cost fps. "
                                 "Reflex IDs is the most precise, but some games (Cyberpunk) fall back to aggressive.",
    "fakenvapi.ForceXeLL": "XeLL without frame generation on AMD / NVIDIA (needs libxell + libxess_fg). Disables FG, "
                           "so only offered while FG is off.",
    "DLSSG.UseGamesReflexMarkers": "Use the game's own Reflex timing markers for DLSS FG inputs. Leave on unless "
                                   "pacing is clearly off.",
    "NvApi.DisableFlipMetering": "Fixes the thick frametime graph with Nukem's FG. Needs fakenvapi.",
    "Menu.FpsOverlayType": "OptiScaler's own FPS overlay. 'Reflex timings' shows whether the latency reducer is active.",
}


def latency_plan(vendor: str, kind: str, fake: bool, xell: bool, inp: str, outp: str) -> dict:
    """Which latency reducer runs for this GPU vendor, add-on, fakenvapi and FG input / output, the keys that apply to
    it and the recommended values. Pure, so the frontend can show it for an unsaved method too."""
    vendor = (vendor or "").lower()
    fg_on = outp not in ("nofg", "", None) or inp not in ("nofg", "", None)
    keys = ["FrameGen.AllowedFrameAhead"] if fg_on else []
    rec: list[list[str]] = [["FrameGen", "AllowedFrameAhead", "1"]] if fg_on else []
    notes: list[str] = []
    if vendor == "nvidia":
        prov, tone = "NVIDIA Reflex (native)", "ok"
        notes.append("Your GPU has real Reflex through Proton's dxvk-nvapi. Turn Reflex on in the game's menu.")
        if kind == "optiscaler" and fake:
            notes.append("fakenvapi is installed but not needed on NVIDIA; it stays unused while nvapi64.dll exists.")
    elif kind == "d4r":
        prov, tone = "Proton's dxvk-nvapi (kept for d4r's DLSS)", "info"
        notes.append("d4r needs Proton's NVIDIA API layer to expose DLSS, so fakenvapi (Anti-Lag / XeLL / LatencyFlex) "
                     "is not used. Keep latency down with the base framerate cap and 1 frame ahead.")
    elif not fake:
        prov, tone = "None (no Reflex replacement)", "warn"
        notes.append("Without fakenvapi the game's Reflex calls go nowhere on AMD / Intel: no latency reducer, and "
                     "DLSS FG methods may stay greyed out. Turn on Install fakenvapi on the Upscale tab and Reinstall.")
    else:
        keys += ["fakenvapi.UseFakenvapi", "fakenvapi.ForceReflex"]
        rec += [["fakenvapi", "UseFakenvapi", "true"]]
        if fg_on:
            rec += [["fakenvapi", "ForceReflex", "2"]]
        if outp == "xefg":
            prov, tone = "XeLL (Intel, via fakenvapi)", "ok"
            notes.append("XeFG always runs with XeLL; LatencyFlex settings have no effect here.")
        elif inp == "fsrfg":
            prov, tone = "LatencyFlex (via fakenvapi)", "ok"
            keys += ["fakenvapi.ForceLatencyFlex", "fakenvapi.LatencyFlexMode"]
            rec += [["fakenvapi", "ForceLatencyFlex", "true"], ["fakenvapi", "LatencyFlexMode", "0"]]
            notes.append("The game's own FSR FG cannot be driven by Anti-Lag or XeLL, so LatencyFlex is forced.")
        elif vendor == "intel":
            prov, tone = "XeLL (via fakenvapi)", "ok"
            notes.append("Intel GPUs get XeLL automatically.")
        else:
            prov, tone = "Anti-Lag (Vulkan) or LatencyFlex (via fakenvapi)", "ok"
            keys += ["fakenvapi.ForceLatencyFlex", "fakenvapi.LatencyFlexMode"]
            rec += [["fakenvapi", "ForceLatencyFlex", "false"], ["fakenvapi", "LatencyFlexMode", "0"]]
            notes.append("fakenvapi picks Anti-Lag through vkd3d-proton when your Mesa / driver offers it, otherwise "
                         "LatencyFlex. Anti-Lag 2 itself is Windows-only.")
        if not fg_on and xell and vendor != "intel":
            keys.append("fakenvapi.ForceXeLL")
        if outp in ("nukems",) or inp == "nvngxfg":
            keys.append("NvApi.DisableFlipMetering")
            rec += [["NvApi", "DisableFlipMetering", "true"]]
    if inp in ("dlssg", "nvngxfg"):
        keys.append("DLSSG.UseGamesReflexMarkers")
        rec += [["DLSSG", "UseGamesReflexMarkers", "true"]]
    keys.append("Menu.FpsOverlayType")
    return {"provider": prov, "tone": tone, "notes": notes, "keys": keys, "recommended": rec}


def cap_multiplier(inp: str, outp: str, xefg_count: str | None = None) -> int:
    """Output frames per rendered frame for an FG input / output pair (1 with FG off)."""
    if outp in ("nofg", "", None) and inp in ("nofg", "", None):
        return 1
    if outp == "xefg":
        try:
            return 1 + max(1, int(float(xefg_count or "1")))
        except ValueError:
            return 2
    return 2


def _get(doc: IniDoc, section: str, key: str) -> str | None:
    v = doc.get(section, key)
    return v.strip() if v is not None else None


def _norm(section: str, key: str, v: str | None) -> str:
    """Values as OptiScaler reads them: auto in the FG keys means off / nofg."""
    v = (v or "auto").strip().lower()
    if v == "auto":
        return {("framegen", "enabled"): "false", ("framegen", "fginput"): "nofg", ("framegen", "fgoutput"): "nofg",
                ("framegen", "fgtype"): "nofg", ("framegen", "fgnvngxreplacement"): "nukems",
                ("optifg", "hudfix"): "false"}.get((section.lower(), key.lower()), "auto")
    return v


def lib_dir(exe: Path, doc: IniDoc | None) -> Path:
    """Where the installed OptiScaler loads its libraries from ([Libraries] OptiDllPath, or the build's default)."""
    exe = Path(exe)
    if doc is not None:
        v = (doc.get("Libraries", "OptiDllPath") or "auto").strip()
        if v and v.lower() != "auto":
            rel = safe_rel(v)
            if rel is not None:
                return exe / rel if rel else exe
        for e in doc.entries():
            if e["section"].lower() == "libraries" and e["key"].lower() == "optidllpath":
                text = " ".join(e["comments"]).lower()
                if "default is .\\optiscaler" in text or "default is ./optiscaler" in text:
                    return exe / "OptiScaler"
                break
    return exe


def search_dirs(exe: Path, doc: IniDoc | None) -> list[Path]:
    exe = Path(exe)
    out = [exe, exe / "OptiScaler", lib_dir(exe, doc)]
    seen, res = set(), []
    for d in out:
        if str(d) not in seen:
            seen.add(str(d))
            res.append(d)
    return res


def lookup_dirs(doc: IniDoc, exe: Path) -> list[Path]:
    """Where OptiScaler looks for the FG libraries: the game folder and OptiDllPath (default .\\ or .\\OptiScaler)."""
    return search_dirs(exe, doc)


def _have(dirs: list[Path], name: str) -> bool:
    for d in dirs:
        try:
            if any(e.name.lower() == name.lower() for e in d.iterdir()):
                return True
        except OSError:
            continue
    return False


def has_fakenvapi(doc: IniDoc | None, exe: Path | None) -> bool:
    """fakenvapi present (its own DLL, or the nvapi64.dll it is installed as) and not switched off."""
    if exe is None:
        return False
    if doc is not None and (doc.get("fakenvapi", "UseFakenvapi") or "auto").strip().lower() == "false":
        return False
    dirs = search_dirs(exe, doc)
    return _have(dirs, "fakenvapi.dll") or _have(dirs, "nvapi64.dll")


def _method_out(m: dict) -> dict:
    return {"id": m["id"], "label": m["label"], "hint": m["hint"], "how": m.get("how", ""),
            "works": m.get("works", ""), "spoof": m.get("spoof", ""), "steps": m.get("steps", [])}


def analyze(doc: IniDoc | None, exe: Path | None, kind: str, route: str | None, native: dict, locked=None,
            env: dict | None = None) -> dict:
    """Everything the Frame generation tab shows. env: {vendor, fallback} (GPU vendor, d4r FG fallback state)."""
    env = env or {}
    if kind == "d4r" and not (env.get("fallback") or {}).get("installed"):
        return {"available": False, "no_fg": True, "fallback": env.get("fallback"),
                "reason": "d4r bundles OptiScaler without its frame generation libraries. Add them below from the "
                          "matching official OptiScaler release (FG fallback), then pick a method."}
    if doc is None or exe is None:
        if kind == "helixsr" and route != "scaler":
            return {"available": False, "reason": "The FSR 3.1 drop-in keeps the game's own FSR frame generation: turn "
                                                  "it on in the game's graphics settings."}
        return {"available": False, "reason": "Install the add-on first: frame generation is set in its OptiScaler.ini."}
    keys = {(e["section"].lower(), e["key"].lower()) for e in doc.entries()}
    legacy = ("framegen", "fgtype") in keys and ("framegen", "fginput") not in keys
    if not legacy and ("framegen", "fginput") not in keys:
        return {"available": False, "reason": "This OptiScaler.ini has no [FrameGen] section (too old for frame "
                                              "generation)."}
    fields = {f"{f['section']}.{f['key']}".lower(): f for f in inischema.fields("optiscaler", doc, locked=locked)}
    methods_src = LEGACY_METHODS if legacy else METHODS
    inp = _norm("FrameGen", "FGInput", _get(doc, "FrameGen", "FGInput"))
    outp = _norm("FrameGen", "FGOutput", _get(doc, "FrameGen", "FGOutput"))
    if legacy:
        inp = _norm("FrameGen", "FGType", _get(doc, "FrameGen", "FGType"))
        outp = "fsrfg" if inp in ("optifg", "nukems") else "nofg"
    allowed_in = {c[0].lower() for c in fields.get("framegen.fginput", {}).get("choices", [])}
    allowed_out = {c[0].lower() for c in fields.get("framegen.fgoutput", {}).get("choices", [])}
    dirs = lookup_dirs(doc, exe)
    vendor = env.get("vendor") or "unknown"
    fake = kind != "d4r" and has_fakenvapi(doc, exe)
    xell = _have(dirs, "libxell.dll")
    methods = []
    plans = {}
    current = "custom"
    for m in methods_src:
        ed = m["edits"]
        if not legacy:
            vi, vo = ed.get(("FrameGen", "FGInput"), "nofg"), ed.get(("FrameGen", "FGOutput"), "nofg")
            if (allowed_in and vi not in allowed_in) or (allowed_out and vo != "auto" and vo not in allowed_out):
                continue
        else:
            t = ed.get(("FrameGen", "FGType"), "nofg")
            vi, vo = t, {"optifg": "fsrfg", "nukems": "nukems"}.get(t, "nofg")
        needs = m.get("needs") or []
        ok_any = not needs or any(all(_have(dirs, n) for n in alt) for alt in needs)
        missing = [] if ok_any else needs[0]
        game_note = None
        want = m.get("game")
        if want and not any(k.startswith(want) or (want == "DLSS FG" and k == "Streamline") for k in native):
            game_note = {"DLSS FG": "The game does not seem to ship DLSS Frame Generation (nvngx_dlssg.dll / "
                                    "Streamline). Without it this method does nothing.",
                         "FSR 3.1": "The game does not seem to ship FSR 3.1 (amd_fidelityfx_*.dll). Without its FSR "
                                    "frame generation this method does nothing.",
                         "XeSS FG": "The game does not seem to ship XeSS frame generation (libxess_fg.dll). Without it "
                                    "this method does nothing."}.get(want)
        methods.append(dict(_method_out(m), missing=missing, game_note=game_note,
                            edits=[[s, k, v] for (s, k), v in ed.items() if (s.lower(), k.lower()) in keys]))
        plans[m["id"]] = latency_plan(vendor, kind, fake, xell, vi, "fsrfg" if vo == "auto" and vi != "nofg" else vo)
        if current == "custom" and all(_norm(s, k, _get(doc, s, k)) == _norm(s, k, v) for (s, k), v in ed.items()
                                       if (s.lower(), k.lower()) in keys and (s, k) not in SOFT_EDITS):
            current = m["id"]
    plans["custom"] = latency_plan(vendor, kind, fake, xell, inp, outp)

    def grp(title, ks):
        return {"title": title, "fields": [fields[k.lower()] for k in ks if k.lower() in fields]}
    core = grp("Frame generation options", CORE)
    by_output = {k: grp(t, ks) for k, (t, ks) in BY_OUTPUT.items()}
    by_input = {k: grp(t, ks) for k, (t, ks) in BY_INPUT.items()}
    spoof = grp("Spoofing", SPOOFING)
    latency_fields = []
    for k, why in LATENCY_KEYS.items():
        f = fields.get(k.lower())
        if f:
            latency_fields.append(dict(f, desc=why, rat_hint=True))
    groups = compose(core, by_output, by_input, spoof, inp, outp)
    warnings = []
    cur = next((m for m in methods if m["id"] == current), None)
    if cur and cur["missing"]:
        warnings.append("Missing for this method: " + ", ".join(cur["missing"]) + " (put it next to the game's .exe or "
                        "in the OptiScaler folder).")
    if inp == "upscaler":
        warnings.append("OptiFG needs an active upscaler: pick DLSS, FSR or XeSS in the game's own menu.")
    used = {f"{s}.{k}".lower() for m in methods_src for (s, k) in m["edits"]}
    cap = fields.get("framerate.frameratelimit")
    return {"available": True, "legacy": legacy, "methods": methods, "current": current, "input": inp,
            "output": outp, "groups": groups, "warnings": warnings,
            "edit_fields": [fields[k] for k in sorted(used) if k in fields],
            "core": core, "by_output": by_output, "by_input": by_input, "spoofing": spoof,
            "latency_fields": latency_fields, "plans": plans, "cap_field": cap,
            "xefg_count": fields.get("xefg.interpolationcount", {}).get("value"),
            "env": {"vendor": vendor, "kind": kind, "fakenvapi": fake, "xell": xell},
            "fallback": env.get("fallback")}


def compose(core: dict, by_output: dict, by_input: dict, spoof: dict, inp: str, outp: str) -> list[dict]:
    """The groups shown for an FG input / output pair (the frontend redoes this for staged, unsaved choices)."""
    groups = [core]
    if outp in by_output:
        groups.append(by_output[outp])
    if inp in by_input and (inp != outp or inp not in by_output):
        have = {f["id"] for g in groups for f in g["fields"]}
        g = by_input[inp]
        groups.append({"title": g["title"], "fields": [f for f in g["fields"] if f["id"] not in have]})
    groups.append(spoof)
    return [g for g in groups if g["fields"]]
