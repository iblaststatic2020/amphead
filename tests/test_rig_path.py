"""One rig file, one place that names it.

Setup saved `amphead.rpp` and `rig.snapshot`/restore fell back to `rig.rpp`, so a restore with no
path reopened a leftover stub. Two modules each answering "where is the rig" is the bug; this test
is the reason they cannot drift apart again.
"""

import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from amphead import rig, tools


def host_seam(target: Path):
    """A stand-in for the host's ``plugins.plugin_storage`` so the seam can be tested without it.

    The plugin ships without a YAML or host dependency on purpose, so the test brings its own: one
    module named the way the real one is named, answering the way the real one answers.
    """
    module = types.ModuleType("plugins.plugin_storage")

    def plugin_data_dir(name: str) -> Path:
        assert name == "amphead", f"asked for the wrong plugin's data: {name!r}"
        target.mkdir(parents=True, exist_ok=True)
        return target

    module.plugin_data_dir = plugin_data_dir
    return mock.patch.dict(sys.modules, {"plugins": types.ModuleType("plugins"),
                                         "plugins.plugin_storage": module})


class OneRigPath(unittest.TestCase):
    def test_the_two_modules_agree(self):
        self.assertEqual(tools.rig_path(), rig.rig_path())

    def test_the_file_is_named_amphead_rpp(self):
        self.assertEqual(rig.rig_path().name, rig.RIG_FILE)
        self.assertEqual(rig.rig_path().name, "amphead.rpp")

    def test_the_data_dir_decides_the_parent(self):
        with mock.patch.dict(os.environ, {"AMPHEAD_HOME": r"C:\tmp\amphead-home"}, clear=False):
            os.environ.pop("AMPHEAD_RIG", None)
            self.assertEqual(rig.rig_path(), rig.data_dir() / "rig" / "amphead.rpp")
            self.assertEqual(tools.rig_path(), rig.rig_path())

    def test_an_explicit_rig_file_wins_over_the_data_dir(self):
        with mock.patch.dict(os.environ, {"AMPHEAD_HOME": r"C:\tmp\amphead-home",
                                          "AMPHEAD_RIG": r"C:\tmp\elsewhere\mine.rpp"}, clear=False):
            self.assertEqual(rig.rig_path(), Path(r"C:\tmp\elsewhere\mine.rpp"))
            self.assertEqual(tools.rig_path(), Path(r"C:\tmp\elsewhere\mine.rpp"))

    def test_the_default_lands_under_the_hermes_data_dir(self):
        """With no host library to ask, the appdata path is the answer, and nothing raises."""
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": r"C:\tmp\lad"}, clear=False):
            for key in ("AMPHEAD_HOME", "AMPHEAD_RIG"):
                os.environ.pop(key, None)
            with mock.patch.object(rig, "_host_data_dir", return_value=None):
                self.assertEqual(rig.data_dir(), Path(r"C:\tmp\lad") / "hermes" / "amphead")
                self.assertEqual(rig.rig_path(), Path(r"C:\tmp\lad") / "hermes" / "amphead"
                                 / "rig" / "amphead.rpp")


class TheHostOwnsTheFolder(unittest.TestCase):
    """G5: state lives where the host says, not where this module used to guess.

    The hand-built path put every profile on a machine into one rig, one log and one set of
    snapshots. The seam is `plugin_data_dir()`, which resolves the active profile on each call.
    """

    def test_the_host_seam_decides_the_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "plugin-data" / "amphead"
            with mock.patch.dict(os.environ, {"LOCALAPPDATA": tmp}, clear=False):
                os.environ.pop("AMPHEAD_HOME", None)
                with host_seam(home):
                    self.assertEqual(rig.data_dir(), home)
                    self.assertEqual(rig.rig_path(), home / "rig" / "amphead.rpp")

    def test_an_old_home_is_carried_over_and_left_in_place(self):
        """A player who has been using this has a rig and a log in the old folder. Keep both."""
        with tempfile.TemporaryDirectory() as tmp:
            legacy = Path(tmp) / "hermes" / "amphead"
            (legacy / "rig").mkdir(parents=True)
            (legacy / "rig" / "amphead.rpp").write_text("<REAPER_PROJECT>", encoding="utf-8")
            (legacy / "log.jsonl").write_text('{"at":"now"}\n', encoding="utf-8")
            home = Path(tmp) / "plugin-data" / "amphead"
            with mock.patch.dict(os.environ, {"LOCALAPPDATA": tmp}, clear=False):
                os.environ.pop("AMPHEAD_HOME", None)
                with host_seam(home):
                    self.assertEqual(rig.data_dir(), home)
                    self.assertTrue((home / "rig" / "amphead.rpp").is_file(), "the rig was left behind")
                    self.assertTrue((home / "log.jsonl").is_file(), "the log was left behind")
            self.assertTrue((legacy / "log.jsonl").is_file(), "the old copy must not be deleted")

    def test_a_home_that_already_has_state_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            legacy = Path(tmp) / "hermes" / "amphead"
            legacy.mkdir(parents=True)
            (legacy / "log.jsonl").write_text("old\n", encoding="utf-8")
            home = Path(tmp) / "plugin-data" / "amphead"
            home.mkdir(parents=True)
            (home / "log.jsonl").write_text("live\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {"LOCALAPPDATA": tmp}, clear=False):
                os.environ.pop("AMPHEAD_HOME", None)
                with host_seam(home):
                    rig.data_dir()
            self.assertEqual((home / "log.jsonl").read_text(encoding="utf-8"), "live\n")

    def test_the_env_override_still_wins_outright(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "plugin-data" / "amphead"
            with mock.patch.dict(os.environ, {"AMPHEAD_HOME": str(Path(tmp) / "scratch"),
                                             "LOCALAPPDATA": tmp}, clear=False):
                with host_seam(home):
                    self.assertEqual(rig.data_dir(), Path(tmp) / "scratch")


class OneHomeForState(unittest.TestCase):
    """The demo switch keeps its flag in the same place as everything else amphead owns."""

    def test_the_flag_lands_beside_the_rig(self):
        from amphead import demo

        with mock.patch.dict(os.environ, {"AMPHEAD_HOME": r"C:\tmp\amphead-home"}, clear=False):
            demo.set_enabled(True)
            self.assertEqual(demo._path(), rig.data_dir() / "demo.json")
            self.assertTrue((rig.data_dir() / "demo.json").is_file())
            os.environ.pop("AMPHEAD_DEMO", None)
            self.assertTrue(demo.enabled())
            demo.set_enabled(False)
            self.assertFalse(demo.enabled())


if __name__ == "__main__":
    unittest.main()
