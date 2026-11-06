"""Building the rig is a checklist, not a hope.

The rig tab exists only once it has a path on disk, because that path is what every later write is
guarded against. Then one BATCH puts the track, the amp, the input and the level in place, and the
read-backs have to agree. The two hazards covered here are the ones this machine actually produced:
a project template that already carries FX, and an amp name that does not resolve.
"""

import json
import os
import tempfile
import unittest

from amphead import bridge, rig
from amphead.fake_reaper import FakeReaper


class BuildCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.fake = FakeReaper(self.root, traps=True).start()
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(self.fake.stop)
        for name in ("HERMES_REAPER_BRIDGE", "AMPHEAD_HOME", "AMPHEAD_LOG"):
            self.addCleanup(os.environ.pop, name, None)
        os.environ["HERMES_REAPER_BRIDGE"] = self.root
        os.environ["AMPHEAD_HOME"] = os.path.join(self.root, "home")
        os.environ["AMPHEAD_LOG"] = os.path.join(self.root, "log.jsonl")

    def build(self, **kwargs):
        target = os.path.join(self.root, "rig", "rig.rpp")
        kwargs.setdefault("path", target)
        return rig.build_rig("RigSculpt", **kwargs)


class TestBuild(BuildCase):
    def test_build_reports_what_landed(self):
        out = self.build()
        self.assertTrue(os.path.exists(out.path))
        self.assertEqual(out.amp_index, len(out.chain) - 1)
        self.assertIn(out.amp, " ".join(out.chain))
        self.assertFalse(out.armed)
        self.assertFalse(out.monitor)
        self.assertGreaterEqual(out.track, 1)
        self.assertAlmostEqual(out.db, rig.START_DB, places=1)
        self.assertLess(out.seconds, 5.0)

    def test_fx_that_came_with_the_tab_are_reported_not_deleted(self):
        out = self.build()
        self.assertTrue(out.pre_fx, "the fixture tab already carries FX")
        after = [f.get("name", "") for f in bridge.fx_list(track=1)]
        self.assertEqual(after, out.pre_fx)

    def test_arm_is_opt_in_and_proven(self):
        quiet = self.build()
        self.assertFalse(quiet.armed)
        loud = self.build(path=os.path.join(self.root, "rig2", "rig.rpp"), arm=True)
        self.assertTrue(loud.armed)
        self.assertTrue(loud.monitor)

    def test_input_is_stored_and_read_back(self):
        out = self.build(input="1")
        self.assertEqual(out.input, 1)

    def test_input_none_leaves_the_input_alone(self):
        out = self.build()
        self.assertIsNone(out.input)

    def test_snapshot_of_the_tab_before_building(self):
        out = self.build()
        self.assertTrue(out.snapshot)
        with open(out.snapshot, encoding="utf-8") as handle:
            snap = json.load(handle)
        self.assertEqual(snap["tag"], "pre-build")
        self.assertIn("tabs", snap)

    def test_existing_rig_is_not_overwritten_by_accident(self):
        self.build()
        with self.assertRaises(rig.RigRefused) as caught:
            self.build()
        self.assertIn("already exists", str(caught.exception))

    def test_unknown_amp_fails_the_whole_build(self):
        with self.assertRaises(rig.RigRefused) as caught:
            rig.build_rig("Shoeless", path=os.path.join(self.root, "rig3", "rig.rpp"))
        message = str(caught.exception)
        self.assertIn("FX_ADD", message)
        self.assertIn("Shoeless", message)

    def test_build_is_logged(self):
        out = self.build()
        with open(os.environ["AMPHEAD_LOG"], encoding="utf-8") as handle:
            rows = [json.loads(line) for line in handle if line.strip()]
        builds = [r for r in rows if r.get("op") == "build_rig"]
        self.assertEqual(len(builds), 1)
        self.assertEqual(builds[0]["path"], out.path)


class TestTeardown(BuildCase):
    def test_teardown_closes_the_rig_and_removes_its_file(self):
        out = self.build()
        result = rig.teardown(out.path)
        self.assertTrue(result["closed"])
        self.assertTrue(result["file_removed"])
        self.assertFalse(os.path.exists(out.path))

    def test_teardown_will_not_close_a_tab_that_is_not_the_rig(self):
        out = self.build()
        screen = os.path.join(self.root, "song.rpp")
        open(screen, "w", encoding="utf-8").write("x")
        self.fake.project_path = screen
        with self.assertRaises(rig.RigRefused) as caught:
            rig.teardown(out.path)
        self.assertIn("refusing to close", str(caught.exception))
        self.assertTrue(os.path.exists(out.path))


class TestBuildTool(BuildCase):
    def test_tool_reports_the_facts(self):
        from amphead import tools

        out = tools.handle_build({"amp": "RigSculpt", "input": "1", "start_db": -10})
        self.assertTrue(out.startswith("built=True"), out)
        self.assertIn("RigSculpt", out)
        self.assertIn("input=1", out)
        self.assertIn("-10", out)

    def test_tool_says_the_player_arms(self):
        from amphead import tools

        out = tools.handle_build({"amp": "RigSculpt"})
        self.assertIn("armed=False", out)
        self.assertIn("the player flips Live", out)
        self.assertIn("untouched (default 0)", out)

    def test_tool_refuses_a_bad_amp_without_pretending(self):
        from amphead import tools

        out = tools.handle_build({"amp": "Shoeless"})
        self.assertTrue(out.startswith("NOT BUILT:"), out)
        self.assertIn("Shoeless", out)


if __name__ == "__main__":
    unittest.main()
