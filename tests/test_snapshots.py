"""A snapshot is a fact about the rig; a comparison is the argument that follows from it.

The tests that matter here are the honest ones: taking a snapshot must not add an undo point,
a comparison must name the knob in the plugin's own units, and applying a preset then undoing it
must compare back to identical, or the undo story is a story rather than a proof.
"""

import os
import tempfile
import unittest

from amphead import bridge, presets, snapshots
from amphead.fake_reaper import FakeReaper


class SnapshotCase(unittest.TestCase):
    nam = True

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.addCleanup(self._tmp.cleanup)
        self.fake = FakeReaper(self.root, traps=True, nam=True).start()
        self.addCleanup(self.fake.stop)
        for key in ("HERMES_REAPER_BRIDGE", "AMPHEAD_HOME"):
            self.addCleanup(os.environ.pop, key, None)
        os.environ["HERMES_REAPER_BRIDGE"] = self.root
        os.environ["AMPHEAD_HOME"] = os.path.join(self.root, "home")

    def undo_depth(self):
        return len(self.fake.undo_stack)


class TestCapture(SnapshotCase):
    def test_every_plugin_and_knob_is_written_down(self):
        snap = snapshots.capture("first")
        self.assertEqual(len(snap.plugins), 1)
        plugin = snap.plugins[0]
        self.assertIn("NAM Universal", plugin.name)
        params = bridge.fx_params(fx=0, keep=snapshots.KEEP_ALL)
        self.assertEqual(len(plugin.knobs), len(params))
        self.assertTrue(all(k.fmt is not None for k in plugin.knobs))

    def test_a_knob_carries_raw_norm_and_the_plugins_words(self):
        snap = snapshots.capture("first")
        blend = snap.plugins[0].knob("IR Blend")
        self.assertIsNotNone(blend)
        self.assertAlmostEqual(blend.norm, 0.5, places=3)
        self.assertTrue(blend.fmt)

    def test_taking_a_snapshot_costs_no_undo_point(self):
        before = self.undo_depth()
        snapshots.capture("free")
        self.assertEqual(self.undo_depth(), before, "a read must not write")

    def test_the_track_level_rides_along(self):
        snap = snapshots.capture("level")
        self.assertEqual(snap.db, bridge.track_info()["db"])

    def test_it_lands_on_disk_under_its_label(self):
        snap = snapshots.capture("keeper")
        self.assertTrue(os.path.isfile(snap.path))
        again = snapshots.load("keeper")
        self.assertEqual(again.label, "keeper")
        self.assertEqual(len(again.plugins), len(snap.plugins))

    def test_catalog_lists_snapshots_newest_last_and_skips_the_pointers(self):
        snapshots.capture("one")
        snapshots.capture("two")
        labels = [row["label"] for row in snapshots.catalog()]
        self.assertEqual(labels, ["one", "two"])

    def test_two_snapshots_of_one_label_keep_the_latest(self):
        first = snapshots.capture("same")
        presets.apply("crunch")
        second = snapshots.capture("same")
        self.assertNotEqual(first.path, second.path)
        loaded = snapshots.load("same")
        self.assertEqual(loaded.at, second.at)
        self.assertNotEqual(loaded.plugins[0].knob("Pedal Active").fmt, "Off")


class TestFind(SnapshotCase):
    def test_a_label_that_does_not_exist_is_refused_by_name(self):
        with self.assertRaises(LookupError) as caught:
            snapshots.load("nope")
        self.assertIn("no snapshot called 'nope'", str(caught.exception))

    def test_an_explicit_path_wins(self):
        snap = snapshots.capture("pathful")
        self.assertEqual(snapshots.find(snap.path).name, os.path.basename(snap.path))


class TestCompare(SnapshotCase):
    def test_a_snapshot_against_itself_is_identical_and_says_so(self):
        snapshots.capture("a")
        text = snapshots.summary("a", "a")
        self.assertIn("differences=0", text)
        self.assertIn("identical: nothing moved", text)

    def test_a_preset_shows_up_as_named_knob_moves(self):
        snapshots.capture("before")
        presets.apply("crunch")
        snapshots.capture("after")
        lines = snapshots.compare("before", "after")
        text = "\n".join(lines)
        self.assertIn("Pedal Active: Off -> On", text)
        self.assertIn("Amplifier Treble:", text)
        self.assertIn("-> 5.2", text)
        self.assertTrue(all(line.count("|") >= 1 for line in lines))

    def test_undo_puts_every_knob_back_and_the_comparison_proves_it(self):
        snapshots.capture("before")
        presets.apply("lead")
        bridge.undo()
        snapshots.capture("after-undo")
        self.assertEqual(snapshots.compare("before", "after-undo"), [])

    def test_a_level_move_is_named_in_db(self):
        snapshots.capture("loud")
        bridge.batch([("TRACK_VOL", {"track": 1, "db": "-12"})], label="test level",
                     expect=bridge.active_project())
        snapshots.capture("quiet")
        lines = snapshots.compare("loud", "quiet")
        self.assertTrue(any(line.startswith("track | level:") and "->" in line for line in lines))

    def test_a_plugin_that_left_the_chain_is_named(self):
        snapshots.capture("with-pedal")
        bridge.call("FX_DEL", track=1, fx=0, confirm=1, expect=bridge.active_project())
        snapshots.capture("gone")
        text = "\n".join(snapshots.compare("with-pedal", "gone"))
        self.assertIn("chain | removed:", text)

    def test_a_captured_snapshot_object_compares_without_a_lookup(self):
        left = snapshots.capture("obj-a")
        presets.apply("clean")
        right = snapshots.capture("obj-b")
        self.assertTrue(snapshots.compare(left, right))


class TestDuplicateNames(SnapshotCase):
    """NAM Universal really does expose two parameters both called "Bypass"."""

    def snap(self, first: str, second: str, other: float = 0.0) -> snapshots.Snapshot:
        plugin = snapshots.Plugin(index=0, name="VST3: NAM Universal (WaveMind)", enabled=True,
                                  knobs=[snapshots.Knob("Bypass", 0.0, 0.0, first),
                                         snapshots.Knob("Bypass", other, other, second)])
        return snapshots.Snapshot(label="x", at="", project="p", track=1, db=0.0, plugins=[plugin])

    def test_two_knobs_with_one_name_are_compared_by_index(self):
        left = self.snap("normal", "Off")
        right = self.snap("Off", "Off")
        lines = snapshots.compare(left, right)
        self.assertEqual(len(lines), 1)
        self.assertIn("Bypass [#0]: normal -> Off", lines[0])

    def test_duplicate_names_do_not_invent_a_difference(self):
        left = self.snap("normal", "Off")
        right = self.snap("normal", "Off")
        self.assertEqual(snapshots.compare(left, right), [])

    def test_a_duplicated_name_is_qualified_only_when_it_needs_to_be(self):
        left = self.snap("normal", "Off")
        self.assertEqual(snapshots._labels(left.plugins[0]),
                         ["Bypass [#0]", "Bypass [#1]"])


class TestTool(SnapshotCase):
    def setUp(self):
        super().setUp()
        from amphead import tools
        self.handler = dict((name, fn) for name, _s, fn, _e in tools.TOOLS)["amphead_ab"]

    def test_snapshot_defaults_to_label_a_and_points_at_the_next_step(self):
        text = self.handler({})
        self.assertIn("snapshot=a", text)
        self.assertIn("knobs=", text)
        self.assertIn("action=compare", text)

    def test_compare_between_two_names_reports_the_diff(self):
        self.handler({"label": "pre"})
        presets.apply("clean")
        self.handler({"label": "post"})
        text = self.handler({"action": "compare", "label": "pre", "other": "post"})
        self.assertIn("ab | pre -> post", text)
        self.assertIn("Amplifier Mid:", text)

    def test_compare_with_a_missing_label_is_refused_and_names_it(self):
        self.handler({"label": "pre"})
        text = self.handler({"action": "compare", "label": "pre", "other": "ghost"})
        self.assertTrue(text.startswith("NOT COMPARED:"))
        self.assertIn("ghost", text)

    def test_list_shows_what_is_on_disk(self):
        self.handler({"label": "store"})
        text = self.handler({"action": "list"})
        self.assertIn("store", text)
        self.assertIn("knobs=", text)

    def test_when_reaper_is_down_it_says_so(self):
        self.fake.stop()
        self.assertTrue(self.handler({}).startswith("NOT SNAPSHOT:"))
