"""Frame generation fallback for d4r.

d4r ships OptiScaler (as dxgi.dll) with its own OptiScaler.ini, but none of OptiScaler's frame generation libraries, so
its [FrameGen] methods have nothing to run. The fallback takes those libraries from the official OptiScaler release that
matches the build d4r bundles (same version, or the newest release of the same major.minor line) and puts them where
d4r's OptiScaler loads its libraries from ([Libraries] OptiDllPath). Nothing d4r ships is ever overwritten: a file that
is already there stays, and every file RAT adds is recorded so Uninstall, Reinstall and "Remove" take it out again.

Groups (picked per game):
  ffx    AMD FidelityFX frame generation (FSR FG): the FFX loader / frame generation DLLs
  xefg   Intel XeFG and XeLL (libxess_fg.dll, libxell.dll)

fakenvapi is never borrowed: d4r relies on Proton's dxvk-nvapi (PROTON_FORCE_NVAPI=1) to expose DLSS, and fakenvapi
would replace it.
"""
from __future__ import annotations

import re
from pathlib import Path

from . import framegen, ini, sources
from .jobs import Job
from .records import rec_file
from .util import Paths, UserError, log, place_file, sha256_of, write_json

OPTI_REPO = "optiscaler/OptiScaler"
OPTI_ASSET = r"(?i)^optiscaler.*\.(7z|zip)$"
OPTI_EXCLUDE = r"(?i)(source|\.exe$)"
# what d4r bundles today (its README: "OptiScaler 0.9.4 as dxgi.dll"); used when the build cannot be read from the game
DEFAULT_BUNDLED = "0.9.4"

GROUPS = {
    "ffx": {"label": "FSR frame generation", "family": "fsr", "default": True,
            "desc": "AMD FidelityFX frame generation libraries. Unlocks OptiFG → FSR FG (any game) and the methods that "
                    "turn the game's own FSR 3.1 / DLSS / XeSS frame generation into FSR FG.",
            "names": ["amd_fidelityfx_dx12.dll", "amd_fidelityfx_loader_dx12.dll",
                      "amd_fidelityfx_framegeneration_dx12.dll"]},
    "xefg": {"label": "XeFG + XeLL", "family": "xess", "default": False,
             "desc": "Intel's XeSS frame generation and its XeLL latency library. Unlocks OptiFG → XeFG. Heavier than "
                     "FSR FG on AMD; mostly useful on Intel GPUs or to compare pacing.",
             "names": ["libxess_fg.dll", "libxell.dll"]},
}

# "OptiScaler 0.9.4", "OptiScaler v0.9.4", "OptiScaler version: 0.9.4". OptiScaler is still on the 0.x line, so only
# 0.x numbers count: other numbers near the name (FFX / FSR SDK versions, ROCm, d4r's own version) are ignored.
_VER = re.compile(r"(?i)optiscaler[^0-9\n]{0,24}?v?(0\.\d+(?:\.\d+)?)\b")
# the build d4r documents and the official release always tried when the bundled build's own line has no release
PINNED_TAG = "v0.9.4"


def _vt(v: str | None) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v or "")[:3])


def _norm(v: str | None) -> str | None:
    t = _vt(v)
    if len(t) < 2:
        return None
    return ".".join(str(x) for x in (t + (0,))[:3])


def _plausible(v: str | None) -> bool:
    """An OptiScaler build number: 0.x (a version resource or text saying 3.0.0 is something else)."""
    t = _vt(v)
    return len(t) >= 2 and t[0] == 0 and t[1] >= 7


def bundled_version(exe: Path, rec: dict) -> tuple[str, str]:
    """The OptiScaler build d4r bundles in this game, and where RAT read it. Implausible numbers are skipped, and
    d4r's documented 0.9.4 is used when nothing reliable is found."""
    from .installer import peinfo_version
    exe = Path(exe)
    for name in ("dxgi.dll", "dxgi.dll.rat-off"):
        v = peinfo_version(exe / name)
        if _plausible(v):
            return _norm(v), f"{name} version resource"
    pkg = sources.get_package(rec.get("package_id", "")) or {}
    root = Path(pkg.get("path") or "/nonexistent")
    texts = sorted(root.glob("*.txt")) + sorted(root.glob("*.md")) + sorted(root.glob("d4r/*.txt"))
    for cand, label in [(c, f"d4r's {c.name}") for c in texts] + [(exe / "OptiScaler.log", "OptiScaler.log")]:
        try:
            if not cand.is_file():
                continue
            text = cand.read_text(encoding="utf-8", errors="replace")[:200_000]
        except OSError:
            continue
        for m in _VER.finditer(text):
            if _plausible(m.group(1)):
                return _norm(m.group(1)), label
    return DEFAULT_BUNDLED, "d4r's documented bundle"


def pick_release(version: str, rels: list[dict]) -> dict | None:
    """The donor release: the exact build first, else the newest stable release of the same major.minor line."""
    want = _vt(version)
    usable = [r for r in rels if not r.get("draft") and sources.pick_asset(r.get("assets") or [], OPTI_ASSET, None,
                                                                         OPTI_EXCLUDE)]
    exact = [r for r in usable if _vt(r.get("tag")) == want and not re.search(r"(?i)(pre|rc|nightly|beta)", r["tag"])]
    if exact:
        return exact[0]
    same = [r for r in usable if _vt(r.get("tag"))[:2] == want[:2] and not r.get("prerelease")]
    if same:
        return sorted(same, key=lambda r: _vt(r["tag"]), reverse=True)[0]
    return None


def _release_for(version: str) -> dict | None:
    """The official release for the bundled build's line, else the pinned v0.9.4. None when GitHub has neither."""
    try:
        rels = sources.releases(OPTI_REPO, force=True)
    except UserError:
        rels = []
    r = pick_release(version, rels)
    tags = [] if r else [f"v{version}", version]
    if not r and _vt(PINNED_TAG) != _vt(version):
        r = next((x for x in rels if x.get("tag") == PINNED_TAG and pick_release(PINNED_TAG, [x])), None)
        tags.append(PINNED_TAG)
    for tag in tags if not r else []:
        try:
            one = sources.release_by_tag(OPTI_REPO, tag)
        except UserError:
            continue
        r = pick_release(tag, [one])
        if r:
            break
    if r is None:
        return None
    a = sources.pick_asset(r["assets"], OPTI_ASSET, None, OPTI_EXCLUDE)
    return {"tag": r["tag"], "asset": a, "assets": r["assets"], "page": r.get("url")}


def _stored(groups: list[str]) -> list[dict]:
    """Every downloaded or imported OptiScaler / HelixSR package that ships the chosen groups' files."""
    out = []
    for p in sources.list_packages():
        if p.get("kind") not in ("optiscaler", "helixsr") or not Path(p.get("path", "")).is_dir():
            continue
        found = {gid for _, _, gid in donor_files(Path(p["path"]), groups)}
        if found >= set(groups):
            out.append(p)
    return out


def _label(p: dict) -> str:
    title = p.get("title") or p.get("source_id") or "package"
    return f"{p.get('version')}" if p.get("source_id") == "optiscaler" else f"{title} {p.get('version')}"


def donor_package(version: str, groups: list[str] | None = None, job: Job | None = None,
                  prefer: str | None = None) -> tuple[dict, str]:
    """The package to take the frame generation files from, and a note on how it was chosen. Order:
      1. a stored package of the bundled build (or the one used last time, or the same major.minor line);
      2. the official release for that line on GitHub, else the pinned v0.9.4 release;
      3. any other stored OptiScaler / HelixSR package that ships the files (version mismatch noted);
    otherwise an error that lists what the user can do."""
    groups = groups or list(GROUPS)
    want = _vt(version)
    have = _stored(groups)
    official = [p for p in have if p.get("kind") == "optiscaler"]
    for test, note in ((lambda p: _vt(p.get("version")) == want, "stored, same build"),
                       (lambda p: prefer and p.get("version") == prefer, "stored, used before"),
                       (lambda p: _vt(p.get("version"))[:2] == want[:2], "stored, same line")):
        hit = sorted((p for p in official if test(p)), key=lambda p: (p.get("source_id") != "optiscaler",
                                                                     tuple(-x for x in _vt(p.get("version")))))
        if hit:
            return hit[0], note
    if job:
        job.stage("fetch", f"Looking for OptiScaler {version} (or {PINNED_TAG}) on GitHub...", 0.05)
    rel = None
    try:
        rel = _release_for(version)
    except UserError:
        rel = None
    if rel:
        try:
            pkg = _download_donor(rel, official, job)
            if donor_files(Path(pkg["path"]), groups):
                note = "official release" if pick_release(version, [{"tag": rel["tag"], "assets": rel["assets"]}]) \
                    else f"official {PINNED_TAG} (no release for {version})"
                return pkg, note
        except UserError as e:
            log.warning("OptiScaler donor download failed: %s", e)
    if have:   # last resort: whatever stored add-on ships the files
        best = sorted(have, key=lambda p: (p.get("kind") != "optiscaler", tuple(-x for x in _vt(p.get("version")))))[0]
        if job:
            job.log(f"Using the frame generation files from the stored {_label(best)} package (d4r bundles OptiScaler "
                    f"{version}); if FG misbehaves, download OptiScaler {PINNED_TAG} and press Update.")
        return best, "stored add-on, version differs"
    raise UserError(
        f"No OptiScaler release or stored package with the frame generation files was found (d4r bundles OptiScaler "
        f"{version}). Try one of these, then press the button again: download OptiScaler on the Upscale tab (pick "
        f"OptiScaler, it only downloads until you inject); import an OptiScaler 0.9.x .7z / .zip with + Add fork "
        f"(link or file), e.g. github.com/{OPTI_REPO}/releases/tag/{PINNED_TAG}; or check the network.")


def _download_donor(rel: dict, have: list[dict], job: Job | None) -> dict:
    """Downloads and stores an official OptiScaler release (shared with the OptiScaler add-on's package store)."""
    for p in have:
        if p.get("source_id") == "optiscaler" and p.get("version") == rel["tag"]:
            return p
    for p in sources.list_packages():   # stored already, even if it lacked the files the filter wanted
        if p.get("source_id") == "optiscaler" and p.get("version") == rel["tag"] and Path(p.get("path", "")).is_dir():
            return p
    name = rel["asset"]["name"]
    dest = Paths.downloads() / name
    if job:
        job.stage("download", f"Downloading {name}...", 0.1)
    sources.download(rel["asset"]["url"], dest,
                     progress=(lambda f, t: job.sub(f, 0.1, 0.6, f"Downloading {name}: {t}")) if job else None,
                     should_cancel=job.cancelled if job else None, size_hint=rel["asset"].get("size"))
    sha = sources.expected_sha256(rel)
    if sha:
        if job:
            job.stage("verify", "Verifying checksum...", 0.62)
        if sha256_of(dest) != sha:
            dest.unlink(missing_ok=True)
            raise UserError("Checksum mismatch on the OptiScaler download. Nothing was changed.")
    if job:
        job.stage("extract", f"Extracting {name}...", 0.66)
    meta = sources.store_package(dest, {"source_id": "optiscaler", "version": rel["tag"], "asset": name,
                                        "url": rel["asset"]["url"], "sha256": sha, "kind": "optiscaler",
                                        "title": "OptiScaler"}, job.cancelled if job else None)
    dest.unlink(missing_ok=True)
    return meta


def donor_files(pkg_path: Path, groups: list[str]) -> list[tuple[Path, str, str]]:
    """(source file, file name, group) for the chosen groups, from the donor package's top folder or its library
    folder (newer OptiScaler builds keep their DLLs in OptiScaler/, HelixSR keeps its OptiScaler a level or two down).
    Files of one group come from one folder, so the FFX loader and its frame generation DLL always match."""
    root = Path(pkg_path)
    if not root.is_dir():
        return []
    files = [p for p in root.rglob("*") if p.is_file() and len(p.relative_to(root).parts) <= 4]
    out = []
    for gid in groups:
        g = GROUPS.get(gid)
        if not g:
            continue
        names = {n.lower() for n in g["names"]}
        by_dir: dict[Path, list[Path]] = {}
        for f in files:
            if f.name.lower() in names:
                by_dir.setdefault(f.parent, []).append(f)
        if not by_dir:
            continue
        # the folder with the most of the group's files, the shallowest on a tie
        best = sorted(by_dir, key=lambda d: (-len(by_dir[d]), len(d.relative_to(root).parts), str(d)))[0]
        out += [(f, f.name, gid) for f in sorted(by_dir[best], key=lambda f: f.name.lower())]
    return out


def target_dir(exe: Path) -> Path:
    """Where d4r's OptiScaler loads its libraries from."""
    p = Path(exe) / "OptiScaler.ini"
    return framegen.lib_dir(exe, ini.read(p) if p.exists() else None)


def place(mgr, g: dict, exe: Path, rec: dict, groups: list[str], job: Job | None = None, quiet: bool = False) -> dict:
    """Adds the chosen groups' files from the matching OptiScaler release. Files d4r already ships are left alone.
    quiet: called inside another job (a reinstall), so only log lines are added, no stages."""
    stage_job = None if quiet else job
    exe = Path(exe)
    if rec.get("kind") != "d4r":
        raise UserError("The frame generation fallback is for d4r installs")
    groups = [x for x in groups if x in GROUPS]
    if not groups:
        raise UserError("Pick at least one group of frame generation files")
    doc_p = exe / "OptiScaler.ini"
    if not doc_p.exists():
        raise UserError("d4r's OptiScaler.ini is missing: Repair d4r on the Upscale tab first")
    keys = {(e["section"].lower(), e["key"].lower()) for e in ini.read(doc_p).entries()}
    if ("framegen", "fginput") not in keys and ("framegen", "fgtype") not in keys:
        raise UserError("d4r's bundled OptiScaler has no [FrameGen] settings, so it cannot run frame generation")
    version, how = bundled_version(exe, rec)
    pkg, note = donor_package(version, groups, stage_job, (rec.get("fg_fallback") or {}).get("donor"))
    files = donor_files(Path(pkg["path"]), groups)
    if not files:
        raise UserError(f"{_label(pkg)} ships none of the chosen frame generation files")
    if job:
        job.log(f"Frame generation files from {_label(pkg)} ({note})")
    if stage_job:
        stage_job.stage("install", f"Adding frame generation files from {_label(pkg)}...", 0.8)
    dest = target_dir(exe)
    old = rec.get("fg_fallback") or {}
    ours = set(old.get("files") or [])
    added, skipped = [], []
    for src, name, gid in files:
        dst = dest / name
        rel = dst.relative_to(exe).as_posix()
        if (dst.exists() or dst.is_symlink()) and rel not in ours:
            skipped.append(rel)   # d4r ships it already: never replaced
            continue
        place_file(src, dst)
        added.append(rel)
        if job:
            job.log(f"Added {rel} ({GROUPS[gid]['label']})")
    for rel in ours - set(added):   # groups switched off
        (exe / rel).unlink(missing_ok=True)
    fb = {"donor": pkg.get("version"), "donor_label": _label(pkg), "donor_note": note, "bundled": version,
          "bundled_from": how, "groups": groups, "files": added, "kept": skipped}
    rec["fg_fallback"] = fb
    write_json(rec_file(exe), rec)
    log.info("%s: d4r FG fallback from %s (%s; bundled %s, %s): %s", g.get("name"), _label(pkg), note, version, how,
             ", ".join(added) or "nothing new")
    if stage_job:
        stage_job.stage("configure", "Done", 0.97)
    return fb


def remove(exe: Path, rec: dict) -> None:
    fb = rec.pop("fg_fallback", None) or {}
    for rel in fb.get("files") or []:
        if rel and not rel.startswith(("/", "..")) and "/../" not in rel:
            (Path(exe) / rel).unlink(missing_ok=True)
    p = Path(exe) / "OptiScaler.ini"
    if p.exists():   # nothing left to run frame generation with: switch it off
        doc = ini.read(p)
        edits = [(s, k, v) for (s, k), v in framegen.METHODS[0]["edits"].items() if doc.get(s, k) is not None]
        # the base framerate cap was written as base x FG multiplier: keep the same rendered cap without FG
        inp = framegen._norm("FrameGen", "FGInput", doc.get("FrameGen", "FGInput"))
        outp = framegen._norm("FrameGen", "FGOutput", doc.get("FrameGen", "FGOutput"))
        mult = framegen.cap_multiplier(inp, outp, doc.get("XeFG", "InterpolationCount"))
        try:
            cap = float(doc.get("Framerate", "FramerateLimit") or "")
        except ValueError:
            cap = 0.0
        if cap > 0 and mult > 1:
            edits.append(("Framerate", "FramerateLimit", str(round(cap / mult))))
        if doc.get("FrameGen", "FGType") is not None:
            edits.append(("FrameGen", "FGType", "nofg"))
        if edits:
            ini.edit_file(p, edits)
    write_json(rec_file(exe), rec)


def state(exe: Path | None, rec: dict | None) -> dict | None:
    """What the FG tab shows about the fallback for a d4r install."""
    if not rec or rec.get("kind") != "d4r" or not exe:
        return None
    fb = rec.get("fg_fallback") or {}
    version, how = bundled_version(exe, rec)
    present = {rel: (Path(exe) / rel).exists() for rel in fb.get("files") or []}
    return {
        "installed": bool(fb.get("groups")),
        "bundled": version, "bundled_from": how, "donor": fb.get("donor_label") or fb.get("donor"),
        "donor_note": fb.get("donor_note"),
        "groups": [{"id": k, "label": v["label"], "desc": v["desc"], "family": v["family"], "default": v["default"],
                    "on": k in (fb.get("groups") or [])} for k, v in GROUPS.items()],
        "files": fb.get("files") or [], "kept": fb.get("kept") or [],
        "missing": [r for r, ok in present.items() if not ok],
    }
