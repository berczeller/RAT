"""Unreal Engine user config in the game's Proton prefix (Engine.ini), for fixes OptiScaler's wiki gives per engine.

OptiFG → XeFG fed by DLSS inputs needs Unreal's dilated motion vectors off; OptiScaler's menu then says it "requires
disabling (dilated) motion vectors". The documented fix (OptiScaler wiki, Frame Generation Options):

    [SystemSettings]
    r.NGX.DLSS.DilateMotionVectors=0

in <prefix>/drive_c/users/steamuser/AppData/Local/<Project>/Saved/Config/<Windows|WindowsNoEditor|WinGDK>/Engine.ini.
RAT writes it when that method is saved, records what it wrote in the game's record, and takes it out again when the
method changes or the injector is removed. Values the user set there themselves are left alone.
"""
from __future__ import annotations

from pathlib import Path

from . import ini

DILATE = ("SystemSettings", "r.NGX.DLSS.DilateMotionVectors")
PLATFORMS = ("Windows", "WindowsNoEditor", "WinGDK")


def prefix(g: dict) -> Path | None:
    """The game's Proton prefix (steamapps/compatdata/<appid>/pfx)."""
    if not g.get("appid"):
        return None
    for up in Path(g["folder"]).parents:
        if up.name.lower() == "steamapps":
            p = up / "compatdata" / str(g["appid"]) / "pfx"
            return p if p.is_dir() else None
    return None


def engine_ini_paths(g: dict, project: str | None, version: str | None) -> list[Path]:
    """Existing Engine.ini config folders for the project, or the folder the engine version creates on first run."""
    pfx = prefix(g)
    if not pfx or not project:
        return []
    local = pfx / "drive_c" / "users" / "steamuser" / "AppData" / "Local"
    bases = [local / project]
    if " " in project:
        bases.append(local / project.replace(" ", ""))
    found = []
    for base in bases:
        cfg = base / "Saved" / "Config"
        for plat in PLATFORMS:
            if (cfg / plat).is_dir():
                found.append(cfg / plat / "Engine.ini")
    if found:
        return found
    plat = "WindowsNoEditor" if version == "4" else "Windows"
    return [bases[0] / "Saved" / "Config" / plat / "Engine.ini"]


def set_dilation_off(g: dict, prof: dict, on: bool, owned: list[str]) -> tuple[list[str], str | None]:
    """on=True writes r.NGX.DLSS.DilateMotionVectors=0, on=False removes what RAT wrote. Returns (paths RAT owns now,
    a note for the job log / panel)."""
    owned = list(owned or [])
    if prof.get("engine") != "unreal":
        return owned, None
    if on:
        note = None
        for p in engine_ini_paths(g, prof.get("project"), prof.get("version")):
            doc = ini.read(p) if p.exists() else ini.IniDoc("")
            cur = doc.get(*DILATE)
            if cur is not None and str(p) not in owned:
                note = f"Engine.ini already sets {DILATE[1]}={cur}; left as you set it"
                continue
            p.parent.mkdir(parents=True, exist_ok=True)
            doc.set(DILATE[0], DILATE[1], "0")
            ini.write(p, doc)
            if str(p) not in owned:
                owned.append(str(p))
            note = note or f"Unreal's dilated motion vectors switched off for XeFG ({p.parent.name}/Engine.ini)"
        return owned, note
    for s in owned:
        p = Path(s)
        if p.is_file():
            doc = ini.read(p)
            if doc.remove(*DILATE):
                ini.write(p, doc)
    return [], ("Unreal's dilated motion vectors back to the game's default" if owned else None)
