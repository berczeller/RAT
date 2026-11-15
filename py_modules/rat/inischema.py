"""Controller-friendly fields for the add-ons' INI files (OptiScaler.ini, d4r.ini, helixsr.ini, fakenvapi.ini).

Every key becomes a field the sidebar can show with a gamepad: a choice list, a range slider or a text value. The control
is derived from the comment block the add-on authors write above each key, so new keys in future releases show up on
their own. OptiScaler documents its keys in a regular way, e.g.

    ; Selects upscaler backend for FSR3.X/4
    ; 0 = FSR 4.0.2 | 1 = FSR 3.1.5 | 2 = FSR 2.3.4
    ; Depending on GPU - Default (auto) is 0 - FSR4 for RDNA4, FSR3 for the rest
    UpscalerIndex=auto

d4r and HelixSR write free prose, so their keys get hand-written hints (KNOWN) taken from their documentation.
"""
from __future__ import annotations

import re

from .ini import IniDoc

FIELD_SEP = "\u0001"   # field id = section + FIELD_SEP + key

# (file kind, section, key) -> choices [[value, label]] or a range {"min", "max", "step"}; lower-case section and key
BOOL = [["true", "On"], ["false", "Off"]]
ONOFF = [["on", "On"], ["off", "Off"]]
KNOWN: dict[tuple[str, str, str], dict] = {
    # d4r (packaging/d4r.ini, D4R_README.txt)
    ("d4r", "dlss", "model"): {"choices": [["K", "K · DLSS 4 (default)"], ["M", "M · DLSS 4.5 (Performance)"],
                                           ["L", "L · DLSS 4.5 (Ultra Perf., experimental)"], ["E", "E · DLSS 3 CNN"],
                                           ["F", "F · DLSS 3 CNN (deprecated)"], ["game", "Game / OptiScaler choice"]]},
    ("d4r", "dlss", "showwatermark"): {"choices": BOOL},
    ("d4r", "rayreconstruction", "enable"): {"choices": [["auto", "Auto (when available)"], ["false", "Off"]]},
    ("d4r", "rayreconstruction", "model"): {"choices": [["auto", "Auto (game choice)"], ["default", "Default"],
                                                        ["D", "D"], ["E", "E (transformer)"], ["F", "F (DLSS 4.5)"]]},
    ("d4r", "rayreconstruction", "backend"): {"choices": [["dlss", "NVIDIA DLSS RR"],
                                                          ["fsr", "AMD FSR Ray Regeneration (experimental)"]]},
    ("d4r", "rayreconstruction", "fsrstabilitybias"): {"min": 0, "max": 1, "step": 0.05, "auto": True},
    ("d4r", "rayreconstruction", "fsrdetail"): {"min": 0, "max": 1, "step": 0.05},
    ("d4r", "latency", "frameage"): {"choices": [["0", "0 · own result every frame"], ["1", "1"], ["2", "2"],
                                                 ["3", "3 · most fps, most lag"]]},
    ("d4r", "kernels", "preferaccuracy"): {"choices": BOOL},
    ("d4r", "kernels", "nativeswinencoders"): {"choices": BOOL},
    ("d4r", "kernels", "nativekernels"): {"choices": ONOFF},
    ("d4r", "kernels", "nativefp8"): {"choices": ONOFF},
    ("d4r", "interop", "motionvectordilation"): {"choices": [["0", "Off"], ["1", "Radius 1"], ["2", "Radius 2"]]},
    ("d4r", "interop", "motiondilationthinsky"): {"choices": BOOL},
    ("d4r", "interop", "vraminterop"): {"choices": BOOL},
    ("d4r", "interop", "linearinputs"): {"choices": BOOL},
    ("d4r", "interop", "elidengxsync"): {"choices": BOOL},
    ("d4r", "interop", "directoutput"): {"choices": [["auto", "Auto"], ["true", "On"], ["false", "Off"]]},
    ("d4r", "engine", "enabled"): {"choices": [["auto", "Auto (when models are built)"], ["true", "Always"],
                                               ["false", "Never"]]},
    ("d4r", "engine", "network"): {"choices": [["auto", "Auto"], ["vulkan", "Vulkan shader"], ["hip", "HIP kernels"]]},
    ("d4r", "debug", "profile"): {"choices": BOOL},
    # HelixSR (README "Settings")
    ("helixsr", "sharpening", "sharpener"): {"choices": [["off", "Off (like DLSS 4)"], ["nvidia", "NVIDIA"],
                                                         ["rcas", "AMD RCAS"]]},
    ("helixsr", "sharpening", "sharpness"): {"min": 0, "max": 1, "step": 0.05},
    ("helixsr", "compatibility", "wavesize"): {"choices": [["auto", "Auto"], ["64", "64"], ["32", "32"]]},
    # OptiScaler keys whose comments are ambiguous
    ("optiscaler", "xefg", "interpolationcount"): {"choices": [["1", "2X"], ["2", "3X"], ["3", "4X"]]},
    ("optiscaler", "framerate", "frameratelimit"): {"min": 0, "max": 360, "step": 1, "auto": True},
    ("optiscaler", "dlssg", "frameratetargetdmfg"): {"min": 0, "max": 360, "step": 1, "auto": True},
    ("optiscaler", "framegen", "allowedframeahead"): {"choices": [["1", "1 · lowest latency"], ["2", "2"], ["3", "3"]]},
    ("optiscaler", "dlssg", "interpolationcount"): {"choices": [["1", "2X"], ["2", "3X"], ["3", "4X"], ["4", "5X"],
                                                              ["5", "6X"]]},
    ("optiscaler", "fakenvapi", "forcereflex"): {"choices": [["0", "Follow the game"], ["1", "Force off"],
                                                             ["2", "Force on"]]},
    ("optiscaler", "fakenvapi", "latencyflexmode"): {"choices": [["0", "Conservative"], ["1", "Aggressive"],
                                                                 ["2", "Reflex frame IDs"]]},
    ("optiscaler", "framegen", "ftinput"): {"choices": [["0", "Input (DLSSG/FSRFG/OptiFG)"],
                                                        ["1", "Time between Present calls"], ["2", "Zero (XeFG only)"]]},
}

_DEFAULT = re.compile(r"default\s*(?:\(auto\))?\s*(?:is|:|=)?\s*(.+)$", re.I)
_PAIR = re.compile(r"^\s*(-?(?:0x)?[0-9A-Fa-f.]+|[A-Za-z][\w.]*)\s*(?:=|:)\s*(.+?)\s*$")
_LOOSE_PAIR = re.compile(r"^\s*(-?(?:0x)?[0-9A-Fa-f]+)\s+([A-Za-z0-9].*?)\s*$")
_DASH_PAIR = re.compile(r"^\s*(-?\d+)\s+-\s+(.+?)\s*$")
_LINE_PAIR = re.compile(r"^\s*(-?\d+)\s*(?:=|:|-)\s+(.+?)\s*$")
_TOKEN = re.compile(r"^([A-Za-z0-9_.+-]{1,24})(?:\s*\(([^)]*)\))?$")
_TOKEN_DESC = re.compile(r"^([A-Za-z][A-Za-z0-9_]{1,23})\s{1,}-\s+(.+)$")
_NUM = r"(-?\d+(?:\.\d+)?)"
_RANGE = [re.compile(r"between\s+" + _NUM + r"\s+and\s+" + _NUM, re.I),
          re.compile(r"(?:^|from\s+)" + _NUM + r"\s+to\s+" + _NUM, re.I),
          re.compile(r"^" + _NUM + r"\s+-\s+" + _NUM + r"(?:\s|$)")]
_FREE = re.compile(r"\b(integer|float|uint|string|any set|path|seconds|in gbs|method names|process names)\b", re.I)


def humanize(key: str) -> str:
    """FGInput -> FG Input, Dx12Upscaler -> Dx12 Upscaler, UseHQFont -> Use HQ Font."""
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", key)
    return s.replace("_", " ").strip()


def _split_tokens(s: str) -> list[str]:
    """'a, b (x, y) or c' -> ['a', 'b (x, y)', 'c'] (commas inside parentheses kept)."""
    out, cur, depth = [], "", 0
    i = 0
    while i < len(s):
        ch = s[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if depth == 0 and (ch == "," or s.startswith(" or ", i)):
            out.append(cur.strip())
            cur = ""
            i += 4 if s.startswith(" or ", i) else 1
            continue
        cur += ch
        i += 1
    if cur.strip():
        out.append(cur.strip())
    return out


def _choices_from(lines: list[str]) -> tuple[list[list[str]], dict[str, str], bool]:
    """(choices, per-value hints, free) from a comment block."""
    pairs: list[list[str]] = []
    # 1) "0 = A | 1 = B" or "0 = Trace / 1 = Debug" (several lines may continue one list)
    for l in lines:
        body = _DEFAULT.split(l)[0] if " - Default" in l else l
        sep = "|" if "|" in body else (" / " if re.search(r"\d\s*=\s*\w+\s+/\s+\d", body) else None)
        if not sep:
            continue
        segs = body.split(sep)
        got = [_PAIR.match(seg) for seg in segs]
        if sum(1 for m in got if m) >= 2:
            for seg, m in zip(segs, got):
                m = m or _LOOSE_PAIR.match(seg)   # "4 Full" next to "3 = Detailed + Graph"
                if m:
                    pairs.append([m.group(1), _label(m.group(1), m.group(2).rstrip(" ;,"))])
    if len(pairs) >= 2:
        return _dedup(pairs), {}, any(_FREE.search(l) for l in lines)
    # 2) "0 - follow in-game setting, 1 - force disable" or "0 = For FSR Native AA, 1 = Ultra Quality"
    for l in lines:
        segs = [s.strip() for s in l.split(",")]
        got = [_DASH_PAIR.match(s) or _PAIR.match(s) for s in segs]
        if len(segs) >= 2 and all(got) and all(re.match(r"-?\d", m.group(1)) for m in got):
            return _dedup([[m.group(1), _label(m.group(1), m.group(2))] for m in got]), {}, False
    # 3) one value per line: "0 = conservative" / "1 = aggressive, ..."
    per_line = [_LINE_PAIR.match(l) for l in lines]
    per_line = [m for m in per_line if m]
    if len(per_line) >= 2:
        return _dedup([[m.group(1), _label(m.group(1), m.group(2))] for m in per_line]), {}, False
    # 4) a token list: "nofg, dlssg, fsrfg - Default (auto) is nofg", "rcas, da or lcda"
    hints: dict[str, str] = {}
    for l in lines:
        m = _TOKEN_DESC.match(l)
        if m:
            hints[m.group(1).lower()] = m.group(2)
    for l in lines:
        body = l.split(" - Default")[0].split(" - default")[0].strip()
        if "," not in body and " or " not in body:
            continue
        if re.search(r"\btrue or false\b|\bor (more|less|higher|lower|above|below)\b", body, re.I):
            continue
        toks = _split_tokens(body)
        free = False
        clean = []
        for t in toks:
            if t in ("...", "…") or t.endswith("..."):
                free = True
                t = t.rstrip(". ").strip()
                if not t:
                    continue
            m = _TOKEN.match(t)
            if not m:
                clean = []
                break
            clean.append([m.group(1), f"{m.group(1)} ({m.group(2)})" if m.group(2) else m.group(1)])
        if len(clean) >= 2:
            return _dedup(clean), hints, free
    return [], hints, False


def _label(value: str, text: str) -> str:
    """'0', 'FSR 4.0.2' -> '0 · FSR 4.0.2' (the value stays visible: it is what the INI holds)."""
    text = text.strip()
    if len(text) > 48:
        text = text[:46].rstrip(" ,;") + "…"
    return text if text.lower() == value.lower() else f"{value} · {text}"


def _dedup(pairs: list[list[str]]) -> list[list[str]]:
    seen, out = set(), []
    for v, l in pairs:
        if v.lower() in seen:
            continue
        seen.add(v.lower())
        out.append([v, l])
    return out


def _range_from(lines: list[str]) -> tuple[float, float] | None:
    for l in lines:
        body = l.split(" - Default")[0]
        for rx in _RANGE:
            m = rx.search(body.strip())
            if m:
                lo, hi = float(m.group(1)), float(m.group(2))
                if hi > lo:
                    return lo, hi
    return None


def _nice_step(lo: float, hi: float, ints: bool) -> float:
    span = hi - lo
    if ints:
        return 1 if span <= 100 else (10 if span <= 2000 else 100)
    for s in (0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1, 5, 10, 50):
        if span / s <= 120:
            return s
    return 100


def _default_of(lines: list[str]) -> str | None:
    for l in reversed(lines):
        m = _DEFAULT.search(l)
        if m:
            v = m.group(1).strip().rstrip(".")
            return v[:80]
    return None


def field(kind: str, entry: dict, locked: str | None = None) -> dict:
    """The control for one INI entry {section, key, value, comments}."""
    sec, key, value = entry["section"], entry["key"], entry["value"]
    lines = entry["comments"]
    desc = " ".join(l for l in lines[:3] if not _DEFAULT.search(l) or len(lines) == 1)
    out = {"id": sec + FIELD_SEP + key, "section": sec, "key": key, "label": humanize(key), "desc": desc if len(desc) <= 170 else desc[:168].rstrip(" ,;.") + "…",
           "help": lines[:14], "value": value, "default": _default_of(lines), "type": "text", "choices": [],
           "hints": {}, "auto": value.lower() == "auto" or any("(auto)" in l.lower() for l in lines),
           "custom": True, "locked": locked}
    known = KNOWN.get((kind, sec.lower(), key.lower()))
    if known and "choices" in known:
        out.update(type="choice", choices=[list(c) for c in known["choices"]], custom=False,
                   auto=out["auto"] or any(c[0] == "auto" for c in known["choices"]))
    elif known and "min" in known:
        out.update(type="range", min=known["min"], max=known["max"], step=known["step"],
                   auto=known.get("auto", out["auto"]), custom=False)
    else:
        choices, hints, free = _choices_from(lines)
        is_bool = any(re.search(r"\btrue or false\b", l, re.I) for l in lines)
        if choices:
            out.update(type="choice", choices=choices, hints=hints, custom=free or bool(_FREE.search(" ".join(lines))))
        elif is_bool or (out["default"] or "").lower().split(" ")[0] in ("true", "false"):
            out.update(type="choice", choices=[list(c) for c in BOOL], custom=False)
        else:
            rng = _range_from(lines)
            if rng:
                ints = all(float(x).is_integer() for x in rng) and not any(re.search(r"\d\.\d", l) for l in lines
                                                                           if re.search(r"\bto\b|-|between", l))
                out.update(type="range", min=rng[0], max=rng[1], step=_nice_step(rng[0], rng[1], ints), custom=False)
            elif value.lower() in ("true", "false"):
                out.update(type="choice", choices=[list(c) for c in BOOL], custom=False)
            elif value.lower() in ("on", "off"):
                out.update(type="choice", choices=[list(c) for c in ONOFF], custom=False)
    if out["type"] == "choice":
        vals = {c[0].lower() for c in out["choices"]}
        if out["auto"] and "auto" not in vals:
            d = out["default"]
            out["choices"].insert(0, ["auto", f"Auto (default: {d})" if d else "Auto (default)"])
        if value and value.lower() not in {c[0].lower() for c in out["choices"]}:
            out["choices"].append([value, f"{value} (current)"])
    return out


def sections(doc: IniDoc) -> list[dict]:
    counts: dict[str, int] = {}
    order: list[str] = []
    for e in doc.entries():
        low = e["section"].lower()
        if low not in counts:
            counts[low] = 0
            order.append(e["section"])
        counts[low] += 1
    return [{"name": s, "count": counts[s.lower()]} for s in order if s]


def fields(kind: str, doc: IniDoc, section: str | None = None, only: list[str] | None = None,
           locked: dict[tuple[str, str], str] | None = None) -> list[dict]:
    """Fields of one section (or of the keys in only, as 'Section.Key'), first occurrence of each key."""
    locked = locked or {}
    want = {o.lower() for o in only} if only else None
    seen = set()
    out = []
    for e in doc.entries():
        sk = (e["section"].lower(), e["key"].lower())
        if sk in seen:
            continue
        seen.add(sk)
        if section is not None and sk[0] != section.lower():
            continue
        if want is not None and f"{sk[0]}.{sk[1]}" not in want:
            continue
        out.append(field(kind, e, locked.get(sk)))
    if want is not None:   # keep the caller's order
        rank = {w: i for i, w in enumerate(o.lower() for o in only)}
        out.sort(key=lambda f: rank.get(f"{f['section'].lower()}.{f['key'].lower()}", 999))
    return out
