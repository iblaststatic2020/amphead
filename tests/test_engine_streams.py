"""The device path: one stream, or a queue that costs latency.

These run against a stand-in for sounddevice rather than a device, because the thing
worth pinning is the *shape* of the open: duplex first, no queue, and a mode name
that admits it when the fallback is used.
"""

import sys
import types
import unittest

import numpy as np

from amphead import engine2


class FakeStream:
    """Records how it was opened, then behaves like a live duplex stream."""

    opened = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.active = False
        self.latency = (0.005, 0.005)
        FakeStream.opened.append(self)

    def start(self):
        self.active = True

    def stop(self):
        self.active = False

    def close(self):
        self.active = False


class FakeInput(FakeStream):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.latency = 0.005


class FakeOutput(FakeStream):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.latency = 0.005


def fake_sounddevice(refuse_duplex=False):
    devices = {
        37: {"name": "Line (Audio Input)", "hostapi": 0, "max_input_channels": 2,
             "max_output_channels": 0},
        38: {"name": "Speakers (Audio Output)", "hostapi": 0, "max_input_channels": 0,
             "max_output_channels": 2},
    }
    sd = types.SimpleNamespace()
    sd.query_hostapis = lambda: [{"name": "Windows WDM-KS"}]
    sd.query_devices = lambda index=None: devices[index] if index is not None else list(devices.values())
    sd.WasapiSettings = lambda **kwargs: types.SimpleNamespace(**kwargs)

    def Stream(**kwargs):
        if refuse_duplex:
            raise RuntimeError("duplex refused")
        return FakeStream(**kwargs)

    sd.Stream = Stream
    sd.InputStream = FakeInput
    sd.OutputStream = FakeOutput
    return sd


class TestTheDuplexOpen(unittest.TestCase):
    def setUp(self):
        FakeStream.opened = []
        self._saved = sys.modules.get("sounddevice")

    def tearDown(self):
        if self._saved is None:
            sys.modules.pop("sounddevice", None)
        else:
            sys.modules["sounddevice"] = self._saved

    def engine(self):
        eng = engine2.Engine(samplerate=44100, blocksize=512)
        eng.input_device, eng.output_device = 37, 38
        return eng

    def test_one_stream_and_no_queue(self):
        sys.modules["sounddevice"] = fake_sounddevice()
        eng = self.engine()
        try:
            mode = eng.start()
            self.assertEqual(mode, "wdm-ks-exclusive")
            self.assertIsNotNone(eng._duplex)
            self.assertIsNone(eng._in_stream, "the fallback streams should not be open")
            self.assertEqual(len(eng._frames), 0, "nothing may stand in the signal path")
            self.assertAlmostEqual(eng.latency_ms, 10.0, places=3)
            self.assertTrue(eng.running)
        finally:
            eng.stop()
        self.assertFalse(eng.running)

    def test_the_tight_latency_is_asked_for_first(self):
        sys.modules["sounddevice"] = fake_sounddevice()
        eng = self.engine()
        try:
            eng.start()
        finally:
            eng.stop()
        asked = FakeStream.opened[0].kwargs["latency"]
        self.assertAlmostEqual(asked, engine2.TARGET_LATENCY_MS / 1000.0, places=6)

    def test_a_driver_that_refuses_duplex_still_plays(self):
        sys.modules["sounddevice"] = fake_sounddevice(refuse_duplex=True)
        eng = self.engine()
        try:
            mode = eng.start()
            self.assertTrue(mode.endswith("+queue"), mode)
            self.assertIsNone(eng._duplex)
            self.assertTrue(eng.running)
            # The cushion is delay, so it is small on purpose.
            self.assertEqual(len(eng._frames), engine2.PRIME_BLOCKS)
            self.assertLessEqual(engine2.PRIME_BLOCKS, 2)
        finally:
            eng.stop()

    def test_the_duplex_callback_writes_both_channels(self):
        eng = self.engine()
        eng.update(armed=False)
        indata = np.zeros((512, 2), dtype=np.float32)
        outdata = np.full((512, 2), 9.0, dtype=np.float32)
        eng._on_duplex(indata, outdata, 512, None, None)
        # Disarmed writes silence, not the stale contents of the buffer.
        self.assertEqual(float(np.max(np.abs(outdata))), 0.0)
        self.assertEqual(eng.meter.blocks, 1)


if __name__ == "__main__":
    unittest.main()
