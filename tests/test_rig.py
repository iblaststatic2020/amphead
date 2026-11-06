"""A write is only done when the read-back proves it.

The two failures replayed here are real, both seen on this machine: ReaEQ gain at 0.35 normalized
and ReaFir output gain at 1.44 raw each accept the write and then format the value as ``-inf``. A
plugin that ignores the write entirely ("Tone" in the fake) is the third case, and it must end in a
refusal with both attempts shown rather than a cheerful report.
"""

import json
import os
import tempfile
import unittest

from amphead import bridge, rig
from amphead.fake_reaper import FakeReaper


class RigCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.fake = FakeReaper(self.root, traps=True).start()
        self.log = os.path.join(self.root, "log.jsonl")
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(self.fake.stop)
        self.addCleanup(os.environ.pop, "HERMES_REAPER_BRIDGE", None)
        os.environ["HERMES_REAPER_BRIDGE"] = self.root

    def entries(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def param(self, fx, name, **kwargs):
        return rig.read_param(fx, rig._resolve_param(name, fx, 1)[0], **kwargs)


class TestHappyPath(RigCase):
    def test_write_lands_and_is_logged(self):
        out = rig.set_param("shoeless", "drive", 0.6, log_path=self.log)
        self.assertEqual(out.verdict, "ok")
        self.assertEqual(out.param_name, "Drive")
        self.assertAlmostEqual(out.norm, 0.6, places=3)
        self.assertFalse(out.retried)
        self.assertEqual(len(self.entries()), 1)
        self.assertEqual(self.entries()[0]["verdict"], "ok")

    def test_one_batch_is_one_undo_point(self):
        rig.set_param("shoeless", "drive", 0.4, log_path=self.log)
        self.assertEqual(len(self.fake.undo_stack), 1)
        bridge.undo()
        self.assertEqual(len(self.fake.undo_stack), 0)

    def test_the_plugin_that_moved_is_the_one_named(self):
        out = rig.set_param("rigsculpt", "presence", 0.4, log_path=self.log)
        self.assertEqual((out.fx, out.param), (1, 0))


class TestRealFailures(RigCase):
    def test_reafir_raw_write_reading_inf_retries_normalized(self):
        # 1.44 raw is 0.72 normalized. The raw write reports -inf; the normalized retry lands.
        out = rig.set_param("reafir", "output gain", 1.44, mode="raw", unit="dB",
                            track=2, log_path=self.log)
        self.assertTrue(out.retried)
        self.assertEqual(out.mode, "norm")
        self.assertAlmostEqual(out.norm, 0.72, places=2)
        first, second = self.entries()
        self.assertEqual(first["verdict"], "rejected")
        self.assertEqual(first["fmt"], "-inf")
        self.assertEqual(second["verdict"], "ok")

    def test_gain_that_reads_inf_in_both_modes_is_refused(self):
        with self.assertRaises(rig.WriteRefused) as ctx:
            rig.set_param("shoeless", "gain", 0.35, log_path=self.log)
        message = str(ctx.exception)
        self.assertIn("refused the write", message)
        self.assertEqual(message.count("-inf"), 2)
        self.assertEqual([e["verdict"] for e in self.entries()], ["rejected", "rejected"])

    def test_plugin_that_ignores_the_write_is_refused(self):
        with self.assertRaises(rig.WriteRefused) as ctx:
            rig.set_param("shoeless", "tone", 0.9, log_path=self.log)
        self.assertIn("did not move", str(ctx.exception))
        self.assertEqual(len(self.entries()), 2)

    def test_refusal_is_not_reported_as_a_write(self):
        self.assertEqual(len(self.fake.undo_stack), 0)
        with self.assertRaises(rig.WriteRefused):
            rig.set_param("shoeless", "tone", 0.9, log_path=self.log)


class TestClampAndModes(RigCase):
    def test_clamp_is_reported_not_hidden(self):
        out = rig.set_param("shoeless", "drive", 1.5, log_path=self.log)
        self.assertTrue(out.clamped)
        self.assertEqual(out.requested, 1.5)
        self.assertAlmostEqual(out.norm, 1.0, places=3)
        self.assertIn("clamped from 1.5", out.sentence())

    def test_percent_unit_is_scaled(self):
        out = rig.set_param("shoeless", "drive", 60, unit="%", log_path=self.log)
        self.assertAlmostEqual(out.norm, 0.6, places=3)

    def test_named_unit_means_raw(self):
        out = rig.set_param("reafir", "output gain", 1.2, unit="dB", track=2, log_path=self.log)
        self.assertEqual(out.mode, "raw")
        self.assertAlmostEqual(out.written, 1.2, places=4)

    def test_unknown_mode_is_refused_before_any_write(self):
        with self.assertRaises(ValueError):
            rig.set_param("shoeless", "drive", 0.5, mode="percent", log_path=self.log)
        self.assertEqual(self.entries(), [])


class TestTool(RigCase):
    def setUp(self):
        super().setUp()
        os.environ["AMPHEAD_LOG"] = self.log
        self.addCleanup(os.environ.pop, "AMPHEAD_LOG", None)

    def test_tool_reports_the_value_the_plugin_returned(self):
        from amphead import tools
        out = tools.handle_set({"fx": "shoeless", "param": "drive", "value": 0.6})
        self.assertIn("set=True", out)
        self.assertIn("param=Drive", out)
        self.assertIn("reads_back=6.0", out)

    def test_tool_refuses_instead_of_claiming_success(self):
        from amphead import tools
        out = tools.handle_set({"fx": "shoeless", "param": "tone", "value": 0.9})
        self.assertTrue(out.startswith("REFUSED:"), out)
        self.assertIn("read back 5.0", out)
        self.assertIn("value did not move", out)

    def test_tool_names_the_candidates_when_the_name_is_ambiguous(self):
        from amphead import tools
        out = tools.handle_set({"fx": "shoeless", "param": "driv", "value": 0.9})
        self.assertTrue(out.startswith("NOT SET:"), out)
        self.assertIn("matches 2 parameters", out)

    def test_tool_says_so_when_reaper_is_not_running(self):
        from amphead import tools
        self.fake.stop()
        os.environ.pop("HERMES_REAPER_BRIDGE", None)
        os.environ["HERMES_REAPER_BRIDGE"] = os.path.join(self.root, "nothing-here")
        out = tools.handle_set({"fx": "shoeless", "param": "drive", "value": 0.6})
        self.assertTrue(out.startswith("NOT SET:"), out)


class TestResolution(RigCase):
    def test_unknown_parameter_lists_candidates(self):
        with self.assertRaises(LookupError) as ctx:
            rig.set_param("shoeless", "driv", 0.5, log_path=self.log)
        self.assertIn("matches 2 parameters", str(ctx.exception))

    def test_unknown_plugin_lists_what_exists(self):
        with self.assertRaises(LookupError) as ctx:
            rig.set_param("plexi", "drive", 0.5, log_path=self.log)
        self.assertIn("no plugin matches", str(ctx.exception))

    def test_index_that_does_not_exist_is_refused(self):
        with self.assertRaises(LookupError) as ctx:
            rig.set_param(9, "drive", 0.5, log_path=self.log)
        self.assertIn("no plugin at index 9", str(ctx.exception))

    def test_wrong_project_guard_stops_the_write(self):
        with self.assertRaises(bridge.CommandFailed):
            rig.set_param("shoeless", "drive", 0.5, expect="C:/elsewhere/song.rpp",
                          log_path=self.log)
        self.assertEqual(self.entries(), [])


if __name__ == "__main__":
    unittest.main()
