"""The amp's state machine, pinned.

These are not tests of the engine; they are tests of the one function every surface reads. If a
combination is not named here, it is a combination the pane and the agent have to guess at.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from amphead import lifecycle as life  # noqa: E402
from amphead import verdict  # noqa: E402

OPEN = {"open": True, "armed": True, "master_db": -6.0,
        "device": {"closed": False, "inputs": 2, "latency_out_ms": 5.1},
        "stages": {"capture": True, "tone": True, "cab": True}}
CHAIN = [{"name": "capture", "enabled": True}, {"name": "tone", "enabled": True}]
LOUD = {"l": 0.2, "r": 0.2, "clip": False, "master_db": -6.0}


def state(**overrides):
    said = dict(OPEN)
    said.update(overrides)
    return said


class TestEveryModeHasAName(unittest.TestCase):
    def test_engine_off_says_where_to_start(self):
        got = life.snapshot({"open": False})
        self.assertEqual(got["state"], life.ENGINE_OFF)
        self.assertEqual(got["engine"], "off")
        self.assertEqual(got["next"], "press SET UP")

    def test_device_closed_is_not_engine_off(self):
        got = life.snapshot(state(device={"closed": True, "inputs": 2}))
        self.assertEqual(got["state"], life.DEVICE_CLOSED)
        self.assertEqual(got["signal"], "fault")
        self.assertEqual(got["next"], "press GO LIVE")

    def test_no_inputs_is_unplugged(self):
        got = life.snapshot(state(device={"closed": False, "inputs": 0}))
        self.assertEqual(got["state"], life.DEVICE_UNPLUGGED)
        self.assertIn("plug", got["next"])

    def test_disarmed_is_standby_with_the_switch_as_next(self):
        got = life.snapshot(state(armed=False), LOUD, CHAIN)
        self.assertEqual(got["state"], life.STANDBY)
        self.assertEqual(got["signal"], "standby")
        self.assertIn("GO LIVE", got["next"])

    def test_idle_park_beats_armed_and_is_named(self):
        got = life.snapshot(state(parked_by_idle=True), LOUD, CHAIN)
        self.assertEqual(got["state"], life.PARKED)
        self.assertEqual(got["signal"], "parked")
        self.assertIn("master", got["next"])

    def test_every_stage_bypassed_is_no_chain(self):
        got = life.snapshot(OPEN, LOUD, [{"name": "capture", "enabled": False},
                                         {"name": "tone", "enabled": False}])
        self.assertEqual(got["state"], life.NO_CHAIN)
        self.assertEqual(got["signal"], "bypassed")
        self.assertEqual(got["stages"]["live"], 0)

    def test_no_meter_is_no_signal(self):
        got = life.snapshot(OPEN, {"l": 0.0, "r": 0.0, "clip": False}, CHAIN)
        self.assertEqual(got["state"], life.NO_SIGNAL)
        self.assertEqual(got["signal"], "silent")

    def test_master_on_the_floor_is_standby_not_no_signal(self):
        got = life.snapshot(state(master_db=life.PARKED_DB),
                            dict(LOUD, master_db=life.PARKED_DB), CHAIN)
        self.assertEqual(got["state"], life.STANDBY)
        self.assertEqual(got["next"], "raise the master")

    def test_clipping_wins_over_a_loud_meter(self):
        got = life.snapshot(OPEN, dict(LOUD, clip=True), CHAIN)
        self.assertEqual(got["state"], life.CLIPPING)
        self.assertEqual(got["next"], "lower IN")

    def test_callback_failure_is_a_fault_not_standby(self):
        got = life.snapshot(state(callback_errors=2, armed=False), LOUD, CHAIN)
        self.assertEqual(got["state"], life.GLITCH)
        self.assertEqual(got["signal"], "fault")
        self.assertEqual(got["health"], life.BAD)
        self.assertIn("callback failed 2", got["problems"][0])

    def test_late_blocks_over_a_live_meter_are_a_glitch(self):
        got = life.snapshot(state(late=17), LOUD, CHAIN)
        self.assertEqual(got["state"], life.GLITCH)
        self.assertEqual(got["health"], life.BAD)
        self.assertEqual(got["next"], "run tune")

    def test_ready_says_play(self):
        got = life.snapshot(OPEN, LOUD, CHAIN)
        self.assertEqual(got["state"], life.READY)
        self.assertEqual(got["next"], "play")
        self.assertEqual(got["health"], life.OK)

    def test_a_running_tune_is_named_not_called_a_closed_device(self):
        got = life.snapshot(state(tuning={"running": True, "phase": "soak 256", "seconds": 4.2},
                                  device={"closed": True, "inputs": 2}), LOUD, CHAIN)
        self.assertEqual(got["state"], life.TUNING)
        self.assertEqual(got["next"], "wait for the measurement to finish")
        self.assertEqual(got["activity"], "tuning")
        self.assertEqual(got["health"], life.OK)
        self.assertIn("soak 256", got["notice"])
        self.assertIn("reopens", got["reason"])


class TestOrthogonalAxes(unittest.TestCase):
    def test_a_take_is_an_activity_not_a_verdict(self):
        got = life.snapshot(state(take={"recording": True}), LOUD, CHAIN)
        self.assertEqual(got["verdict"], life.READY)
        self.assertEqual(got["activity"], life.RECORDING)

    def test_rate_mismatch_is_a_notice_and_a_warning_not_a_dead_amp(self):
        got = life.snapshot(state(rate_mismatch={"capture_hz": 48000, "stream_hz": 44100}),
                            LOUD, CHAIN)
        self.assertEqual(got["verdict"], life.READY)
        self.assertEqual(got["health"], life.WARN)
        self.assertIn("48 kHz", got["notice"])
        self.assertIn("44.1 kHz", got["notice"])
        self.assertIn("8% dark", got["notice"])

    def test_a_missing_look_at_the_chain_does_not_invent_bypass(self):
        got = life.snapshot(OPEN, LOUD, None)
        self.assertEqual(got["stages"]["live"], None)
        self.assertEqual(got["stages"]["bypassed"], [])
        self.assertEqual(got["state"], life.READY)


class TestOneVocabulary(unittest.TestCase):
    def test_verdict_is_the_state(self):
        got = verdict.judge(OPEN, LOUD, CHAIN)
        self.assertEqual(got["verdict"], got["state"])
        self.assertEqual(got["verdict"], life.READY)

    def test_judge_still_accepts_two_arguments(self):
        """Every existing caller passes state and one meter sample; chain is optional."""
        got = verdict.judge(OPEN, LOUD)
        self.assertEqual(got["verdict"], life.READY)


if __name__ == "__main__":
    unittest.main()
