"""Tests for RAT's main features: upscaling modes, value aliasing between OptiScaler builds, vendor-agnostic GPU
detection, d4r's frame generation fallback, ReShade (proxy and OptiScaler chaining, packages, presets), RenoDX
(index / wiki matching, auto profiles, install), profiles and the launch status.
No network: downloads are served from local files."""
import json
import os
import shutil
import sys
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_backend import OPTI_INI, D4R_INI, Base, tree  # noqa: E402

from rat import addons as A  # noqa: E402
from rat import d4rfg, framegen, gpu, ini, peinfo, renodx, sources  # noqa: E402
from rat.installer import REC_DIR, VERSION, Manager  # noqa: E402
from rat.jobs import Job, STAGES_INSTALL  # noqa: E402
from rat.util import Paths, UserError  # noqa: E402

OPTI_094 = OPTI_INI.replace("[Upscalers]\n; Select Upscaler for Dx12 games\n",
                            "[Upscalers]\n; Select Upscaler for Dx12 games\n; xess, fsr21, fsr22, fsr31 (also for FSR4), "
                            "dlss - Default (auto) is xess\n") + """
[FrameGen]
; nofg, dlssg, nukems, fsrfg, upscaler, fsrfg30 - Default (auto) is nofg
FGInput=auto
; nofg, fsrfg, xefg, nukems - Default (auto) is nofg
FGOutput=auto
Enabled=auto

[Menu]
DisableSplash=auto

[Plugins]
LoadReshade=auto

[Libraries]
; Main folder for OptiScaler to check dll files below
; Default is .\\
OptiDllPath=auto
"""
OPTI_MASTER = OPTI_094.replace("; xess, fsr21, fsr22, fsr31 (also for FSR4), dlss - Default (auto) is xess",
                               "; xess, fsr21, fsr22, ffx (FSR 2.3; 3.1; 4.x), dlss").replace(
    "; Default is .\\\n", "; Default is .\\OptiScaler\n")


class Offline(Base):
    """Base with downloads served from a local map {url part: file}."""

    def setUp(self):
        super().setUp()
        self.serve: dict[str, Path] = {}

        def download(url, dest, progress=None, should_cancel=None, size_hint=None):
            for part, f in self.serve.items():
                if part in url:
                    Path(dest).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, dest)
                    return Path(dest)
            raise UserError(f"offline (tests): {url}")
        sources.download = download

    def opti094(self):
        return {"OptiScaler.dll": b"OPTI", "OptiScaler.ini": OPTI_094, "amd_fidelityfx_upscaler_dx12.dll": b"OPTI-FSR",
                "amd_fidelityfx_dx12.dll": b"FFX-LOADER", "amd_fidelityfx_framegeneration_dx12.dll": b"FFX-FG",
                "libxess.dll": b"XESS", "libxess_fg.dll": b"XEFG", "libxell.dll": b"XELL",
                "dlssg_to_fsr3_amd_is_better.dll": b"NUKEM", "fakenvapi.dll": b"FAKE", "fakenvapi.ini": "[fakenvapi]\n"}

    def install(self, sid, key="1001"):
        self.m.set_choice(key, source_id=sid)
        res, job = self.run_job(lambda j: self.m._install(self.m.game(key), sid, j, False))
        return res, job

    def second_game(self, name="Other Game", appid="1002"):
        apps = self.home / ".local/share/Steam/steamapps"
        folder = apps / "common" / name
        (folder / "bin").mkdir(parents=True)
        (folder / "bin" / "Other.exe").write_bytes(b"MZ" + b"\0" * 4000)
        (apps / f"appmanifest_{appid}.acf").write_text(
            '"AppState"\n{\n "appid" "%s"\n "name" "%s"\n "installdir" "%s"\n "StateFlags" "4"\n}\n' % (appid, name, name))
        self.m.games(refresh=True)
        return folder / "bin"


class TestCatalogAndModes(Offline):
    def test_catalog_and_paths(self):
        self.assertEqual(sorted(A.ADDONS), ["d4r", "helixsr", "optiscaler"])
        self.assertEqual(A.addon_for("custom-123", "d4r")["id"], "d4r")
        self.assertEqual(A.paths_of(A.ADDONS["d4r"]), ["native"])   # d4r runs its DLSS networks only
        self.assertEqual(A.paths_of(A.ADDONS["optiscaler"], gpu={"vendor": "AMD", "dlss": False}), ["fsr", "xess"])
        self.assertIn("native", A.paths_of(A.ADDONS["optiscaler"], gpu={"vendor": "NVIDIA", "dlss": True}))
        self.assertEqual(A.default_for_path(A.ADDONS["optiscaler"], "fsr", {"family": "RDNA4"}), "fsr4")
        self.assertEqual(A.default_for_path(A.ADDONS["optiscaler"], "fsr", {"family": "RDNA3"}), "fsr31")
        self.assertEqual(A.default_for_path(A.ADDONS["helixsr"], "native"), "helix")
        for ad in A.ADDONS.values():
            for u in ad["upscalers"]:
                self.assertIn(u.get("family"), ("dlss", "fsr", "xess", "fork", "neutral"), u)

    def test_values_follow_the_installed_build(self):
        self.store("optiscaler", "v0.9.4", self.opti094(), "optiscaler")
        self.m.set_choice("1001", upscaler="fsr4")
        self.install("optiscaler")
        o = ini.read(self.win64 / "OptiScaler.ini")
        self.assertEqual(o.get("Upscalers", "Dx12Upscaler"), "fsr31")   # 0.9.4 naming
        self.assertEqual(o.get("FSR", "UpscalerIndex"), "0")
        # a newer build documents "ffx"
        p = self.win64 / "OptiScaler.ini"
        p.write_text(p.read_text().replace("fsr31 (also for FSR4)", "ffx (FSR 2.3; 3.1; 4.x)"))
        self.m.set_upscaler("1001", "fsr31")
        self.assertEqual(ini.read(p).get("Upscalers", "Dx12Upscaler"), "ffx")

    def test_set_mode_spoofs_on_amd(self):
        self.store("optiscaler", "v0.9.4", self.opti094(), "optiscaler")
        self.install("optiscaler")
        d = self.m.set_mode("1001", "xess")
        self.assertEqual(d["upscaler"], "xess")
        self.assertEqual(d["path"], "xess")
        self.assertTrue(d["options"]["spoof"])
        with self.assertRaises(UserError):   # real DLSS is hidden on GPUs that cannot run it
            self.m.set_mode("1001", "native")
        with self.assertRaises(UserError):
            self.m.set_upscaler("1001", "dlss")
        self.m._gpu = dict(self.m._gpu, vendor="NVIDIA", family="Ada", dlss=True)
        d = self.m.set_mode("1001", "native")
        self.assertFalse(d["options"]["spoof"])
        self.assertEqual(ini.read(self.win64 / "OptiScaler.ini").get("Spoofing", "Dxgi"), "false")
        with self.assertRaises(UserError):
            self.m.set_mode("1001", "warp")

    def test_mode_before_install_is_a_choice(self):
        self.m.set_choice("1001", source_id="optiscaler")
        d = self.m.set_mode("1001", "fsr")
        self.assertEqual(d["upscaler"], "fsr31")
        self.assertEqual(self.m.choice("1001")["upscaler"], "fsr31")
        self.m.set_choice("1001", source_id="d4r")
        with self.assertRaises(UserError):   # d4r is native only
            self.m.set_mode("1001", "fsr")


class TestGpuAgnostic(unittest.TestCase):
    def sysfs(self, root: Path, vendor: str, device: str):
        d = root / "sys/class/drm/card0/device"
        d.mkdir(parents=True)
        (d / "vendor").write_text(vendor)
        (d / "device").write_text(device)
        (d / "boot_vga").write_text("1")

    def test_nvidia_and_intel(self):
        import tempfile
        r = Path(tempfile.mkdtemp())
        try:
            self.sysfs(r, "0x10de", "0x2684")
            g = gpu.detect(str(r))
            self.assertEqual((g["vendor"], g["family"], g["dlss"]), ("NVIDIA", "Ada", True))
            self.assertEqual(A.gpu_support(A.ADDONS["optiscaler"], g)["level"], "ok")
            self.assertEqual(A.gpu_support(A.ADDONS["d4r"], g)["level"], "unsupported")
            shutil.rmtree(r / "sys")
            self.sysfs(r, "0x8086", "0xe20b")
            g = gpu.detect(str(r))
            self.assertEqual((g["vendor"], g["family"], g["xmx"]), ("Intel", "Xe2", True))
        finally:
            shutil.rmtree(r, ignore_errors=True)


class TestPackageStore(Offline):
    def test_packages_stored_before_the_rename_are_kept(self):
        meta = self.store("helixsr", "v1.0.3", {"helixsr-setup.sh": "#!/bin/sh"}, "helixsr")
        d = Path(meta["path"])
        (d / "network.bin").write_bytes(b"BUILT")   # what HelixSR's one-time build leaves in its folder
        os.replace(d / sources.PKG_META, d / sources.OLD_PKG_META)   # a store written by the earlier build
        self.assertEqual([p["id"] for p in self.m.packages_for("helixsr")], [meta["id"]])
        self.assertTrue((d / sources.PKG_META).is_file())
        self.assertFalse((d / sources.OLD_PKG_META).exists())
        # storing the same archive again never wipes the folder (and the network built in it)
        again = sources.store_package(self.tmp / "helixsr-v1.0.3.zip", {"source_id": "helixsr", "version": "v1.0.3",
                                                                         "asset": "x", "url": None, "sha256": None,
                                                                         "kind": "helixsr", "title": "HelixSR"})
        self.assertEqual(again["id"], meta["id"])
        self.assertEqual((d / "network.bin").read_bytes(), b"BUILT")

    def test_old_metadata_is_never_copied_into_a_game(self):
        meta = self.store("optiscaler", "v0.9.4", self.opti094(), "optiscaler")
        d = Path(meta["path"])
        (d / sources.OLD_PKG_META).write_text("{}")
        rels = [rel for _, rel in self.m._package_files(meta, "optiscaler", {"proxy": "dxgi.dll"})]
        self.assertNotIn(sources.OLD_PKG_META, rels)
        self.assertNotIn(sources.PKG_META, rels)


    def test_helixsr_is_pinned_to_the_1x_line(self):
        # 2.0's setup downloads NVIDIA's driver package for its FG step and fails unattended: never fetched, never kept
        self.assertEqual(A.ADDONS["helixsr"]["max_major"], 1)
        rels = [{"tag": t, "name": t, "draft": False, "prerelease": False, "published": None, "url": None, "body": "",
                 "assets": [{"name": f"HelixSR-{t[1:]}.zip", "url": f"https://x/{t}.zip", "size": 1}]}
                for t in ("v2.0.0", "v1.0.3", "v1.0.2")]
        orig = sources.releases
        sources.releases = lambda repo, force=False: rels
        try:
            a = A.ADDONS["helixsr"]
            pick = sources.latest(a["repo"], a["asset"], a["asset_fallback"], a["asset_exclude"], max_major=1)
            self.assertEqual(pick["tag"], "v1.0.3")
            self.assertEqual(sources.latest(a["repo"], a["asset"])["tag"], "v2.0.0")
            self.assertEqual(self.m.check_updates("helixsr")["latest"]["tag"], "v1.0.3")
        finally:
            sources.releases = orig
        v1 = self.store("helixsr", "v1.0.3", {"helixsr-setup.sh": "#!/bin/sh"}, "helixsr")
        v2 = self.store("helixsr", "v2.0.0", {"helixsr-setup.sh": "#!/bin/sh"}, "helixsr")
        self.assertEqual([p["id"] for p in self.m.packages_for("helixsr")], [v1["id"]])
        self.assertFalse(Path(v2["path"]).exists())
        self.assertTrue(Path(v1["path"]).is_dir())


class TestTuneEssentials(Offline):
    def test_essentials_list_only_keys_the_ini_has(self):
        self.store("optiscaler", "v0.9.4", self.opti094(), "optiscaler")
        self.install("optiscaler")
        groups = self.m.ini_essentials("1001")
        by = {g["title"]: [f["key"] for f in g["fields"]] for g in groups}
        self.assertEqual(by.get("Sharpening"), ["Enabled", "Sharpness"])
        self.assertIn("DisableSplash", by.get("Overlay menu", []))
        self.assertTrue(all(g["file"] == "optiscaler" for g in groups))
        # frame generation and the upscaler choice live on their own tabs
        keys = {f["key"].lower() for g in groups for f in g["fields"]}
        self.assertFalse(keys & {"fginput", "fgoutput", "dx12upscaler"})

    def test_show_advanced_is_off_by_default(self):
        self.assertIs(self.m.pref("show_advanced"), False)
        self.assertIs(self.m.set_pref("show_advanced", True)["show_advanced"], True)


class TestUninstallLaunch(Offline):
    HDR = {"DXVK_HDR": "1", "PROTON_ENABLE_HDR": "1", "ENABLE_HDR_WSI": "1", "ENABLE_GAMESCOPE_WSI": "1"}

    def hdr_on(self, on=True):
        self.m.renodx.launch = (lambda key: {"env": dict(self.HDR), "dlls": {}}) if on else (lambda key: {})

    def apply(self, key, current):
        plan = self.m.launch_plan(key, current)
        self.m.launch_applied(key, current)
        return plan["new"]

    def test_native_hdr_variables_the_user_had_survive_renodx_removal(self):
        self.hdr_on()
        mine = "PROTON_ENABLE_HDR=1 DXVK_HDR=1 gamemoderun %command%"
        with_hdr = self.apply("1001", mine)
        for k in self.HDR:
            self.assertIn(f"{k}=1", with_hdr)
        self.hdr_on(False)
        after = self.m.launch_plan("1001", with_hdr)["new"]
        self.assertIn("PROTON_ENABLE_HDR=1", after)
        self.assertIn("DXVK_HDR=1", after)
        self.assertNotIn("ENABLE_HDR_WSI", after)
        self.assertNotIn("ENABLE_GAMESCOPE_WSI", after)
        self.assertIn("gamemoderun %command%", after)

    def test_removal_preview_and_keep_choice_for_one_game(self):
        self.second_game()
        self.hdr_on()
        with_hdr = self.apply("1001", "%command% -dx12")
        other = self.apply("1002", "%command%")
        r = self.m.launch_removal("1001", "renodx", with_hdr)
        self.assertTrue(r["changed"])
        self.assertEqual(sorted(r["removed"]), sorted(f"{k}=1" for k in self.HDR))
        self.assertEqual(r["after"], "%command% -dx12")
        # keep: the arguments become the user's own in this game only
        self.m.launch_keep("1001", "renodx", with_hdr)
        self.hdr_on(False)
        self.assertEqual(self.m.launch_plan("1001", with_hdr)["new"], with_hdr)
        self.assertEqual(self.m.launch_plan("1002", other)["new"], "")
        # restore in the other game puts it back as it was
        self.assertFalse(self.m.launch_removal("1002", "renodx", "")["changed"])

    def test_reshade_removal_while_hdr_uses_it_changes_nothing(self):
        self.hdr_on()
        self.m.reshade.launch = lambda key: {"env": {}, "dlls": {"dxgi": "n,b"}}
        cur = self.apply("1001", "")
        self.assertFalse(self.m.launch_removal("1001", "reshade", cur)["changed"])
        self.assertTrue(self.m.launch_removal("1001", "renodx", cur)["changed"])

    def test_renodx_settings_leave_a_reshade_ini_that_stays(self):
        from rat import renodx as R
        p = self.tmp / "ReShade.ini"
        p.write_text("[GENERAL]\nPresetPath=.\\a.ini\n\n[renodx]\nSettingsMode=1\n\n[renodx-preset1]\nToneMapType=3\n\n[INPUT]\nKeyOverlay=36,0,0,0\n")
        self.assertTrue(R.strip_settings(p))
        t = p.read_text()
        self.assertNotIn("renodx", t.lower())
        self.assertIn("[INPUT]", t)
        self.assertIn("PresetPath", t)


class TestD4rFrameGen(Offline):
    def d4r_pkg(self, extra=None):
        files = {"dxgi.dll": b"D4R-OPTI", "OptiScaler.ini": OPTI_094.replace("OptiDllPath=auto", "OptiDllPath=d4r"),
                 "d3d12.dll": b"VKD3D", "d3d12core.dll": b"VKD3DCORE", "d4r/nvngx.dll": b"SHIM", "d4r/d4r.ini": D4R_INI,
                 "D4R_README.txt": "Contents: OptiScaler 0.9.4 as dxgi.dll, with an OptiScaler.ini set up for d4r.\n"}
        files.update(extra or {})
        self.store("d4r", "v0.1.3", files, "d4r")

    def test_d4r_needs_the_fallback_first(self):
        self.d4r_pkg()
        self.install("d4r")
        lib = self.win64 / "d4r"
        for n in ("amd_fidelityfx_framegeneration_dx12.dll", "libxess_fg.dll", "dlssg_to_fsr3_amd_is_better.dll"):
            self.assertFalse((lib / n).exists(), n)
        fg = self.m.frame_gen("1001")
        self.assertFalse(fg["available"])
        self.assertTrue(fg["no_fg"])
        fb = fg["fallback"]
        self.assertFalse(fb["installed"])
        self.assertEqual((fb["bundled"], fb["bundled_from"]), ("0.9.4", "d4r's D4R_README.txt"))
        self.assertEqual({g["id"]: g["default"] for g in fb["groups"]}, {"ffx": True, "xefg": False})
        d = self.m.detail("1001")
        self.assertTrue(any("FG fallback" in w for w in d["warnings"]))
        self.assertEqual(d["paths"], ["native"])

    def test_fallback_from_the_matching_optiscaler(self):
        before = tree(self.game)
        self.store("optiscaler", "v0.9.4", self.opti094(), "optiscaler")
        # d4r already ships libxell.dll: the fallback never replaces it
        self.d4r_pkg({"d4r/libxell.dll": b"D4R-XELL"})
        self.install("d4r")
        g = self.m.game("1001")
        rec = self.m.record_for(g)
        fb, job = self.run_job(lambda j: d4rfg.place(self.m, g, self.win64, rec, ["ffx", "xefg"], j))
        lib = self.win64 / "d4r"
        self.assertEqual((lib / "amd_fidelityfx_framegeneration_dx12.dll").read_bytes(), b"FFX-FG")
        self.assertEqual((lib / "libxess_fg.dll").read_bytes(), b"XEFG")
        self.assertEqual((lib / "libxell.dll").read_bytes(), b"D4R-XELL")
        self.assertIn("d4r/libxell.dll", fb["kept"])
        self.assertFalse((lib / "fakenvapi.dll").exists())   # d4r keeps Proton's dxvk-nvapi
        self.assertFalse((lib / "libxess.dll").exists())
        self.assertEqual((fb["donor"], fb["bundled"]), ("v0.9.4", "0.9.4"))
        fg = self.m.frame_gen("1001")
        self.assertTrue(fg["available"])
        self.assertTrue(fg["fallback"]["installed"])
        m = {x["id"]: x for x in fg["methods"]}
        self.assertEqual(m["optifg_fsr"]["missing"], [])
        self.assertEqual(m["optifg_xefg"]["missing"], [])
        self.assertTrue(m["game_dlssg_nukems_out"]["missing"])   # Nukem's DLL is never borrowed
        self.assertIn("dxvk-nvapi", fg["plans"]["optifg_fsr"]["provider"])
        # a reinstall replaces d4r's folder; the fallback files come back with it
        self.install("d4r")
        self.assertTrue((lib / "amd_fidelityfx_framegeneration_dx12.dll").exists())
        self.assertEqual(self.m.record_for(g)["fg_fallback"]["groups"], ["ffx", "xefg"])
        # switching a group off removes its files
        rec = self.m.record_for(g)
        self.run_job(lambda j: d4rfg.place(self.m, g, self.win64, rec, ["ffx"], j))
        self.assertFalse((lib / "libxess_fg.dll").exists())
        ini.edit_file(self.win64 / "OptiScaler.ini", [("FrameGen", "Enabled", "true"), ("FrameGen", "FGInput", "upscaler"),
                                                      ("FrameGen", "FGOutput", "fsrfg"), ("Framerate", "FramerateLimit", "120")])
        fg = self.m.remove_fg_fallback("1001")
        # a 60 fps base cap was written as 120 for 2x FG: without FG it stays 60 rendered frames
        self.assertEqual(ini.read(self.win64 / "OptiScaler.ini").get("Framerate", "FramerateLimit"), "60")
        self.assertTrue(fg["no_fg"])
        self.assertFalse((lib / "amd_fidelityfx_framegeneration_dx12.dll").exists())
        o = ini.read(self.win64 / "OptiScaler.ini")
        self.assertEqual((o.get("FrameGen", "Enabled"), o.get("FrameGen", "FGOutput")), ("false", "nofg"))
        self.run_job(lambda j: d4rfg.place(self.m, g, self.win64, self.m.record_for(g), ["ffx"], j))
        self.run_job(lambda j: self.m._uninstall(self.m.game("1001"), j))
        self.assertEqual(tree(self.game), before)

    def test_fallback_downloads_the_matching_release(self):
        self.d4r_pkg()
        self.install("d4r")
        z = self.tmp / "Optiscaler_0.9.4.7z.zip"
        with zipfile.ZipFile(z, "w") as zf:
            for name, data in self.opti094().items():
                zf.writestr(name, data)
        rels = [{"tag": "v0.9.5", "name": "0.9.5", "draft": False, "prerelease": False, "published": "", "url": "",
                 "body": "", "assets": [{"name": "Optiscaler_0.9.5.zip", "url": "https://x/0.9.5.zip", "size": 1}]},
                {"tag": "v0.9.4", "name": "0.9.4", "draft": False, "prerelease": False, "published": "", "url": "",
                 "body": "", "assets": [{"name": "Optiscaler_0.9.4.zip", "url": "https://x/0.9.4.zip", "size": 1}]}]
        orig = sources.releases
        self.addCleanup(setattr, sources, "releases", orig)
        sources.releases = lambda repo, force=False: rels
        self.serve["0.9.4.zip"] = z
        g = self.m.game("1001")
        fb, job = self.run_job(lambda j: d4rfg.place(self.m, g, self.win64, self.m.record_for(g), ["ffx"], j))
        self.assertEqual(fb["donor"], "v0.9.4")
        self.assertTrue((self.win64 / "d4r" / "amd_fidelityfx_dx12.dll").exists())

    def offline_github(self, rels=None):
        orig_r, orig_t = sources.releases, sources.release_by_tag
        self.addCleanup(setattr, sources, "releases", orig_r)
        self.addCleanup(setattr, sources, "release_by_tag", orig_t)

        def releases(repo, force=False):
            if rels is None:
                raise UserError("No connection to api.github.com. Check the network.")
            return rels

        def by_tag(repo, tag):
            r = next((x for x in rels or [] if x["tag"] == tag), None)
            if r is None:
                raise UserError(f"{repo} has no release {tag}")
            return r
        sources.releases, sources.release_by_tag = releases, by_tag

    def test_implausible_bundled_versions_are_ignored(self):
        # numbers near "OptiScaler" that are not OptiScaler builds (FFX SDK, d4r's own version) must not be taken
        self.d4r_pkg({"D4R_README.txt": "Built on the OptiScaler FFX SDK 3.0.0 headers.\n"})
        self.install("d4r")
        (self.win64 / "OptiScaler.log").write_text("[info] OptiScaler v3.0.0 (d4r)\n")
        v, how = d4rfg.bundled_version(self.win64, self.m.record_for(self.m.game("1001")))
        self.assertEqual((v, how), ("0.9.4", "d4r's documented bundle"))
        (self.win64 / "OptiScaler.log").write_text("[info] OptiScaler v3.0.0 (d4r)\n[info] OptiScaler v0.9.4\n")
        self.assertEqual(d4rfg.bundled_version(self.win64, self.m.record_for(self.m.game("1001")))[0], "0.9.4")

    def test_line_without_release_falls_back_to_pinned_v094(self):
        self.d4r_pkg({"D4R_README.txt": "Contents: OptiScaler 0.12.0 as dxgi.dll\n"})
        self.install("d4r")
        z = self.tmp / "Optiscaler_0.9.4.zip"
        with zipfile.ZipFile(z, "w") as zf:
            for name, data in self.opti094().items():
                zf.writestr(name, data)
        rel = {"tag": "v0.9.4", "name": "0.9.4", "draft": False, "prerelease": False, "published": "", "url": "",
               "body": "", "assets": [{"name": "Optiscaler_0.9.4.zip", "url": "https://x/0.9.4.zip", "size": 1}]}
        self.offline_github([rel])
        self.serve["0.9.4.zip"] = z
        g = self.m.game("1001")
        fb, job = self.run_job(lambda j: d4rfg.place(self.m, g, self.win64, self.m.record_for(g), ["ffx"], j))
        self.assertEqual((fb["bundled"], fb["donor"]), ("0.12.0", "v0.9.4"))
        self.assertIn("v0.9.4", fb["donor_note"])
        self.assertTrue((self.win64 / "d4r" / "amd_fidelityfx_framegeneration_dx12.dll").exists())

    def test_offline_uses_a_stored_helixsr_package(self):
        self.d4r_pkg()
        self.install("d4r")
        self.store("helixsr", "1.4.0", {"helixsr-setup.sh": "#!/bin/sh", "scaler/OptiScaler/OptiScaler.dll": b"H",
                                        "scaler/OptiScaler/amd_fidelityfx_loader_dx12.dll": b"H-LOADER",
                                        "scaler/OptiScaler/amd_fidelityfx_framegeneration_dx12.dll": b"H-FG",
                                        "other/amd_fidelityfx_loader_dx12.dll": b"STRAY"}, "helixsr")
        self.offline_github(None)
        g = self.m.game("1001")
        fb, job = self.run_job(lambda j: d4rfg.place(self.m, g, self.win64, self.m.record_for(g), ["ffx"], j))
        lib = self.win64 / "d4r"
        # loader and frame generation DLL come from the same folder
        self.assertEqual((lib / "amd_fidelityfx_loader_dx12.dll").read_bytes(), b"H-LOADER")
        self.assertEqual((lib / "amd_fidelityfx_framegeneration_dx12.dll").read_bytes(), b"H-FG")
        self.assertEqual(fb["donor_note"], "stored add-on, version differs")
        self.assertTrue(any("helixsr 1.4.0" in l for l in job.lines))

    def test_nothing_anywhere_explains_the_way_out(self):
        self.d4r_pkg()
        self.install("d4r")
        self.offline_github(None)
        g = self.m.game("1001")
        with self.assertRaises(UserError) as e:
            d4rfg.place(self.m, g, self.win64, self.m.record_for(g), ["ffx"])
        self.assertIn("+ Add fork", str(e.exception))
        self.assertIn("v0.9.4", str(e.exception))

    def test_pick_release(self):
        def r(tag, pre=False):
            return {"tag": tag, "draft": False, "prerelease": pre, "assets": [{"name": f"Optiscaler_{tag}.7z", "url": "u"}]}
        rels = [r("v0.9.6-pre1", True), r("v0.9.5"), r("v0.9.4"), r("v0.8.2")]
        self.assertEqual(d4rfg.pick_release("0.9.4", rels)["tag"], "v0.9.4")
        self.assertEqual(d4rfg.pick_release("0.9.3", rels)["tag"], "v0.9.5")   # newest of the same line
        self.assertIsNone(d4rfg.pick_release("0.7.7", rels))
        with self.assertRaises(UserError):
            g = self.m.game("1001")
            d4rfg.place(self.m, g, self.win64, {"kind": "optiscaler"}, ["ffx"])

    def test_lib_dir_rules(self):
        self.assertEqual(framegen.lib_dir(Path("/g"), ini.IniDoc(OPTI_094)), Path("/g"))
        self.assertEqual(framegen.lib_dir(Path("/g"), ini.IniDoc(OPTI_MASTER)), Path("/g/OptiScaler"))
        self.assertEqual(framegen.lib_dir(Path("/g"), ini.IniDoc("[Libraries]\nOptiDllPath=d4r\n")), Path("/g/d4r"))
        self.assertEqual(framegen.lib_dir(Path("/g"), ini.IniDoc("[Libraries]\nOptiDllPath=..\\x\n")), Path("/g"))


class TestJobsAndStatus(Offline):
    def test_job_stages(self):
        j = Job("x", stages=STAGES_INSTALL)
        j.stage("download")
        j.sub(0.5, 0.0, 0.6)
        j.stage("install")
        snap = j.snapshot()
        states = {s["id"]: s["state"] for s in snap["steps"]}
        self.assertEqual(states["fetch"], "done")
        self.assertEqual(states["download"], "done")
        self.assertEqual(states["verify"], "skipped")
        self.assertEqual(states["install"], "active")
        self.assertEqual(states["configure"], "pending")
        self.assertAlmostEqual(snap["progress"], 0.3)

    def test_launch_status_header_and_splash(self):
        self.store("optiscaler", "v0.9.4", self.opti094(), "optiscaler")
        self.m.set_choice("1001", upscaler="fsr4")
        self.install("optiscaler")
        st = self.m.launch_status("1001")
        self.assertEqual(st["header"], f"RAT (Radical Advanced Tools) {VERSION} (Running OptiScaler by cdozdil)")
        self.assertEqual(st["upscaler"]["mode"], "Spoofed")
        self.assertEqual(st["upscaler"]["name"], "FSR 4")
        self.assertFalse(st["fg"]["active"])
        with self.assertRaises(UserError):   # unknown settings are refused
            self.m.set_pref("replace_splash", True)
        self.m.set_option("1001", "cas", True)
        self.assertEqual(ini.read(self.win64 / "OptiScaler.ini").get("Menu", "DisableSplash"), "false")   # splash shown
        self.assertIsNone(self.m.game_started("424242"))
        st = self.m.game_started("1001")
        self.assertTrue(st["show"])

    def test_unmanaged_game_has_no_banner_and_reinstall_keeps_the_injector(self):
        self.assertIsNone(self.m.game_started("1001"))
        self.store("optiscaler", "v0.9.4", self.opti094(), "optiscaler")
        self.install("optiscaler")
        self.m.profiles.delete("1001")   # forgets the choice, the install stays
        self.assertEqual(self.m.detail("1001")["source_id"], "optiscaler")
        captured = {}
        self.m.jobs.start = lambda title, fn, game=None, stages=None: captured.setdefault("title", title)
        self.m.start_install("1001", True)
        self.assertEqual(captured["title"], "Install OptiScaler")

    def test_game_profile_engine_and_api(self):
        exe = self.win64 / "SpaceGame-Win64-Shipping.exe"
        exe.write_bytes(b"MZ" + b"\0" * 100 + b"++UE5+Release-5.3" + b"\0" * 50 + b"d3d12.dll\0vulkan-1.dll\0")
        p = self.m.game_profile(self.m.game("1001"))
        self.assertEqual((p["engine"], p["version"], p["api"]), ("unreal", "5", "DX12"))
        self.assertEqual(peinfo.apis_from_imports(["kernel32.dll", "d3d11.dll", "dxgi.dll"]), ["DX11"])


class TestReShade(Offline):
    def setUp(self):
        super().setUp()
        setup = self.tmp / "ReShade_Setup_6.7.3_Addon.exe"
        with zipfile.ZipFile(setup, "w") as zf:
            zf.writestr("ReShade64.dll", b"RS64")
            zf.writestr("ReShade32.dll", b"RS32")
            zf.writestr("ReShade64.json", "{}")
        self.serve["ReShade_Setup_6.7.3_Addon.exe"] = setup
        self.m.set_pref("reshade_version", "6.7.3")
        self.m.reshade.ensure_d3dcompiler = lambda arch, job=None: None
        # official package list and one package
        Paths.data("cache")
        (Paths.runtime_dir / "cache" / "EffectPackages.ini").write_text(
            "[00]\nRequired=1\nPackageName=Standard effects\nPackageDescription=Std\nInstallPath=.\\reshade-shaders\\Shaders\n"
            "TextureInstallPath=.\\reshade-shaders\\Textures\nDownloadUrl=https://github.com/crosire/reshade-shaders/archive/slim.zip\n"
            "RepositoryUrl=https://github.com/crosire/reshade-shaders/tree/slim\nEffectFiles=LUT.fx\n\n"
            "[01]\nPackageName=Evil\nPackageDescription=x\nInstallPath=..\\..\\etc\nTextureInstallPath=.\\t\n"
            "DownloadUrl=https://example.com/evil.zip\nRepositoryUrl=https://example.com\nEffectFiles=a.fx\n")
        pkg = self.tmp / "slim.zip"
        with zipfile.ZipFile(pkg, "w") as zf:
            zf.writestr("reshade-shaders-slim/Shaders/ReShade.fxh", "// header")
            zf.writestr("reshade-shaders-slim/Shaders/LUT.fx",
                        'technique LUT < ui_label = "Lookup table"; > { }\ntechnique Other { }\n')
            zf.writestr("reshade-shaders-slim/Textures/lut.png", b"PNG")
            zf.writestr("reshade-shaders-slim/evil.exe", b"MZ")
            zf.writestr("reshade-shaders-slim/Shaders/../../../escape.fx", "x")
        self.serve["reshade-shaders/archive/slim.zip"] = pkg
        (self.win64 / "SpaceGame-Win64-Shipping.exe").write_bytes(b"MZ" + b"\0" * 64 + b"d3d12.dll\0")

    def test_install_direct_then_chained_with_optiscaler(self):
        g = self.m.game("1001")
        res, job = self.run_job(lambda j: self.m.reshade.install_game(g, j))
        self.assertEqual((self.win64 / "dxgi.dll").read_bytes(), b"RS64")
        rs = self.m.reshade.record(self.win64)
        self.assertEqual((rs["mode"], rs["proxy"]), ("direct", "dxgi.dll"))
        r = ini.read(self.win64 / "ReShade.ini")
        self.assertTrue(r.get("GENERAL", "EffectSearchPaths").startswith("Z:"))
        self.assertEqual(r.get("GENERAL", "PresetPath"), ".\\ReShadePreset.ini")
        self.assertEqual(self.m.wanted_launch(None, "1001")["dlls"], {"dxgi": "n,b"})
        pk = self.m.reshade.packages()
        self.assertEqual([p["name"] for p in pk], ["Standard effects"])   # non-GitHub entries are refused
        self.assertTrue(pk[0]["installed"])
        root = self.m.reshade.shaders_root()
        self.assertTrue((root / "reshade-shaders/Shaders/LUT.fx").exists())
        self.assertTrue((root / "reshade-shaders/Textures/lut.png").exists())
        self.assertFalse(any(p.name in ("evil.exe", "escape.fx") for p in Paths.runtime_dir.rglob("*")))
        # an OptiScaler injector takes dxgi.dll: ReShade moves to ReShade64.dll, OptiScaler loads it
        self.store("optiscaler", "v0.9.4", self.opti094(), "optiscaler")
        self.install("optiscaler")
        self.assertEqual((self.win64 / "dxgi.dll").read_bytes(), b"OPTI")
        self.assertEqual((self.win64 / "ReShade64.dll").read_bytes(), b"RS64")
        self.assertEqual(ini.read(self.win64 / "OptiScaler.ini").get("Plugins", "LoadReshade"), "true")
        self.assertEqual(self.m.reshade.record(self.win64)["mode"], "chain")
        self.assertEqual(self.m.wanted_launch(self.m.record_for(g), "1001")["dlls"], {"dxgi": "n,b"})
        # injection off: ReShade takes dxgi.dll again; back on: chained again
        self.m.set_active("1001", False)
        self.assertEqual((self.win64 / "dxgi.dll").read_bytes(), b"RS64")
        self.m.set_active("1001", True)
        self.assertEqual((self.win64 / "dxgi.dll").read_bytes(), b"OPTI")
        self.assertEqual((self.win64 / "ReShade64.dll").read_bytes(), b"RS64")
        # removing the injector keeps ReShade (and its record)
        self.run_job(lambda j: self.m._uninstall(g, j))
        self.assertEqual((self.win64 / "dxgi.dll").read_bytes(), b"RS64")
        self.assertFalse((self.win64 / "ReShade64.dll").exists())
        self.assertTrue((self.win64 / REC_DIR / "reshade.json").exists())
        self.m.reshade.uninstall_game("1001")
        self.assertFalse((self.win64 / "dxgi.dll").exists())
        self.assertFalse((self.win64 / REC_DIR).exists())

    def test_presets_and_effects(self):
        g = self.m.game("1001")
        self.run_job(lambda j: self.m.reshade.install_game(g, j))
        fx = self.m.reshade.effects()
        self.assertEqual({e["id"] for e in fx}, {"LUT@LUT.fx", "Other@LUT.fx"})
        self.assertEqual(next(e for e in fx if e["technique"] == "LUT")["label"], "Lookup table")
        self.m.reshade.preset_action("1001", "new", "Vivid")
        pr = self.m.reshade.presets("1001")
        self.assertEqual(pr["active"], "vivid.ini")
        st = self.m.reshade.toggle_effect("1001", "LUT@LUT.fx", True)
        self.assertEqual(st["enabled"], ["LUT@LUT.fx"])
        self.assertIn("Techniques=LUT@LUT.fx", (self.win64 / "vivid.ini").read_text())
        self.m.reshade.preset_action("1001", "save_library", "vivid.ini")
        self.assertEqual(len(self.m.reshade.presets("1001")["library"]), 1)
        out = self.m.reshade.preset_action("1001", "export", "vivid.ini")
        self.assertTrue(Path(out["exported"]).exists())
        self.assertEqual(self.m.reshade.status("1001")["effects"], 1)
        with self.assertRaises(UserError):
            self.m.reshade.preset_action("1001", "activate", "../../etc/passwd")

    def test_vulkan_game_needs_compositor(self):
        (self.win64 / "SpaceGame-Win64-Shipping.exe").write_bytes(b"MZ" + b"\0" * 64 + b"vulkan-1.dll\0")
        self.m.invalidate()
        self.m._scan_cache.clear()
        g = self.m.game("1001")
        with self.assertRaises(UserError):
            self.run_job(lambda j: self.m.reshade.install_game(g, j))
        self.assertFalse(self.m.reshade.game_state("1001")["supported"])


WIKI = """# Installation
Install Reshade.
# List
### Legend
| Name | Maintainer | Links | Status |
| :--- | :--- | :--- | :---: |
| Space Game | Someone | [![Snapshot](https://img.shields.io/x)](https://someone.github.io/renodx/renodx-spacegame.addon64) | :white_check_mark: |
| [Other Game](https://github.com/clshortfuse/renodx/discussions/1) | X | [![Nexus Mods](https://img.shields.io/y)](https://www.nexusmods.com/othergame/mods/1) | [:construction:](# "Some bugs.") |
## Multi-Game Mods
### Unreal Engine Extended [![Snapshot](https://img.shields.io/x)](https://marat569.github.io/renodx/renodx-ue-extended.addon64)
| Name | Status | Notes |
| :--- | :---: | :--- |
| Space Game | :white_check_mark: | Use Upgrade Path |
# Deprecated mods
| Old | x | y | z |
"""


class TestRenoDX(Offline):
    def setUp(self):
        super().setUp()
        Paths.data("cache")
        idx = {"games": [{"id": "space", "title": "Space Game™", "aliases": [], "steam_appid": 1001,
                          "mods": [{"id": "spacegame", "title": "Space Game", "category": "game", "status": "stable",
                                    "maintainers": ["ShortFuse"], "notes": [], "urls": [],
                                    "artifacts": [{"name": "renodx-spacegame.addon64", "arch": "x64",
                                                   "url": "./renodx-spacegame.addon64"}]}]}]}
        (Paths.runtime_dir / "cache" / "renodx-index-0.json").write_text(json.dumps(idx))
        (Paths.runtime_dir / "cache" / "renodx-mods.md").write_text(WIKI)
        setup = self.tmp / "ReShade_Setup_6.7.3_Addon.exe"
        with zipfile.ZipFile(setup, "w") as zf:
            zf.writestr("ReShade64.dll", b"RS64")
            zf.writestr("ReShade32.dll", b"RS32")
        self.serve["ReShade_Setup_6.7.3_Addon.exe"] = setup
        mod = self.tmp / "renodx-spacegame.addon64"
        mod.write_bytes(b"MZ" + b"\0" * 20000)
        self.serve["renodx-spacegame.addon64"] = mod
        self.m.set_pref("reshade_version", "6.7.3")
        self.m.reshade.ensure_d3dcompiler = lambda arch, job=None: None
        (Paths.runtime_dir / "cache" / "EffectPackages.ini").write_text("")
        (self.win64 / "SpaceGame-Win64-Shipping.exe").write_bytes(b"MZ" + b"\0" * 64 + b"++UE4+Release\0d3d11.dll\0")

    def test_wiki_parser(self):
        w = renodx.parse_wiki(WIKI)
        self.assertEqual(len(w["mods"]), 2)
        self.assertEqual(w["mods"][0]["links"], ["https://someone.github.io/renodx/renodx-spacegame.addon64"])
        self.assertEqual((w["mods"][1]["name"], w["mods"][1]["status"], w["mods"][1]["note"]),
                         ("Other Game", "in progress", "Some bugs."))
        sec = next(iter(w["engines"]))
        self.assertTrue(sec.startswith("unreal engine extended"))
        self.assertEqual(w["engines"][sec]["spacegame"]["status"], "working")

    def test_match_and_auto_profile(self):
        m = self.m.renodx.match(self.m.game("1001"))
        self.assertEqual(len(m["official"]), 1)
        self.assertTrue(m["official"][0]["urls"]["x64"][0].startswith("https://renodx.com/renodx-spacegame"))
        self.assertEqual(len(m["forks"]), 1)   # someone.github.io build
        a = m["auto"]
        self.assertTrue(a["supported"])
        self.assertEqual(a["mod"], "ue-extended")
        self.assertEqual(a["known"]["status"], "working")
        self.assertEqual(a["settings"]["ToneMapPeakNits"], "1000")
        self.m.set_pref("hdr_peak_nits", 800)
        self.assertEqual(self.m.renodx.auto_profile(self.m.game("1001"), {"engine": "unity", "arch": "x86",
                                                                          "api": "DX11"})["mod"], "unityengine")
        self.assertFalse(self.m.renodx.auto_profile(self.m.game("1001"), {"engine": "other", "api": "Vulkan"})["supported"])

    def test_install_official_mod(self):
        before = tree(self.game)
        res, job = self.run_job(lambda j: self.m.renodx._install(self.m.game("1001"), "official:spacegame", j, None, None))
        self.assertTrue((self.win64 / "renodx-spacegame.addon64").exists())
        self.assertEqual((self.win64 / "dxgi.dll").read_bytes(), b"RS64")
        self.assertEqual(self.m.reshade.record(self.win64)["requested_by"], ["hdr"])
        r = ini.read(self.win64 / "ReShade.ini")
        self.assertEqual(r.get("renodx-preset1", "ToneMapPeakNits"), "1000")
        env = self.m.wanted_launch(None, "1001")["env"]
        self.assertEqual(env["DXVK_HDR"], "1")
        self.assertEqual(env["ENABLE_GAMESCOPE_WSI"], "1")
        st = self.m.renodx.set_setting("1001", "ToneMapPeakNits", 800)
        self.assertEqual(st["settings"]["ToneMapPeakNits"], "800")
        self.m.renodx.set_enabled("1001", False)
        self.assertNotIn("DXVK_HDR", self.m.wanted_launch(None, "1001")["env"])
        self.m.renodx.uninstall("1001")
        self.assertEqual(tree(self.game), before)

    def test_custom_mod_validation(self):
        with self.assertRaises(UserError):
            self.run_job(lambda j: self.m.renodx._install(self.m.game("1001"), "custom", j, "http://x/y.addon64", None))
        with self.assertRaises(UserError):
            self.run_job(lambda j: self.m.renodx._install(self.m.game("1001"), "custom", j, None, "/etc/passwd"))


class TestHdrChain(TestRenoDX):
    """RenoDX and ReShade as one dependency, Unreal folder layout, and the automatic RenoDX configuration."""

    def setUp(self):
        super().setUp()
        ue = self.tmp / "renodx-ue-extended.addon64"
        ue.write_bytes(b"MZ" + b"U" * 20000)
        self.serve["renodx-ue-extended.addon64"] = ue

    def test_unreal_goes_next_to_the_shipping_exe_not_the_bootstrapper(self):
        g = self.m.game("1001")
        prof = self.m.game_profile(g)
        self.assertEqual((prof["engine"], prof["version"]), ("unreal", "4"))
        self.assertEqual(Path(prof["binaries"]), self.win64)
        self.assertEqual(prof["api"], "DX11")   # UE4 default, even with other RHIs compiled in
        self.assertEqual(self.m.reshade.game_dir(g), self.win64)
        # a setup a previous build left at the bootstrapper is moved where the game loads it
        root = self.game
        (root / "dxgi.dll").write_bytes(b"RS64")
        (root / "renodx-ue-extended.addon64").write_bytes(b"MZ" + b"U" * 20000)
        from rat.records import write_extra
        from rat.util import sha256_of
        write_extra(root, "reshade", {"version": 1, "reshade_version": "6.7.3", "addon": True, "arch": "x64",
                                      "proxy": "dxgi.dll", "proxy_sha": sha256_of(root / "dxgi.dll"),
                                      "requested_by": ["hdr"], "enabled": True, "mode": "direct"})
        write_extra(root, "renodx", {"version": 1, "id": "auto:ue-extended", "title": "Unreal Engine Extended",
                                     "source": "auto", "file": "renodx-ue-extended.addon64", "arch": "x64",
                                     "enabled": True})
        self.assertIn(root, self.m.reshade.stray_dirs(g))
        self.run_job(lambda j: self.m.reshade.install_game(g, j, by="user"))
        self.assertFalse((root / "dxgi.dll").exists())
        self.assertFalse((root / "renodx-ue-extended.addon64").exists())
        self.assertEqual((self.win64 / "dxgi.dll").read_bytes(), b"RS64")
        self.assertTrue((self.win64 / "renodx-ue-extended.addon64").exists())
        self.assertIsNotNone(self.m.renodx.record(self.win64))
        self.assertEqual(self.m.reshade.stray_dirs(g), [])

    def test_renodx_always_sets_up_reshade(self):
        g = self.m.game("1001")
        # a stale ReShade record whose DLL a file verification removed: RenoDX must not trust it
        self.run_job(lambda j: self.m.reshade.install_game(g, j, by="user"))
        (self.win64 / "dxgi.dll").unlink()
        res, job = self.run_job(lambda j: self.m.renodx._install(g, "auto:ue-extended", j, None, None))
        self.assertEqual((self.win64 / "dxgi.dll").read_bytes(), b"RS64")
        self.assertTrue((self.win64 / "renodx-ue-extended.addon64").exists())
        self.assertEqual(sorted(self.m.reshade.record(self.win64)["requested_by"]), ["hdr", "user"])
        self.assertIn("dxgi", self.m.wanted_launch(None, "1001")["dlls"])

    def test_reshade_relinks_renodx(self):
        g = self.m.game("1001")
        self.run_job(lambda j: self.m.renodx._install(g, "auto:ue-extended", j, None, None))
        (self.win64 / "renodx-ue-extended.addon64").unlink()   # e.g. removed by hand or by another tool
        res, job = self.run_job(lambda j: self.m.reshade.install_game(g, j, addon=False, by="user"))
        self.assertTrue((self.win64 / "renodx-ue-extended.addon64").exists())   # from RAT's download cache
        self.assertTrue(self.m.reshade.record(self.win64)["addon"])   # RenoDX needs the add-on build
        self.assertTrue(any("linked again" in l for l in job.lines))

    def test_autoconfiguration_for_unreal(self):
        g = self.m.game("1001")
        self.run_job(lambda j: self.m.renodx._install(g, "auto:ue-extended", j, None, None))
        r = ini.read(self.win64 / "ReShade.ini")
        self.assertEqual(r.get("renodx", "SettingsMode"), "2")
        self.assertEqual(r.get("renodx", "Upgrade_R10G10B10A2_UNORM"), "1")   # Output size
        self.assertEqual(r.get("renodx", "ForceBorderless"), "1")
        self.assertEqual(r.get("renodx", "PreventFullscreen"), "1")
        self.assertEqual(r.get("renodx", "Upgrade_UseSCRGB"), "0")   # HDR10
        self.assertEqual(r.get("renodx-preset1", "ToneMapPeakNits"), "1000")
        self.assertEqual(r.get("renodx-preset1", "GammaCorrection"), "1")
        self.assertIsNone(r.get("renodx", "Upgrade_SwapChainCompatibility"))   # no injector in front of ReShade
        st = self.m.renodx.game_state("1001")
        self.assertEqual(st["context"]["engine"], "unreal")
        self.assertTrue(any(x["key"] == "Upgrade_R10G10B10A2_UNORM" for x in st["auto"]["report"]))
        # the user's own choice in RenoDX's menu survives a re-apply; a forced re-apply restores RAT's value
        st = self.m.renodx.set_setting("1001", "Upgrade_R10G10B10A2_UNORM", 2)
        self.m.renodx.autoconfigure("1001")
        self.assertEqual(ini.read(self.win64 / "ReShade.ini").get("renodx", "Upgrade_R10G10B10A2_UNORM"), "2")
        self.m.renodx.autoconfigure("1001", force=True)
        self.assertEqual(ini.read(self.win64 / "ReShade.ini").get("renodx", "Upgrade_R10G10B10A2_UNORM"), "1")
        # once the mod wrote its own sections, RAT's values are mirrored there
        ini.edit_file(self.win64 / "ReShade.ini", [("renodx-ue-extended", "Foo", "1")])
        self.m.renodx.game_state("1001")
        self.assertEqual(ini.read(self.win64 / "ReShade.ini").get("renodx-ue-extended", "ForceBorderless"), "1")

    def test_official_mod_keeps_its_own_upgrades(self):
        g = self.m.game("1001")
        self.run_job(lambda j: self.m.renodx._install(g, "official:spacegame", j, None, None))
        r = ini.read(self.win64 / "ReShade.ini")
        self.assertIsNone(r.get("renodx", "Upgrade_R10G10B10A2_UNORM"))   # game mods upgrade in code
        self.assertEqual(r.get("renodx", "SwapChainForceBorderless"), "1")

    def test_profile_notes_and_injector_context(self):
        from rat import renodx_auto
        rows, advice = renodx_auto.parse_notes(["Set R10G10B10A2_UNORM to Output ratio, use scRGB",
                                                "Turn off in-game HDR", "Upgrade R11G11B10_FLOAT"])
        got = {k: v for k, v, _ in rows}
        self.assertEqual(got["Upgrade_R10G10B10A2_UNORM"], "2")
        self.assertEqual(got["Upgrade_R11G11B10_FLOAT"], "1")
        self.assertEqual(got["Upgrade_UseSCRGB"], "1")
        self.assertEqual(advice, ["Turn off in-game HDR"])
        ctx = {"engine": "unreal", "engine_version": "5.3", "api": "DX12", "injector": "optiscaler", "chained": True,
               "frame_generation": True, "peak_nits": 800, "game_hdr": True, "game_fullscreen": 0}
        pl = renodx_auto.plan(ctx, {"id": "auto:ue-extended", "mod": "ue-extended", "source": "auto"}, [])
        keys = {x["key"]: x["value"] for x in pl["settings"]}
        self.assertEqual(keys["Upgrade_SwapChainCompatibility"], "1")
        self.assertEqual(keys["ToneMapPeakNits"], "800")
        self.assertTrue(any("Upgrade Path" in a for a in pl["advice"]))
        self.assertTrue(any("exclusive fullscreen" in a for a in pl["advice"]))
        pl = renodx_auto.plan(dict(ctx, engine="unity", injector=None, chained=False, frame_generation=False),
                              {"id": "auto:unityengine", "source": "auto"}, [])
        self.assertIn("Upgrade_R11G11B10_FLOAT", {x["key"] for x in pl["settings"]})

    def test_wingdk_layout_and_numeric_ownership(self):
        from rat import peinfo, renodx_auto
        root = self.tmp / "gdk"
        bd = root / "Proj" / "Binaries" / "WinGDK"
        bd.mkdir(parents=True)
        (root / "Engine").mkdir()
        (root / "gamelaunchhelper.exe").write_bytes(b"MZ")
        (bd / "Proj-WinGDK-Shipping.exe").write_bytes(b"MZ" + b"\0" * 64 + b"++UE5+Release-5.3\0d3d12.dll\0")
        self.assertEqual(peinfo.ue_binaries(root), (bd, "Proj-WinGDK-Shipping.exe"))
        prof = peinfo.game_profile(root, root, "gamelaunchhelper.exe")
        self.assertEqual((prof["engine"], prof["version"], prof["version_full"], prof["api"]),
                         ("unreal", "5", "5.3", "DX12"))
        rini = self.tmp / "ReShade.ini"
        rini.write_text("[renodx-preset1]\nToneMapGameNits=203.000000\n")
        owned, rep = renodx_auto.apply(rini, [{"key": "ToneMapGameNits", "value": "180", "section": "renodx-preset1",
                                               "reason": ""}], {"ToneMapGameNits": "203"})
        self.assertEqual(ini.read(rini).get("renodx-preset1", "ToneMapGameNits"), "180")   # still RAT's value
        pl = renodx_auto.plan({"engine": "unreal", "peak_nits": 1000, "injector": None, "chained": False,
                               "frame_generation": False}, {"id": "auto:ue-extended", "mod": "ue-extended",
                                                            "source": "auto"}, [])
        self.assertEqual({x["key"]: x["value"] for x in pl["settings"]}["ToneMapType"], "1")

    def test_profile_with_hdr_sets_up_both(self):
        src = self.second_game("Other Game", "1002")
        (src / "ReShade.ini").write_text("[renodx-preset1]\nToneMapPeakNits=650\n")
        from rat.records import write_extra
        write_extra(src, "renodx", {"version": 1, "id": "auto:ue-extended", "title": "Unreal Engine Extended",
                                    "source": "auto", "file": "renodx-ue-extended.addon64", "enabled": True})
        prof = self.m.profiles.snapshot("1002")
        self.assertTrue(prof["hdr"]["installed"])
        jobs = []
        self.m.jobs.start = lambda title, fn, game=None, stages=None: jobs.append(fn) or {"id": "j"}
        r = self.m.profiles.paste("1001", prof)
        self.assertEqual(r["job"], {"id": "j"})
        self.assertTrue(self.m.renodx.pending("1001"))
        self.run_job(jobs[0])
        self.assertTrue((self.win64 / "renodx-ue-extended.addon64").exists())
        self.assertEqual((self.win64 / "dxgi.dll").read_bytes(), b"RS64")
        self.assertEqual(ini.read(self.win64 / "ReShade.ini").get("renodx-preset1", "ToneMapPeakNits"), "650")
        self.assertIsNone(self.m.renodx.pending("1001"))
        self.assertEqual(self.m.reshade.record(self.win64)["requested_by"], ["hdr"])


class TestInGame(TestHdrChain):
    """d4r + ReShade + RenoDX in the game: chaining keys, menu keys, splash, live apply, engine fixes."""

    def d4r(self, ini_text=None):
        text = ini_text or OPTI_094.replace("OptiDllPath=auto", "OptiDllPath=d4r").replace(
            "[Plugins]\nLoadReshade=auto\n", "")   # d4r's own OptiScaler.ini may not list LoadReshade at all
        self.store("d4r", "v0.1.3", {"dxgi.dll": b"D4R-OPTI", "OptiScaler.ini": text, "d4r/nvngx.dll": b"SHIM",
                                     "d4r/d4r.ini": D4R_INI}, "d4r")
        self.install("d4r")

    def test_reshade_is_loaded_by_d4r_and_renodx_gets_its_hotfix(self):
        self.d4r("[Menu]\nShortcutKey=0x24\nDisableSplash=true\n[FrameGen]\nFGInput=auto\nFGOutput=auto\n"
                 "[Libraries]\nOptiDllPath=d4r\n[Hotfix]\nCreateD3D12DeviceForLuma=auto\n")
        o = ini.read(self.win64 / "OptiScaler.ini")
        self.assertEqual(o.get("Menu", "DisableSplash"), "false")   # splash shown on install
        g = self.m.game("1001")
        self.run_job(lambda j: self.m.renodx._install(g, "auto:ue-extended", j, None, None))
        rs = self.m.reshade.record(self.win64)
        self.assertEqual((rs["proxy"], rs["mode"]), ("ReShade64.dll", "chain"))
        o = ini.read(self.win64 / "OptiScaler.ini")
        self.assertEqual(o.get("Plugins", "LoadReshade"), "true")   # written although d4r's ini lacked it
        self.assertEqual(o.get("Hotfix", "CreateD3D12DeviceForLuma"), "true")
        self.assertEqual(o.get("Menu", "ShortcutKey"), "0x2D")   # ReShade keeps Home, OptiScaler moves to Insert
        self.assertEqual(ini.read(self.win64 / "ReShade.ini").get("INPUT", "KeyOverlay"), "36,0,0,0")
        self.m.renodx.uninstall("1001")
        self.assertEqual(ini.read(self.win64 / "OptiScaler.ini").get("Hotfix", "CreateD3D12DeviceForLuma"), "auto")

    def test_live_apply_and_running_edits(self):
        from rat import liveinput
        g = self.m.game("1001")
        self.run_job(lambda j: self.m.renodx._install(g, "auto:ue-extended", j, None, None))
        r = ini.read(self.win64 / "ReShade.ini")
        self.assertEqual(r.get("GENERAL", "PresetShortcutKeys"), "135,0,0,0")   # F24
        self.assertEqual(r.get("GENERAL", "PresetShortcutPaths"), ".\\ReShadePreset.ini")
        taps = []
        orig = liveinput.tap_f24
        self.addCleanup(setattr, liveinput, "tap_f24", orig)
        liveinput.tap_f24 = lambda: taps.append(1) or True
        self.m.is_running = lambda g: True
        self.m.reshade.session_start("1001")
        self.assertEqual(self.m.reshade.live_apply("1001"), "live")
        self.assertEqual(len(taps), 1)
        # another preset activated mid-game: ReShade still has the old one, so it waits for the next start
        self.m.reshade.preset_action("1001", "new", "Other")
        self.assertEqual(self.m.reshade.live_apply("1001"), "next")
        self.assertEqual(ini.read(self.win64 / "ReShade.ini").get("GENERAL", "PresetShortcutPaths"), ".\\other.ini")
        # RenoDX settings stay editable while the game runs
        st = self.m.renodx.set_setting("1001", "ToneMapPeakNits", 700)
        self.assertEqual(st["applied"], "next")
        self.assertEqual(ini.read(self.win64 / "ReShade.ini").get("renodx-preset1", "ToneMapPeakNits"), "700")

    def test_xefg_turns_off_unreal_dilated_motion_vectors(self):
        self.store("optiscaler", "v0.9.4", self.opti094(), "optiscaler")
        self.install("optiscaler")
        pfx = self.home / ".local/share/Steam/steamapps/compatdata/1001/pfx"
        pfx.mkdir(parents=True)
        self.m.save_ini("1001", "optiscaler", [["FrameGen", "FGInput", "upscaler"], ["FrameGen", "FGOutput", "xefg"],
                                               ["FrameGen", "Enabled", "true"]])
        prof = self.m.game_profile(self.m.game("1001"))
        eng = pfx / "drive_c/users/steamuser/AppData/Local" / prof["project"] / "Saved/Config/WindowsNoEditor/Engine.ini"
        self.assertEqual(ini.read(eng).get("SystemSettings", "r.NGX.DLSS.DilateMotionVectors"), "0")
        r = self.m.save_ini("1001", "optiscaler", [["FrameGen", "FGOutput", "fsrfg"]])
        self.assertIsNone(ini.read(eng).get("SystemSettings", "r.NGX.DLSS.DilateMotionVectors"))
        self.assertIn("default", r["note"])

    def test_overlay_key_names(self):
        from rat.reshade import _key_name
        self.assertEqual(_key_name("36,0,0,0"), "Home")
        self.assertEqual(_key_name("45,1,0,0"), "Ctrl+Insert")
        self.assertEqual(_key_name("113,0,1,0"), "Shift+F2")


class TestProfiles(Offline):
    def test_copy_paste_export_import_delete(self):
        self.store("optiscaler", "v0.9.4", self.opti094(), "optiscaler")
        self.m.set_choice("1001", upscaler="xess")
        self.install("optiscaler")
        self.m.save_ini("1001", "optiscaler", [["CAS", "Sharpness", "0.4"]])
        self.m.set_preserve_swapchain("1001", True)
        summary = self.m.profiles.copy("1001")
        self.assertIn("optiscaler · xess", summary["parts"][0])
        other = self.second_game()
        r = self.m.profiles.paste("1002")
        self.assertTrue(any("apply when you inject" in a for a in r["applied"]))
        self.assertEqual(self.m.choice("1002")["upscaler"], "xess")
        self.assertTrue(r["detail"]["preserve_swapchain"])
        self.install("optiscaler", key="1002")
        self.assertEqual(ini.read(other / "OptiScaler.ini").get("CAS", "Sharpness"), "0.4")
        self.assertEqual(ini.read(other / "OptiScaler.ini").get("Upscalers", "Dx12Upscaler"), "xess")
        self.assertNotIn("pending_profile", self.m.settings()["overrides"].get("1002", {}))
        out = self.m.profiles.export("1001")
        self.assertTrue(out["path"].endswith(".rat.json"))
        self.assertEqual(len(self.m.profiles.list_files()), 1)
        self.m.save_ini("1002", "optiscaler", [["CAS", "Sharpness", "0.9"]])
        self.m.profiles.import_file("1002", out["path"])
        self.assertEqual(ini.read(other / "OptiScaler.ini").get("CAS", "Sharpness"), "0.4")
        d = self.m.profiles.delete("1002")
        self.assertFalse(d["preserve_swapchain"])
        self.assertEqual(self.m.choice("1002"), {})
        bad = self.tmp / "bad.json"
        bad.write_text("{}")
        with self.assertRaises(UserError):
            self.m.profiles.import_file("1002", str(bad))


if __name__ == "__main__":
    unittest.main()
