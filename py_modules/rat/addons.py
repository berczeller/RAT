"""The add-on catalog: where each injector comes from, what it needs and what RAT can configure.

An add-on has an id (its catalog entry) and a kind (the install logic it uses). Imported forks get the catalog entry of
their kind.

Everything here follows the authors' own documentation (README / release notes / shipped INI) at the time of writing:
  d4r          https://github.com/countervolts/d4r              (D4R_README.txt, d4r.ini, shipped OptiScaler.ini)
  HelixSR      https://github.com/lonewolf0622/HelixSR          (README.md, helixsr-setup.sh, helixsr_install.py)
  OptiScaler   https://github.com/optiscaler/OptiScaler         (OptiScaler.ini, setup_linux.sh, release notes)

Upscaler paths (the "Upscaling mode" picker):
  native  the game's DLSS request runs a DLSS network: real DLSS on NVIDIA RTX, d4r's DLSS on AMD, HelixSR's Model E
  fsr     the game is shown an NVIDIA GPU / DLSS (spoofed) and OptiScaler swaps the request for FSR
  xess    the same, swapped for XeSS
"""
from __future__ import annotations

PROXY_NAMES = ["dxgi.dll", "winmm.dll", "version.dll", "dbghelp.dll", "d3d12.dll", "wininet.dll", "winhttp.dll"]

OPTI = "OptiScaler.ini"
D4R_INI = "d4r/d4r.ini"
HELIX_INI = "helixsr.ini"   # next to HelixSR's DLL (drop-in: next to the game's FSR DLL; Scaler: HelixSR/helixsr.ini)

PATHS = {"native": "Native DLSS", "fsr": "Spoofed → FSR", "xess": "Spoofed → XeSS"}

# Upscaler values differ between OptiScaler builds: 0.9.x stable uses fsr31 / fsr31_12, newer builds (and the forks
# built from them) use ffx / ffx_12. Each choice lists candidates; the first one the installed INI documents is written.
FFX12 = ["ffx", "fsr31"]
FFX11 = ["ffx_12", "fsr31_12"]

_OPTI_UPSCALERS = [
    {"id": "auto", "label": "Auto (OptiScaler default)", "path": "auto", "family": "neutral", "tech": "Auto",
     "model": "OptiScaler default",
     "hint": "DLSS on NVIDIA RTX, FSR 4 on RDNA4, XeSS on other GPUs."},
    {"id": "dlss", "label": "DLSS (native)", "path": "native", "family": "dlss", "tech": "DLSS", "model": "Game / driver preset",
     "requires": "dlss",
     "hint": "Real DLSS. Needs an NVIDIA RTX GPU (on AMD use d4r or HelixSR for a native DLSS network)."},
    {"id": "fsr4", "label": "FSR 4", "path": "fsr", "family": "fsr", "tech": "FSR 4", "model": "ML (INT8 off RDNA4)",
     "hint": "Native on RDNA4. Other GPUs run the INT8 model where available (experimental, heavier)."},
    {"id": "fsr31", "label": "FSR 3.1", "path": "fsr", "family": "fsr", "tech": "FSR 3.1", "model": "Analytical",
     "hint": "Light and compatible on every GPU. A good pick for handhelds."},
    {"id": "fsr22", "label": "FSR 2.2", "path": "fsr", "family": "fsr", "tech": "FSR 2.2", "model": "Analytical",
     "hint": "Older FSR with the lowest cost."},
    {"id": "xess", "label": "XeSS", "path": "xess", "family": "xess", "tech": "XeSS", "model": "XMX on Arc, DP4a elsewhere",
     "hint": "Intel XeSS. Uses XMX units on Arc GPUs and DP4a on AMD / NVIDIA."},
    {"id": "none", "label": "None (disable injection)", "path": "none", "family": "neutral", "tech": "Off", "model": "",
     "hint": "Keeps the files, the game runs without the injector."},
]

_OPTI_OPTIONS = [
    {"id": "proxy", "type": "dropdown", "label": "Injection DLL name", "meta": True,
     "desc": "dxgi.dll works for almost every DX12 game. Change it only if the menu (Insert) never shows.",
     "values": [[n, n] for n in PROXY_NAMES], "default": "dxgi.dll"},
    {"id": "spoof", "type": "toggle", "label": "DLSS inputs (spoof NVIDIA)",
     "desc": "Shows the game an NVIDIA GPU so it offers DLSS, which OptiScaler then swaps. Not needed on NVIDIA RTX.",
     "file": OPTI, "section": "Spoofing", "key": "Dxgi", "on": "true", "off": "false", "default": True},
    {"id": "force_nvapi", "type": "toggle", "label": "Force NVAPI (DLSS menu on AMD / Intel)",
     "desc": "Adds PROTON_FORCE_NVAPI=1, which some games need before they offer DLSS on AMD and Intel GPUs.",
     "meta": True, "default": False},
    {"id": "cas", "type": "toggle", "label": "RCAS sharpening",
     "desc": "AMD's Robust Contrast Adaptive Sharpening on top of the upscaler (RAT's default).",
     "file": OPTI, "section": "CAS", "key": "Enabled", "on": "true", "off": "false", "default": True},
    {"id": "fakenvapi", "type": "toggle", "label": "Install fakenvapi",
     "desc": "Answers the game's NVIDIA Reflex calls with Anti-Lag (Vulkan), XeLL or LatencyFlex on AMD and Intel. "
             "Needed for low latency with frame generation and for DLSS FG methods there. Not needed on NVIDIA.",
     "meta": True, "default": False},
]

ADDONS: dict[str, dict] = {
    "d4r": {
        "id": "d4r",
        "kind": "d4r",
        "title": "d4r",
        "subtitle": "NVIDIA DLSS 3 / 4 / 4.5 networks running on AMD RDNA3 / RDNA4",
        "repo": "countervolts/d4r",
        "asset": r"^d4r-\d+(\.\d+)+\.zip$",
        "asset_fallback": r"^d4r-.*\.zip$",
        "asset_exclude": r"(?i)(-asi|source)",
        "author": "countervolts",
        "author_short": "countervolts",
        "license": "GPL-3.0 (bundles NVIDIA files under NVIDIA's terms)",
        "family": "dlss",
        "native_fg": False,
        "fg_fallback": True,
        "gpu": {
            "families": ["RDNA3", "RDNA4"],
            "note": "d4r needs an AMD RDNA3 (RX 7000) or RDNA4 (RX 9000) GPU and /dev/kfd. On NVIDIA RTX pick OptiScaler "
                    "with native DLSS instead; Intel and older AMD GPUs are not supported by d4r.",
        },
        "proton": {
            "recommended": "GE-Proton11-3",
            "match": r"^GE-Proton11",
            "note": "The author tests with GE-Proton11-3 (GE-Proton 11 is required). Install it with ProtonUp-Qt or "
                    "Wine Cellar, then select it here.",
        },
        "launch": {"env": {"PROTON_FORCE_NVAPI": "1", "DXVK_NVAPI_GPU_ARCH": "AD100"}, "dlls": {}},
        "in_game": "Select DLSS in the game's graphics settings. The first start can freeze for a minute or two while "
                   "DLSS kernels compile (cached in ~/.cache/d4r).",
        "deactivate": ["dxgi.dll", "d3d12.dll", "d3d12core.dll"],
        "configs": [OPTI, D4R_INI],
        "logs": ["d4r/d4r_nvngx.log", "OptiScaler.log"],
        # keys d4r needs: kept at the new release's value when an update merges the user's INI
        "locked": {OPTI: {("libraries", "nvngxpath"), ("libraries", "optidllpath"), ("hooks", "hookoriginalnvngxonly"),
                          ("inputs", "enabledlssinputs")}},
        "upscalers": [
            {"id": "K", "label": "DLSS 4 · Preset K", "path": "native", "family": "dlss", "tech": "DLSS 4",
             "model": "Preset K (transformer)", "hint": "Transformer model, d4r's default. Best for DLAA / Quality / Balanced."},
            {"id": "M", "label": "DLSS 4.5 · Preset M", "path": "native", "family": "dlss", "tech": "DLSS 4.5",
             "model": "Preset M (transformer)", "hint": "Newest transformer. Recommended for Performance; heavier on RDNA3."},
            {"id": "L", "label": "DLSS 4.5 · Preset L", "path": "native", "family": "dlss", "tech": "DLSS 4.5",
             "model": "Preset L (Ultra Performance)", "hint": "Experimental. Pick Ultra Performance in the game; best at 4K."},
            {"id": "E", "label": "DLSS 3 · Preset E", "path": "native", "family": "dlss", "tech": "DLSS 3",
             "model": "Preset E (CNN)", "hint": "The DLSS 3.x CNN model. A little faster, softer image."},
            {"id": "F", "label": "DLSS 3 · Preset F", "path": "native", "family": "dlss", "tech": "DLSS 3",
             "model": "Preset F (CNN, deprecated)", "hint": "Older CNN model; d4r recommends K or L instead."},
            {"id": "game", "label": "DLSS · Game preset", "path": "native", "family": "dlss", "tech": "DLSS",
             "model": "Game's choice", "hint": "Uses whatever preset the game requests."},
            {"id": "none", "label": "None (disable injection)", "path": "none", "family": "neutral", "tech": "Off",
             "model": "", "hint": "Keeps the files, the game runs without d4r."},
        ],
        "default_upscaler": "K",
        "options": [
            {"id": "prefer_accuracy", "type": "toggle", "label": "Prefer accuracy",
             "desc": "Accuracy variants of every kernel. Slower, aims to match RTX DLSS.",
             "file": D4R_INI, "section": "Kernels", "key": "PreferAccuracy", "on": "true", "off": "false",
             "default": False},
            {"id": "native_swin", "type": "toggle", "label": "Native Swin encoders",
             "desc": "Turn off if DLSS 4.5 (L/M) shows motion trails.",
             "file": D4R_INI, "section": "Kernels", "key": "NativeSwinEncoders", "on": "true", "off": "false",
             "default": True},
            {"id": "native_fp8", "type": "toggle", "label": "Native FP8 (RDNA4)",
             "desc": "RDNA4 only: FP8 matrix units. Ignored on RDNA3.", "gpu": ["RDNA4"],
             "file": D4R_INI, "section": "Kernels", "key": "NativeFp8", "on": "on", "off": "off", "default": True},
            {"id": "engine", "type": "dropdown", "label": "Native engine",
             "desc": "d4r's own Vulkan engine (faster) when its models are built.",
             "file": D4R_INI, "section": "Engine", "key": "Enabled",
             "values": [["auto", "Auto"], ["true", "Always"], ["false", "Never"]], "default": "auto"},
            {"id": "frame_age", "type": "dropdown", "label": "Latency (frame age)",
             "desc": "0 = every frame shows its own DLSS result. 1-3 = slightly more fps, more input lag.",
             "file": D4R_INI, "section": "Latency", "key": "FrameAge",
             "values": [["0", "0 (default)"], ["1", "1"], ["2", "2"], ["3", "3"]], "default": "0"},
            {"id": "rr", "type": "dropdown", "label": "Ray Reconstruction",
             "desc": "Experimental. Uses the denoiser when the game enables DLSS RR.",
             "file": D4R_INI, "section": "RayReconstruction", "key": "Enable",
             "values": [["auto", "Auto"], ["false", "Off"]], "default": "auto"},
            {"id": "rr_launch", "type": "toggle", "label": "Ray Reconstruction launch fixes",
             "desc": "Adds the author's ICU and d3d12 DLL overrides needed by RR on GE-Proton11-3.",
             "meta": True, "default": False},
            {"id": "build_engine", "type": "toggle", "label": "Build engine models on install",
             "desc": "Only for releases with install.sh. Needs python3 with numpy and GE-Proton 11; takes a few "
                     "minutes once.", "meta": True, "default": False},
        ],
    },
    "helixsr": {
        "id": "helixsr",
        "kind": "helixsr",
        "title": "HelixSR",
        "subtitle": "DLSS Model E network as D3D12 compute shaders on any modern GPU",
        "repo": "lonewolf0622/HelixSR",
        "asset": r"^HelixSR-\d+(\.\d+)+\.zip$",
        "asset_fallback": r"^HelixSR-[\d.]+.*\.zip$",
        "asset_exclude": r"(?i)(scaler|desktop|\.exe$|\.deb$|appimage)",
        # pinned to the 1.x line: 2.0's setup adds a frame-generation step ([helixsr-fg]) that downloads NVIDIA's full
        # Linux driver package for the NGX core and then wants a game folder, so its unattended network build fails.
        # 1.x builds the same upscaling network without it.
        "max_major": 1,
        "author": "lonewolf0622 (Lone Wolf)",
        "author_short": "lonewolf0622",
        "license": "HelixSR Freeware License (unmodified redistribution only); network built locally from NVIDIA's DLL",
        "family": "dlss",
        "native_fg": False,
        "gpu": {
            "families": ["RDNA1", "RDNA2", "RDNA3", "RDNA3.5", "RDNA4"],
            "experimental": ["GCN5", "Turing", "Ampere", "Ada", "Blackwell", "Pascal", "Alchemist", "Xe2", "Xe-LPG",
                             "Intel", "NVIDIA", "Turing-GTX"],
            "note": "Developed on AMD RDNA (Steam Deck included). NVIDIA and Intel GPUs are experimental per the author.",
        },
        "proton": {
            "recommended": None,
            "match": None,
            "note": "No specific Proton is required by the author: any current Proton works. One Proton or Wine must "
                    "be installed in Steam, since the one-time setup runs the shader compiler through it.",
        },
        "launch": {"env": {}, "dlls": {}},
        "needs_prepare": True,
        "prepare_note": "First use builds the upscaling network on this device (downloads NVIDIA's DLSS 310.7.0 DLL "
                        "from NVIDIA's GitHub, a portable Python on SteamOS). Takes a few minutes, once per version.",
        "routes": [["auto", "Automatic"], ["direct", "FSR 3.1 drop-in"], ["scaler", "HelixSR Scaler (OptiScaler)"]],
        "in_game": {"direct": "Select AMD FSR in the game.",
                    "scaler": "Select DLSS in the game (FSR / XeSS if it has no DLSS). Insert opens the menu."},
        "configs": [OPTI, "HelixSR/helixsr.ini", HELIX_INI],
        "logs": ["HelixSR/helixsr.log", "helixsr.log", "OptiScaler.log"],
        "locked": {},
        "upscalers": [
            {"id": "helix", "label": "HelixSR · DLSS Model E", "path": "native", "family": "dlss", "tech": "DLSS 3 CNN",
             "model": "Model E (HelixSR)", "hint": "NVIDIA's DLSS Model E network running as D3D12 compute shaders."},
            {"id": "fsr31", "label": "FSR 3.1 (Scaler)", "path": "fsr", "family": "fsr", "tech": "FSR 3.1",
             "model": "Analytical", "route": "scaler", "hint": "HelixSR Scaler's FSR 3.1 instead of the network."},
            {"id": "xess", "label": "XeSS (Scaler)", "path": "xess", "family": "xess", "tech": "XeSS", "model": "DP4a / XMX",
             "route": "scaler", "hint": "HelixSR Scaler's XeSS instead of the network."},
            {"id": "none", "label": "None (disable injection)", "path": "none", "family": "neutral", "tech": "Off",
             "model": "", "hint": "The game's own upscaler, nothing replaced."},
        ],
        "default_upscaler": "helix",
        "options": [
            {"id": "route", "type": "dropdown", "label": "Install method", "meta": True,
             "desc": "Drop-in replaces the game's FSR 3.1 DLL (works with anti-cheat). Scaler uses OptiScaler for "
                     "DLSS / XeSS / FSR 2 games.",
             "values": [["auto", "Automatic"], ["direct", "FSR 3.1 drop-in"], ["scaler", "HelixSR Scaler"]],
             "default": "auto"},
            {"id": "sharpener", "type": "toggle", "label": "RCAS sharpening", "route": "direct",
             "desc": "AMD's Robust Contrast Adaptive Sharpening after upscaling (RAT's default). Off = no sharpening, "
                     "like DLSS 4.",
             "file": HELIX_INI, "section": "Sharpening", "key": "Sharpener", "on": "rcas", "off": "off",
             "default": True},
            {"id": "sharpness", "type": "slider", "label": "Sharpness", "route": "direct",
             "desc": "Strength of the sharpener above.",
             "file": HELIX_INI, "section": "Sharpening", "key": "Sharpness", "min": 0, "max": 1, "step": 0.05,
             "default": 0.1},
            {"id": "wave_direct", "type": "dropdown", "label": "Wave size", "route": "direct",
             "desc": "Leave on Auto unless the picture is broken.",
             "file": HELIX_INI, "section": "Compatibility", "key": "WaveSize",
             "values": [["auto", "Auto"], ["64", "64"], ["32", "32"]], "default": "auto"},
            {"id": "int8", "type": "dropdown", "label": "Speed boost (INT8)", "route": "scaler",
             "desc": "INT8 math for the network: faster, tiny quality cost.",
             "file": OPTI, "section": "HelixSR", "key": "Int8",
             "values": [["auto", "Auto"], ["on", "On"], ["off", "Off"]], "default": "auto"},
            {"id": "wave_scaler", "type": "dropdown", "label": "Wave size", "route": "scaler",
             "desc": "RDNA3/3.5 with a broken picture: try 64.",
             "file": OPTI, "section": "HelixSR", "key": "WaveSize",
             "values": [["auto", "Auto"], ["64", "64"], ["32", "32"]], "default": "auto"},
            {"id": "cas", "type": "toggle", "label": "RCAS sharpening", "route": "scaler",
             "desc": "AMD's Robust Contrast Adaptive Sharpening on top of the upscaler (RAT's default).",
             "file": OPTI, "section": "CAS", "key": "Enabled", "on": "true", "off": "false", "default": True},
        ],
    },
    "optiscaler": {
        "id": "optiscaler",
        "kind": "optiscaler",
        "title": "OptiScaler",
        "subtitle": "Swap DLSS / FSR / XeSS inputs for DLSS, FSR 4, FSR 3.1 or XeSS on any GPU",
        "repo": "optiscaler/OptiScaler",
        "asset": r"(?i)^optiscaler.*\.(7z|zip)$",
        "asset_fallback": r"(?i)\.(7z|zip)$",
        "asset_exclude": r"(?i)(source|\.exe$)",
        "author": "OptiScaler team (cdozdil and contributors)",
        "author_short": "cdozdil",
        "license": "GPL-3.0 (bundles AMD FidelityFX, Intel XeSS under their licenses)",
        "family": "fsr",
        "native_fg": True,
        "gpu": {"families": ["any"], "note": "Works on any GPU. DLSS needs NVIDIA RTX; FSR 4 is native on RDNA4 and "
                                             "uses the INT8 model elsewhere (experimental)."},
        "proton": {
            "recommended": "GE-Proton11-3",
            "match": r"^(GE-Proton1[01]|proton-cachyos|Proton.?Experimental|Proton 1[01])",
            "note": "OptiScaler 0.9 needs a recent Proton whose VKD3D supports FSR 4.1 / FFX SDK 2.3 (GE-Proton 11, "
                    "Proton-CachyOS or Proton Experimental). Do not combine with PROTON_USE_OPTISCALER.",
        },
        "launch": {"env": {}, "dlls": {"dxgi": "n,b"}},
        "in_game": "Select DLSS (or FSR / XeSS) in the game; OptiScaler swaps it. Insert opens the menu.",
        "configs": [OPTI, "fakenvapi.ini"],
        "logs": ["OptiScaler.log"],
        "locked": {},
        "skip_files": [r"(?i)^setup_(linux\.sh|windows\.bat)$", r"(?i)^!!.*", r"(?i)^remove_optiscaler\.sh$"],
        "upscalers": _OPTI_UPSCALERS,
        "default_upscaler": "fsr31",
        "options": _OPTI_OPTIONS,
    },
}

# keys that are only written when the add-on's INI already has them (newer OptiScaler builds)
OPTIONAL_KEYS = {("fsr", "fsr4forcemodel"), ("menu", "disablesplash"), ("plugins", "loadreshade"),
                 ("hotfix", "created3d12deviceforluma"), ("menu", "shortcutkey")}

# What an imported fork behaves like: its kind decides the install logic
KIND_TITLES = {"d4r": "d4r-compatible", "helixsr": "HelixSR-compatible", "optiscaler": "OptiScaler-compatible"}


def addon_for(source_id: str | None, kind: str | None) -> dict:
    """The catalog entry for an installed or selected source: its own when official, else its kind's."""
    if source_id and source_id in ADDONS:
        return ADDONS[source_id]
    return ADDONS.get(kind or "optiscaler", ADDONS["optiscaler"])


def usable(u: dict, gpu: dict | None) -> bool:
    """NVIDIA-only upscalers (real DLSS through OptiScaler) are hidden on GPUs that cannot run them."""
    return not u.get("requires") or bool((gpu or {}).get(u["requires"]))


def upscalers_for(addon: dict, gpu: dict | None) -> list[dict]:
    return [u for u in addon["upscalers"] if usable(u, gpu)]


def default_upscaler(addon: dict, gpu: dict | None) -> str:
    """The add-on's default upscaler, or the best one this GPU can run (FSR when DLSS needs an RTX GPU it lacks)."""
    u = upscaler(addon, addon["default_upscaler"])
    if u and usable(u, gpu):
        return u["id"]
    return default_for_path(addon, "fsr", gpu) or next(x["id"] for x in upscalers_for(addon, gpu) if x["id"] != "none")


def upscaler(addon: dict, uid: str | None) -> dict | None:
    return next((u for u in addon["upscalers"] if u["id"] == uid), None)


def default_for_path(addon: dict, path: str, gpu: dict | None = None, route: str | None = None) -> str | None:
    """The upscaler a mode switch picks: FSR 4 on RDNA4 for the FSR path, the add-on default otherwise."""
    cands = [u for u in upscalers_for(addon, gpu) if u.get("path") == path
             and (not u.get("route") or u["route"] == route or not route)]
    if not cands:
        return None
    fam = (gpu or {}).get("family")
    if path == "fsr":
        for pref in (["fsr4"] if fam == "RDNA4" else []) + ["fsr31", "fsr4", "fsr22"]:
            if any(u["id"] == pref for u in cands):
                return pref
    if any(u["id"] == addon["default_upscaler"] for u in cands):
        return addon["default_upscaler"]
    return cands[0]["id"]


def paths_of(addon: dict, route: str | None = None, gpu: dict | None = None) -> list[str]:
    out = []
    for u in (upscalers_for(addon, gpu) if gpu is not None else addon["upscalers"]):
        if u.get("route") and route and u["route"] != route:
            continue
        p = u.get("path")
        if p in PATHS and p not in out:
            out.append(p)
    return out


def upscaler_edits(kind: str, upscaler_id: str, gpu_family: str = "unknown", route: str | None = None
                   ) -> list[tuple[str, str, str, object]]:
    """(file, section, key, value) edits for an upscaler choice ('none' is handled as deactivation). A value may be a
    list of candidates (see FFX12); the installer writes the first one the INI documents."""
    if kind == "d4r":
        if upscaler_id in ("K", "M", "L", "E", "F", "game"):
            return [(D4R_INI, "DLSS", "Model", upscaler_id), (OPTI, "Upscalers", "Dx12Upscaler", "dlss")]
        return []
    if kind == "helixsr":
        if route != "scaler":
            return []
        if upscaler_id == "helix":
            return [(OPTI, "Upscalers", "Dx12Upscaler", "auto")]
        if upscaler_id == "fsr31":
            return [(OPTI, "Upscalers", "Dx12Upscaler", FFX12), (OPTI, "FSR", "UpscalerIndex", "1")]
        if upscaler_id == "xess":
            return [(OPTI, "Upscalers", "Dx12Upscaler", "xess")]
        return []
    # OptiScaler and its forks
    table: dict[str, tuple] = {
        "auto": ("auto", "auto", "auto", "auto"),
        "fsr4": (FFX12, FFX11, FFX11, "0"),
        "fsr31": (FFX12, ["fsr31", "ffx_12"], FFX12, "1"),
        "fsr22": ("fsr22", "fsr22", "fsr22", "auto"),
        "xess": ("xess", "xess_12", "xess", "auto"),
        "dlss": ("dlss", "dlss", "dlss", "auto"),
    }
    if upscaler_id not in table:
        return []
    dx12, dx11, vk, idx = table[upscaler_id]
    edits = [(OPTI, "Upscalers", "Dx12Upscaler", dx12), (OPTI, "Upscalers", "Dx11Upscaler", dx11),
             (OPTI, "Upscalers", "VulkanUpscaler", vk), (OPTI, "FSR", "UpscalerIndex", idx)]
    if upscaler_id == "fsr4":
        edits.append((OPTI, "FSR", "Fsr4ForceModel", "auto" if gpu_family == "RDNA4" else "2"))
    else:
        edits.append((OPTI, "FSR", "Fsr4ForceModel", "auto"))
    return edits


def gpu_support(addon: dict, gpu: dict) -> dict:
    """{level: ok|experimental|unsupported|unknown, text}"""
    fam = gpu.get("family", "unknown")
    g = addon.get("gpu", {})
    fams = g.get("families", [])
    if "any" in fams:
        return {"level": "ok", "text": "Works on this GPU."}
    if fam in fams:
        return {"level": "ok", "text": f"Supported on {fam}."}
    if fam in g.get("experimental", []):
        return {"level": "experimental", "text": f"Experimental on {fam}. {g.get('note', '')}".strip()}
    if fam in ("unknown",):
        return {"level": "unknown", "text": "Could not detect the GPU architecture."}
    return {"level": "unsupported", "text": g.get("note", "Not supported on this GPU.")}


def option_visible(opt: dict, route: str | None, gpu_family: str) -> bool:
    if opt.get("route") and route and opt["route"] != route:
        return False
    if opt.get("route") and not route:
        return False
    if opt.get("gpu") and gpu_family not in opt["gpu"]:
        return False
    return True


def public_catalog() -> list[dict]:
    """The catalog without regexes, for the frontend."""
    out = []
    for a in ADDONS.values():
        out.append({k: v for k, v in a.items() if k not in ("asset", "asset_fallback", "asset_exclude", "locked",
                                                             "skip_files")})
    return out
