"""Regression tests for the fixes in RAT 1.1.

    python3 -m unittest discover -s tests -v
"""
import io
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "py_modules"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_backend  # noqa: E402
import test_suite  # noqa: E402

from rat import gpu, ini, launchopts, profiles, sources, steam  # noqa: E402
from rat.installer import REC_DIR  # noqa: E402
from rat.util import UserError  # noqa: E402


class TestInstallerRestore(test_backend.TestOptiScalerInstall):
    def test_game_update_of_a_file_rat_added_is_kept(self):
        self.install({"upscaler": "fsr4"})
        self.assertEqual((self.win64 / "dxgi.dll").read_bytes(), b"OPTI")
        # a game patch now ships its own dxgi.dll where RAT had added one (no original to back up)
        (self.win64 / "dxgi.dll").write_bytes(b"GAME-PATCH-DXGI")
        self.run_job(lambda j: self.m._uninstall(self.m.game("1001"), j))
        self.assertEqual((self.win64 / "dxgi.dll").read_bytes(), b"GAME-PATCH-DXGI")

    def test_stale_backup_still_restored(self):
        # an interrupted earlier run left the game's original in the backup and RAT's file in the game
        b = self.win64 / REC_DIR / "backup" / "amd_fidelityfx_upscaler_dx12.dll"
        b.parent.mkdir(parents=True)
        b.write_bytes(b"GAME-FSR")
        (self.win64 / "amd_fidelityfx_upscaler_dx12.dll").write_bytes(b"LEFTOVER")
        self.install({"upscaler": "fsr4"})
        self.run_job(lambda j: self.m._uninstall(self.m.game("1001"), j))
        self.assertEqual((self.win64 / "amd_fidelityfx_upscaler_dx12.dll").read_bytes(), b"GAME-FSR")

    def test_option_values_are_checked(self):
        self.install({"upscaler": "fsr4"})
        with self.assertRaises(UserError):
            self.m.set_option("1001", "proxy", "../../evil.dll")

    def test_d4r_off_leaves_game_owned_dlls(self):
        rec = {"kind": "d4r", "files": ["dxgi.dll", "d4r/x.dll"]}
        self.assertEqual([p.name for p in self.m._deactivate_paths(rec, self.win64)], ["dxgi.dll"])


class TestReShadeFixes(test_suite.TestReShade):
    def test_reshade_uninstall_never_overwrites_the_injector(self):
        (self.win64 / "dxgi.dll").write_bytes(b"GAME-DXGI")
        g = self.m.game("1001")
        self.run_job(lambda j: self.m.reshade.install_game(g, j))
        self.assertEqual((self.win64 / "dxgi.dll").read_bytes(), b"RS64")
        self.store("optiscaler", "v0.9.4", self.opti094(), "optiscaler")
        self.install("optiscaler")
        self.assertEqual((self.win64 / "dxgi.dll").read_bytes(), b"OPTI")
        self.m.reshade.uninstall_game("1001")
        self.assertEqual((self.win64 / "dxgi.dll").read_bytes(), b"OPTI")   # the injector keeps working
        self.run_job(lambda j: self.m._uninstall(g, j))
        self.assertEqual((self.win64 / "dxgi.dll").read_bytes(), b"GAME-DXGI")   # and the original comes back

    def test_preset_actions_never_touch_other_ini_files(self):
        g = self.m.game("1001")
        self.run_job(lambda j: self.m.reshade.install_game(g, j))
        (self.win64 / "OptiScaler.ini").write_text("[Upscalers]\nDx12Upscaler=auto\n")
        (self.win64 / "Game.ini").write_text("[Settings]\nVsync=1\n")
        for name in ("OptiScaler.ini", "Game.ini"):
            with self.assertRaises(UserError):
                self.m.reshade.preset_action("1001", "delete", name)
            self.assertTrue((self.win64 / name).exists())

    def test_bad_reshade_proxy_refused(self):
        g = self.m.game("1001")
        with self.assertRaises(UserError):
            self.run_job(lambda j: self.m.reshade.install_game(g, j, proxy_pref="../evil.dll"))


class TestHelpers(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def card(self, n, vendor, device, boot_vga):
        dev = self.root / f"sys/class/drm/card{n}/device"
        dev.mkdir(parents=True)
        (dev / "vendor").write_text(vendor + "\n")
        (dev / "device").write_text(device + "\n")
        (dev / "boot_vga").write_text("1\n" if boot_vga else "0\n")

    def test_compat_tool_default_entry(self):
        cfg = self.root / ".local/share/Steam/config"
        cfg.mkdir(parents=True)
        (self.root / ".local/share/Steam/steamapps").mkdir(parents=True)
        (cfg / "config.vdf").write_text(
            '"InstallConfigStore"{"Software"{"Valve"{"Steam"{"CompatToolMapping"{'
            '"0"{"name" "proton_experimental"} "1001"{"name" ""}}}}}}')
        self.assertEqual(steam.current_compat_tool("1001", self.root), "proton_experimental")

    def test_hybrid_amd_apu_nvidia_dgpu_picks_nvidia(self):
        self.card(0, "0x1002", "0x15bf", True)    # Radeon 780M APU, boot VGA
        self.card(1, "0x10de", "0x2684", False)   # NVIDIA discrete
        kfd = self.root / "sys/class/kfd/kfd/topology/nodes/1"
        kfd.mkdir(parents=True)
        (kfd / "properties").write_text("simd_count 24\ngfx_target_version 110003\n")
        self.assertEqual(gpu.detect(str(self.root))["vendor"], "NVIDIA")

    def test_exe_fallback_when_every_name_matches_a_helper_word(self):
        (self.root / "Observer.exe").write_bytes(b"MZ" + bytes(1000))
        self.assertEqual([x["exe"] for x in steam.exe_candidates(self.root)], ["Observer.exe"])

    def test_env_with_options_survives_clean(self):
        out, _ = launchopts.clean("gamescope -f -- env -u FOO %command%")
        self.assertIn("env -u FOO", out)

    def test_backslash_escapes_keep_tokens(self):
        self.assertEqual(launchopts.split_tokens(r'FOO=a\ b "x\"y" %command%'),
                         [r"FOO=a\ b", r'"x\"y"', "%command%"])

    def test_bsd_and_dot_slash_checksums(self):
        saved = sources._open
        h = "a" * 64
        rel = {"asset": {"name": "pkg.zip"}, "assets": [{"name": "SHA256SUMS", "url": "x"}]}
        try:
            for text in (f"SHA256 (pkg.zip) = {h}\n", f"{h}  ./pkg.zip\n"):
                sources._open = lambda url, *a, _t=text, **k: io.BytesIO(_t.encode())
                self.assertEqual(sources.expected_sha256(rel), h)
        finally:
            sources._open = saved

    def test_ini_utf16_round_trip_and_form_feed(self):
        p = self.root / "Engine.ini"
        p.write_bytes("[Core]\r\nName=Café\r\n".encode("utf-16"))
        doc = ini.read(p)
        self.assertEqual(doc.get("Core", "Name"), "Café")
        doc.set("Core", "X", "1")
        ini.write(p, doc)
        self.assertTrue(p.read_bytes().startswith(b"\xff\xfe"))
        self.assertEqual(ini.read(p).get("Core", "X"), "1")
        d = ini.IniDoc("[A]\nk=a\x0cb\n")
        self.assertEqual(d.get("A", "k"), "a\x0cb")
        self.assertEqual(d.text(), "[A]\nk=a\x0cb\n")

    def test_profile_sanitize(self):
        p = profiles._sanitize({"rat_profile": 1, "compositor": "bad", "reshade": [1], "hdr": {"settings": "x"},
                                "injector": {"options": 5}})
        self.assertIsNone(p["compositor"])
        self.assertEqual((p["reshade"], p["hdr"]["settings"], p["injector"]["options"]), ({}, {}, {}))
        ok = profiles._sanitize({"compositor": {"effect": "LUT@LUT.fx"}})
        self.assertEqual(ok["compositor"], {"effect": "LUT@LUT.fx"})

    def test_latest_falls_back_to_tag_lookup(self):
        saved = (sources.releases, sources.release_by_tag)
        try:
            sources.releases = lambda repo, force=False: []
            sources.release_by_tag = lambda repo, tag: {
                "tag": tag, "name": tag, "draft": False, "prerelease": False, "published": None, "url": "u",
                "body": "", "assets": [{"name": "Old-v0.1.zip", "url": "x", "size": 1, "digest": None}]}
            r = sources.latest("o/r", tag="v0.1")
            self.assertEqual((r["tag"], r["asset"]["name"]), ("v0.1", "Old-v0.1.zip"))
        finally:
            sources.releases, sources.release_by_tag = saved


def load_tests(loader, tests, pattern):
    """Only the tests defined here: the subclasses above would otherwise re-run every inherited base test."""
    suite = unittest.TestSuite()
    for cls in (TestInstallerRestore, TestReShadeFixes, TestHelpers):
        for name in loader.getTestCaseNames(cls):
            if name in cls.__dict__:
                suite.addTest(cls(name))
    return suite


if __name__ == "__main__":
    unittest.main()
