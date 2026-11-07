"""One rig file, one place that names it.

Setup saved `amphead.rpp` and `rig.snapshot`/restore fell back to `rig.rpp`, so a restore with no
path reopened a leftover stub. Two modules each answering "where is the rig" is the bug; this test
is the reason they cannot drift apart again.
"""

import os
import unittest
from pathlib import Path
from unittest import mock

from amphead import rig, tools


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
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": r"C:\tmp\lad"}, clear=False):
            for key in ("AMPHEAD_HOME", "AMPHEAD_RIG"):
                os.environ.pop(key, None)
            self.assertEqual(rig.data_dir(), Path(r"C:\tmp\lad") / "hermes" / "amphead")
            self.assertEqual(rig.rig_path(), Path(r"C:\tmp\lad") / "hermes" / "amphead"
                             / "rig" / "amphead.rpp")


if __name__ == "__main__":
    unittest.main()
