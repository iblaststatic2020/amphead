"""A preset is intent; applying it has to prove the knobs moved, and cost one undo.

The fake's Shoeless Drive Pedal carries the same traps the real plugins showed: "Tone" accepts a write
and ignores it, and a chord of several knobs still has to arrive as one undo point. A preset that
names a plugin this rig does not have is reported, not silently dropped, and the knobs that did land
are still named with the numbers the plugin reported.
"""

import json
import os
import tempfile
import unittest

from amphead import bridge, presets, rig
from amphead.fake_reaper import FakeReaper


class PresetCase(unittest.TestCase):
    nam = False

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.fake = FakeReaper(self.root, traps=True, nam=self.nam).start()
        self.log = os.path.join(self.root, "log.jsonl")
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(self.fake.stop)
        self.addCleanup(os.environ.pop, "HERMES_REAPER_BRIDGE", None)
        os.environ["HERMES_REAPER_BRIDGE"] = self.root

    def write_preset(self, data, name="probe"):
        path = os.path.join(self.root, f"{name}.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        return path

    def norm(self, fx, param):
        return rig.read_param(fx, rig._resolve_param(param, fx, 1)[0])["norm"]

    def param(self, name, fx=0):
        return rig.read_param(fx, rig._resolve_param(name, fx, 1)[0], track=1)

    def undo_depth(self):
        return len(self.fake.undo_stack)


class TestCatalog(PresetCase):
    def test_the_three_presets_ship(self):
        names = [row["name"] for row in presets.catalog()]
        self.assertEqual(names, ["clean", "crunch", "lead"])

    def test_a_preset_says_what_it_touches(self):
        rows = {row["name"]: row for row in presets.catalog()}
        self.assertIn("NAM Universal", rows["crunch"]["targets"])
        self.assertTrue(rows["crunch"]["about"])

    def test_a_name_with_no_file_is_refused_by_name(self):
        with self.assertRaises(LookupError) as caught:
            presets.load("thrash")
        self.assertIn("no preset called 'thrash'", str(caught.exception))


class TestTidy(PresetCase):
    def test_float_noise_is_trimmed_for_display(self):
        self.assertEqual(presets.tidy("-4.7999992"), "-4.8")
        self.assertEqual(presets.tidy("0.0000229"), "0")

    def test_a_real_number_is_left_alone(self):
        self.assertEqual(presets.tidy("-3.0 dB"), "-3.0 dB")
        self.assertEqual(presets.tidy("7.2"), "7.2")

    def test_garbage_stays_garbage(self):
        self.assertEqual(presets.tidy("-inf"), "-inf")
        self.assertEqual(presets.tidy(""), "")


class TestApply(PresetCase):
    nam = True

    def test_preset_moves_the_named_knobs(self):
        out = presets.apply("crunch", log_path=self.log)
        self.assertTrue(out.landed)
        gain = [c for c in out.changes if c.param == "Pedal Input Gain"][0]
        self.assertIn("NAM Universal", gain.fx)
        self.assertAlmostEqual(self.param("pedal input gain")["norm"], 0.62, places=3)

    def test_whole_preset_is_one_undo_point(self):
        path = self.write_preset({
            "name": "no-traps",
            "targets": {"NAM Universal": {"amplifier bass": 0.44, "amplifier mid": 0.3}},
        })
        before = self.undo_depth()
        presets.apply(path)
        self.assertEqual(self.undo_depth() - before, 1)

    def test_a_stubborn_knob_costs_one_corrective_batch_not_one_per_knob(self):
        path = self.write_preset({
            "name": "two-stubborn",
            "targets": {"NAM Universal": {"oomph": 0.9, "amplifier bass": 0.3}},
        })
        before = self.undo_depth()
        out = presets.apply(path)
        self.assertEqual(self.undo_depth() - before, 2)
        self.assertEqual([c.param for c in out.refused], ["Oomph"])

    def test_preset_that_leaves_a_knob_behind_names_it(self):
        path = self.write_preset({
            "name": "oomph-test",
            "targets": {"NAM Universal": {"amplifier bass": 0.5, "oomph": 0.9}},
        })
        out = presets.apply(path)
        self.assertEqual([c.param for c in out.landed], ["Amplifier Bass"])
        self.assertEqual([c.param for c in out.refused], ["Oomph"])
        self.assertIn("did not move", out.refused[0].note)

    def test_plugin_that_is_not_on_the_rig_is_named_not_dropped(self):
        path = self.write_preset({
            "name": "elsewhere",
            "targets": {"Shoeless Drive": {"drive": 0.5},
                        "NAM Universal": {"amplifier bass": 0.3}},
        })
        out = presets.apply(path)
        missing = " ".join(out.missing)
        self.assertIn("Shoeless Drive", missing)
        self.assertIn("no plugin matches", missing)
        self.assertTrue(out.landed, "the plugin that is present should still have been applied")

    def test_diff_reads_in_the_plugins_own_units(self):
        path = self.write_preset({
            "name": "one-knob",
            "targets": {"NAM Universal": {"amplifier bass": 0.33}},
        })
        out = presets.apply(path)
        line = presets.diff_lines(out)[0]
        self.assertIn("Amplifier Bass:", line)
        self.assertIn("->", line)

    def test_track_level_rides_in_the_same_group(self):
        path = self.write_preset({
            "name": "quiet",
            "track_db": -12.0,
            "targets": {"NAM Universal": {"amplifier bass": 0.4}},
        })
        before = self.undo_depth()
        presets.apply(path)
        self.assertEqual(self.undo_depth() - before, 1)
        self.assertEqual(rig.bridge.call("TRACK_INFO", track=1)["db"], -12.0)

    def test_a_preset_with_nothing_to_do_writes_nothing(self):
        path = self.write_preset({"name": "empty", "targets": {}})
        with self.assertRaises(ValueError):
            presets.apply(path)

    def test_summary_counts_what_landed(self):
        out = presets.apply("clean")
        sentence = out.sentence()
        self.assertIn("preset=clean", sentence)
        self.assertIn(f"changed={len(out.moved)}", sentence)
        self.assertIn(f"already there={len(out.already)}", sentence)
        self.assertIn("one undo point", sentence)

    def test_every_knob_is_logged_under_the_preset_name(self):
        presets.apply("clean", log_path=self.log)
        with open(self.log, encoding="utf-8") as handle:
            rows = [json.loads(line) for line in handle if line.strip()]
        applied_rows = [r for r in rows if r.get("op") == "preset"]
        self.assertTrue(applied_rows)
        self.assertTrue(all(r["preset"] == "clean" for r in applied_rows))
        self.assertTrue(any(r.get("op") == "preset_done" for r in rows))


class TestShippedPresets(PresetCase):
    """The three presets that ship, against an amp shaped like the real one."""

    nam = True

    def test_every_shipped_preset_resolves_every_knob(self):
        for name in ("clean", "crunch", "lead"):
            with self.subTest(preset=name):
                out = presets.apply(name)
                self.assertTrue(out.changes, f"{name} touched no knob")
                self.assertEqual(out.missing, [], f"{name} could not resolve: {out.missing}")
                self.assertEqual(out.refused, [], f"{name} left knobs behind")

    def test_lead_switches_both_drive_stages_in(self):
        presets.apply("lead")
        self.assertEqual(rig.read_param(0, rig._resolve_param("pedal active", 0, 1)[0])["fmt"], "On")
        self.assertEqual(
            rig.read_param(0, rig._resolve_param("instigator active", 0, 1)[0])["fmt"], "On")

    def test_clean_switches_the_pedal_out(self):
        presets.apply("crunch")
        presets.apply("clean")
        self.assertEqual(rig.read_param(0, rig._resolve_param("pedal active", 0, 1)[0])["fmt"], "Off")

    def test_presets_are_ordered_by_gain(self):
        def amp_gain(name):
            presets.apply(name)
            return rig.read_param(0, rig._resolve_param("amplifier input gain", 0, 1)[0])["norm"]

        self.assertLess(amp_gain("clean"), amp_gain("crunch"))
        self.assertLess(amp_gain("crunch"), amp_gain("lead"))

    def test_a_shipped_preset_costs_one_undo_point(self):
        before = self.undo_depth()
        presets.apply("crunch")
        self.assertEqual(self.undo_depth() - before, 1)


class TestPresetTool(PresetCase):
    nam = True

    def setUp(self):
        super().setUp()
        from amphead import tools
        self.tools = tools
        self.handler = dict((name, fn) for name, _s, fn, _e in tools.TOOLS)["amphead_preset"]

    def test_tool_reports_the_diff(self):
        text = self.handler({"name": "crunch"})
        self.assertIn("preset=crunch", text)
        self.assertIn("| Amplifier Input Gain:", text)
        self.assertIn("one undo point", text)

    def test_tool_lists_presets_when_asked_for_nothing(self):
        text = self.handler({})
        for name in ("clean", "crunch", "lead"):
            self.assertIn(name, text)

    def test_tool_refuses_an_unknown_preset(self):
        text = self.handler({"name": "thrash"})
        self.assertTrue(text.startswith("NOT APPLIED:"))
        self.assertIn("thrash", text)

    def test_tool_when_reaper_is_down(self):
        self.fake.stop()
        text = self.handler({"name": "crunch"})
        self.assertTrue(text.startswith("NOT APPLIED:"))
