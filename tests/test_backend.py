"""Backend tests with a fake Steam library and fake add-on packages (no network, no Steam needed).

    python3 -m unittest discover -s tests -v

Optional integration tests use real release archives when RAT_REAL_DIR points at a folder with
Optiscaler_*.7z, HelixSR-*.zip and/or d4r-*.zip.
"""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "py_modules"))

from rat import addons as A  # noqa: E402
from rat import gpu, ini, launchopts, sources, steam, vdf  # noqa: E402
from rat.installer import OFF, REC_DIR, Manager  # noqa: E402
from rat.jobs import Job  # noqa: E402
from rat.util import Paths, UserError  # noqa: E402

OPTI_INI = """[Upscalers]
; Select Upscaler for Dx12 games
Dx11Upscaler=auto
Dx12Upscaler=auto
VulkanUpscaler=auto

[FSR]
UpscalerIndex=auto

[Spoofing]
Dxgi=auto

[CAS]
Enabled=auto
Sharpness=auto
"""

D4R_INI = """[DLSS]
Model = K
ShowWatermark = false

[Kernels]
PreferAccuracy = false
NativeSwinEncoders = true
NativeFp8 = on

[Latency]
FrameAge = 0
"""


def tree(d: Path) -> dict:
    out = {}
    for p in sorted(Path(d).rglob("*")):
        if p.is_file():
            out[p.relative_to(d).as_posix()] = p.read_bytes()
    return out


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="rat-test-"))
        self.home = self.tmp / "home"
        steam_root = self.home / ".local/share/Steam"
        apps = steam_root / "steamapps"
        (apps / "common").mkdir(parents=True)
        (steam_root / "config").mkdir(parents=True)
        (apps / "libraryfolders.vdf").write_text('"libraryfolders"\n{\n "0"\n {\n  "path" "%s"\n }\n}\n' % steam_root)
        # an Unreal Engine game with DLSS (deep in Engine plugins) and FSR 3.1 next to the exe
        self.game = apps / "common" / "Space Game"
        win64 = self.game / "SpaceGame" / "Binaries" / "Win64"
        win64.mkdir(parents=True)
        (win64 / "SpaceGame-Win64-Shipping.exe").write_bytes(b"MZ" + b"\0" * 5000)
        (win64 / "amd_fidelityfx_upscaler_dx12.dll").write_bytes(b"GAME-FSR")
        plug = self.game / "Engine" / "Plugins" / "Runtime" / "Nvidia" / "DLSS" / "Binaries" / "ThirdParty" / "Win64"
        plug.mkdir(parents=True)
        (plug / "nvngx_dlss.dll").write_bytes(b"DLSS")
        (self.game / "Engine" / "Binaries" / "Win64").mkdir(parents=True)
        (self.game / "Engine" / "Binaries" / "Win64" / "CrashReportClient.exe").write_bytes(b"MZ" + b"\0" * 9000)
        (self.game / "SpaceGame.exe").write_bytes(b"MZ" + b"\0" * 100)
        (apps / "appmanifest_1001.acf").write_text(
            '"AppState"\n{\n "appid" "1001"\n "name" "Space Game"\n "installdir" "Space Game"\n "StateFlags" "4"\n}\n')
        (apps / "common" / "Proton 10.0").mkdir()
        (apps / "appmanifest_228980.acf").write_text(
            '"AppState"\n{\n "appid" "228980"\n "name" "Steamworks Common Redistributables"\n "installdir" "SW"\n}\n')
        (apps / "common" / "SW").mkdir()
        (steam_root / "config" / "config.vdf").write_text(
            '"InstallConfigStore"\n{\n "Software"\n {\n  "Valve"\n  {\n   "Steam"\n   {\n    "CompatToolMapping"\n'
            '    {\n     "1001"\n     {\n      "name" "GE-Proton11-3"\n      "config" ""\n     }\n    }\n   }\n  }\n }\n}\n')
        self.win64 = win64
        Paths.configure(self.home, ROOT, self.tmp / "settings", self.tmp / "runtime")
        # no network in tests: anything that would reach GitHub / reshade.me fails like an offline device
        self._net = (sources.get_json, sources.get_text, sources.download)

        def offline(*a, **k):
            raise UserError("offline (tests)")
        sources.get_json = sources.get_text = sources.download = offline
        self.m = Manager()
        self.m._gpu = {"vendor": "AMD", "name": "Test", "gfx": "gfx1101", "family": "RDNA3", "steam_deck": False}

    def tearDown(self):
        sources.get_json, sources.get_text, sources.download = self._net
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make_zip(self, name: str, files: dict) -> Path:
        z = self.tmp / name
        with zipfile.ZipFile(z, "w") as zf:
            for rel, data in files.items():
                zf.writestr(rel, data)
        return z

    def store(self, sid: str, version: str, files: dict, kind: str) -> dict:
        z = self.make_zip(f"{sid}-{version}.zip", files)
        return sources.store_package(z, {"source_id": sid, "version": version, "asset": z.name, "url": None,
                                         "sha256": None, "kind": kind, "title": sid})

    def opti_files(self, marker=b"OPTI"):
        return {"OptiScaler.dll": marker, "OptiScaler.ini": OPTI_INI, "amd_fidelityfx_upscaler_dx12.dll": b"OPTI-FSR",
                "D3D12_Optiscaler/D3D12Core.dll": b"CORE", "setup_linux.sh": "#!/bin/sh", "fakenvapi.dll": b"F",
                "fakenvapi.ini": "[fakenvapi]\n", "Licenses/x.txt": "lic", "!! README_EXTRACT ALL FILES !!.txt": "x"}

    def run_job(self, fn):
        job = Job("test")
        return fn(job), job


class TestVdfSteam(Base):
    def test_games_and_tools(self):
        games = steam.games(self.home)
        self.assertEqual([g["name"] for g in games], ["Space Game"])
        self.assertEqual(steam.current_compat_tool("1001", self.home), "GE-Proton11-3")
        tools = steam.compat_tools(self.home)
        self.assertEqual(tools, [])  # "Proton 10.0" has no proton script
        (self.home / ".local/share/Steam/steamapps/common/Proton 10.0/proton").write_text("")
        self.assertEqual(steam.compat_tools(self.home)[0]["name"], "Proton 10.0")

    def test_exe_candidates_prefer_unreal_shipping(self):
        c = steam.exe_candidates(self.game)
        self.assertEqual(Path(c[0]["dir"]), self.win64)
        self.assertNotIn("CrashReportClient.exe", [x["exe"] for x in c])

    def test_scan_finds_engine_dlss(self):
        s = steam.scan_game(self.game)
        self.assertIn("DLSS", s["native"])
        self.assertIn("FSR 3.1+", s["native"])
        self.assertEqual(s["anticheat"], [])

    def test_vdf_escapes(self):
        d = vdf.loads('"a" { "b" "x\\"y" // c\n "C" { "d" "1" } }')
        self.assertEqual(d["a"]["b"], 'x"y')
        self.assertEqual(vdf.get_ci(d, "A", "c", "D"), "1")


class TestGpu(unittest.TestCase):
    def test_families(self):
        self.assertEqual(gpu.family_of("gfx1033"), "RDNA2")
        self.assertEqual(gpu.family_of("gfx1101"), "RDNA3")
        self.assertEqual(gpu.family_of("gfx1151"), "RDNA3.5")
        self.assertEqual(gpu.family_of("gfx1201"), "RDNA4")
        self.assertEqual(gpu.family_of("gfx1010"), "RDNA1")
        self.assertEqual(gpu._gfx_from_version(110001), "gfx1101")
        self.assertEqual(gpu._gfx_from_version(100303), "gfx1033")

    def test_steam_deck_sysfs(self):
        root = Path(tempfile.mkdtemp())
        try:
            (root / "sys/devices/virtual/dmi/id").mkdir(parents=True)
            (root / "sys/devices/virtual/dmi/id/board_vendor").write_text("Valve\n")
            (root / "sys/devices/virtual/dmi/id/product_name").write_text("Galileo\n")
            dev = root / "sys/class/drm/card0/device"
            dev.mkdir(parents=True)
            (dev / "vendor").write_text("0x1002\n")
            (dev / "device").write_text("0x1435\n")
            g = gpu.detect(str(root))
            self.assertTrue(g["steam_deck"])
            self.assertEqual(g["family"], "RDNA2")
            self.assertEqual(g["name"], "Steam Deck OLED")
            self.assertEqual(A.gpu_support(A.ADDONS["d4r"], g)["level"], "unsupported")
            self.assertEqual(A.gpu_support(A.ADDONS["helixsr"], g)["level"], "ok")
        finally:
            shutil.rmtree(root)


class TestIni(unittest.TestCase):
    def test_set_preserves_comments_and_crlf(self):
        doc = ini.IniDoc(OPTI_INI.replace("\n", "\r\n"))
        self.assertTrue(doc.set("Upscalers", "Dx12Upscaler", "ffx"))
        self.assertFalse(doc.set("upscalers", "dx12upscaler", "ffx"))
        doc.set("FSR", "NewKey", "1")
        doc.set("Brand", "K", "v")
        t = doc.text()
        self.assertIn("; Select Upscaler for Dx12 games\r\nDx11Upscaler=auto\r\nDx12Upscaler=ffx", t)
        self.assertIn("UpscalerIndex=auto\r\nNewKey=1", t)
        self.assertTrue(t.endswith("[Brand]\r\nK=v\r\n"))

    def test_spaces_and_inline_comment(self):
        doc = ini.IniDoc("[DLSS]\nModel = K ; DLSS 4\n")
        self.assertEqual(doc.get("dlss", "model"), "K")
        doc.set("DLSS", "Model", "M")
        self.assertEqual(doc.text(), "[DLSS]\nModel = M ; DLSS 4\n")

    def test_duplicate_section_header(self):
        doc = ini.IniDoc("[Upscalers]\n[Upscalers]\n; c\nDx12Upscaler=auto\n")
        doc.set("Upscalers", "Dx12Upscaler", "xess")
        self.assertEqual(doc.get("Upscalers", "Dx12Upscaler"), "xess")
        self.assertEqual(doc.text().count("Dx12Upscaler"), 1)

    def test_merge_user_values(self):
        new = "[A]\nx=auto\ny=1\nz=new\n[L]\npath=new\n"
        old = "[A]\nx=user\ny=1\ngone=5\n[L]\npath=old\n"
        out = ini.merge_user_values(new, old, {("l", "path")})
        self.assertEqual(out, "[A]\nx=user\ny=1\nz=new\n[L]\npath=new\n")


class TestLaunchOptions(unittest.TestCase):
    def test_add_to_empty(self):
        self.assertEqual(launchopts.merge("", {"PROTON_FORCE_NVAPI": "1"}, {"dxgi": "n,b"}),
                         'PROTON_FORCE_NVAPI=1 WINEDLLOVERRIDES="dxgi=n,b" %command%')

    def test_keeps_user_parts_and_merges_overrides(self):
        cur = 'MANGOHUD=1 WINEDLLOVERRIDES="winhttp=n,b" gamemoderun %command% -dx12 -skipintro'
        out = launchopts.merge(cur, {"DXVK_NVAPI_GPU_ARCH": "AD100"}, {"dxgi": "n,b"})
        self.assertEqual(out, 'MANGOHUD=1 DXVK_NVAPI_GPU_ARCH=AD100 WINEDLLOVERRIDES="dxgi=n,b" '
                              'gamemoderun %command% -dx12 -skipintro')
        # winhttp counted as RAT's (unknown ownership): replaced. With ownership known it stays:
        out2 = launchopts.merge(cur, {}, {"dxgi": "n,b"}, owned_env=set(), owned_dlls=set())
        self.assertIn('WINEDLLOVERRIDES="winhttp=n,b;dxgi=n,b"', out2)

    def test_args_without_command(self):
        self.assertEqual(launchopts.merge("-dx12", {}, {"dxgi": "n,b"}), 'WINEDLLOVERRIDES="dxgi=n,b" %command% -dx12')

    def test_removes_conflicting_builtin_optiscaler_and_cleans_up(self):
        cur = "PROTON_USE_OPTISCALER=1 PROTON_FORCE_NVAPI=1 DXVK_NVAPI_GPU_ARCH=AD100 %command%"
        out = launchopts.merge(cur, {"PROTON_FORCE_NVAPI": "1"}, {})
        self.assertEqual(out, "PROTON_FORCE_NVAPI=1 %command%")
        self.assertEqual(launchopts.merge(out, {}, {}, remove_conflicts=False), "")

    def test_multi_name_override(self):
        self.assertEqual(launchopts.parse_dll_overrides("'a,b=n,b;c'"), [("a", "n,b"), ("b", "n,b"), ("c", "")])


class TestOptiScalerInstall(Base):
    def install(self, choice=None, files=None):
        self.store("optiscaler", "v0.9.4", files or self.opti_files(), "optiscaler")
        if choice:
            for k, v in choice.items():
                if k == "upscaler":
                    self.m.set_choice("1001", upscaler=v)
                else:
                    self.m.set_choice("1001", option=[k, v])
        self.m.set_choice("1001", source_id="optiscaler")
        res, job = self.run_job(lambda j: self.m._install(self.m.game("1001"), "optiscaler", j, False))
        return res, job

    def test_install_activate_uninstall_restores_everything(self):
        before = tree(self.game)
        res, job = self.install({"upscaler": "fsr4"})
        self.assertEqual((self.win64 / "dxgi.dll").read_bytes(), b"OPTI")
        self.assertEqual((self.win64 / "amd_fidelityfx_upscaler_dx12.dll").read_bytes(), b"OPTI-FSR")
        self.assertEqual((self.win64 / REC_DIR / "backup/amd_fidelityfx_upscaler_dx12.dll").read_bytes(), b"GAME-FSR")
        self.assertFalse((self.win64 / "OptiScaler.dll").exists())
        self.assertFalse((self.win64 / "setup_linux.sh").exists())
        self.assertFalse((self.win64 / "fakenvapi.dll").exists())
        o = ini.read(self.win64 / "OptiScaler.ini")
        self.assertEqual(o.get("Upscalers", "Dx12Upscaler"), "ffx")
        self.assertEqual(o.get("FSR", "UpscalerIndex"), "0")
        self.assertIsNone(o.get("FSR", "Fsr4ForceModel"))   # 0.9.4 has no such key: not added
        self.assertEqual(res["launch"]["dlls"], {"dxgi": "n,b"})
        d = self.m.detail("1001")
        self.assertEqual(d["status"]["state"], "active")
        self.assertEqual(d["upscaler"], "fsr4")
        # deactivate / activate
        self.m.set_active("1001", False)
        self.assertTrue((self.win64 / ("dxgi.dll" + OFF)).exists())
        self.assertFalse((self.win64 / "dxgi.dll").exists())
        self.assertEqual(self.m.detail("1001")["status"]["state"], "inactive")
        self.assertEqual(self.m.wanted_launch(self.m.record_for(self.m.game("1001")))["dlls"], {})
        self.m.set_upscaler("1001", "xess")   # picking an upscaler re-enables
        self.assertTrue((self.win64 / "dxgi.dll").exists())
        self.assertEqual(ini.read(self.win64 / "OptiScaler.ini").get("Upscalers", "Dx12Upscaler"), "xess")
        # runtime files appear while playing
        (self.win64 / "OptiScaler.log").write_text("log")
        (self.win64 / "D3D12_Optiscaler" / "cache.bin").write_text("c")
        self.run_job(lambda j: self.m._uninstall(self.m.game("1001"), j))
        self.assertEqual(tree(self.game), before)

    def test_options_and_proxy(self):
        self.install({"proxy": "winmm.dll", "fakenvapi": True, "cas": True, "spoof": False})
        self.assertEqual((self.win64 / "winmm.dll").read_bytes(), b"OPTI")
        self.assertTrue((self.win64 / "fakenvapi.dll").exists())
        o = ini.read(self.win64 / "OptiScaler.ini")
        self.assertEqual(o.get("CAS", "Enabled"), "true")
        self.assertEqual(o.get("Spoofing", "Dxgi"), "false")
        plan = self.m.launch_plan("1001", "MANGOHUD=1 %command%")
        self.assertEqual(plan["new"], 'MANGOHUD=1 WINEDLLOVERRIDES="winmm=n,b" %command%')
        self.m.launch_applied("1001")
        d = self.m.detail("1001")
        self.assertTrue(d["options"]["cas"])
        self.assertFalse(d["options"]["spoof"])

    def test_update_keeps_user_settings(self):
        self.install({"upscaler": "fsr31"})
        p = self.win64 / "OptiScaler.ini"
        doc = ini.read(p)
        doc.set("CAS", "Sharpness", "0.7")      # the user's own tweak
        ini.write(p, doc)
        self.store("optiscaler", "v0.9.5", self.opti_files(b"OPTI2"), "optiscaler")
        time.sleep(0.01)
        rec = self.m.record_for(self.m.game("1001"))
        # simulate the update path without network: newest stored package is used
        self.m._download_source = lambda sid, job: self.m.packages_for(sid)[0]
        self.run_job(lambda j: self.m._install(self.m.game("1001"), "optiscaler", j, True, update=True))
        self.assertEqual((self.win64 / "dxgi.dll").read_bytes(), b"OPTI2")
        o = ini.read(p)
        self.assertEqual(o.get("CAS", "Sharpness"), "0.7")
        self.assertEqual(o.get("Upscalers", "Dx12Upscaler"), "ffx")
        self.assertEqual(self.m.record_for(self.m.game("1001"))["package_version"], "v0.9.5")
        self.assertEqual(rec["installed"], self.m.record_for(self.m.game("1001"))["installed"])
        # the game's original FSR DLL is still the one in the backup (not OptiScaler's from the first install)
        self.assertEqual((self.win64 / REC_DIR / "backup/amd_fidelityfx_upscaler_dx12.dll").read_bytes(), b"GAME-FSR")

    def test_game_update_after_install_is_kept(self):
        self.install({"upscaler": "fsr31"})
        # a game patch replaces RAT's copy of the FSR DLL with a newer game file
        (self.win64 / "amd_fidelityfx_upscaler_dx12.dll").write_bytes(b"GAME-FSR-PATCHED")
        self.run_job(lambda j: self.m._uninstall(self.m.game("1001"), j))
        self.assertEqual((self.win64 / "amd_fidelityfx_upscaler_dx12.dll").read_bytes(), b"GAME-FSR-PATCHED")
        self.assertFalse((self.win64 / REC_DIR).exists())

    def test_switch_addon_does_not_inherit_upscaler_and_inactive_survives_reinstall(self):
        self.install({"upscaler": "none"})
        self.assertEqual(self.m.detail("1001")["status"]["state"], "inactive")
        # reinstall the same add-on: stays inactive
        self.run_job(lambda j: self.m._install(self.m.game("1001"), "optiscaler", j, False))
        self.assertEqual(self.m.detail("1001")["status"]["state"], "inactive")
        self.m.set_active("1001", True)
        self.run_job(lambda j: self.m._install(self.m.game("1001"), "optiscaler", j, False))
        self.assertEqual(self.m.detail("1001")["status"]["state"], "active")
        # switch to d4r: its default model, active
        self.store("d4r", "v1", {"dxgi.dll": b"D", "OptiScaler.ini": OPTI_INI, "d4r/d4r.ini": D4R_INI,
                                 "d4r/nvngx.dll": b"S"}, "d4r")
        self.m.set_choice("1001", source_id="d4r")
        d = self.m.detail("1001")
        self.assertFalse(d["editing_installed"])
        self.assertEqual(d["upscaler"], "K")
        self.m.set_upscaler("1001", "M")      # goes to the choice, not to OptiScaler's record
        self.assertEqual(self.m.record_for(self.m.game("1001"))["upscaler"], "fsr31")
        self.run_job(lambda j: self.m._install(self.m.game("1001"), "d4r", j, False))
        rec = self.m.record_for(self.m.game("1001"))
        self.assertEqual((rec["kind"], rec["upscaler"], rec["active"]), ("d4r", "M", True))
        self.assertEqual((self.win64 / "amd_fidelityfx_upscaler_dx12.dll").read_bytes(), b"GAME-FSR")

    def test_update_keeps_option_changed_in_ini_and_pending_shows(self):
        self.install()
        ini.edit_file(self.win64 / "OptiScaler.ini", [("CAS", "Enabled", "true")])   # changed in-game
        self.assertTrue(self.m.detail("1001")["options"]["cas"])
        self.m._download_source = lambda sid, job: self.m.packages_for(sid)[0]
        self.run_job(lambda j: self.m._install(self.m.game("1001"), "optiscaler", j, True, update=True))
        self.assertEqual(ini.read(self.win64 / "OptiScaler.ini").get("CAS", "Enabled"), "true")
        r = self.m.set_option("1001", "proxy", "version.dll")
        self.assertTrue(r["needs_reinstall"])
        self.assertEqual(r["options"]["proxy"], "version.dll")
        self.run_job(lambda j: self.m._install(self.m.game("1001"), "optiscaler", j, False))
        self.assertTrue((self.win64 / "version.dll").exists())
        self.assertFalse((self.win64 / "dxgi.dll").exists())

    def test_first_launch_apply_keeps_user_vars(self):
        self.install()
        plan = self.m.launch_plan("1001", 'PROTON_ENABLE_NVAPI=1 WINEDLLOVERRIDES="d3d11=n,b" %command%')
        self.assertEqual(plan["new"], 'PROTON_ENABLE_NVAPI=1 WINEDLLOVERRIDES="d3d11=n,b;dxgi=n,b" %command%')

    def test_running_game_blocks(self):
        self.m.is_running = lambda g: True
        self.store("optiscaler", "v1", self.opti_files(), "optiscaler")
        with self.assertRaises(UserError):
            self.m.start_install("1001")


class TestD4rInstall(Base):
    def d4r_files(self):
        return {"dxgi.dll": b"D4R-OPTI", "OptiScaler.ini": OPTI_INI + "\n[Libraries]\nNvngxPath=d4r\\nvngx.dll\n",
                "d3d12.dll": b"VKD3D", "d3d12core.dll": b"VKD3DCORE", "D4R_README.txt": "readme",
                "d4r/nvngx.dll": b"SHIM", "d4r/d4r.ini": D4R_INI, "d4r/rocm/lib/libamdhip64.so": b"HIP"}

    def test_d4r_install_model_and_launch(self):
        before = tree(self.game)
        self.store("d4r", "v0.1.3", self.d4r_files(), "d4r")
        self.m.set_choice("1001", source_id="d4r", upscaler="M")
        self.m.set_choice("1001", option=["prefer_accuracy", True])
        self.m.set_choice("1001", option=["rr_launch", True])
        res, _ = self.run_job(lambda j: self.m._install(self.m.game("1001"), "d4r", j, False))
        d = ini.read(self.win64 / "d4r/d4r.ini")
        self.assertEqual(d.get("DLSS", "Model"), "M")
        self.assertEqual(d.get("Kernels", "PreferAccuracy"), "true")
        self.assertEqual(res["launch"]["env"], {"PROTON_FORCE_NVAPI": "1", "DXVK_NVAPI_GPU_ARCH": "AD100"})
        self.assertIn("icuuc", res["launch"]["dlls"])
        self.assertEqual(self.m.detail("1001")["upscaler"], "M")
        self.m.set_upscaler("1001", "none")
        for n in ("dxgi.dll", "d3d12.dll", "d3d12core.dll"):
            self.assertTrue((self.win64 / (n + OFF)).exists())
        self.m.set_upscaler("1001", "E")
        self.assertEqual(ini.read(self.win64 / "d4r/d4r.ini").get("DLSS", "Model"), "E")
        self.assertTrue((self.win64 / "d3d12.dll").exists())
        (self.win64 / "d4r" / "d4r_nvngx.log").write_text("x")
        self.run_job(lambda j: self.m._uninstall(self.m.game("1001"), j))
        self.assertEqual(tree(self.game), before)

    def test_unsupported_gpu_blocked(self):
        self.m._gpu = {"vendor": "AMD", "name": "Steam Deck", "gfx": "gfx1033", "family": "RDNA2", "steam_deck": True}
        self.store("d4r", "v0.1.3", self.d4r_files(), "d4r")
        self.m.set_choice("1001", source_id="d4r")
        with self.assertRaises(UserError):
            self.m.start_install("1001")
        self.m.set_pref("allow_unsupported_gpu", True)
        self.m._check_installable(self.m.game("1001"), A.ADDONS["d4r"], False)


class TestSources(Base):
    def test_pick_asset(self):
        assets = [{"name": "d4r-0.1.3.zip.sha256"}, {"name": "d4r-0.1.3-asi.zip"}, {"name": "d4r-0.1.3.zip"},
                  {"name": "Source code.zip"}]
        a = A.ADDONS["d4r"]
        self.assertEqual(sources.pick_asset(assets, a["asset"], a["asset_fallback"], a["asset_exclude"])["name"],
                         "d4r-0.1.3.zip")
        h = A.ADDONS["helixsr"]
        assets = [{"name": "HelixSR-1.9.3.zip"}, {"name": "HelixSR-Scaler-1.0.6-linux.tar.gz"},
                  {"name": "HelixSR.Desktop_1.0.3_amd64.AppImage"}, {"name": "SHA256SUMS.txt"}]
        self.assertEqual(sources.pick_asset(assets, h["asset"], h["asset_fallback"], h["asset_exclude"])["name"],
                         "HelixSR-1.9.3.zip")
        assets = [{"name": "Optiscaler_0.9.4-final.20260718._MM.7z"}]
        o = A.ADDONS["optiscaler"]
        self.assertTrue(sources.pick_asset(assets, o["asset"], o["asset_fallback"], o["asset_exclude"]))
        self.assertIsNone(sources.pick_asset([{"name": "setup.exe"}, {"name": "x.deb"}], None))

    def test_nested_package_and_unsafe_zip(self):
        z = self.make_zip("fork.zip", {"MyFork-1.0/bin/OptiScaler.dll": b"X", "MyFork-1.0/bin/OptiScaler.ini": "[A]\n",
                                       "MyFork-1.0/README.md": "r"})
        meta = sources.store_package(z, {"source_id": "custom-x", "version": "1.0", "asset": "fork.zip", "url": None,
                                         "sha256": None, "title": "fork"})
        self.assertEqual(meta["kind"], "optiscaler")
        self.assertTrue((Path(meta["path"]) / "OptiScaler.dll").exists())
        bad = self.make_zip("bad.zip", {"../../evil.txt": "x"})
        with self.assertRaises(UserError):
            sources.store_package(bad, {"source_id": "custom-y", "version": "1", "asset": "bad.zip", "url": None,
                                        "sha256": None, "title": "bad"})
        junk = self.make_zip("junk.zip", {"readme.txt": "x"})
        with self.assertRaises(UserError):
            sources.store_package(junk, {"source_id": "custom-z", "version": "1", "asset": "junk.zip", "url": None,
                                         "sha256": None, "title": "junk"})

    def test_import_file_job(self):
        z = self.make_zip("MyOpti.zip", self.opti_files())
        snap = self.m.import_file(str(z))
        for _ in range(100):
            j = self.m.jobs.get()
            if j["state"] != "running":
                break
            time.sleep(0.05)
        self.assertEqual(j["state"], "done", j)
        sid = j["result"]["source_id"]
        src = self.m.source(sid)
        self.assertEqual(src["kind"], "optiscaler")
        self.assertEqual(len(self.m.packages_for(sid)), 1)
        self.assertTrue(snap["id"])

    def test_resolve_link_rejects_non_archive(self):
        with self.assertRaises(UserError):
            sources.resolve_link("https://example.com/page.html")
        r = sources.resolve_link("https://github.com/o/r/releases/download/v1/pkg.zip")
        self.assertEqual(r["kind"], "direct")
        self.assertEqual(r["repo"], "o/r")


REAL = os.environ.get("RAT_REAL_DIR")


@unittest.skipUnless(REAL, "set RAT_REAL_DIR to run with real release archives")
class TestRealPackages(Base):
    def find(self, pattern):
        hits = sorted(Path(REAL).glob(pattern))
        if not hits:
            self.skipTest(f"no {pattern} in {REAL}")
        return hits[-1]

    def test_real_optiscaler_7z(self):
        arc = self.find("Optiscaler_*.7z")
        meta = sources.store_package(arc, {"source_id": "optiscaler", "version": "v0.9.4", "asset": arc.name,
                                           "url": None, "sha256": None, "kind": "optiscaler", "title": "OptiScaler"})
        before = tree(self.game)
        self.m.set_choice("1001", source_id="optiscaler", upscaler="fsr31")
        self.run_job(lambda j: self.m._install(self.m.game("1001"), "optiscaler", j, False))
        o = ini.read(self.win64 / "OptiScaler.ini")
        self.assertEqual(o.get("Upscalers", "Dx12Upscaler"), "ffx")
        self.assertEqual(o.get("FSR", "UpscalerIndex"), "1")
        self.assertTrue((self.win64 / "dxgi.dll").stat().st_size > 1_000_000)
        cfg = self.m.read_configs("1001")
        self.assertEqual(cfg[0]["name"], "OptiScaler.ini")
        self.run_job(lambda j: self.m._uninstall(self.m.game("1001"), j))
        self.assertEqual(tree(self.game), before)
        self.assertTrue(meta["path"])

    def test_real_d4r(self):
        arc = self.find("d4r-*.zip")
        sources.store_package(arc, {"source_id": "d4r", "version": "v-real", "asset": arc.name, "url": None,
                                    "sha256": None, "kind": "d4r", "title": "d4r"})
        before = tree(self.game)
        self.m.set_choice("1001", source_id="d4r", upscaler="L")
        self.run_job(lambda j: self.m._install(self.m.game("1001"), "d4r", j, False))
        self.assertEqual(ini.read(self.win64 / "d4r/d4r.ini").get("DLSS", "Model"), "L")
        o = ini.read(self.win64 / "OptiScaler.ini")
        self.assertEqual(o.get("Libraries", "OptiDllPath"), "d4r")
        self.assertEqual(o.get("Upscalers", "Dx12Upscaler"), "dlss")
        self.m.set_option("1001", "native_swin", False)
        self.assertEqual(ini.read(self.win64 / "d4r/d4r.ini").get("Kernels", "NativeSwinEncoders"), "false")
        self.run_job(lambda j: self.m._uninstall(self.m.game("1001"), j))
        self.assertEqual(tree(self.game), before)

    def test_real_helixsr_with_stub_network(self):
        arc = self.find("HelixSR-*.zip")
        meta = sources.store_package(arc, {"source_id": "helixsr", "version": "v-real", "asset": arc.name,
                                           "url": None, "sha256": None, "kind": "helixsr", "title": "HelixSR"})
        pkg = Path(meta["path"])
        self.assertFalse(self.m.helix_ready(meta))
        # stand-in for the locally built network (the real build needs Proton + NVIDIA's DLL)
        for n in ("helixsr_weights.bin", "helixsr_kernels.pak"):
            (pkg / n).write_bytes(b"stub")
        self.assertTrue(self.m.helix_ready(meta))
        st = self.m._bridge(["status", str(pkg), str(self.game)])
        self.assertTrue(st["found"])
        self.assertIn("direct", st["ways"])
        before = tree(self.game)
        # drop-in route
        self.m.set_choice("1001", source_id="helixsr")
        self.m.set_choice("1001", option=["route", "direct"])
        self.run_job(lambda j: self.m._install(self.m.game("1001"), "helixsr", j, False))
        rec = self.m.record_for(self.m.game("1001"))
        self.assertEqual(rec["route"], "direct")
        fsr = self.win64 / "amd_fidelityfx_upscaler_dx12.dll"
        self.assertNotEqual(fsr.read_bytes(), b"GAME-FSR")
        self.assertEqual((self.win64 / "amd_fidelityfx_upscaler_dx12.original.dll").read_bytes(), b"GAME-FSR")
        self.m.set_option("1001", "sharpener", "rcas")
        self.assertEqual(ini.read(self.win64 / "helixsr.ini").get("Sharpening", "Sharpener"), "rcas")
        self.m.set_active("1001", False)
        self.assertEqual(fsr.read_bytes(), b"GAME-FSR")
        self.m.set_active("1001", True)
        self.assertNotEqual(fsr.read_bytes(), b"GAME-FSR")
        # switch to the Scaler route (reinstall)
        self.m.set_choice("1001", option=["route", "scaler"])
        self.run_job(lambda j: self.m._install(self.m.game("1001"), "helixsr", j, False))
        rec = self.m.record_for(self.m.game("1001"))
        self.assertEqual(rec["route"], "scaler")
        self.assertTrue((self.win64 / "dxgi.dll").exists())
        self.assertEqual(fsr.read_bytes(), b"GAME-FSR")
        self.m.set_upscaler("1001", "xess")
        self.assertEqual(ini.read(self.win64 / "OptiScaler.ini").get("Upscalers", "Dx12Upscaler"), "xess")
        self.run_job(lambda j: self.m._uninstall(self.m.game("1001"), j))
        after = tree(self.game)
        extra = set(after) - set(before)
        self.assertEqual(extra, set(), extra)
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
