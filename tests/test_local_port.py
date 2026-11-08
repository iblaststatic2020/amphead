"""The engine rig, driven with no audio device.

The pane talks to a rig through verbs, and the engine answers them from a separate
process. These tests replace that process with a dictionary so the pane-facing answers
(which tab is active, what a knob reads back, what bypass means) are pinned without
opening an interface or making a sound.
"""

import os
import pathlib
import subprocess
import unittest
from unittest import mock

from amphead import local_port, port


class FakeClient:
    """The wire, without the engine: one dict in, one dict out."""

    def __init__(self):
        self.tone = {"bass": 0.0, "mid": 0.0, "treble": 0.0}
        self.capture = ""
        self.cab = ""
        self.input_channel = 1
        self.master_db = port.SAFE_MASTER_DB
        self.armed = False
        self.stages = {"capture": True, "tone": True, "cabinet": True}
        self.calls = []

    running = True

    def call(self, timeout=None, **request):
        self.calls.append(request)
        cmd = request.get("cmd")
        if cmd in ("status", "ping"):
            return {"ok": True, "running": True, "armed": self.armed,
                    "master_db": self.master_db, "samplerate": 44100, "blocksize": 512,
                    "capture": self.capture, "cab": self.cab, "tone": dict(self.tone),
                    "stages": dict(self.stages), "mode": "wdm-ks", "latency_ms": 20.0,
                    "input_channel": self.input_channel, "input_channels": 2}
        if cmd == "input":
            self.input_channel = int(request["channel"])
            return {"input_channel": self.input_channel, "input_channels": 2}
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
        if cmd == "bypass":
            self.stages[str(request["stage"])] = bool(request["on"])
            return {"stage": request["stage"], "enabled": bool(request["on"])}
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
        self.assertEqual(r.client.capture, "C:/captures/bugera.nam")

    def test_a_bypassed_stage_is_still_loaded_and_can_come_back(self):
        """Panic has to be undoable: the capture is switched out, not forgotten."""
        r = rig()
        r.client.capture = "C:/captures/bugera.nam"
        r.fx_bypass(fx=0, on=True)
        self.assertFalse(r.fx_list()[0]["enabled"])
        self.assertTrue(r.fx_bypass(fx=0, on=False)["enabled"])
        self.assertTrue(r.fx_list()[0]["enabled"])

    def test_bypass_off_is_refused_when_nothing_is_loaded(self):
        """Offering a switch that would restore silence is worse than refusing it."""
        with self.assertRaises(local_port.EngineRefused):
            rig().fx_bypass(fx=0, on=False)


class InterpreterTest(unittest.TestCase):
    def test_an_explicit_interpreter_wins_over_everything(self):
        self.assertEqual(local_port.engine_python("C:/elsewhere/python.exe"),
                         "C:/elsewhere/python.exe")

    def test_the_environment_can_name_the_interpreter(self):
        os.environ["AMPHEAD_ENGINE_PYTHON"] = "C:/said/python.exe"
        try:
            self.assertEqual(local_port.engine_python(), "C:/said/python.exe")
        finally:
            del os.environ["AMPHEAD_ENGINE_PYTHON"]

    def test_a_venv_beside_the_plugin_is_found_without_being_configured(self):
        """The pane runs in Hermes's Python, so the venv has to be discoverable, not typed."""
        os.environ.pop("AMPHEAD_ENGINE_PYTHON", None)
        found = local_port.engine_python()
        self.assertTrue(os.path.isfile(found))

    def test_the_capability_check_runs_in_the_engine_not_here(self):
        """A rig must not refuse because the pane's own Python is thin."""
        said = {}

        class Fake(subprocess.CompletedProcess):
            def __init__(self, *a, **k):
                pass

        def fake_run(argv, **kw):
            said["argv"] = argv
            said["cwd"] = kw.get("cwd")
            return subprocess.CompletedProcess(argv, 0, "", "")

        with mock.patch.object(local_port.subprocess, "run", fake_run):
            local_port.EngineClient(python="C:/engine/python.exe").check_env()
        self.assertEqual(said["argv"][0], "C:/engine/python.exe")
        self.assertIn("sounddevice", said["argv"][-1])

    def test_a_thin_engine_says_what_is_missing_and_where(self):
        def fake_run(argv, **kw):
            return subprocess.CompletedProcess(
                argv, 1, "", "ModuleNotFoundError: No module named 'sounddevice'")

        with mock.patch.object(local_port.subprocess, "run", fake_run):
            with self.assertRaises(local_port.EngineNotInstalled) as caught:
                local_port.EngineClient(python="C:/engine/python.exe").check_env()
        said = str(caught.exception)
        self.assertIn("C:/engine/python.exe", said)
        self.assertIn("sounddevice", said)


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


class InputTest(unittest.TestCase):
    def test_the_switch_offers_the_interfaces_sockets_not_windows_devices(self):
        r = rig()
        out = r.inputs()
        self.assertEqual(out["count"], 2)
        self.assertEqual([row["name"] for row in out["channels"]], ["IN 1", "IN 2"])

    def test_the_track_reads_input_one_by_default(self):
        self.assertEqual(rig()._input_spec({"running": True, "input_channel": 1}), 0)

    def test_set_input_names_a_socket_not_a_device(self):
        r = rig()
        out = r.set_input(track=1, spec=2)
        self.assertEqual(out["recinput"], 1)
        self.assertEqual(r.client.calls[-1], {"cmd": "input", "channel": 2})


class MenuTest(unittest.TestCase):
    """The amp and cabinet menus exist whether or not the interface is open."""

    def down(self) -> local_port.AmpHead2Rig:
        client = FakeClient()
        client.running = False
        return local_port.AmpHead2Rig(client=client)

    def test_the_menu_lists_files_before_the_engine_is_running(self):
        import tempfile

        room = pathlib.Path(tempfile.mkdtemp())
        (room / "caps").mkdir()
        (room / "irs").mkdir()
        (room / "caps" / "Amp A.nam").write_text("{}")
        (room / "irs" / "Cab A.wav").write_bytes(b"RIFF")
        with mock.patch.dict("os.environ", {
            "AMPHEAD_CAPTURE_DIR": str(room / "caps"),
            "AMPHEAD_CAB_DIR": str(room / "irs"),
        }, clear=False):
            got = self.down().library()
        self.assertIn("Amp A", [row["name"] for row in got["captures"]])
        self.assertIn("Cab A", [row["name"] for row in got["cabs"]])

    def test_a_choice_made_before_setup_is_remembered_for_setup(self):
        """Setup plays the last chain; picking an amp while parked is that same path."""
        saved = {}
        with mock.patch("amphead.lastchain.save", lambda **kw: saved.update(kw)):
            got = self.down().open_capture("C:/amps/Bugera 6262/Lead.nam")
        self.assertTrue(got["on_setup"])
        self.assertEqual(got["capture"], "Lead")
        self.assertEqual(saved["capture"], "C:/amps/Bugera 6262/Lead.nam")


if __name__ == "__main__":
    unittest.main()
