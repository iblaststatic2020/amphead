"""The engine rig, driven with no audio device.

The pane talks to a rig through verbs, and the engine answers them from a separate
process. These tests replace that process with a dictionary so the pane-facing answers
(which tab is active, what a knob reads back, what bypass means) are pinned without
opening an interface or making a sound.
"""

import unittest

from amphead import local_port, port


class FakeClient:
    """The wire, without the engine: one dict in, one dict out."""

    def __init__(self):
        self.tone = {"bass": 0.0, "mid": 0.0, "treble": 0.0}
        self.capture = ""
        self.cab = ""
        self.master_db = port.SAFE_MASTER_DB
        self.armed = False
        self.calls = []

    running = True

    def call(self, timeout=None, **request):
        self.calls.append(request)
        cmd = request.get("cmd")
        if cmd in ("status", "ping"):
            return {"ok": True, "running": True, "armed": self.armed,
                    "master_db": self.master_db, "samplerate": 44100, "blocksize": 512,
                    "capture": self.capture, "cab": self.cab, "tone": dict(self.tone),
                    "mode": "wdm-ks", "latency_ms": 20.0}
        if cmd == "tone":
            for key in ("bass", "mid", "treble"):
                if key in request:
                    self.tone[key] = float(request[key])
            return {"tone": dict(self.tone)}
        if cmd == "gain":
            if "master_db" in request:
                self.master_db = float(request["master_db"])
            return {"master_db": self.master_db, "output_gain_db": self.master_db}
        if cmd == "arm":
            self.armed = True
            self.master_db = -6.0
            return {"armed": True, "master_db": self.master_db}
        if cmd == "disarm":
            self.armed = False
            return {"armed": False, "master_db": self.master_db}
        if cmd == "panic":
            self.armed = False
            self.master_db = port.SAFE_MASTER_DB
            return {"armed": False, "master_db": self.master_db}
        if cmd == "clear_capture":
            self.capture = ""
            return {}
        if cmd == "clear_cab":
            self.cab = ""
            return {}
        raise AssertionError(f"unexpected verb {cmd}")


def rig() -> local_port.AmpHead2Rig:
    return local_port.AmpHead2Rig(client=FakeClient())


class PaneTest(unittest.TestCase):
    def test_the_engine_publishes_itself_as_the_active_tab(self):
        """The pane refuses to write without an active tab, and the engine has no tabs."""
        tabs = rig().tabs_list()
        self.assertEqual(len(tabs), 1)
        self.assertTrue(tabs[0]["active"])
        self.assertTrue(tabs[0]["path"])

    def test_the_active_tab_is_named_for_the_player_not_for_a_file(self):
        r = rig()
        r.client.capture = "C:/captures/bugera.nam"
        self.assertEqual(r.tabs_list()[0]["path"], r.project())

    def test_state_never_names_a_project_file_that_does_not_exist(self):
        state = rig().state()
        self.assertEqual(state["project"], local_port.LABEL)
        self.assertIn("no project file", state["path"])


class KnobTest(unittest.TestCase):
    def test_a_write_reports_what_the_engine_holds_afterwards(self):
        r = rig()
        wrote = r.set_param(fx=1, param="Bass", value=3.0)
        self.assertTrue(wrote["landed"])
        self.assertEqual(wrote["fmt"], "+3.0 dB")
        self.assertEqual(r.client.tone["bass"], 3.0)

    def test_a_knob_index_and_a_knob_name_mean_the_same_thing(self):
        by_name = rig().set_param(fx=1, param="Treble", value=-2.0)
        by_index = rig().set_param(fx=1, param=2, value=-2.0)
        self.assertEqual(by_name["param_name"], by_index["param_name"])
        self.assertEqual(by_name["fmt"], by_index["fmt"])

    def test_a_normalized_write_is_scaled_to_the_knob_range(self):
        wrote = rig().set_param(fx=1, param="Mid", value=1.0, unit="norm")
        self.assertAlmostEqual(wrote["raw"], 12.0)
        self.assertAlmostEqual(wrote["norm"], 1.0)

    def test_a_capture_and_a_cabinet_have_no_knobs_to_turn(self):
        for stage in (0, 2):
            with self.assertRaises(local_port.EngineRefused):
                rig().set_param(fx=stage, param="Bass", value=3.0)

    def test_an_unknown_knob_is_refused_by_name(self):
        with self.assertRaises(local_port.EngineRefused):
            rig().set_param(fx=1, param="Presence", value=3.0)


class BypassTest(unittest.TestCase):
    def test_bypass_on_takes_the_stage_out_of_the_path(self):
        r = rig()
        r.client.capture = "C:/captures/bugera.nam"
        out = r.fx_bypass(fx=0, on=True)
        self.assertFalse(out["enabled"])
        self.assertEqual(r.client.capture, "")

    def test_bypass_off_is_refused_because_the_capture_is_gone(self):
        """Reporting a stage as restored while it is still clean is the bug worth refusing."""
        with self.assertRaises(local_port.EngineRefused):
            rig().fx_bypass(fx=0, on=False)


class ArmTest(unittest.TestCase):
    def test_arming_a_rig_with_a_level_already_set_keeps_that_level(self):
        r = rig()
        r.set_master(-9.0)
        out = r.arm()
        self.assertTrue(out["armed"])
        self.assertAlmostEqual(out["master_db"], -9.0)

    def test_arming_from_silence_uses_the_measured_default(self):
        out = rig().arm()
        self.assertTrue(out["armed"])
        self.assertAlmostEqual(out["master_db"], -6.0)

    def test_a_capture_is_not_a_guessed_default(self):
        self.assertIsNone(rig().project())


if __name__ == "__main__":
    unittest.main()
