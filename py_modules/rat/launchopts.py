"""Merging RAT's environment variables into a game's existing Steam launch options.

Only what RAT manages is touched: its environment variables, its WINEDLLOVERRIDES entries and, while an add-on is
installed, GE-Proton/Proton-CachyOS's own OptiScaler switch (two OptiScalers in one game fight each other). Wrappers
such as gamemoderun or mangohud, other variables and arguments after %command% stay as the user wrote them.
"""
from __future__ import annotations

import re

COMMAND = "%command%"
_ASSIGN = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", re.S)

# Variables an injector may need (removed with it, and when wiping an injector RAT did not install)
INJECTOR_ENV = {"PROTON_FORCE_NVAPI", "DXVK_NVAPI_GPU_ARCH"}
# Variables RAT may set for any feature (injector, swapchain, HDR); removed when no longer wanted
MANAGED_ENV = INJECTOR_ENV | {"VKD3D_SWAPCHAIN_IMAGES", "DXVK_HDR", "PROTON_ENABLE_HDR", "ENABLE_HDR_WSI",
                              "ENABLE_GAMESCOPE_WSI", "DXVK_CONFIG"}
# DLL overrides RAT may add (injector proxy names, d4r's Ray Reconstruction set, ReShade proxies and its compiler)
INJECTOR_DLLS = {"dxgi", "winmm", "version", "dbghelp", "d3d12", "d3d12core", "wininet", "winhttp",
                 "icuuc", "icuin", "icudt"}
MANAGED_DLLS = INJECTOR_DLLS | {"d3d11", "d3d9", "d3d8", "opengl32", "d3dcompiler_47"}
# Built-in OptiScaler of GE-Proton / Proton-CachyOS: conflicts with a game-local OptiScaler
CONFLICTING_ENV = {"PROTON_USE_OPTISCALER", "PROTON_OPTISCALER_NAME"}


def split_tokens(s: str) -> list[str]:
    """Splits on whitespace outside quotes, keeping each token's raw text (quotes included)."""
    out, cur, quote, esc = [], [], None, False
    for ch in s:
        if esc:   # the character after a backslash is literal (FOO=a\ b is one token, "x\"y" one quoted string)
            cur.append(ch)
            esc = False
            continue
        if ch == "\\" and quote != "'":
            cur.append(ch)
            esc = True
            continue
        if quote:
            cur.append(ch)
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
            cur.append(ch)
        elif ch.isspace():
            if cur:
                out.append("".join(cur))
                cur = []
        else:
            cur.append(ch)
    if cur:
        out.append("".join(cur))
    return out


def unquote(v: str) -> str:
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    return v.replace('"', "").replace("'", "")


def parse_dll_overrides(value: str) -> list[tuple[str, str]]:
    """'a=n,b;b,c=n' -> [('a','n,b'), ('b','n'), ('c','n')] (Wine allows several names before one mode)."""
    out: list[tuple[str, str]] = []
    for part in unquote(value).split(";"):
        part = part.strip()
        if not part:
            continue
        if "=" in part:
            names, mode = part.split("=", 1)
        else:
            names, mode = part, ""
        for n in names.split(","):
            n = n.strip()
            if n:
                out.append((n, mode.strip()))
    return out


def parse(options: str) -> dict:
    """Breaks launch options into env assignments (ordered), wrapper tokens before %command% and the rest after it."""
    options = (options or "").strip()
    if COMMAND in options:
        before, after = options.split(COMMAND, 1)
        has_command = True
    else:
        # without %command% Steam appends the options to the game's command line
        before, after = "", options
        has_command = False
    env: list[tuple[str, str]] = []
    wrappers: list[str] = []
    for tok in split_tokens(before):
        m = _ASSIGN.match(tok)
        if m and not wrappers:
            env.append((m.group(1), m.group(2)))
        elif m and wrappers and wrappers[-1] == "env":
            env.append((m.group(1), m.group(2)))
        else:
            wrappers.append(tok)
    if wrappers == ["env"]:
        wrappers = []
    return {"env": env, "wrappers": wrappers, "after": after.strip(), "has_command": has_command}


def _quote_value(v: str) -> str:
    """Shell-safe: single quotes, so $(...), backticks and variables in a value are never expanded."""
    if v == "" or re.search(r"[\s;'\"$`\\|&<>(){}*?!#~]", v):
        return "'" + v.replace("'", "'\\''") + "'"
    return v


def merge(current: str, env: dict[str, str] | None = None, dlls: dict[str, str] | None = None,
          remove_conflicts: bool = True, owned_env: set[str] | None = None,
          owned_dlls: set[str] | None = None) -> str:
    """Returns the launch options with RAT's wanted env and DLL overrides applied.

    owned_env / owned_dlls are what RAT set for this game before (from its install record); those are removed
    when no longer wanted. When unknown (None) every variable and DLL RAT could have set counts as owned.
    With env and dlls empty (and remove_conflicts False) this strips what RAT added, e.g. after uninstalling."""
    env = dict(env or {})
    dlls = {k.lower(): v for k, v in (dlls or {}).items()}
    managed_env = MANAGED_ENV if owned_env is None else set(owned_env)
    managed_dlls = MANAGED_DLLS if owned_dlls is None else {d.lower() for d in owned_dlls}
    p = parse(current)
    out_env: list[tuple[str, str]] = []
    seen = set()
    overrides: list[tuple[str, str]] = []
    for k, v in p["env"]:
        if k == "WINEDLLOVERRIDES":
            overrides += parse_dll_overrides(v)
            continue
        if k in env:
            if k not in seen:
                out_env.append((k, _quote_value(env[k])))
                seen.add(k)
            continue
        if k in managed_env:
            continue
        if remove_conflicts and k in CONFLICTING_ENV:
            continue
        out_env.append((k, v))
    for k, v in env.items():
        if k not in seen:
            out_env.append((k, _quote_value(v)))
    # DLL overrides: the user's own first (in order), then ours
    merged: list[tuple[str, str]] = []
    for name, mode in overrides:
        low = name.lower()
        if low in dlls or low in managed_dlls:
            continue
        merged.append((name, mode))
    for name, mode in dlls.items():
        merged.append((name, mode))
    if merged:
        # group consecutive names that share a mode? keep it simple and explicit: one entry per DLL
        val = ";".join(f"{n}={m}" if m else n for n, m in merged)
        out_env.append(("WINEDLLOVERRIDES", f'"{val}"'))
    parts = [f"{k}={v}" for k, v in out_env] + p["wrappers"]
    if not parts and not p["has_command"]:
        return p["after"]
    if not parts and p["has_command"] and not p["after"]:
        return ""   # only "%command%" left: same as no options
    s = " ".join(parts + [COMMAND] + ([p["after"]] if p["after"] else []))
    return s.strip()


def describe(env: dict[str, str], dlls: dict[str, str]) -> str:
    """RAT's part on its own, for display: 'A=1 WINEDLLOVERRIDES="dxgi=n,b" %command%'."""
    return merge("", env, dlls, remove_conflicts=False) or COMMAND


# --- redundancy cleaner -----------------------------------------------------------------------------------------------

# Launch wrappers that only ever make sense once (a second copy runs the game twice through the same tool)
KNOWN_WRAPPERS = {"gamemoderun", "mangohud", "mangohud.x86", "mangohud.x86_64", "obs-gamecapture", "game-performance",
                  "prime-run", "strangle", "pvkrun", "dxvk-cache-tool"}


def _base(tok: str) -> str:
    return unquote(tok).rsplit("/", 1)[-1].lower()


def clean(options: str) -> tuple[str, list[str]]:
    """Scans launch options for redundancy that breaks or confuses a launch and returns (cleaned, notes):
    duplicate environment variables (the last value wins, as in the shell), several WINEDLLOVERRIDES (merged, last
    mode per DLL wins), repeated wrappers (gamemoderun, mangohud...), repeated %command% and repeated bare flags after
    it (-dx12 -dx12). Options without redundancy come back unchanged, byte for byte."""
    options = options or ""
    if not options.strip():
        return "", []
    notes: list[str] = []
    p = parse(options)
    env: dict[str, str] = {}
    order: list[str] = []
    overrides: list[tuple[str, str]] = []
    n_over = 0
    for k, v in p["env"]:
        if k == "WINEDLLOVERRIDES":
            n_over += 1
            overrides += parse_dll_overrides(v)
            continue
        if k in env:
            if env[k] != v:
                notes.append(f"{k} was set twice: kept the last value ({unquote(v)})")
            else:
                notes.append(f"Removed duplicate {k}")
        else:
            order.append(k)
        env[k] = v
    if n_over > 1:
        notes.append(f"Merged {n_over} WINEDLLOVERRIDES into one")
    dll: dict[str, str] = {}
    dll_order: list[str] = []
    for name, mode in overrides:
        low = name.lower()
        if low in dll:
            if dll[low] != mode or n_over <= 1:
                notes.append(f"DLL override {name} was listed twice: kept {name}={mode or '(default)'}")
        else:
            dll_order.append(low)
        dll[low] = mode
    names = {n.lower(): n for n, _ in overrides}
    wrappers: list[str] = []
    seen_wrappers: set[str] = set()
    for i, tok in enumerate(p["wrappers"]):
        b = _base(tok)
        if b in KNOWN_WRAPPERS and b in seen_wrappers:
            notes.append(f"Removed repeated wrapper {b}")
            continue
        nxt = p["wrappers"][i + 1] if i + 1 < len(p["wrappers"]) else ""
        # an env with its own options (env -u FOO, env -i ...) is doing work: only a bare one is stray
        if b == "env" and wrappers and not nxt.startswith("-"):
            notes.append("Removed a stray env")
            continue
        seen_wrappers.add(b)
        wrappers.append(tok)
    after = split_tokens(p["after"])
    cleaned_after: list[str] = []
    bare_seen: set[str] = set()
    for i, tok in enumerate(after):
        if tok == COMMAND:
            notes.append("Removed a repeated %command%")
            continue
        nxt = after[i + 1] if i + 1 < len(after) else None
        bare = tok.startswith("-") and (nxt is None or nxt.startswith("-") or nxt == COMMAND)
        if bare and tok in bare_seen:   # only a flag used without a value counts as a repeat
            notes.append(f"Removed repeated game argument {tok}")
            continue
        if bare:
            bare_seen.add(tok)
        cleaned_after.append(tok)
    if not notes:
        return options, []
    out_env = [(k, env[k]) for k in order]
    if dll_order:
        val = ";".join(f"{names[n]}={dll[n]}" if dll[n] else names[n] for n in dll_order)
        out_env.append(("WINEDLLOVERRIDES", f'"{val}"'))
    parts = [f"{k}={v}" for k, v in out_env] + wrappers
    tail = " ".join(cleaned_after)
    if not p["has_command"]:
        return (" ".join(parts + [tail]) if parts else tail).strip(), notes
    return " ".join(parts + [COMMAND] + ([tail] if tail else [])).strip(), notes
