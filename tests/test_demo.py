"""The canned rig: it must be off by default, honest when on, and shaped exactly like the real one."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from amphead import demo, verdict  # noqa: E402

# Every key the pane reads. A demo payload missing one of these breaks the face in a way tests of
# the real path would never catch, because the real path is the one that gets exercised.
STATE_KEYS = {"verdict", "reason", "project", "device", "master_db", "ui", "armed", "monitor",
              "problems", "chain", "plugin", "knobs", "input", "presets", "demo"}


class DemoHome(unittest.TestCase):
    """Each test gets its own AMPHEAD_HOME so a switch written by one cannot leak into the next.

    Isolating on HERMES_HOME used to work only because the demo flag kept a home of its own; the
    flag now lives in ``rig.data_dir()`` like the rest of amphead's state, so the test moves the
    data dir instead of the Hermes one.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {"AMPHEAD_HOME": str(self.home),
                                                "HERMES_HOME": str(self.home)}, clear=False)
        self.env.start()
        os.environ.pop("AMPHEAD_DEMO", None)
        demo.reset()

    def tearDown(self) -> None:
        self.env.stop()
        self.tmp.cleanup()


class TestSwitching(DemoHome):
    def test_off_unless_asked(self):
        self.assertFalse(demo.enabled(), "the demo must never turn itself on")

    def test_the_switch_sticks_across_a_call(self):
        self.assertTrue(demo.set_enabled(True))
        self.assertTrue(demo.enabled())
        demo.set_enabled(False)
        self.assertFalse(demo.enabled())

    def test_the_environment_variable_is_enough(self):
        os.environ["AMPHEAD_DEMO"] = "1"
        self.assertTrue(demo.enabled())


class TestTheRigItShows(DemoHome):
    def setUp(self) -> None:
        super().setUp()
        demo.set_enabled(True)

    def test_the_payload_is_the_shape_the_pane_draws(self):
        state = demo.state()
        self.assertEqual(set(state), STATE_KEYS)
        self.assertEqual(set(state["device"]), {"buffer", "rate", "out_ms", "closed"})
        self.assertTrue(state["demo"])

    def test_a_visitor_arrives_at_a_rig_that_is_ready(self):
        state = demo.state()
        self.assertEqual(state["verdict"], verdict.READY, state["reason"])
        self.assertEqual(state["problems"], [])
        self.assertEqual(len(state["chain"]), 1)
        self.assertEqual(len(state["knobs"]), 8)
        # The latency a player reads has to be the rig's own number, not a zero left by a key typo.
        self.assertIn("11.61 ms", state["reason"])
        self.assertAlmostEqual(state["device"]["out_ms"], 11.61, places=2)

    def test_the_meter_moves_and_never_clips(self):
        # A real plucked phrase takes seconds; the test walks the clock instead of waiting, so it
        # cannot pass by accident on a fast machine or flake on a slow one.
        ticks = iter(demo._START + 0.04 * step for step in range(400))
        with mock.patch.object(demo, "time", mock.Mock(monotonic=lambda: next(ticks))):
            levels = []
            for _ in range(40):
                sample = demo.meter()
                self.assertTrue(sample["live"])
                self.assertFalse(sample["clip"])
                levels.append(sample["l"])
        self.assertGreater(max(levels), verdict.FLOOR, "the meter has to leave the floor")
        self.assertGreater(len(set(levels)), 20, "a still meter is not a demo")

    def test_every_verdict_the_demo_draws_is_a_word_the_pane_knows(self):
        words = {demo.state()["verdict"]}
        demo.live_switch({"on": False})
        words.add(demo.state()["verdict"])
        demo.live_switch({"on": True})
        demo.panic({"restore": False})
        words.add(demo.state()["verdict"])
        demo.panic({"restore": True})
        known = {verdict.READY, verdict.STANDBY, verdict.NO_CHAIN}
        self.assertTrue(words <= known, words - known)


class TestHandsOn(DemoHome):
    def setUp(self) -> None:
        super().setUp()
        demo.set_enabled(True)

    def test_a_knob_moves_and_says_where_it_went(self):
        out = demo.tweak({"param": "Amplifier Bass", "value": 0.9, "mode": "norm"})
        self.assertTrue(out["ok"], out["output"])
        knobs = {k["name"]: k for k in demo.state()["knobs"]}
        self.assertAlmostEqual(knobs["Amplifier Bass"]["norm"], 0.9, places=3)
        self.assertIn("9", knobs["Amplifier Bass"]["value"])

    def test_the_socket_starts_at_the_first_channel_and_turns(self):
        """The demo used to park its input at REAPER's 1.0, which is the second socket."""
        first = demo.state()["input"]
        self.assertEqual((first["number"], first["name"], first["value"]), (1, "IN 1", "input 1"))
        out = demo.input_switch({"input": 2})
        self.assertTrue(out["ok"], out["output"])
        self.assertEqual((out["number"], out["name"]), (2, "IN 2"))
        self.assertEqual(demo.state()["input"]["number"], 2)
        demo.input_switch({"input": 1})

    def test_a_socket_the_interface_does_not_have_is_refused(self):
        self.assertFalse(demo.input_switch({"input": 3})["ok"])
        self.assertFalse(demo.input_switch({})["ok"])

    def test_an_unknown_knob_is_refused_not_guessed(self):
        out = demo.tweak({"param": "Drive", "value": 0.5, "mode": "norm"})
        self.assertFalse(out["ok"])
        self.assertTrue(out["output"].startswith("NOT SET"))

    def test_a_knob_with_no_value_is_refused(self):
        self.assertFalse(demo.tweak({"param": "Amplifier Bass"})["ok"])
        self.assertFalse(demo.tweak({"value": 0.5})["ok"])

    def test_panic_bypasses_the_chain_and_restore_brings_it_back(self):
        demo.panic({"restore": False})
        state = demo.state()
        self.assertEqual(state["verdict"], verdict.NO_CHAIN, state["reason"])
        self.assertFalse(state["chain"][0]["enabled"])
        demo.panic({"restore": True})
        self.assertEqual(demo.state()["verdict"], verdict.READY)

    def test_live_arms_the_track_and_parking_says_standby(self):
        demo.live_switch({"on": False})
        state = demo.state()
        self.assertFalse(state["armed"])
        self.assertEqual(state["verdict"], verdict.STANDBY, state["reason"])
        demo.live_switch({"on": True})
        self.assertEqual(demo.state()["verdict"], verdict.READY)

    def test_a_preset_moves_the_knobs_and_an_unknown_one_is_refused(self):
        out = demo.preset({"query": "doom"})
        self.assertTrue(out["ok"], out["output"])
        self.assertFalse(demo.preset({"query": "polka"})["ok"])
        self.assertFalse(demo.preset({"query": ""})["ok"])

    def test_there_is_no_reaper_window_to_focus(self):
        out = demo.focus({})
        self.assertFalse(out["focused"])
        self.assertFalse(out["ok"])


class TestTheRoutesUseIt(DemoHome):
    """The pane talks to the routes, not to the module, so the switch has to be wired there."""

    def test_the_routes_answer_from_the_demo_when_it_is_on(self):
        from dashboard import plugin_api

        demo.set_enabled(True)
        self.assertTrue(plugin_api.state()["demo"])
        self.assertTrue(plugin_api.meter()["live"])
        self.assertTrue(plugin_api.tweak({"param": "Amplifier Bass", "value": 0.25,
                                          "mode": "norm"})["ok"])
        self.assertFalse(plugin_api.preset({"query": "polka"})["ok"])
        self.assertTrue(plugin_api.live_switch({"on": False, "master_db": -18})["on"] is False)
        self.assertTrue(plugin_api.panic({"restore": True})["ok"])

    def test_switching_off_hands_the_pane_back_to_reaper(self):
        from dashboard import plugin_api

        out = plugin_api.demo_switch({"on": True})
        self.assertTrue(out["on"])
        out = plugin_api.demo_switch({"on": False})
        self.assertFalse(out["on"])
        # With no REAPER on this machine the honest answer is one of the not-running words, never
        # a demo payload left behind.
        state = plugin_api.state()
        self.assertFalse(state["demo"])


if __name__ == "__main__":
    unittest.main()
