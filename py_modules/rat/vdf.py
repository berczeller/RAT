"""Minimal reader for Valve's text KeyValues (VDF/ACF) files: libraryfolders.vdf, appmanifest_*.acf, config.vdf."""
from __future__ import annotations

from pathlib import Path
from typing import Any

_ESC = {"n": "\n", "t": "\t", "\\": "\\", '"': '"'}


def _tokens(text: str):
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in " \t\r\n":
            i += 1
        elif c == "/" and text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j + 1
        elif c in "{}":
            yield c
            i += 1
        elif c == '"':
            i += 1
            out = []
            while i < n and text[i] != '"':
                if text[i] == "\\" and i + 1 < n:
                    out.append(_ESC.get(text[i + 1], text[i + 1]))
                    i += 2
                else:
                    out.append(text[i])
                    i += 1
            i += 1
            yield ("s", "".join(out))
        elif c == "[":   # conditional like [$WIN32]: skipped
            j = text.find("]", i)
            i = n if j < 0 else j + 1
        else:
            j = i
            while j < n and text[j] not in ' \t\r\n{}"':
                j += 1
            yield ("s", text[i:j])
            i = j


def loads(text: str) -> dict[str, Any]:
    """Parses VDF text into nested dicts. Keys keep their case; lookups should go through get_ci."""
    root: dict[str, Any] = {}
    stack = [root]
    key = None
    for tok in _tokens(text):
        if tok == "{":
            new: dict[str, Any] = {}
            if key is not None:
                stack[-1][key] = new
            stack.append(new)
            key = None
        elif tok == "}":
            if len(stack) > 1:
                stack.pop()
            key = None
        else:
            s = tok[1]
            if key is None:
                key = s
            else:
                stack[-1][key] = s
                key = None
    return root


def load(path: str | Path) -> dict[str, Any]:
    try:
        return loads(Path(path).read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return {}


def get_ci(d: Any, *keys: str, default: Any = None) -> Any:
    """Case-insensitive nested lookup: get_ci(cfg, 'InstallConfigStore', 'Software', 'Valve', 'Steam')."""
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        if k in cur:
            cur = cur[k]
            continue
        low = k.lower()
        for kk, v in cur.items():
            if kk.lower() == low:
                cur = v
                break
        else:
            return default
    return cur
