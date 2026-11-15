"""Tests for RAT's injector settings editor (INI field schema), frame generation methods, removing injectors
RAT did not install, and the Preserve Swapchain Image Count launch option.

    python3 -m unittest discover -s tests -v
"""
import json
import os
import stat
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "py_modules"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from rat import external, framegen, ini, inischema, launchopts  # noqa: E402
from rat.installer import Manager  # noqa: E402
from rat.util import Paths, UserError  # noqa: E402
from test_backend import Base, tree  # noqa: E402

OPTI_FULL = """; -------------------------------------------------------
[Upscalers]
; -------------------------------------------------------
; Select Upscaler for Dx12 games
; xess, fsr21, fsr22, ffx (FSR 2.3; 3.1; 4.x), dlss
; Default (auto) is DLSS when capable gpu, FSR4 when capable gpu, XeSS otherwise
Dx12Upscaler=auto
Dx11Upscaler=auto
VulkanUpscaler=auto

[FrameGen]
; Enables Frame Generation
; true or false - Default (auto) is false
Enabled=auto

; nofg, dlssg, nvngxfg, fsrfg, upscaler, fsrfg30, xefg - Default (auto) is nofg
FGInput=auto

; nofg, fsrfg, xefg, dlssg - Default (auto) is nofg
FGOutput=auto

; None, Nukems, Arturs, FFX, Combo - Default (auto) is Nukems
FGNvngxReplacement=auto

; 1 to 3 - Default (auto) is 1
AllowedFrameAhead=auto

[FSRFG]
; Enables Async FSR3.1 Frame Generation
; true or false - Default (auto) is false
AllowAsync=auto

[OptiFG]
; true or false - Default (auto) is false
HUDFix=auto

; integer value above > 0 - Default (auto) is 1
HUDLimit=auto

[FSR]
; Selects upscaler backend for FSR3.X/4
; 0 = FSR 4.0.2 | 1 = FSR 3.1.5 | 2 = FSR 2.3.4
; Depending on GPU - Default (auto) is 0 - FSR4 for RDNA4, FSR3 for the rest
UpscalerIndex=auto

; Sets Velocity Factor for FSR3.1.1 and above
; 0.0 to 1.0 - Default (auto) is 1.0
VelocityFactor=auto

[Spoofing]
; true or false - Default (auto) is true for AMD/Intel, false for Nvidia
Dxgi=auto

[CAS]
; true or false - Default (auto) is false
Enabled=auto
Sharpness=auto

[Framerate]
; Frame rate limit that uses Reflex whenever possible
; This includes any tech that replaces Reflex like XeLL or AntiLag 2
; float - Default (auto) is 0.0 (disabled)
FramerateLimit=auto

[fakenvapi]
; 0 - follow in-game setting, 1 - force disable, 2 - force enable
; Default (auto) is 0
ForceReflex=auto
"""


class TestSchema(unittest.TestCase):
    def setUp(self):
        self.doc = ini.IniDoc(OPTI_FULL)
        self.f = {f"{f['section']}.{f['key']}": f for f in inischema.fields("optiscaler", self.doc)}

    def test_controls_from_comments(self):
        f = self.f["Upscalers.Dx12Upscaler"]
        self.assertEqual(f["type"], "choice")
        self.assertEqual([c[0] for c in f["choices"]], ["auto", "xess", "fsr21", "fsr22", "ffx", "dlss"])
        self.assertEqual([c[0] for c in self.f["FrameGen.Enabled"]["choices"]], ["auto", "true", "false"])
        self.assertEqual(self.f["FSR.UpscalerIndex"]["choices"][1], ["0", "0 · FSR 4.0.2"])
        v = self.f["FSR.VelocityFactor"]
        self.assertEqual((v["type"], v["min"], v["max"]), ("range", 0.0, 1.0))
        self.assertEqual([c[0] for c in self.f["FrameGen.AllowedFrameAhead"]["choices"]], ["auto", "1", "2", "3"])
        self.assertEqual([c[0] for c in self.f["fakenvapi.ForceReflex"]["choices"]], ["auto", "0", "1", "2"])
        self.assertEqual(self.f["OptiFG.HUDLimit"]["type"], "text")
        # the shared comment block of Dx12Upscaler does not leak into the next key's block after a key line
        self.assertEqual(self.f["Upscalers.Dx11Upscaler"]["choices"][0][0], "auto")

    def test_sections_and_unknown_value_kept(self):
        self.assertEqual([s["name"] for s in inischema.sections(self.doc)][:3], ["Upscalers", "FrameGen", "FSRFG"])
        doc = ini.IniDoc("[DLSS]\n; true or false\nX=weird\n")
        f = inischema.fields("optiscaler", doc)[0]
        self.assertIn(["weird", "weird (current)"], f["choices"])

    def test_d4r_hints(self):
        doc = ini.IniDoc("[DLSS]\n; prose\nModel = K\n[Kernels]\nNativeFp8 = on\n")
        f = {x["key"]: x for x in inischema.fields("d4r", doc)}
        self.assertIn("M", [c[0] for c in f["Model"]["choices"]])
        self.assertEqual([c[0] for c in f["NativeFp8"]["choices"]], ["on", "off"])

    def test_entries_and_remove(self):
        doc = ini.IniDoc("[A]\n; c1\nx=1\ny=2\n\n; c2\nz=3\n")
        e = doc.entries()
        self.assertEqual([x["comments"] for x in e], [["c1"], ["c1"], ["c2"]])
        self.assertTrue(doc.remove("a", "Y"))
        self.assertEqual(doc.text(), "[A]\n; c1\nx=1\n\n; c2\nz=3\n")


class TestFrameGen(unittest.TestCase):
    def test_methods_and_groups(self):
        import shutil
        import tempfile
        exe = Path(tempfile.mkdtemp(prefix="rat-fg-"))
        self.addCleanup(shutil.rmtree, exe, True)
        doc = ini.IniDoc(OPTI_FULL)
        r = framegen.analyze(doc, exe, "optiscaler", None, {})
        self.assertTrue(r["available"])
        self.assertEqual(r["current"], "off")
        ids = [m["id"] for m in r["methods"]]
        self.assertIn("optifg_fsr", ids)
        m = next(m for m in r["methods"] if m["id"] == "optifg_fsr")
        self.assertEqual(m["missing"], ["amd_fidelityfx_dx12.dll"])
        (exe / "amd_fidelityfx_dx12.dll").write_bytes(b"x")
        doc.set("FrameGen", "Enabled", "true")
        doc.set("FrameGen", "FGInput", "upscaler")
        doc.set("FrameGen", "FGOutput", "fsrfg")
        r = framegen.analyze(doc, exe, "optiscaler", None, {})
        self.assertEqual(r["current"], "optifg_fsr")
        self.assertFalse(next(m for m in r["methods"] if m["id"] == "optifg_fsr")["missing"])
        titles = [g["title"] for g in r["groups"]]
        self.assertEqual(titles[:3], ["Frame generation options", "FSR frame generation", "OptiFG / HUDFix"])
        self.assertIn("optifg.hudfix", {f"{f['section']}.{f['key']}".lower() for f in r["edit_fields"]})

    def test_method_guides_and_cap_and_latency_fields(self):
        r = framegen.analyze(ini.IniDoc(OPTI_FULL), Path("/nonexistent"), "optiscaler", None, {}, None, {"vendor": "AMD"})
        for m in r["methods"]:
            self.assertTrue(m["how"] and m["works"] and m["spoof"], m["id"])
            if m["id"] != "off":
                self.assertTrue(m["steps"], m["id"])
        # the framerate limit has its own control (whole frames), not a row in the FG options
        self.assertEqual((r["cap_field"]["min"], r["cap_field"]["step"]), (0, 1))
        self.assertNotIn("framerate.frameratelimit", {f"{f['section']}.{f['key']}".lower() for g in r["groups"]
                                                       for f in g["fields"]})
        lat = {f"{f['section']}.{f['key']}" for f in r["latency_fields"]}
        self.assertIn("fakenvapi.ForceReflex", lat)
        self.assertTrue(all(f["rat_hint"] for f in r["latency_fields"]))
        self.assertIn("optifg_fsr", r["plans"])
        self.assertIn("custom", r["plans"])
        # the game's own FG libraries decide the "game lacks it" notes
        g = {m["id"]: m["game_note"] for m in r["methods"]}
        self.assertTrue(g["game_dlssg_fsr"] and g["game_fsr"])
        r = framegen.analyze(ini.IniDoc(OPTI_FULL), Path("/nonexistent"), "optiscaler", None,
                             {"Streamline": ["x"], "FSR 3.1+": ["y"]}, None, {"vendor": "AMD"})
        g = {m["id"]: m["game_note"] for m in r["methods"]}
        self.assertIsNone(g["game_dlssg_fsr"])
        self.assertIsNone(g["game_fsr"])

    def test_latency_plans(self):
        p = framegen.latency_plan("NVIDIA", "optiscaler", False, False, "upscaler", "fsrfg")
        self.assertIn("Reflex", p["provider"])
        self.assertNotIn("fakenvapi.ForceReflex", p["keys"])
        self.assertIn(["FrameGen", "AllowedFrameAhead", "1"], p["recommended"])
        p = framegen.latency_plan("AMD", "optiscaler", False, False, "upscaler", "fsrfg")
        self.assertEqual(p["tone"], "warn")   # no Reflex replacement without fakenvapi
        p = framegen.latency_plan("AMD", "optiscaler", True, False, "upscaler", "fsrfg")
        self.assertIn("Anti-Lag", p["provider"])
        self.assertIn(["fakenvapi", "ForceReflex", "2"], p["recommended"])
        self.assertIn("fakenvapi.LatencyFlexMode", p["keys"])
        p = framegen.latency_plan("AMD", "optiscaler", True, False, "fsrfg", "fsrfg")
        self.assertIn("LatencyFlex", p["provider"])
        self.assertIn(["fakenvapi", "ForceLatencyFlex", "true"], p["recommended"])
        p = framegen.latency_plan("AMD", "optiscaler", True, True, "upscaler", "xefg")
        self.assertIn("XeLL", p["provider"])
        self.assertNotIn("fakenvapi.ForceLatencyFlex", p["keys"])
        p = framegen.latency_plan("AMD", "optiscaler", True, True, "nofg", "nofg")
        self.assertIn("fakenvapi.ForceXeLL", p["keys"])   # only while FG is off
        self.assertNotIn("FrameGen.AllowedFrameAhead", p["keys"])
        p = framegen.latency_plan("AMD", "d4r", False, False, "upscaler", "fsrfg")
        self.assertIn("dxvk-nvapi", p["provider"])
        self.assertFalse(any(k.startswith("fakenvapi.") for k in p["keys"]))
        p = framegen.latency_plan("Intel", "optiscaler", True, True, "dlssg", "fsrfg")
        self.assertIn("XeLL", p["provider"])
        self.assertIn("DLSSG.UseGamesReflexMarkers", p["keys"])

    def test_cap_multiplier(self):
        self.assertEqual(framegen.cap_multiplier("nofg", "nofg"), 1)
        self.assertEqual(framegen.cap_multiplier("upscaler", "fsrfg"), 2)
        self.assertEqual(framegen.cap_multiplier("upscaler", "xefg", "2"), 3)
        self.assertEqual(framegen.cap_multiplier("upscaler", "xefg", "auto"), 2)

    def test_nvngx_methods_are_recognised(self):
        doc = ini.IniDoc(OPTI_FULL)
        doc.set("FrameGen", "Enabled", "true")
        doc.set("FrameGen", "FGInput", "nvngxfg")
        doc.set("FrameGen", "FGNvngxReplacement", "FFX")
        self.assertEqual(framegen.analyze(doc, Path("/nonexistent"), "optiscaler", None, {})["current"], "game_dlssg_ffx")

    def test_legacy_and_helix_direct(self):
        doc = ini.IniDoc("[FrameGen]\nFGType=optifg\n")
        r = framegen.analyze(doc, Path("/nonexistent"), "optiscaler", None, {})
        self.assertTrue(r["legacy"])
        self.assertEqual(r["current"], "optifg")
        r = framegen.analyze(None, None, "helixsr", "direct", {})
        self.assertFalse(r["available"])
        self.assertIn("game's own FSR", r["reason"])


class TestEditorAndFrameGenOnInstall(Base):
    def setUp(self):
        super().setUp()
        files = self.opti_files()
        files["OptiScaler.ini"] = OPTI_FULL
        self.store("optiscaler", "v0.9.5", files, "optiscaler")
        self.m.set_choice("1001", source_id="optiscaler", upscaler="fsr31")
        self.run_job(lambda j: self.m._install(self.m.game("1001"), "optiscaler", j, False))

    def test_files_sections_and_save(self):
        f = self.m.ini_files("1001")
        self.assertEqual(f["files"][0]["id"], "optiscaler")
        fields = self.m.ini_section("1001", "optiscaler", "Upscalers")
        dx12 = next(x for x in fields if x["key"] == "Dx12Upscaler")
        self.assertIn("Upscaler picker", dx12["locked"])
        with self.assertRaises(UserError):
            self.m.save_ini("1001", "optiscaler", [["Upscalers", "Dx12Upscaler", "xess"]])
        r = self.m.save_ini("1001", "optiscaler", [["CAS", "Enabled", "true"], ["FrameGen", "FGInput", "upscaler"]])
        self.assertEqual(r["saved"], 2)
        o = ini.read(self.win64 / "OptiScaler.ini")
        self.assertEqual(o.get("CAS", "Enabled"), "true")
        # the Inject tab's CAS option follows the INI, so toggling injection does not write the old value back
        self.assertTrue(self.m.record_for(self.m.game("1001"))["options"]["cas"])
        self.m.set_active("1001", False)
        self.m.set_active("1001", True)
        self.assertEqual(ini.read(self.win64 / "OptiScaler.ini").get("CAS", "Enabled"), "true")
        with self.assertRaises(UserError):
            self.m.save_ini("1001", "optiscaler", [["CAS", "Enabled\n[x]", "1"]])

    def test_validation_and_toggle_mapping(self):
        for bad in (["Upscalers ", "Dx12Upscaler", "xess"], ["Up]x\n[Hack", "K", "1"], ["CAS", "Enabled", "a\x0bb"],
                    ["CAS", "Enabled", "a\u2028b"], ["CAS", "En=abled", "1"]):
            with self.assertRaises(UserError):
                self.m.save_ini("1001", "optiscaler", [bad])
        self.m.save_ini("1001", "optiscaler", [["Spoofing", "Dxgi", "true"]])
        self.assertTrue(self.m.record_for(self.m.game("1001"))["options"]["spoof"])
        self.m.set_upscaler("1001", "xess")
        self.assertEqual(ini.read(self.win64 / "OptiScaler.ini").get("Spoofing", "Dxgi"), "true")

    def test_frame_gen_endpoint(self):
        r = self.m.frame_gen("1001")
        self.assertTrue(r["available"])
        self.assertEqual(r["file_id"], "optiscaler")
        m = next(m for m in r["methods"] if m["id"] == "game_fsr")
        self.m.save_ini("1001", "optiscaler", m["edits"])
        self.assertEqual(self.m.frame_gen("1001")["current"], "game_fsr")


class TestExternal(Base):
    def manual_opti(self, d: Path):
        (d / "dxgi.dll").write_bytes(b"MZ....OptiScaler v0.9....")
        (d / "OptiScaler.ini").write_text("[Upscalers]\nDx12Upscaler=auto\n")
        (d / "OptiScaler.log").write_text("log")
        (d / "remove_optiscaler.sh").write_text("#!/bin/sh")
        (d / "D3D12_Optiscaler").mkdir()
        (d / "D3D12_Optiscaler" / "D3D12Core.dll").write_bytes(b"c")
        (d / "nvapi64.dll").write_bytes(b"MZ fakenvapi build")
        (d / "amd_fidelityfx_dx12.dll").write_bytes(b"OPTI-FFX")   # copied by the manual install
        (d / "libxess.dll").write_bytes(b"GAME-XESS")               # the game's own

    def test_detect_and_wipe_manual_optiscaler(self):
        self.manual_opti(self.win64)
        found = external.detect(self.game)
        self.assertEqual(len(found), 1)
        x = found[0]
        self.assertEqual(x["kind"], "OptiScaler")
        self.assertEqual(x["rel_dir"], "SpaceGame/Binaries/Win64")
        self.assertIn("dxgi.dll", x["files"])
        self.assertIn("nvapi64.dll", x["files"])
        self.assertIn("D3D12_Optiscaler", x["dirs"])
        self.assertEqual(sorted(x["shared"]), ["amd_fidelityfx_dx12.dll", "amd_fidelityfx_upscaler_dx12.dll",
                                               "libxess.dll"])
        # RAT's package store has the same FFX DLL, so that copy is recognised as the injector's
        self.store("optiscaler", "v1", {"OptiScaler.dll": b"O", "amd_fidelityfx_dx12.dll": b"OPTI-FFX"}, "optiscaler")
        d = self.m.detail("1001")
        self.assertEqual(len(d["external"]), 1)
        self.assertTrue(any("did not install" in w for w in d["warnings"]))
        self.m.set_choice("1001", source_id="optiscaler")
        res, job = self.run_job(lambda j: self.m._wipe(self.m.game("1001"), external.detect(self.game), j))
        self.assertTrue(res["strip"])
        self.assertFalse((self.win64 / "dxgi.dll").exists())
        self.assertFalse((self.win64 / "OptiScaler.ini").exists())
        self.assertFalse((self.win64 / "D3D12_Optiscaler").exists())
        self.assertFalse((self.win64 / "nvapi64.dll").exists())
        self.assertFalse((self.win64 / "amd_fidelityfx_dx12.dll").exists())
        self.assertEqual((self.win64 / "libxess.dll").read_bytes(), b"GAME-XESS")
        self.assertEqual((self.win64 / "amd_fidelityfx_upscaler_dx12.dll").read_bytes(), b"GAME-FSR")
        self.assertIn("SpaceGame/Binaries/Win64/libxess.dll", res["kept"])
        self.assertEqual(external.detect(self.game), [])
        # the manual install's launch options go too, the user's own parts stay
        plan = self.m.launch_plan("1001", 'MANGOHUD=1 WINEDLLOVERRIDES="dxgi=n,b" PROTON_USE_OPTISCALER=1 %command%',
                                  strip_foreign=True)
        self.assertEqual(plan["new"], "MANGOHUD=1 %command%")

    def test_game_dll_with_common_name_is_not_touched(self):
        (self.win64 / "version.dll").write_bytes(b"MZ the game's own version.dll")
        (self.win64 / "d3d12.dll").write_bytes(b"MZ game")
        self.assertEqual(external.detect(self.game), [])

    def test_rat_helix_dropin_folder_is_not_external(self):
        other = self.game / "SpaceGame" / "Plugins" / "FSR"
        other.mkdir(parents=True)
        (other / "amd_fidelityfx_upscaler_dx12.original.dll").write_bytes(b"orig")
        (other / "amd_fidelityfx_upscaler_dx12.dll").write_bytes(b"HelixSR")
        self.assertEqual(len(external.detect(self.game)), 1)
        self.assertEqual(external.detect(self.game, ours=[other]), [])

    def test_rat_install_is_not_external(self):
        self.store("optiscaler", "v0.9.4", self.opti_files(b"MZ OptiScaler"), "optiscaler")
        self.m.set_choice("1001", source_id="optiscaler")
        self.run_job(lambda j: self.m._install(self.m.game("1001"), "optiscaler", j, False))
        self.assertEqual(external.detect(self.game), [])

    def test_helix_dropin_restores_original(self):
        (self.win64 / "amd_fidelityfx_upscaler_dx12.original.dll").write_bytes(b"GAME-FSR-ORIG")
        (self.win64 / "amd_fidelityfx_upscaler_dx12.dll").write_bytes(b"MZ HelixSR 1.9")
        (self.win64 / "helixsr.ini").write_text("[Sharpening]\n")
        found = external.detect(self.game)
        self.assertEqual(found[0]["kind"], "HelixSR")
        r = external.remove(found[0])
        self.assertEqual(r["restored"], 1)
        self.assertEqual((self.win64 / "amd_fidelityfx_upscaler_dx12.dll").read_bytes(), b"GAME-FSR-ORIG")
        self.assertFalse((self.win64 / "helixsr.ini").exists())

    def test_helix_dropin_after_game_update_keeps_new_dll(self):
        (self.win64 / "amd_fidelityfx_upscaler_dx12.original.dll").write_bytes(b"GAME-FSR-OLD")
        (self.win64 / "amd_fidelityfx_upscaler_dx12.dll").write_bytes(b"GAME-FSR-NEWER")   # a game patch
        (self.win64 / "helixsr.ini").write_text("[Sharpening]\n")
        external.remove(external.detect(self.game)[0])
        self.assertEqual((self.win64 / "amd_fidelityfx_upscaler_dx12.dll").read_bytes(), b"GAME-FSR-NEWER")

    def test_symlinked_game_folder_and_licenses_kept(self):
        link = self.tmp / "linked"
        link.symlink_to(self.game)
        self.manual_opti(self.win64)
        (self.win64 / "Licenses").mkdir()
        (self.win64 / "Licenses" / "game_eula.txt").write_text("the game's own")
        (self.win64 / "setup_windows.bat").write_text("@echo the game's installer")
        x = external.detect(link)[0]
        self.assertNotIn("setup_windows.bat", x["files"])
        r = external.remove(x)
        self.assertIn("Licenses/", r["kept"])
        self.assertTrue((self.win64 / "Licenses" / "game_eula.txt").exists())
        self.assertTrue((self.win64 / "setup_windows.bat").exists())
        self.assertFalse((self.win64 / "dxgi.dll").exists())

    def test_manual_d4r(self):
        (self.win64 / "d4r").mkdir()
        (self.win64 / "d4r" / "d4r.ini").write_text("[DLSS]\nModel = K\n")
        (self.win64 / "dxgi.dll").write_bytes(b"OptiScaler")
        (self.win64 / "d3d12.dll").write_bytes(b"vkd3d-proton patched")
        (self.win64 / "d3d12core.dll").write_bytes(b"vkd3d core")
        (self.win64 / "D4R_README.txt").write_text("x")
        x = external.detect(self.game)[0]
        self.assertEqual(x["kind"], "d4r")
        self.assertIn("d4r", x["dirs"])
        self.assertTrue({"d3d12.dll", "d3d12core.dll", "dxgi.dll", "D4R_README.txt"} <= set(x["files"]))
        external.remove(x)
        self.assertFalse((self.win64 / "d4r").exists())
        self.assertFalse((self.win64 / "d3d12.dll").exists())


class TestPreserveSwapchain(Base):
    def test_toggle_writes_and_removes_launch_option(self):
        self.assertIn("VKD3D_SWAPCHAIN_IMAGES", launchopts.MANAGED_ENV)
        d = self.m.set_preserve_swapchain("1001", True)
        self.assertTrue(d["preserve_swapchain"])
        plan = self.m.launch_plan("1001", "MANGOHUD=1 %command% -dx12")
        self.assertEqual(plan["new"], "MANGOHUD=1 VKD3D_SWAPCHAIN_IMAGES=3 %command% -dx12")
        self.m.launch_applied("1001")
        # with an injector installed both are kept, and switching injection off keeps the swapchain pin
        self.store("optiscaler", "v0.9.4", self.opti_files(), "optiscaler")
        self.m.set_choice("1001", source_id="optiscaler")
        self.run_job(lambda j: self.m._install(self.m.game("1001"), "optiscaler", j, False))
        plan = self.m.launch_plan("1001", plan["new"])
        self.assertEqual(plan["new"], 'MANGOHUD=1 VKD3D_SWAPCHAIN_IMAGES=3 WINEDLLOVERRIDES="dxgi=n,b" %command% -dx12')
        self.m.launch_applied("1001")
        self.m.set_active("1001", False)
        self.assertEqual(self.m.launch_plan("1001", plan["new"])["new"], "MANGOHUD=1 VKD3D_SWAPCHAIN_IMAGES=3 %command% -dx12")
        self.m.set_active("1001", True)
        d = self.m.set_preserve_swapchain("1001", False)
        self.assertFalse(d["preserve_swapchain"])
        self.assertEqual(self.m.launch_plan("1001", plan["new"])["new"], 'MANGOHUD=1 WINEDLLOVERRIDES="dxgi=n,b" %command% -dx12')

    def test_user_value_survives_wipe_strip(self):
        plan = self.m.launch_plan("1001", 'VKD3D_SWAPCHAIN_IMAGES=4 WINEDLLOVERRIDES="dxgi=n,b" %command%', strip_foreign=True)
        self.assertEqual(plan["new"], "VKD3D_SWAPCHAIN_IMAGES=4 %command%")


if __name__ == "__main__":
    unittest.main()
