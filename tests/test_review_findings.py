"""The audio-review findings, each one a test so it cannot come back quietly.

Everything here is a defect a player would meet before an agent would: a take that is shorter
than the performance, an amp that says LIVE while it is silent, a model played at the wrong
rate, a reply that lands in the next caller's hands.
"""

import os
import queue
import tempfile
import time
import unittest
import wave
from pathlib import Path

try:
    import numpy as np

    from amphead import engine2, engine_main, local_port, record, tone
    from amphead.engine2 import SAFE_MASTER_DB, Engine
except ModuleNotFoundError:  # pragma: no cover
    raise unittest.SkipTest("numpy is required for the engine")


class FakeAmp:
    """Gain of two with a one-sample delay, so its history is visible from outside."""

    sample_rate = None

    def __init__(self) -> None:
        self.previous = 0.0

    def process(self, x):
        out = np.concatenate(([self.previous], x[:-1])) * 2.0
        if x.size:
            self.previous = float(x[-1])
        return out.astype(np.float32)


class FakeCab:
    def process(self, x):
        return (x * 0.5).astype(np.float32)


def _engine():
    engine = Engine(samplerate=44100, blocksize=128)
    engine.update(amp=FakeAmp(), cab=FakeCab())
    return engine


class TestTheChainKeepsRunningWhileParked(unittest.TestCase):
    """Parking is silence, not a frozen chain: skipping the work leaves stale state behind."""

    def test_a_disarmed_block_still_moves_the_model_history(self):
        engine = _engine()
        engine.update(armed=False, master_db=SAFE_MASTER_DB)
        amp = engine.chain.amp
        before = amp.previous
        y = engine._render(np.full(128, 0.25, dtype=np.float32), 128)
        self.assertEqual(float(np.max(np.abs(y))), 0.0, "a parked amp must be silent")
        self.assertNotEqual(amp.previous, before,
                            "the model was skipped, so the next GO LIVE plays old history")

    def test_a_take_while_parked_is_as_long_as_the_performance(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "parked.wav"
        engine = _engine()
        engine.update(armed=False, master_db=SAFE_MASTER_DB)
        engine.start_take(path)
        for _ in range(4):
            engine._render(np.full(128, 0.25, dtype=np.float32), 128)
        said = engine.stop_take()
        with wave.open(str(path), "rb") as got:
            self.assertEqual(got.getnframes(), 512,
                             "blocks were dropped while parked, so the file lost time")
        self.assertEqual(said["frames"], 512)
        self.assertIsNone(said["peak_db"], "a parked take is digital silence")


class TestAFailedCallbackIsVisible(unittest.TestCase):
    def test_a_callback_failure_drops_the_arm_instead_of_leaving_it_on(self):
        engine = _engine()
        engine.update(armed=True, master_db=-6.0)
        engine._fail_callback(RuntimeError("could not broadcast input array"))
        self.assertFalse(engine.chain.armed, "the pane would say LIVE over silence")
        self.assertAlmostEqual(engine.chain.master_db, SAFE_MASTER_DB)
        self.assertEqual(engine.counters["callback_errors"], 1)
        self.assertIn("broadcast", engine._last_error)

    def test_the_count_survives_a_reopen_because_the_glitch_happened(self):
        engine = _engine()
        engine._fail_callback(RuntimeError("x"))
        engine.reset_counters()
        self.assertEqual(engine.counters["callback_errors"], 1,
                         "clearing per-stream counters must not hide a real failure")

    def test_a_closed_device_says_so_and_unarms(self):
        engine = _engine()
        engine.update(armed=True, master_db=-6.0)
        engine._device_closed()
        self.assertFalse(engine.chain.armed)
        self.assertIn("device closed", engine._last_error)

    def test_a_stop_i_asked_for_is_not_a_device_failure(self):
        engine = _engine()
        engine.update(armed=True, master_db=-6.0)
        closed = []

        class Stream:
            def stop(self):
                # PortAudio runs finished_callback while the stream is being closed, which
                # is exactly the path that used to record a fault after every tune.
                closed.append(engine._closing)
                engine._device_closed()

            def close(self):
                pass

        engine._duplex = Stream()
        engine.stop()
        self.assertEqual(closed, [True])
        self.assertIsNone(engine._last_error)
        self.assertTrue(engine.chain.armed, "STOP is not an unplug")


class TestTakesAreNeverOverwritten(unittest.TestCase):
    def test_free_path_steps_aside_instead_of_replacing(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        first = Path(tmp.name) / "take 14-03.wav"
        first.write_bytes(b"first")
        second = record.free_path(first)
        second.write_bytes(b"second")
        third = record.free_path(first)
        self.assertEqual(second.name, "take 14-03 (2).wav")
        self.assertEqual(third.name, "take 14-03 (3).wav")
        self.assertEqual(first.read_bytes(), b"first", "the first take was destroyed")

    def test_a_second_take_in_the_same_minute_lands_beside_the_first(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        folder = Path(tmp.name)
        server = engine_main.Server(_engine())
        server.engine.update(armed=True, master_db=-6.0)
        one = server.handle({"cmd": "record_start", "path": str(folder / "same.wav")})["result"]
        server.engine._render(np.full(128, 0.2, dtype=np.float32), 128)
        server.handle({"cmd": "record_stop"})
        two = server.handle({"cmd": "record_start", "path": str(folder / "same.wav")})["result"]
        server.handle({"cmd": "record_stop"})
        self.assertNotEqual(one["path"], two["path"])
        self.assertTrue(Path(one["path"]).exists())
        self.assertTrue(Path(two["path"]).exists())

    def test_a_folder_that_is_not_there_is_refused_not_created(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        missing = Path(tmp.name) / "no" / "such" / "folder" / "take.wav"
        server = engine_main.Server(_engine())
        server.engine.update(armed=True, master_db=-6.0)
        said = server.handle({"cmd": "record_start", "path": str(missing)})
        self.assertFalse(said["ok"])
        self.assertIn("does not exist", said["error"])
        self.assertFalse(missing.parent.exists(), "a take must not make a tree")


class TestWhatWasJustRecorded(unittest.TestCase):
    def test_the_finished_take_is_reported_instead_of_whatever_is_newest(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        folder = Path(tmp.name)
        server = engine_main.Server(_engine())
        server.recordings_dir = lambda: folder                 # keep scratch out of APPDATA
        server.engine.update(armed=True, master_db=-6.0)
        made = server.handle({"cmd": "record_start"})["result"]["path"]
        server.engine._render(np.full(128, 0.2, dtype=np.float32), 128)
        server.handle({"cmd": "record_stop"})
        state = server.take_state()
        self.assertEqual(Path(state["path"]).name, Path(made).name)
        self.assertTrue(state.get("this_session"))

    def test_a_wav_that_was_already_there_is_flagged_as_a_guess(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        folder = Path(tmp.name)
        (folder / "old.wav").write_bytes(b"not really a wav")
        server = engine_main.Server(_engine())
        server.recordings_dir = lambda: folder
        state = server.take_state()
        self.assertFalse(state.get("this_session"),
                         "an old file was reported as this session's take")


class TestACaptureSetsTheRate(unittest.TestCase):
    def _server_with_model_rate(self, rate):
        from amphead.nam import WaveNet
        import amphead.nam as nam

        fixture = Path(__file__).parent / "fixtures" / "tiny_wavenet.nam"
        model = WaveNet.load(fixture)
        model.sample_rate = rate
        original = nam.WaveNet.load
        nam.WaveNet.load = staticmethod(lambda path: model)
        self.addCleanup(setattr, nam.WaveNet, "load", original)
        return engine_main.Server(_engine()), fixture

    def test_the_stream_follows_the_capture_instead_of_refusing_it(self):
        server, fixture = self._server_with_model_rate(48000)
        said = server.load_capture(str(fixture))
        self.assertEqual(server.engine.samplerate, 48000)
        self.assertEqual(said["followed_capture_rate"], 48000)
        self.assertIs(server.engine.chain.amp.sample_rate, 48000,
                      "the capture was loaded at a rate the stream is not running at")

    def test_a_device_that_will_not_open_at_the_capture_rate_still_plays_it_and_says_why(self):
        server, fixture = self._server_with_model_rate(48000)
        def refuse(rate):
            raise RuntimeError("invalid sample rate 48000")

        server.engine.reopen = refuse
        said = server.load_capture(str(fixture))
        self.assertEqual(said["rate_mismatch"], {"capture_hz": 48000, "stream_hz": 44100,
                                                 "why": "invalid sample rate 48000"})
        self.assertIn("8% dark", said["warning"])
        self.assertIs(server.engine.chain.amp.sample_rate, 48000,
                      "the capture did not load at all, which is what the library cannot afford")
        self.assertEqual(server.status()["rate_mismatch"]["capture_hz"], 48000)
        server.clear_capture()
        self.assertIsNone(server.status()["rate_mismatch"])


class TestAToneChangeNeverSplitsACoefficientSet(unittest.TestCase):
    def test_the_published_set_is_what_the_filter_reads(self):
        stack = tone.ToneStack(44100)
        stack.set("treble", 9.0)
        for band in stack.bands:
            self.assertIsInstance(band.coeffs, tuple)
            self.assertEqual(len(band.coeffs), 5)
            self.assertEqual(band.coeffs, tone._cookbook(band.kind, band.freq, band.q,
                                                         band.db, band.samplerate))


class TestALateReplyNeverReachesTheNextCall(unittest.TestCase):
    class FakeProc:
        def poll(self):
            return None

    def _client(self, *lines):
        client = local_port.EngineClient(python="unused")
        for line in lines:
            client._lines.put_nowait(line)
        return client

    def test_a_stale_reply_is_dropped_and_the_right_one_returned(self):
        client = self._client('{"ok": true, "result": {"which": "old"}, "id": 1}\n',
                              '{"ok": true, "result": {"which": "new"}, "id": 2}\n')
        line = client._read_line(self.FakeProc(), time.monotonic() + 1.0, 2)
        self.assertIn('"new"', line)

    def test_an_engine_that_does_not_echo_ids_is_still_understood(self):
        client = self._client('{"ok": true, "result": {"which": "old"}}\n')
        line = client._read_line(self.FakeProc(), time.monotonic() + 1.0, 7)
        self.assertIn('"old"', line)

    def test_a_queue_of_only_stale_replies_times_out_rather_than_lying(self):
        client = self._client('{"ok": true, "result": {"which": "old"}, "id": 1}\n')
        self.assertIsNone(client._read_line(self.FakeProc(), time.monotonic() + 0.2, 2))


if __name__ == "__main__":
    unittest.main()

class TestTheShippedEngineEchoesTheId(unittest.TestCase):
    """`main` used to run a near-copy of `serve` that never echoed the id.

    The client matches replies by id, so every reply from the real engine looked stale and
    the plugin reported timeouts on a pipe that was working. No in-process test saw it,
    because the tests called `serve`.
    """

    def test_a_reply_from_the_real_entry_point_carries_the_request_id(self):
        import json
        import subprocess
        import sys

        root = Path(__file__).resolve().parent.parent
        asked = chr(10).join(['{"cmd": "status", "id": 41}', '{"cmd": "quit", "id": 42}'])
        done = subprocess.run([sys.executable, "-u", "-m", "amphead.engine_main"],
                              cwd=str(root), input=asked, capture_output=True, text=True,
                              timeout=120)
        lines = [json.loads(line) for line in done.stdout.splitlines() if line.strip()]
        self.assertTrue(lines, done.stderr[-800:])
        self.assertEqual(lines[0].get("id"), 41)
        self.assertEqual(lines[-1].get("id"), 42)


class TestABrokenSettingsFileIsKeptNotErased(unittest.TestCase):
    def _settings(self, text):
        from amphead import settings

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        target = Path(tmp.name) / "settings.json"
        target.write_text(text, encoding="utf-8")
        old = os.environ.get(settings.SETTINGS_ENV)
        os.environ[settings.SETTINGS_ENV] = str(target)
        self.addCleanup(lambda: os.environ.__setitem__(settings.SETTINGS_ENV, old)
                        if old is not None else os.environ.pop(settings.SETTINGS_ENV, None))
        return settings, target

    def test_the_hand_written_values_survive_a_save(self):
        settings, target = self._settings('{"audio": {"blocksize": 128,,}}  // half typed')
        self.assertEqual(settings.load()["audio"]["blocksize"], 256, "defaults were not used")
        aside = target.with_suffix(".json.broken")
        self.assertTrue(aside.exists(), "the unreadable file was not kept")
        self.assertIn("128", aside.read_text(encoding="utf-8"))
        self.assertIn("could not be read", settings.problem())
        settings.save({"gate_db": -55.0})
        self.assertIn("128", aside.read_text(encoding="utf-8"),
                      "the save erased the only copy of the player's settings")

    def test_a_file_that_is_not_an_object_is_reported_not_silently_dropped(self):
        settings, target = self._settings('[1, 2, 3]')
        self.assertEqual(settings.load()["gate_db"], settings.DEFAULTS["gate_db"])
        self.assertIn("not a JSON object", settings.problem())
        self.assertTrue(target.with_suffix(".json.broken").exists())

    def test_a_good_file_says_nothing(self):
        settings, _ = self._settings('{"gate_db": -42.0}')
        self.assertEqual(settings.load()["gate_db"], -42.0)
        self.assertEqual(settings.problem(), "")


class TestACallbackFailureReachesTheVerdict(unittest.TestCase):
    def test_the_problem_is_named_in_the_readout(self):
        from amphead import verdict

        state = {"open": True, "callback_errors": 3, "armed": True, "master_db": -6.0,
                 "samplerate": 44100, "blocksize": 256}
        reading = {"peak_in": 0.2, "peak_out": 0.2, "master_db": -6.0, "underflows": 0}
        said = verdict.judge(state, reading)
        self.assertTrue(any("callback failed 3 time" in p for p in said["problems"]),
                        said["problems"])


class TestALateBlockReachesTheVerdict(unittest.TestCase):
    def test_shaky_playback_is_named_where_the_levels_are_read(self):
        from amphead import verdict

        state = {"open": True, "late": 17, "armed": True, "master_db": -6.0,
                 "samplerate": 44100, "blocksize": 64}
        reading = {"peak_in": 0.2, "peak_out": 0.2, "master_db": -6.0, "underflows": 0}
        said = verdict.judge(state, reading)
        self.assertTrue(any("17 block(s) reached the driver too late" in p
                            for p in said["problems"]), said["problems"])
        self.assertTrue(any("tune" in p for p in said["problems"]))


class TestTheFinishedTakeSaysWhatTheLiveStateSays(unittest.TestCase):
    def test_the_two_reports_do_not_drift(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        take = record.Take(Path(tmp.name) / "one.wav", samplerate=44100, channels=2)
        take.open()
        take.push(np.zeros((128, 2), dtype=np.float32))
        take.stop()
        self.assertEqual(take.summary(), take.state())
        self.assertEqual(take.summary()["channels"], 2)
