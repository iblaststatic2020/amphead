"""The judge speaks for the amp, and it only has the words the face can paint.

There is no host application any more, so there is no "bridge" to be gone and no project tab to
be closed: the engine is either open or it is off, and every reason names the control that fixes
it by the name printed on the face.
"""

import unittest

from amphead import verdict


class EngineWordsTest(unittest.TestCase):
    def judge(self, state, reading=None, chain=None):
        return verdict.judge(state, reading or {"l": 0.0, "r": 0.0, "clip": False}, chain or [])

    def open_state(self, **over):
        state = {"open": True, "engine": "amphead2", "armed": False, "master_db": -6.0,
                 "device": {"closed": False, "inputs": 2}}
        state.update(over)
        return state

    def test_a_stopped_engine_says_off_and_names_the_switch(self):
        got = self.judge({"open": False, "engine": "amphead2"})
        self.assertEqual(got["verdict"], verdict.ENGINE_OFF)
        self.assertIn("SET UP", got["reason"])

    def test_a_closed_device_is_named_before_a_missing_chain(self):
        got = self.judge(self.open_state(device={"closed": True}))
        self.assertEqual(got["verdict"], verdict.DEVICE_CLOSED)

    def test_an_interface_with_no_inputs_is_unplugged_not_silent(self):
        got = self.judge(self.open_state(device={"closed": False, "inputs": 0}))
        self.assertEqual(got["verdict"], verdict.DEVICE_UNPLUGGED)

    def test_an_open_engine_says_it_is_open_and_silent(self):
        got = self.judge(self.open_state(armed=False, master_db=-60.0),
                         {"l": 0.0, "r": 0.0, "clip": False, "master_db": -60.0}, [])
        self.assertEqual(got["verdict"], verdict.STANDBY)
        self.assertIn("open", got["reason"])
        self.assertIn("GO LIVE", got["reason"])
        self.assertIn("knob", got["reason"], "arming takes the knob, not a fixed level")

    def test_an_armed_engine_hearing_nothing_says_no_signal(self):
        got = self.judge(self.open_state(armed=True), {"l": 0.0, "r": 0.0, "clip": False},
                         [{"name": "capture", "enabled": True}])
        self.assertEqual(got["verdict"], verdict.NO_SIGNAL)

    def test_an_armed_engine_with_every_stage_bypassed_says_no_chain(self):
        chain = [{"name": "capture", "enabled": False}, {"name": "tone", "enabled": False}]
        got = self.judge(self.open_state(armed=True),
                         {"l": 0.5, "r": 0.5, "clip": False}, chain)
        self.assertEqual(got["verdict"], verdict.NO_CHAIN)

    def test_an_armed_engine_at_the_ceiling_says_clipping(self):
        got = self.judge(self.open_state(armed=True), {"l": 1.0, "r": 1.0, "clip": True}, [])
        self.assertEqual(got["verdict"], verdict.CLIPPING)

    def test_a_healthy_amp_is_ready_and_states_its_round_trip(self):
        state = self.open_state(armed=True)
        state["device"]["latency_out_ms"] = 5.1
        got = self.judge(state, {"l": 0.2, "r": 0.2, "clip": False},
                         [{"name": "capture", "enabled": True}])
        self.assertEqual(got["verdict"], verdict.READY)
        self.assertIn("ms round trip", got["reason"])


if __name__ == "__main__":
    unittest.main()
