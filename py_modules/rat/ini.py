"""Comment-preserving INI editing for OptiScaler.ini, d4r.ini and helixsr.ini.

The files are edited line by line so that comments, ordering and line endings stay exactly as the add-on authors wrote
them. Section and key names are matched case-insensitively. A section that appears twice (HelixSR Scaler's OptiScaler.ini
has a doubled [Upscalers] header) is treated as one: lookups and edits go to the first occurrence of the key.
"""
from __future__ import annotations

import re
from pathlib import Path

_SECTION = re.compile(r"^\s*\[([^\]]+)\]\s*(?:[;#].*)?$")
_KEY = re.compile(r"^(\s*)([^=;#\[\s][^=]*?)(\s*=\s*)(.*?)(\s*)$")


class IniDoc:
    def __init__(self, text: str = ""):
        self.newline = "\r\n" if "\r\n" in text else "\n"
        self.bom = text.startswith("﻿")
        if self.bom:
            text = text[1:]
        # only real line breaks: str.splitlines() would also split on \x0c, \x85 or   inside a value
        self.lines = re.split(r"\r\n|\n|\r", text)
        self.trailing_newline = text.endswith(("\n", "\r"))
        if self.trailing_newline or self.lines == [""]:
            self.lines.pop()
        self.encoding = "utf-8"

    # --- reading -------------------------------------------------------------------------------------------------

    def _walk(self):
        """Yields (line index, section lower, key, value) for every key line."""
        section = ""
        for i, line in enumerate(self.lines):
            m = _SECTION.match(line)
            if m:
                section = m.group(1).strip().lower()
                continue
            stripped = line.strip()
            if not stripped or stripped[0] in ";#":
                continue
            k = _KEY.match(line)
            if k:
                yield i, section, k.group(2).strip(), _strip_inline_comment(k.group(4))

    def get(self, section: str, key: str, default: str | None = None) -> str | None:
        s, kl = section.lower(), key.lower()
        for _, sec, k, v in self._walk():
            if sec == s and k.lower() == kl:
                return v
        return default

    def to_dict(self) -> dict[str, dict[str, str]]:
        out: dict[str, dict[str, str]] = {}
        for _, sec, k, v in self._walk():
            out.setdefault(sec, {}).setdefault(k.lower(), v)
        return out

    def sections(self) -> list[str]:
        seen, out = set(), []
        for line in self.lines:
            m = _SECTION.match(line)
            if m:
                name = m.group(1).strip()
                if name.lower() not in seen:
                    seen.add(name.lower())
                    out.append(name)
        return out

    # --- editing -------------------------------------------------------------------------------------------------

    def set(self, section: str, key: str, value: str) -> bool:
        """Sets section.key = value. Adds the key (at the end of the section) or the section when missing.
        Returns True when the file changed."""
        value = str(value)
        s, kl = section.lower(), key.lower()
        for i, sec, k, v in self._walk():
            if sec == s and k.lower() == kl:
                if v == value:
                    return False
                m = _KEY.match(self.lines[i])
                assert m
                comment = _inline_comment(m.group(4))
                self.lines[i] = f"{m.group(1)}{m.group(2)}{m.group(3)}{value}{comment}"
                return True
        # key missing: insert after the last key line of the (first) matching section
        start = None
        current = None
        last_key_line = None
        for i, line in enumerate(self.lines):
            m = _SECTION.match(line)
            if m:
                if current == s and start is not None:
                    break
                current = m.group(1).strip().lower()
                if current == s and start is None:
                    start = i
                    last_key_line = i
                continue
            if current == s and _KEY.match(line) and not line.strip().startswith((";", "#")):
                last_key_line = i
        if start is None:
            if self.lines and self.lines[-1].strip():
                self.lines.append("")
            self.lines += [f"[{section}]", f"{key}={value}"]
        else:
            assert last_key_line is not None
            self.lines.insert(last_key_line + 1, f"{key}={value}")
        return True

    def remove(self, section: str, key: str) -> bool:
        """Deletes every section.key line (case-insensitive). Returns True when something was removed."""
        s, kl = section.lower(), key.lower()
        drop = [i for i, sec, k, _ in self._walk() if sec == s and k.lower() == kl]
        for i in reversed(drop):
            del self.lines[i]
        return bool(drop)

    def entries(self) -> list[dict]:
        """Every key with the comment block written above it: [{section, key, value, comments}]. A comment block
        belongs to the keys that directly follow it (OptiScaler shares one block between e.g. its RenderPreset keys);
        a blank line ends it."""
        out: list[dict] = []
        section = ""
        block: list[str] = []
        last_key = False
        for line in self.lines:
            m = _SECTION.match(line)
            if m:
                section, block, last_key = m.group(1).strip(), [], False
                continue
            stripped = line.strip()
            if not stripped:
                block, last_key = [], False
                continue
            if stripped[0] in ";#":
                if last_key:
                    block, last_key = [], False
                body = stripped.lstrip(";#").strip()
                if body and not re.fullmatch(r"[-=_*~\s]+", body):
                    block.append(body)
                continue
            k = _KEY.match(line)
            if k:
                out.append({"section": section, "key": k.group(2).strip(), "value": _strip_inline_comment(k.group(4)),
                            "comments": list(block)})
                last_key = True
        return out

    def text(self) -> str:
        body = self.newline.join(self.lines)
        if self.trailing_newline or not self.lines:
            body += self.newline
        return ("﻿" if self.bom else "") + body


def _strip_inline_comment(v: str) -> str:
    # OptiScaler and d4r put comments on their own lines; d4r.ini has a few "value ; comment" lines
    m = re.match(r"^(.*?)\s+[;#].*$", v)
    return (m.group(1) if m else v).strip()


def _inline_comment(v: str) -> str:
    m = re.match(r"^.*?(\s+[;#].*)$", v)
    return m.group(1) if m else ""


def read(path: Path) -> IniDoc:
    raw = Path(path).read_bytes()
    # Unreal can save its .ini files as UTF-16 with a BOM: read them, and write them back, in that encoding
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        doc = IniDoc(raw.decode("utf-16", errors="replace"))
        doc.encoding = "utf-16"
        doc.bom = False   # the utf-16 codec writes its own BOM
        return doc
    return IniDoc(raw.decode("utf-8", errors="replace"))


def write(path: Path, doc: IniDoc) -> None:
    Path(path).write_text(doc.text(), encoding=doc.encoding, newline="")


def edit_file(path: Path, edits: list[tuple[str, str, str]]) -> bool:
    """Applies (section, key, value) edits to the file at path. Returns True when something changed."""
    path = Path(path)
    doc = read(path) if path.exists() else IniDoc("")
    changed = False
    for section, key, value in edits:
        changed |= doc.set(section, key, value)
    if changed:
        write(path, doc)
    return changed


def merge_user_values(new_text: str, old_text: str, locked: set[tuple[str, str]] = frozenset()) -> str:
    """The new release's INI with the user's values carried over: every key present in both files takes the old
    value, except locked keys (lower-case (section, key) pairs the add-on needs to keep at the new release's value).
    Keys the new release dropped are not brought back; keys it added keep their new defaults."""
    new = IniDoc(new_text)
    old = IniDoc(old_text).to_dict()
    have = new.to_dict()
    for sec, keys in old.items():
        for k, v in keys.items():
            if (sec, k) in locked or sec not in have or k not in have[sec]:
                continue
            if have[sec][k] != v:
                new.set(sec, k, v)
    return new.text()
