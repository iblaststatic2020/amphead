"""The engine process verbs, driven in memory. No device, no subprocess."""

import io
import json
import unittest
from pathlib import Path
from unittest import mock

try:
    import numpy as np
    from amphead.engine2 import ARM_MASTER_DB, SAFE_MASTER_DB
    from amphead.engine_main import Server, serve
except ModuleNotFoundError:  # pragma: no cover - the engine is an optional extra
    np = None

if np is None:  # pragma: no cover
    raise unittest.SkipTest("numpy is not installed")


@unittest.skipIf(np is None, "numpy is not installed")
class VerbTest(unittest.TestCase):
    def setUp(self):
        from amphead.engine2 import Engine

        self.server = Server(Engine(samplerate=48000))

    def test_status_describes_a_disarmed_engine(self):
        result = self.server.handle({"cmd": "status"})["result"]
        self.assertEqual(result["engine"], "amphead2")
        self.assertFalse(result["running"])
        self.assertFalse(result["armed"])
        self.assertEqual(result["samplerate"], 48000)
        self.assertEqual(result["tone"], {"bass": 0.0, "mid": 0.0, "treble": 0.0})

    def test_status_reports_the_block_on_top_of_the_driver_buffer(self):
        """The felt round trip is the buffer the driver gives plus the block it must fill.

        Reporting the driver half alone is how a 512-frame build looked as quick as a
        256-frame one on the face while sounding a block slower in the hands.
        """
        result = self.server.handle({"cmd": "status"})["result"]
        block_ms = 1000.0 * 256 / 48000
        self.assertAlmostEqual(result["block_ms"], round(block_ms, 2))
        if result["latency_ms"] is not None:
            self.assertAlmostEqual(result["round_trip_ms"], round(result["latency_ms"] + block_ms, 1))
        else:
            self.assertIsNone(result["round_trip_ms"], "no open stream, no number to show")

    def test_starting_opens_the_device_without_raising_the_master(self):
        """A started engine that says armed:false has to be silent, not merely quiet."""
        with mock.patch.object(type(self.server.engine), "start", return_value="test"):
            self.server.handle({"cmd": "start"})
        status = self.server.handle({"cmd": "status"})["result"]
        self.assertFalse(status["armed"])
        self.assertEqual(status["master_db"], SAFE_MASTER_DB)

    def test_start_plays_the_chain_that_was_played_last(self):
        """Setup is not a clean DI: the pane has no picker, so it reads the CLI's last chain."""
        fixture = str(Path(__file__).resolve().parent / "fixtures" / "tiny_wavenet.nam")
        with mock.patch.object(type(self.server.engine), "start", return_value="test"), \
                mock.patch("amphead.lastchain.load", return_value={"capture": fixture}):
            done = self.server.handle({"cmd": "start"})["result"]
        self.assertEqual(done["chain"]["capture"], fixture)
        self.assertEqual(self.server.capture_name, fixture)

    def test_a_capture_that_is_gone_is_skipped_rather_than_fatal(self):
        """A moved file leaves the device open with nothing in front of it, and says nothing."""
        with mock.patch.object(type(self.server.engine), "start", return_value="test"), \
                mock.patch("amphead.lastchain.load",
                           return_value={"capture": "C:/captures/gone.nam"}):
            done = self.server.handle({"cmd": "start"})["result"]
        self.assertEqual(done["chain"], {})
        self.assertIsNone(self.server.capture_name)

    def test_arm_uses_the_measured_default(self):
        result = self.server.handle({"cmd": "arm"})["result"]
        self.assertTrue(result["armed"])
        self.assertEqual(result["master_db"], SAFE_MASTER_DB,
                         "arming without a level must not lift the master off the floor")

    def test_arm_can_be_overridden(self):
        self.assertEqual(self.server.handle({"cmd": "arm", "master_db": -12})["result"]["master_db"], -12.0)

    def test_bypass_switches_a_stage_out_and_keeps_it_loaded(self):
        fixture = str(Path(__file__).resolve().parent / "fixtures" / "tiny_wavenet.nam")
        self.server.handle({"cmd": "load_capture", "path": fixture})
        got = self.server.handle({"cmd": "bypass", "stage": "capture", "on": False})["result"]
        self.assertFalse(got["enabled"])
        status = self.server.handle({"cmd": "status"})["result"]
        self.assertFalse(status["stages"]["capture"])
        self.assertEqual(status["capture"], fixture)
        back = self.server.handle({"cmd": "bypass", "stage": "capture", "on": True})["result"]
        self.assertTrue(back["enabled"])

    def test_bypass_refuses_a_stage_that_does_not_exist(self):
        got = self.server.handle({"cmd": "bypass", "stage": "reverb", "on": False})["result"]
        self.assertIn("refused", got)

    def test_panic_is_a_hard_mute(self):
        self.server.handle({"cmd": "arm", "master_db": 0})
        result = self.server.handle({"cmd": "panic"})["result"]
        self.assertFalse(result["armed"])
        self.assertEqual(result["master_db"], SAFE_MASTER_DB)

    def test_tone_verbs_persist_and_report(self):
        self.assertEqual(self.server.handle({"cmd": "tone", "bass": 4})["result"]["bass"], 4.0)
        result = self.server.handle({"cmd": "status"})["result"]
        self.assertEqual(result["tone"]["bass"], 4.0)
        self.assertEqual(result["tone"]["mid"], 0.0)

    def test_the_gate_starts_off_and_moves(self):
        self.assertEqual(self.server.gate()["gate_db"], -80.0)
        self.assertFalse(self.server.gate()["open"])
        self.assertEqual(self.server.gate(-38.0)["gate_db"], -38.0)
        self.assertTrue(self.server.gate()["open"])

    def test_tune_measures_without_opening_the_device(self):
        got = self.server.tune(live=False)
        self.assertEqual(got["running"], False)
        self.assertIn(got["picked"], (64, 128, 256, 512, 1024))
        self.assertTrue(got["rows"])
        self.assertIn("frames", got["verdict"])

    def test_gain_refuses_an_unknown_name(self):
        self.server.handle({"cmd": "gain", "master_db": -6})
        self.assertEqual(self.server.handle({"cmd": "status"})["result"]["master_db"], -6.0)

    def test_capture_verbs_update_the_chain(self):
        self.server.handle({"cmd": "clear_capture"})
        result = self.server.handle({"cmd": "status"})["result"]
        self.assertIsNone(result["capture"])

    def test_the_amp_hears_input_one_until_told_otherwise(self):
        status = self.server.handle({"cmd": "status"})["result"]
        self.assertEqual(status["input_channel"], 1)

    def test_input_switches_the_socket_live(self):
        self.server.engine.input_channels = 2
        done = self.server.handle({"cmd": "input", "channel": 2})["result"]
        self.assertEqual(done, {"input_channel": 2, "input_channels": 2})
        self.assertEqual(self.server.engine.input_channel, 1)

    def test_input_refuses_a_socket_the_interface_does_not_have(self):
        self.server.engine.input_channels = 2
        reply = self.server.handle({"cmd": "input", "channel": 3})
        self.assertFalse(reply.get("ok", True))
        self.assertEqual(self.server.engine.input_channel, 0)

    def test_the_callback_reads_the_chosen_socket(self):
        engine = self.server.engine
        engine.input_channel = 1
        block = np.zeros((4, 2), dtype=np.float32)
        block[:, 1] = 0.5
        engine._process_block(block, 4)
        self.assertAlmostEqual(engine._meter.peak_in, 0.5)
        engine.input_channel = 0
        engine._process_block(block, 4)
        self.assertAlmostEqual(engine._meter.peak_in, 0.0)

    def test_unknown_command_is_an_error_not_a_crash(self):
        reply = self.server.handle({"cmd": "make_me_a_sandwich"})
        self.assertFalse(reply["ok"])
        self.assertIn("unknown command", reply["error"])

    def test_a_failing_verb_reports_and_keeps_the_engine(self):
        reply = self.server.handle({"cmd": "load_capture", "path": "no-such-file.nam"})
        self.assertFalse(reply["ok"])
        self.assertTrue(self.server.handle({"cmd": "status"})["ok"])

    def test_quit_stops_the_loop(self):
        self.assertTrue(self.server.handle({"cmd": "quit"})["stop"])

    def test_the_library_lists_both_kinds_of_file_by_folder(self):
        """The pane offers what is on the machine; a player needs the folder, not the root."""
        import tempfile

        room = Path(tempfile.mkdtemp())
        (room / "caps").mkdir()
        (room / "irs").mkdir()
        (room / "caps" / "Amp A.nam").write_text("{}")
        (room / "irs" / "Cab A.wav").write_bytes(b"RIFF")
        with mock.patch.dict("os.environ", {
            "AMPHEAD_CAPTURE_DIR": str(room / "caps"),
            "AMPHEAD_CAB_DIR": str(room / "irs"),
        }, clear=False):
            got = self.server.handle({"cmd": "library"})["result"]
        self.assertIn("Amp A", [c["name"] for c in got["captures"]])
        self.assertIn("Cab A", [c["name"] for c in got["cabs"]])
        self.assertTrue(all(c["folder"] for c in got["captures"]))


@unittest.skipIf(np is None, "numpy is not installed")
class LoopTest(unittest.TestCase):
    def test_serve_reads_json_lines_and_writes_one_reply_each(self):
        stdin = io.StringIO('{"cmd":"status"}\n\n{"cmd":"arm"}\n{"cmd":"quit"}\n')
        stdout = io.StringIO()
        self.assertEqual(serve(stdin, stdout), 0)
        lines = [json.loads(line) for line in stdout.getvalue().splitlines()]
        self.assertEqual(len(lines), 3)
        self.assertTrue(lines[0]["result"]["engine"] == "amphead2")
        self.assertTrue(lines[1]["result"]["armed"])

    def test_bad_json_does_not_end_the_loop(self):
        stdin = io.StringIO("not json\n{\"cmd\":\"status\"}\n")
        stdout = io.StringIO()
        serve(stdin, stdout)
        lines = [json.loads(line) for line in stdout.getvalue().splitlines()]
        self.assertFalse(lines[0]["ok"])
        self.assertTrue(lines[1]["ok"])


if __name__ == "__main__":
    unittest.main()
