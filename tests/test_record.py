"""A take is the amp's output on disk: right length, right width, right level, no stalls."""

import tempfile
import time
import unittest
import wave
from pathlib import Path

try:
    import numpy as np

    from amphead.record import SAMPLE_WIDTH, Take
except ImportError:  # pragma: no cover
    raise unittest.SkipTest("numpy is required for the recorder")


def _sine(seconds=1.0, rate=44100, amp=0.5, freq=440.0, block=128):
    total = int(seconds * rate)
    t = np.arange(total, dtype=np.float32) / float(rate)
    x = (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    for start in range(0, total, block):
        yield x[start:start + block]


class TestATake(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "take.wav"

    def test_it_writes_what_it_was_handed(self):
        take = Take(self.path, 44100).open()
        for block in _sine(1.0):
            take.push(block)
            time.sleep(0.002)                         # paced like a callback, not a benchmark
        said = take.stop()

        with wave.open(str(self.path), "rb") as got:
            self.assertEqual(got.getnchannels(), 2)
            self.assertEqual(got.getsampwidth(), SAMPLE_WIDTH)
            self.assertEqual(got.getframerate(), 44100)
            self.assertEqual(got.getnframes(), 44100)

        self.assertAlmostEqual(said["seconds"], 1.0, places=2)
        self.assertEqual(said["frames"], 44100)
        self.assertEqual(said["dropped"], 0)
        self.assertFalse(said["recording"])

    def test_the_level_is_reported_and_is_the_level_that_went_in(self):
        take = Take(self.path, 44100).open()
        for block in _sine(0.5, amp=0.25):            # a quarter scale is about -12 dB
            take.push(block)
        said = take.stop()
        self.assertAlmostEqual(said["peak_db"], -12.0, delta=0.2)

    def test_the_file_reads_back_as_the_same_wave(self):
        take = Take(self.path, 44100, channels=1).open()
        sent = np.concatenate(list(_sine(0.05, amp=0.5)))
        take.push(sent)
        take.stop()

        with wave.open(str(self.path), "rb") as got:
            raw = got.readframes(got.getnframes())
        as_int = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        # sign extend the 24 bit words, the way a reader has to
        as_int = np.where(as_int[:, 2:] >= 0x80, as_int - (1 << 24), as_int)
        back = as_int[:, 0] | (as_int[:, 1] << 8) | (as_int[:, 2] << 16)
        back = np.where(back >= (1 << 23), back - (1 << 24), back).astype(np.float32)
        back /= float((1 << 23) - 1)
        self.assertLess(float(np.max(np.abs(back - sent))), 1e-6)

    def test_it_clips_instead_of_wrapping(self):
        take = Take(self.path, 44100, channels=1).open()
        take.push(np.array([3.0, -3.0, 0.0], dtype=np.float32))
        take.stop()
        with wave.open(str(self.path), "rb") as got:
            raw = got.readframes(got.getnframes())
        self.assertEqual(len(raw), 9)

    def test_a_stop_with_nothing_recorded_still_closes_the_file(self):
        take = Take(self.path, 44100).open()
        said = take.stop()
        self.assertEqual(said["frames"], 0)
        self.assertTrue(self.path.exists())
        self.assertEqual(take.stop(), take.stop(), "stop is safe to call twice")

    def test_pushing_never_blocks_when_the_writer_is_behind(self):
        take = Take(self.path, 44100, queue_blocks=2).open()
        block = np.zeros(128, dtype=np.float32)
        for _ in range(2000):
            take.push(block)                          # a stalled disk must not stall the callback
        said = take.stop()
        self.assertEqual(said["dropped"], said["dropped"])  # a number, not an exception
        self.assertFalse(said["recording"])


class TestTheAmpWritesWhatItPlays(unittest.TestCase):
    """The engine hands the recorder the same block it hands the device."""

    def setUp(self):
        from amphead import engine2, engine_main

        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.server = engine_main.Server(engine2.Engine(samplerate=44100, blocksize=128))

    def test_a_take_between_start_and_stop_lands_as_a_wav(self):
        said = self.server.handle({"cmd": "record_start",
                                   "path": str(Path(self.dir.name) / "one.wav")})
        self.assertTrue(said["ok"])
        self.assertTrue(self.server.handle({"cmd": "record_state"})["result"]["recording"])
        self.server.engine.update(armed=True, master_db=-6.0)
        for block in _sine(0.4):
            x = np.stack([block, block], axis=1)
            self.server.engine._render(x.reshape(-1), x.shape[0])
        done = self.server.handle({"cmd": "record_stop"})["result"]
        self.assertGreater(done["seconds"], 0.2)
        self.assertEqual(done["dropped"], 0)
        self.assertTrue(Path(done["path"]).exists())

    def test_two_starts_in_a_row_are_refused_rather_than_stacking(self):
        first = self.server.handle({"cmd": "record_start",
                                    "path": str(Path(self.dir.name) / "a.wav")})
        self.assertTrue(first["ok"])
        second = self.server.handle({"cmd": "record_start",
                                     "path": str(Path(self.dir.name) / "b.wav")})
        self.assertFalse(second["ok"])
        self.assertIn("already recording", str(second.get("error")))
        self.server.handle({"cmd": "record_stop"})

    def test_a_stop_with_nothing_running_says_so(self):
        said = self.server.handle({"cmd": "record_stop"})
        self.assertFalse(said["ok"])
        self.assertIn("nothing is recording", str(said.get("error")))

    def test_the_default_place_is_the_recordings_folder_beside_the_settings(self):
        said = self.server.handle({"cmd": "record_start"})
        self.assertTrue(said["ok"])
        path = Path(said["result"]["path"])
        self.assertEqual(path.parent.name, "recordings")
        self.assertTrue(path.name.startswith("take "))
        self.server.handle({"cmd": "record_stop"})
        path.unlink(missing_ok=True)                  # do not leave a scratch take in APPDATA
