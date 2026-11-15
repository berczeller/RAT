"""Tests for RAT's tools: the launch option cleaner, dangling symlinks left by other ReShade tools, the ReShade master
switch and shader parameters, RenoDX settings (view, export / import, any-game mods), cross-game profiles, and
NVIDIA-only options being hidden elsewhere.
No network: downloads are served from local files."""
import json
import os
import sys
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_suite import WIKI, Offline  # noqa: E402

from rat import addons as A  # noqa: E402
from rat import ini, launchopts, sources  # noqa: E402
from rat.installer import Manager  # noqa: E402
from rat.reshade import parse_uniforms  # noqa: E402
from rat.renodx import settings_view  # noqa: E402
from rat.util import Paths, UserError  # noqa: E402

FX = """// a test effect
#include "ReShade.fxh"
uniform float Strength < ui_type = "slider"; ui_min = 0.0; ui_max = 2.0; ui_label = "Strength"; > = 0.5;
uniform bool Enable < ui_label = "Enable"; > = true;
uniform int Mode < ui_type = "combo"; ui_items = "Soft\\0Hard\\0Extreme\\0"; > = 1;
uniform float3 Tint < ui_type = "color"; > = float3(1.0, 0.5, 0.25);
uniform float Timer < source = "timer"; >;
uniform float Hidden < hidden = true; > = 3.0;
/* uniform float Commented = 1.0; */
technique Test { }
"""


def reshade_setup(t: Offline, fx: str = FX):
    setup = t.tmp / "ReShade_Setup_6.7.3_Addon.exe"
    with zipfile.ZipFile(setup, "w") as zf:
        zf.writestr("ReShade64.dll", b"RS64")
        zf.writestr("ReShade32.dll", b"RS32")
    t.serve["ReShade_Setup_6.7.3_Addon.exe"] = setup
    t.m.set_pref("reshade_version", "6.7.3")
    Paths.data("cache")
    (Paths.runtime_dir / "cache" / "EffectPackages.ini").write_text(
        "[00]\nRequired=1\nPackageName=Standard effects\nPackageDescription=Std\nInstallPath=.\\reshade-shaders\\Shaders\n"
        "TextureInstallPath=.\\reshade-shaders\\Textures\nDownloadUrl=https://github.com/crosire/reshade-shaders/archive/slim.zip\n"
        "RepositoryUrl=https://github.com/crosire/reshade-shaders/tree/slim\nEffectFiles=Test.fx\n")
    pkg = t.tmp / "slim.zip"
    with zipfile.ZipFile(pkg, "w") as zf:
        zf.writestr("reshade-shaders-slim/Shaders/ReShade.fxh", "// header")
        zf.writestr("reshade-shaders-slim/Shaders/Test.fx", fx)
    t.serve["reshade-shaders/archive/slim.zip"] = pkg
    (t.win64 / "SpaceGame-Win64-Shipping.exe").write_bytes(b"MZ" + b"\0" * 64 + b"d3d12.dll\0")


class TestLaunchCleaner(Offline):
    def test_clean_rules(self):
        new, notes = launchopts.clean("A=1 A=1 gamemoderun gamemoderun mangohud %command% -dx12 -dx12 %command%")
        self.assertEqual(new, "A=1 gamemoderun mangohud %command% -dx12")
        self.assertTrue(notes)
        new, notes = launchopts.clean('WINEDLLOVERRIDES="dxgi=n,b" A=2 WINEDLLOVERRIDES="winmm=n,b;dxgi=b" %command%')
        self.assertEqual(new.count("WINEDLLOVERRIDES"), 1)
        self.assertIn("dxgi=b", new)
        self.assertIn("winmm=n,b", new)
        self.assertNotIn("dxgi=n,b", new)
        # nothing to clean: returned exactly as given
        same = "PROTON_LOG=1  mangohud   %command% -windowed"
        self.assertEqual(launchopts.clean(same), (same, []))
        self.assertEqual(launchopts.clean(""), ("", []))
        self.assertEqual(launchopts.clean("%command% -a x -a")[0], "%command% -a x -a")   # -a x is a flag with a value
        # values are single-quoted: nothing in them is expanded by the shell
        self.assertEqual(launchopts.merge("%command%", {"FOO": "$(id)"}), "FOO='$(id)' %command%")

    def test_launch_plan_reports_cleanup(self):
        p = self.m.launch_plan("1001", "MANGOHUD=1 MANGOHUD=1 %command% %command%")
        self.assertEqual(p["new"].count("MANGOHUD=1"), 1)
        self.assertEqual(p["new"].count("%command%"), 1)
        self.assertTrue(p["cleaned"])
        self.assertTrue(p["changed"])


class TestDanglingLinks(Offline):
    """Clive Barker's Hellraiser - Revival: a dangling d3dcompiler_47.dll link (another ReShade tool) made the copy fail
    with FileNotFoundError."""

    def setUp(self):
        super().setUp()
        reshade_setup(self)

    def test_install_over_dangling_links(self):
        comp = self.tmp / "compiler.dll"
        comp.write_bytes(b"D3DC")
        self.m.reshade.ensure_d3dcompiler = lambda arch, job=None: comp
        missing = self.tmp / "gone" / "deeper"
        os.symlink(missing / "d3dcompiler_47.dll", self.win64 / "d3dcompiler_47.dll")
        os.symlink(missing / "dxgi.dll", self.win64 / "dxgi.dll")
        self.run_job(lambda j: self.m.reshade.install_game(self.m.game("1001"), j))
        self.assertFalse((self.win64 / "d3dcompiler_47.dll").is_symlink())
        self.assertEqual((self.win64 / "d3dcompiler_47.dll").read_bytes(), b"D3DC")
        self.assertEqual((self.win64 / "dxgi.dll").read_bytes(), b"RS64")
        self.assertFalse(missing.exists())   # never created the link's target folder
        self.assertTrue(self.m.reshade.record(self.win64)["d3dcompiler"])

    def test_game_compiler_is_kept_and_missing_source_falls_back(self):
        (self.win64 / "d3dcompiler_47.dll").write_bytes(b"GAME")
        self.m.reshade.ensure_d3dcompiler = lambda arch, job=None: self.tmp / "nope.dll"
        self.run_job(lambda j: self.m.reshade.install_game(self.m.game("1001"), j))
        self.assertEqual((self.win64 / "d3dcompiler_47.dll").read_bytes(), b"GAME")
        (self.win64 / "d3dcompiler_47.dll").unlink()
        self.run_job(lambda j: self.m.reshade.install_game(self.m.game("1001"), j))   # source missing: no crash
        self.assertFalse(self.m.reshade.record(self.win64).get("d3dcompiler"))
        self.assertNotIn("d3dcompiler_47", self.m.reshade.launch("1001")["dlls"])


class TestReShadeSwitch(Offline):
    def setUp(self):
        super().setUp()
        reshade_setup(self)
        self.m.reshade.ensure_d3dcompiler = lambda arch, job=None: None
        self.run_job(lambda j: self.m.reshade.install_game(self.m.game("1001"), j))

    def test_master_switch(self):
        self.assertEqual((self.win64 / "dxgi.dll").read_bytes(), b"RS64")
        st = self.m.reshade.set_enabled("1001", False)
        self.assertFalse((self.win64 / "dxgi.dll").exists())
        self.assertTrue((self.win64 / "ReShade.ini").exists())   # settings kept
        self.assertEqual(self.m.reshade.launch("1001"), {})
        self.assertNotIn("dxgi", self.m.wanted_launch(None, "1001")["dlls"])
        self.assertTrue(self.m.reshade.status("1001").get("paused"))
        self.assertIsInstance(st, dict)
        self.m.reshade.set_enabled("1001", True)
        self.assertEqual((self.win64 / "dxgi.dll").read_bytes(), b"RS64")
        self.assertEqual(self.m.wanted_launch(None, "1001")["dlls"], {"dxgi": "n,b"})

    def test_master_switch_gives_back_the_games_file_and_survives_offline(self):
        self.m.reshade.uninstall_game("1001")
        (self.win64 / "dxgi.dll").write_bytes(b"GAME-DXGI")
        self.run_job(lambda j: self.m.reshade.install_game(self.m.game("1001"), j))
        self.assertEqual((self.win64 / "dxgi.dll").read_bytes(), b"RS64")
        self.m.reshade.set_enabled("1001", False)
        self.assertEqual((self.win64 / "dxgi.dll").read_bytes(), b"GAME-DXGI")
        self.m.reshade.set_enabled("1001", True)
        self.assertEqual((self.win64 / "dxgi.dll").read_bytes(), b"RS64")
        # switching off never needs the network, switching on without the binary fails cleanly
        import shutil
        shutil.rmtree(self.m.reshade.binary_dir("6.7.3", True))
        self.serve.clear()
        self.m.reshade.set_enabled("1001", False)
        with self.assertRaises(UserError):
            self.m.reshade.set_enabled("1001", True)
        self.assertFalse(self.m.reshade.record(self.win64)["enabled"])
        self.m.reshade.uninstall_game("1001")
        self.assertEqual((self.win64 / "dxgi.dll").read_bytes(), b"GAME-DXGI")

    def test_master_switch_with_optiscaler_chain(self):
        self.store("optiscaler", "v0.9.4", self.opti094(), "optiscaler")
        self.install("optiscaler")
        o = self.win64 / "OptiScaler.ini"
        self.assertEqual(ini.read(o).get("Plugins", "LoadReshade"), "true")
        self.m.reshade.set_enabled("1001", False)
        self.assertEqual((self.win64 / "dxgi.dll").read_bytes(), b"OPTI")   # the injector is untouched
        self.assertFalse((self.win64 / "ReShade64.dll").exists())
        self.assertNotEqual(ini.read(o).get("Plugins", "LoadReshade"), "true")
        self.m.reshade.set_enabled("1001", True)
        self.assertEqual((self.win64 / "ReShade64.dll").read_bytes(), b"RS64")
        self.assertEqual(ini.read(o).get("Plugins", "LoadReshade"), "true")

    def test_parse_uniforms(self):
        ps = {p["name"]: p for p in parse_uniforms(FX)}
        self.assertEqual(set(ps), {"Strength", "Enable", "Mode", "Tint"})   # source / hidden / commented are skipped
        self.assertEqual((ps["Strength"]["min"], ps["Strength"]["max"], ps["Strength"]["default"]), (0.0, 2.0, [0.5]))
        self.assertEqual((ps["Enable"]["kind"], ps["Enable"]["default"]), ("bool", [1.0]))
        self.assertEqual(ps["Mode"]["items"], ["Soft", "Hard", "Extreme"])
        self.assertEqual((ps["Mode"]["min"], ps["Mode"]["max"]), (0.0, 2.0))
        self.assertEqual((ps["Tint"]["components"], ps["Tint"]["default"]), (3, [1.0, 0.5, 0.25]))

    def test_params_edit_save_restore(self):
        self.m.reshade.preset_action("1001", "new", "Mine")
        self.m.reshade.toggle_effect("1001", "Test@Test.fx", True)
        info = self.m.reshade.effect_params("1001", "Test.fx")
        self.assertEqual(info["preset"], "mine.ini")
        self.assertFalse(any(p["changed"] for p in info["params"]))
        info = self.m.reshade.set_param("1001", "Test.fx", "Strength", [9.0])   # clamped to ui_max
        self.assertEqual(next(p for p in info["params"] if p["name"] == "Strength")["value"], [2.0])
        self.m.reshade.set_param("1001", "Test.fx", "Tint", [0.1])
        self.m.reshade.set_param("1001", "Test.fx", "Enable", [0])
        txt = (self.win64 / "mine.ini").read_text()
        self.assertIn("[Test.fx]", txt)
        self.assertIn("Strength=2.000000", txt)
        self.assertIn("Tint=0.100000,0.100000,0.100000", txt)
        self.assertIn("Enable=0", txt)
        # restore without a saved default: the shader's own values
        info = self.m.reshade.restore_param_defaults("1001", "Test.fx")
        self.assertEqual(next(p for p in info["params"] if p["name"] == "Strength")["value"], [0.5])
        # save a default, change, restore: back to the saved default
        self.m.reshade.set_param("1001", "Test.fx", "Strength", [1.5])
        info = self.m.reshade.save_param_defaults("1001", "Test.fx")
        self.assertTrue(info["has_saved_default"])
        self.m.reshade.set_param("1001", "Test.fx", "Strength", [0.2])
        info = self.m.reshade.restore_param_defaults("1001", "Test.fx")
        self.assertEqual(next(p for p in info["params"] if p["name"] == "Strength")["value"], [1.5])
        with self.assertRaises(UserError):
            self.m.reshade.set_param("1001", "Test.fx", "Timer", [1])
        with self.assertRaises(UserError):
            self.m.reshade.effect_params("1001", "../../etc/passwd.fx")


class TestRenoDXSettings(Offline):
    def setUp(self):
        super().setUp()
        Paths.data("cache")
        idx = {"games": [{"id": "space", "title": "Space Game", "aliases": [], "steam_appid": 1001,
                          "mods": [{"id": "spacegame", "title": "Space Game", "category": "game", "status": "stable",
                                    "maintainers": ["ShortFuse"], "notes": [], "urls": [],
                                    "artifacts": [{"name": "renodx-spacegame.addon64", "arch": "x64",
                                                   "url": "./renodx-spacegame.addon64"}]}]},
                         {"id": "far", "title": "Far Game", "aliases": [], "steam_appid": 9999,
                          "mods": [{"id": "fargame", "title": "Far Game", "category": "game", "status": "stable",
                                    "maintainers": ["X"], "notes": [], "urls": [],
                                    "artifacts": [{"name": "renodx-fargame.addon64", "arch": "x64",
                                                   "url": "./renodx-fargame.addon64"}]}]}]}
        (Paths.runtime_dir / "cache" / "renodx-index-0.json").write_text(json.dumps(idx))
        (Paths.runtime_dir / "cache" / "renodx-mods.md").write_text(WIKI)
        reshade_setup(self)
        self.m.reshade.ensure_d3dcompiler = lambda arch, job=None: None
        for name in ("renodx-spacegame.addon64", "renodx-fargame.addon64"):
            mod = self.tmp / name
            mod.write_bytes(b"MZ" + b"\0" * 20000)
            self.serve[name] = mod
        (self.win64 / "SpaceGame-Win64-Shipping.exe").write_bytes(b"MZ" + b"\0" * 64 + b"++UE4+Release\0d3d11.dll\0")

    def test_settings_view(self):
        v = settings_view({"ToneMapType": "3", "ToneMapPeakNits": "800", "FxBloom": "50", "Label": "text"}, 1000)
        keys = [x["key"] for x in v]
        self.assertEqual(keys[:3], ["ToneMapPeakNits", "ToneMapGameNits", "ToneMapUINits"])
        self.assertIn("ToneMapType", keys)
        self.assertIn("FxBloom", keys)
        self.assertNotIn("Label", keys)   # text values stay in RenoDX's menu
        peak = next(x for x in v if x["key"] == "ToneMapPeakNits")
        self.assertEqual(peak["value"], 800.0)
        game = next(x for x in v if x["key"] == "ToneMapGameNits")
        self.assertFalse(game["present"])
        bloom = next(x for x in v if x["key"] == "FxBloom")
        self.assertEqual((bloom["min"], bloom["max"]), (0, 100))

    def test_settings_export_import_and_any_game_mod(self):
        g = self.m.game("1001")
        self.run_job(lambda j: self.m.renodx._install(g, "official:spacegame", j, None, None))
        st = self.m.renodx.game_state("1001")
        self.assertTrue(st["params"])
        # a key RenoDX wrote that RAT does not know about is still editable
        ini.edit_file(self.win64 / "ReShade.ini", [("renodx-preset1", "FxSomethingNew", "0.25")])
        st = self.m.renodx.set_setting("1001", "FxSomethingNew", 0.75)
        self.assertEqual(float(st["settings"]["FxSomethingNew"]), 0.75)
        with self.assertRaises(UserError):
            self.m.renodx.set_setting("1001", "NotARealKey", 1)
        out = self.m.renodx.export_settings("1001")
        text = Path(out["path"]).read_text()
        self.assertIn("[renodx-preset1]", text)
        self.assertIn("FxSomethingNew=", text)
        imp = self.tmp / "in.ini"
        imp.write_text("[renodx-preset1]\nToneMapPeakNits=650\nEvil=../../x\n[other]\nA=1\n")
        r = self.m.renodx.import_settings("1001", str(imp))
        self.assertEqual(r["imported"], 1)
        self.assertEqual(r["settings"]["ToneMapPeakNits"], "650")
        self.assertNotIn("Evil", r["settings"])
        # any game's mod, no app id / title match needed
        allm = self.m.renodx.browse_all()
        ids = [m["id"] for m in allm]
        self.assertIn("any:index:fargame", ids)
        self.assertTrue(any(i.startswith("any:generic:") for i in ids))
        self.m.renodx.uninstall("1001")
        self.run_job(lambda j: self.m.renodx._install(self.m.game("1001"), "any:index:fargame", j, None, None))
        self.assertTrue((self.win64 / "renodx-fargame.addon64").exists())
        self.assertEqual(self.m.renodx.record(self.win64)["file"], "renodx-fargame.addon64")

    def test_hdr_pauses_with_reshade(self):
        self.run_job(lambda j: self.m.renodx._install(self.m.game("1001"), "official:spacegame", j, None, None))
        self.assertEqual(self.m.wanted_launch(None, "1001")["env"].get("DXVK_HDR"), "1")
        self.m.reshade.set_enabled("1001", False)
        self.assertNotIn("DXVK_HDR", self.m.wanted_launch(None, "1001")["env"])
        st = self.m.renodx.status("1001")
        self.assertFalse(st["active"])
        self.assertTrue(st["paused"])
        # enabling HDR again switches ReShade back on
        self.run_job(lambda j: self.m.renodx._install(self.m.game("1001"), "official:spacegame", j, None, None))
        self.assertTrue(self.m.reshade.record(self.win64)["enabled"])
        self.assertEqual((self.win64 / "dxgi.dll").read_bytes(), b"RS64")
        self.assertTrue(self.m.renodx.status("1001")["active"])

    def test_dangling_addon_link(self):
        os.symlink(self.tmp / "nowhere" / "x.addon64", self.win64 / "renodx-spacegame.addon64")
        self.run_job(lambda j: self.m.renodx._install(self.m.game("1001"), "official:spacegame", j, None, None))
        p = self.win64 / "renodx-spacegame.addon64"
        self.assertFalse(p.is_symlink())
        self.assertTrue(p.read_bytes().startswith(b"MZ"))


class TestCrossGameProfiles(Offline):
    def test_apply_from_another_game(self):
        self.store("optiscaler", "v0.9.4", self.opti094(), "optiscaler")
        self.m.set_choice("1001", upscaler="xess")
        self.install("optiscaler")
        self.m.save_ini("1001", "optiscaler", [["CAS", "Sharpness", "0.33"]])
        self.second_game()
        c = self.m.profiles.candidates("1002")
        self.assertEqual([x["key"] for x in c], ["1001"])
        self.assertEqual(self.m.profiles.candidates("1001"), [])
        r = self.m.profiles.apply_from_game("1002", "1001")
        self.assertEqual(self.m.choice("1002")["upscaler"], "xess")
        self.assertTrue(r["applied"])
        with self.assertRaises(UserError):
            self.m.profiles.apply_from_game("1001", "1001")

    def test_imported_profile_cannot_inject_launch_commands(self):
        prof = self.m.profiles.snapshot("1001")
        prof["launch"] = {"extra_env": {"FOO": "$(id>/tmp/p)", "VKD3D_SWAPCHAIN_IMAGES": "`id`",
                                        "LD_PRELOAD": "/tmp/x.so"}}
        self.m.profiles.paste("1001", prof)
        self.assertEqual(self.m.extra_env("1001"), {})
        prof["launch"] = {"extra_env": {"VKD3D_SWAPCHAIN_IMAGES": "3"}}
        self.m.profiles.paste("1001", prof)
        self.assertEqual(self.m.extra_env("1001"), {"VKD3D_SWAPCHAIN_IMAGES": "3"})


class TestNvidiaOnlyHidden(unittest.TestCase):
    def test_upscalers_and_paths_by_gpu(self):
        amd = {"vendor": "AMD", "family": "RDNA3", "dlss": False}
        nv = {"vendor": "NVIDIA", "family": "Ada", "dlss": True}
        ids = lambda gpu: [u["id"] for u in A.upscalers_for(A.ADDONS["optiscaler"], gpu)]
        self.assertNotIn("dlss", ids(amd))
        self.assertIn("dlss", ids(nv))
        self.assertNotIn("native", A.paths_of(A.ADDONS["optiscaler"], gpu=amd))
        self.assertEqual(A.paths_of(A.ADDONS["d4r"], gpu=amd), ["native"])
        du = A.default_upscaler(A.ADDONS["optiscaler"], amd)
        self.assertNotEqual(du.get("id") if isinstance(du, dict) else du, "dlss")
        # RCAS is the default sharpening, and no option offers NVIDIA's sharpener
        for ad in A.ADDONS.values():
            for o in ad.get("options", []):
                vals = [str(c.get("value", c) if isinstance(c, dict) else c).lower() for c in o.get("choices", [])]
                self.assertNotIn("nvidia", vals, (ad["id"], o.get("key")))


if __name__ == "__main__":
    unittest.main()
