"""The platform surface: three resource paths, one process layer, one declaration.

REAPER is one application on Windows, macOS and Linux. Everything AmpHead does with it is
platform neutral except where its files live and how a process is started and stopped, so
these tests pin exactly those two things plus the declaration that names all three.

They run on any host: ``sys.platform`` is patched, never the real one.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

from amphead import appdecl, bridge, launch, paths


class ResourcePathTests(unittest.TestCase):
    def setUp(self):
        self.home = Path.home()

    def test_windows_uses_appdata(self):
        with mock.patch.object(sys, "platform", "win32"), mock.patch.dict(
            "os.environ", {"APPDATA": r"C:\Users\someone\AppData\Roaming"}, clear=False
        ):
            self.assertEqual(paths.resource_path(), Path(r"C:\Users\someone\AppData\Roaming") / "REAPER")

    def test_windows_without_appdata_falls_back_to_the_home_folder(self):
        with mock.patch.object(sys, "platform", "win32"), mock.patch.dict("os.environ", {"APPDATA": ""}, clear=False):
            self.assertEqual(paths.resource_path(), self.home / "AppData" / "Roaming" / "REAPER")

    def test_macos_uses_application_support(self):
        with mock.patch.object(sys, "platform", "darwin"), mock.patch.dict(
            "os.environ", {"APPDATA": "", "XDG_CONFIG_HOME": ""}, clear=False
        ):
            self.assertEqual(
                paths.resource_path(), self.home / "Library" / "Application Support" / "REAPER"
            )

    def test_linux_uses_xdg_config_home(self):
        with mock.patch.object(sys, "platform", "linux"), mock.patch.dict(
            "os.environ", {"XDG_CONFIG_HOME": "/home/someone/.xdg"}, clear=False
        ):
            self.assertEqual(paths.resource_path(), Path("/home/someone/.xdg") / "REAPER")

    def test_linux_without_xdg_uses_dot_config(self):
        with mock.patch.object(sys, "platform", "linux"), mock.patch.dict(
            "os.environ", {"XDG_CONFIG_HOME": ""}, clear=False
        ):
            self.assertEqual(paths.resource_path(), self.home / ".config" / "REAPER")

    def test_portable_install_wins_over_every_platform_default(self):
        with mock.patch.object(sys, "platform", "darwin"), mock.patch.dict(
            "os.environ", {"REAPER_RESOURCE_PATH": "/tmp/portable-reaper"}, clear=False
        ):
            self.assertEqual(paths.resource_path(), Path("/tmp/portable-reaper"))
        self.assertEqual(paths.resource_path("/explicit"), Path("/explicit"))

    def test_bridge_dir_hangs_off_the_resource_path(self):
        with mock.patch.object(sys, "platform", "linux"), mock.patch.dict(
            "os.environ", {"XDG_CONFIG_HOME": "/home/someone/.config"}, clear=False
        ):
            self.assertEqual(
                bridge.bridge_root(), Path("/home/someone/.config/REAPER/Scripts/hermes_bridge")
            )


class ProcessLayerTests(unittest.TestCase):
    def test_start_on_linux_spawns_the_binary_and_returns_its_pid(self):
        proc = mock.Mock(pid=4321)
        with mock.patch.object(sys, "platform", "linux"), mock.patch.object(
            launch, "find_reaper", return_value=Path("/opt/REAPER/reaper")
        ), mock.patch.object(launch.subprocess, "Popen", return_value=proc) as popen:
            self.assertEqual(launch.start(), 4321)
        self.assertEqual(popen.call_args.args[0], [str(Path("/opt/REAPER/reaper"))])
        self.assertTrue(popen.call_args.kwargs["start_new_session"])

    def test_start_on_macos_opens_the_bundle(self):
        done = mock.Mock(returncode=0, stderr="")
        with mock.patch.object(sys, "platform", "darwin"), mock.patch.object(
            launch, "find_reaper", return_value=Path("/Applications/REAPER.app")
        ), mock.patch.object(launch.subprocess, "run", return_value=done) as run, mock.patch.object(
            launch, "_posix_pids", return_value=[99]
        ):
            self.assertEqual(launch.start(), 99)
        self.assertEqual(
            run.call_args.args[0], ["open", "-na", str(Path("/Applications/REAPER.app"))]
        )

    def test_minimizing_is_a_no_op_off_windows(self):
        with mock.patch.object(sys, "platform", "darwin"):
            self.assertFalse(launch.minimize_window())
            self.assertFalse(launch.ui_visible())

    def test_stop_off_windows_signals_the_process_instead_of_refusing(self):
        killed = []
        with mock.patch.object(sys, "platform", "linux"), mock.patch.object(
            launch, "_posix_pids", return_value=[77]
        ), mock.patch.object(launch, "_running", return_value=False), mock.patch.object(
            launch.os, "kill", side_effect=lambda pid, sig: killed.append(pid)
        ):
            self.assertTrue(launch.stop(wait=0.01))
        self.assertEqual(killed, [77])

    def test_pgrep_exit_one_means_no_process_not_a_failure(self):
        done = mock.Mock(returncode=1, stdout="")
        with mock.patch.object(launch.subprocess, "run", return_value=done):
            self.assertEqual(launch._posix_pids(), [])


class DeclarationTests(unittest.TestCase):
    def test_the_declaration_names_all_three_platforms(self):
        try:
            from hermes_platform import declaration as vocab
        except ImportError:  # a checkout without Hermes: the plugin still has to import
            self.skipTest("hermes_platform is not installed")
        decl = vocab.parse_declaration(
            appdecl.NAME, raw_app=appdecl.RAW_APP, raw_requires=appdecl.RAW_REQUIRES, where="test"
        )
        self.assertEqual(sorted(decl.app.per_os), ["darwin", "linux", "win32"])
        self.assertEqual(decl.app.per_os["darwin"].version_kind, "plist")
        self.assertEqual(decl.app.per_os["linux"].version_kind, "none")
        self.assertEqual(decl.app.per_os["win32"].version_kind, "pe_resource")

    def test_unsupported_os_names_the_three_platforms(self):
        line = appdecl.sentence({"state": "unsupported_os", "version": None, "path": None})
        self.assertIn("Windows, macOS and Linux", line)
        self.assertIn(sys.platform, line)


if __name__ == "__main__":
    unittest.main()
