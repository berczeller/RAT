"""Game profiles: copy a game's whole RAT setup (injector, upscaler, options, injector .ini values, frame generation, ReShade preset, HDR settings, launch toggles) and paste it onto another game, export it to a file
(~/RAT/profiles) or import one, or delete a game's stored configuration.

Pasting onto a game with the same injector kind installed applies everything at once. Otherwise the injector choice is
staged and the .ini part waits until the next install of that game (applied at the end of the install).
Keys an injector needs to run (locked keys) and keys that do not exist in the target's .ini are never written.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from . import ini
from .jobs import Job
from .util import Paths, UserError, slug

FORMAT = 1
SKIP_SECTIONS = {"libraries"}   # absolute / per-game paths
RESERVED = {"reshade.ini", "optiscaler.ini", "fakenvapi.ini", "helixsr.ini", "d4r.ini", "nvngx.ini"}


# launch variables a profile may carry: only the ones RAT itself sets, with plain values (a profile file is untrusted,
# and launch options run through a shell)
PROFILE_ENV = {"VKD3D_SWAPCHAIN_IMAGES"}


def safe_env(env) -> dict[str, str]:
    if not isinstance(env, dict):
        return {}
    return {k: str(v) for k, v in env.items()
            if k in PROFILE_ENV and isinstance(v, (str, int)) and re.fullmatch(r"[\w.,:+-]{0,32}", str(v))}


def _clean(t) -> bool:
    """One line, no INI syntax characters: the same rule as the Tune tab's editor (a file may come from anywhere)."""
    t = str(t)
    return bool(t) and len(("x" + t + "x").splitlines()) == 1 and not any(c in t for c in "[]=;#")


def _sanitize(prof: dict) -> dict:
    """A profile may come from any .rat.json file: wrong types are dropped here so they can neither break the paste nor
    get stored and crash later reads (the compositor setting is read on every game start)."""
    out = dict(prof)
    for k in ("injector", "launch", "reshade", "hdr"):
        if not isinstance(out.get(k), dict):
            out[k] = {}
    inj = out["injector"] = dict(out["injector"])
    if not isinstance(inj.get("options"), dict):
        inj["options"] = {}
    hd = out["hdr"] = dict(out["hdr"])
    if not isinstance(hd.get("settings"), dict):
        hd["settings"] = {}
    comp = out.get("compositor")
    if not (isinstance(comp, dict) and isinstance(comp.get("effect"), (str, type(None)))):
        out["compositor"] = None
    return out


def _triple(x) -> tuple[str, str, str]:
    try:
        s, k, v = (str(p) for p in x)
    except (TypeError, ValueError):
        return "", "", ""
    if not _clean(s) or not _clean(k) or len(("x" + v + "x").splitlines()) != 1:
        return "", "", ""
    return s.strip(), k.strip(), v.strip()


class Profiles:
    def __init__(self, mgr):
        self.m = mgr

    def snapshot(self, key: str) -> dict:
        g = self.m.game(key)
        exe, _ = self.m.exe_dir(g)
        rec = self.m.record_for(g)
        ch = self.m.choice(key)
        prof: dict = {"rat_profile": FORMAT, "game": g["name"], "appid": g.get("appid"), "created": time.time()}
        if rec:
            prof["injector"] = {"source_id": rec.get("source_id"), "addon": rec.get("addon"), "kind": rec.get("kind"),
                                "upscaler": rec.get("upscaler"), "options": rec.get("options", {}),
                                "route": rec.get("route")}
            exe = Path(rec["exe_dir"])
            files: dict[str, list] = {}
            for fid, grp in self.m._ini_groups(rec, exe).items():
                p = next((x for x in grp["paths"] if x.exists()), None)
                if not p:
                    continue
                locked = set(self.m._locked_keys(rec, fid))
                vals = []
                for e in ini.read(p).entries():
                    sk = (e["section"].lower(), e["key"].lower())
                    if sk in locked or sk[0] in SKIP_SECTIONS:
                        continue
                    vals.append([e["section"], e["key"], e["value"]])
                files[fid] = vals
            prof["ini"] = files
        elif ch:
            prof["injector"] = {"source_id": ch.get("source_id"), "upscaler": ch.get("upscaler"),
                                "options": ch.get("options", {})}
        o = self.m.game_overrides(key)
        prof["launch"] = {"extra_env": o.get("extra_env") or {}}
        rs_dir = self.m.reshade.game_dir(g)
        rs = self.m.reshade.record(rs_dir)
        if rs:
            pr = self.m.reshade.presets(key)
            text = None
            if pr.get("active") and (rs_dir / pr["active"]).exists():
                text = (rs_dir / pr["active"]).read_text(encoding="utf-8", errors="replace")[:256_000]
            prof["reshade"] = {"installed": True, "addon": rs.get("addon", True), "preset_name": pr.get("active"),
                               "preset": text, "proxy_pref": rs.get("proxy_pref", "auto")}
        if o.get("compositor"):
            prof["compositor"] = o["compositor"]
        hd = self.m.renodx.record(rs_dir)
        if hd:
            settings = {}
            if rs_dir and (rs_dir / "ReShade.ini").exists():
                d = ini.read(rs_dir / "ReShade.ini")
                for k in ("ToneMapPeakNits", "ToneMapGameNits", "ToneMapUINits"):
                    v = d.get("renodx-preset1", k)
                    if v is not None:
                        settings[k] = v
            prof["hdr"] = {"installed": True, "id": hd.get("id"), "title": hd.get("title"), "settings": settings}
        return prof

    # --- clipboard ----------------------------------------------------------------------------------------------

    def copy(self, key: str) -> dict:
        prof = self.snapshot(key)
        self.m._update_settings(lambda s: s.__setitem__("clipboard", prof))
        return self.summary(prof)

    def clipboard(self) -> dict | None:
        c = self.m.settings().get("clipboard")
        return self.summary(c) if c else None

    @staticmethod
    def summary(prof: dict) -> dict:
        inj = prof.get("injector") or {}
        parts = []
        if inj.get("source_id"):
            parts.append(f"{inj.get('source_id')} · {inj.get('upscaler') or 'default'}")
        if prof.get("ini"):
            parts.append(f"{sum(len(v) for v in prof['ini'].values())} .ini values")
        if prof.get("reshade"):
            parts.append("ReShade" + (f" ({prof['reshade'].get('preset_name')})" if prof["reshade"].get("preset_name")
                                      else ""))
        if prof.get("hdr"):
            parts.append("HDR")
        return {"game": prof.get("game"), "created": prof.get("created"), "parts": parts}

    def paste(self, key: str, prof: dict | None = None) -> dict:
        prof = prof or self.m.settings().get("clipboard")
        if not prof or prof.get("rat_profile") != FORMAT:
            raise UserError("Nothing to paste: copy a game's profile first")
        g = self.m.game(key)
        if self.m.is_running(g):
            raise UserError("Close the game first.")
        prof = _sanitize(prof)
        done: list[str] = []
        inj = prof.get("injector") or {}
        rec = self.m.record_for(g)
        sid = inj.get("source_id")
        if sid and sid in self.m.sources():
            same = bool(rec) and rec.get("kind") == inj.get("kind") and rec.get("source_id") == sid
            if same:
                self.m.set_choice(key, source_id=sid)
                for oid, v in (inj.get("options") or {}).items():
                    try:
                        self.m.set_option(key, oid, v)
                    except UserError:
                        pass
                if inj.get("upscaler"):
                    try:
                        self.m.set_upscaler(key, inj["upscaler"])
                    except UserError:
                        pass
                n = self.apply_ini(key, prof)
                done.append(f"injector settings ({n} .ini values)")
            else:
                self.m.set_choice(key, source_id=sid, upscaler=inj.get("upscaler"))
                for oid, v in (inj.get("options") or {}).items():
                    self.m.set_choice(key, option=[oid, v])
                self.m._update_settings(lambda s: s["overrides"].setdefault(key, {}).__setitem__("pending_profile", prof))
                done.append(f"injector choice ({sid}); its .ini values apply when you inject")
        env = safe_env((prof.get("launch") or {}).get("extra_env"))
        self.m._update_settings(lambda s: s["overrides"].setdefault(key, {}).__setitem__("extra_env", env))
        if prof.get("compositor"):
            self.m._update_settings(lambda s: s["overrides"].setdefault(key, {}).__setitem__(
                "compositor", prof["compositor"]))
        rsp = prof.get("reshade") or {}
        hd = prof.get("hdr") or {}
        rs_dir = self.m.reshade.game_dir(g)
        have_rs = bool(rs_dir and self.m.reshade.record(rs_dir))
        have_hdr = bool(rs_dir and self.m.renodx.record(rs_dir))
        if rsp.get("preset") and have_rs:
            self._write_preset(rs_dir, rsp)
            done.append("ReShade preset " + self._preset_name(rsp))
        if hd.get("settings") and have_hdr:
            ini.edit_file(rs_dir / "ReShade.ini", [("renodx-preset1", k, str(v)) for k, v in hd["settings"].items()
                                                   if _clean(k) and re.fullmatch(r"-?\d+(\.\d+)?", str(v))])
            done.append("HDR brightness")
        job = None
        # ReShade and RenoDX are one dependency: a profile with either one sets up both in one background job
        if (hd.get("installed") and not have_hdr) or (rsp.get("installed") and not have_rs):
            if hd.get("installed") and not have_hdr:
                self.m.renodx.set_pending(key, {"id": hd.get("id"), "title": hd.get("title"),
                                                "settings": {k: v for k, v in (hd.get("settings") or {}).items()
                                                             if _clean(k)}})
            what = "ReShade" + (f" + HDR ({hd.get('title') or 'RenoDX'})" if hd.get("installed") and not have_hdr else "")
            try:
                job = self.m.jobs.start("Profile: " + what, lambda j: self._setup(g, rsp, hd, j), game=key,
                                        stages=["fetch", "download", "verify", "extract", "install", "configure"])
                done.append(what + ": downloading and setting up in the background")
            except UserError as e:   # another job runs: the HDR request waits for the next ReShade install
                done.append(f"{what} not set up yet ({e}). Install ReShade on the ReShade section; RenoDX follows.")
        if rsp.get("preset") and not have_rs and job is None and not rsp.get("installed"):
            done.append("ReShade preset skipped (install ReShade in this game first)")
        self.m.invalidate()
        return {"applied": done, "detail": self.m.detail(key), "job": job}

    @staticmethod
    def _preset_name(rsp: dict) -> str:
        name = Path(str(rsp.get("preset_name") or "ReShadePreset.ini")).name
        if not re.fullmatch(r"[\w .()-]{1,80}\.ini", name, re.I) or name.lower() in RESERVED:
            name = "ReShadePreset.ini"
        return name

    def _write_preset(self, rs_dir: Path, rsp: dict) -> None:
        name = self._preset_name(rsp)
        (rs_dir / name).write_text(str(rsp["preset"])[:256_000], encoding="utf-8")
        ini.edit_file(rs_dir / "ReShade.ini", [("GENERAL", "PresetPath", ".\\" + name)])

    def _setup(self, g: dict, rsp: dict, hd: dict, job: Job) -> dict:
        """Installs ReShade (add-on build when HDR comes along); RenoDX follows through ReShade's install hook."""
        from .reshade import PROXY_CHOICES
        pref = rsp.get("proxy_pref") if rsp.get("proxy_pref") in PROXY_CHOICES else "auto"
        res = self.m.reshade.install_game(g, job, addon=bool(rsp.get("addon", True)) or bool(hd.get("installed")),
                                          proxy_pref=pref, by="user" if rsp.get("installed") else "hdr")
        if not rsp.get("installed") and self.m.renodx.pending(g["key"]):   # installed "for HDR": the hook was skipped
            msg = self.m.renodx.ensure_linked(g, job)
            if msg:
                job.log(msg)
        rs_dir = self.m.reshade.game_dir(g)
        if rsp.get("preset") and rs_dir:
            self._write_preset(rs_dir, rsp)
            job.log(f"ReShade preset {self._preset_name(rsp)} applied")
        return res

    def apply_ini(self, key: str, prof: dict, job: Job | None = None) -> int:
        g = self.m.game(key)
        rec = self.m.record_for(g)
        if not rec:
            return 0
        inj = prof.get("injector") or {}
        if inj.get("kind") and inj.get("kind") != rec.get("kind"):
            return 0
        exe = Path(rec["exe_dir"])
        groups = self.m._ini_groups(rec, exe)
        n = 0
        for fid, vals in (prof.get("ini") or {}).items():
            grp = groups.get(fid)
            if not grp:
                continue
            locked = set(self.m._locked_keys(rec, fid))
            for p in grp["paths"]:
                if not p.exists():
                    continue
                have = ini.read(p).to_dict()
                edits = [(s, k, v) for s, k, v in (_triple(x) for x in vals) if s and (s.lower(), k.lower()) not in locked
                         and s.lower() not in SKIP_SECTIONS and k.lower() in have.get(s.lower(), {})]
                if ini.edit_file(p, edits):
                    n += len(edits)
        self.m._sync_record_options(rec, exe)
        if job:
            job.log(f"Pasted profile: {n} .ini values")
        return n

    # --- straight from another game -----------------------------------------------------------------------------

    def candidates(self, key: str) -> list[dict]:
        """Games (other than this one) with something RAT set up, to take their profile from directly."""
        out = []
        for g in self.m.games():
            if g["key"] == key:
                continue
            exe = self.m.reshade.game_dir(g) if (g.get("installed") or self.m.game_overrides(g["key"])) else None
            rec_title = g.get("installed")
            rs = self.m.reshade.record(exe) if exe else None
            hd = self.m.renodx.record(exe) if exe else None
            if not (rec_title or rs or hd):
                continue
            parts = [p for p in (rec_title, "ReShade" if rs else None, "HDR" if hd else None) if p]
            out.append({"key": g["key"], "name": g["name"], "parts": parts})
        return sorted(out, key=lambda x: x["name"].upper())

    def apply_from_game(self, key: str, source_key: str) -> dict:
        """Pastes another game's profile onto this one without going through the clipboard. No app id / title check:
        any game's setup can be tried on any game."""
        if source_key == key:
            raise UserError("Pick another game")
        return self.paste(key, self.snapshot(source_key))

    # --- files --------------------------------------------------------------------------------------------------

    def export(self, key: str) -> dict:
        prof = self.snapshot(key)
        d = Paths.exports() / "profiles"
        d.mkdir(parents=True, exist_ok=True)
        f = d / f"{slug(prof['game'])}.rat.json"
        f.write_text(json.dumps(prof, indent=1), encoding="utf-8")
        return {"path": str(f), "summary": self.summary(prof)}

    def list_files(self) -> list[dict]:
        d = Paths.exports() / "profiles"
        out = []
        for f in sorted(d.glob("*.json")):
            try:
                prof = json.loads(f.read_text(encoding="utf-8"))
                if prof.get("rat_profile") == FORMAT:
                    out.append({"path": str(f), "name": f.name, **self.summary(prof)})
            except (OSError, ValueError):
                continue
        return out

    def import_file(self, key: str, path: str) -> dict:
        p = Path(path)
        try:
            if p.stat().st_size > 4 << 20:
                raise UserError("That file is too big to be a RAT profile")
            prof = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise UserError("Not a RAT profile (.rat.json)")
        if not isinstance(prof, dict) or prof.get("rat_profile") != FORMAT:
            raise UserError("Not a RAT profile (.rat.json)")
        return self.paste(key, prof)

    def delete(self, key: str) -> dict:
        """Forgets RAT's stored configuration for the game (choices, launch toggles, compositor effect,
        a pasted profile waiting for install). Installed files stay: uninstall them from their tabs."""
        self.m.game(key)

        def fn(s):
            s["choices"].pop(key, None)
            o = s["overrides"].get(key, {})
            for k in ("extra_env", "compositor", "pending_profile"):
                o.pop(k, None)
        self.m._update_settings(fn)
        self.m.invalidate()
        return self.m.detail(key)

    def delete_file(self, path: str) -> list[dict]:
        p = Path(path)
        d = (Paths.exports() / "profiles").resolve()
        if p.resolve().parent == d and p.suffix == ".json":
            p.unlink(missing_ok=True)
        return self.list_files()
