"""The device path: one stream, or a queue that costs latency.

These run against a stand-in for sounddevice rather than a device, because the thing
worth pinning is the *shape* of the open: duplex first, no queue, and a mode name
that admits it when the fallback is used.
"""

import sys
import types
import unittest

try:
    import numpy as np
except ModuleNotFoundError:  # pragma: no cover - engine modules need numpy
    raise unittest.SkipTest("numpy is not installed")

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


def fake_sounddevice(refuse_duplex=False, refuse_invalid_device=False):
    # PortAudio reports every device position, including the empty ones before the
    # interface's kernel-streaming endpoints. Tests keep those positions so a stale
    # index looks exactly like it does on Windows.
    devices = [{"name": "unused", "hostapi": 0, "max_input_channels": 0,
                "max_output_channels": 0} for _ in range(39)]
    devices[2] = {"name": "Microphone (USB)", "hostapi": 0, "max_input_channels": 1,
                  "max_output_channels": 0}
    devices[37] = {"name": "Line (Audio Input)", "hostapi": 1, "max_input_channels": 2,
                   "max_output_channels": 0}
    devices[38] = {"name": "Speakers (Audio Output)", "hostapi": 1, "max_input_channels": 0,
                   "max_output_channels": 2}
    sd = types.SimpleNamespace()
    sd.query_hostapis = lambda: [{"name": "MME"}, {"name": "Windows WDM-KS"}]
    sd.query_devices = lambda index=None: devices[index] if index is not None else devices
    sd.default = types.SimpleNamespace(device=(37, 38))
    sd.WasapiSettings = lambda **kwargs: types.SimpleNamespace(**kwargs)

    def Stream(**kwargs):
        if refuse_duplex:
            raise RuntimeError("duplex refused")
        if refuse_invalid_device and kwargs.get("device") == (2, 38):
            raise RuntimeError("Error opening InputStream: Invalid device [PaErrorCode -9996]")
        return FakeStream(**kwargs)

    def InputStream(**kwargs):
        if refuse_invalid_device and kwargs.get("device") == 2:
            raise RuntimeError("Error opening InputStream: Invalid device [PaErrorCode -9996]")
        return FakeInput(**kwargs)

    def OutputStream(**kwargs):
        return FakeOutput(**kwargs)

    sd.Stream = Stream
    sd.InputStream = InputStream
    sd.OutputStream = OutputStream
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

    def test_a_cabinet_is_primed_before_the_callback_and_primes_for_each_block_size(self):
        """The partition bank depends on the block size. Building it in the callback is a
        dropout, and leaving the old bank in place makes a smaller block run padded to the
        old size. Both loading and opening must prime at the size the stream will use."""
        sys.modules["sounddevice"] = fake_sounddevice()

        class SpyCab:
            def __init__(self):
                self.calls = []

            def prime(self, frames):
                self.calls.append(frames)

        eng = self.engine()
        cab = SpyCab()
        eng.update(cab=cab)
        self.assertEqual(cab.calls, [512], "loading did not prime at the current size")
        try:
            eng.start()
        finally:
            eng.stop()
        self.assertEqual(cab.calls, [512, 512], "opening did not prime again off the thread")
        cab.calls.clear()
        eng.blocksize = 128
        try:
            eng.start()
        finally:
            eng.stop()
        self.assertEqual(cab.calls, [128], "the new block size used the old partition bank")

    def test_a_stale_device_number_is_replaced_before_the_open(self):
        """A saved slot with no input channels is not a device, it is a hole. PortAudio's
        answer to a hole is -9996, so pick the live pair before asking it."""
        devices = fake_sounddevice()
        devices.query_devices()[2]["max_input_channels"] = 0
        sys.modules["sounddevice"] = devices
        eng = self.engine()
        eng.input_device = 2
        try:
            mode = eng.start()
            self.assertEqual(mode, "wdm-ks-exclusive")
            self.assertEqual((eng.input_device, eng.output_device), (37, 38))
            self.assertTrue(eng.running)
        finally:
            eng.stop()

    def test_an_invalid_device_error_reopens_once_with_a_fresh_pair(self):
        """The list can shift between the check and the open. One fresh pick, one reopen,
        then report the driver error if it still refuses."""
        sys.modules["sounddevice"] = fake_sounddevice(refuse_invalid_device=True)
        eng = self.engine()
        eng.input_device = 2
        try:
            mode = eng.start()
            self.assertEqual(mode, "wdm-ks-exclusive")
            self.assertEqual((eng.input_device, eng.output_device), (37, 38))
            self.assertTrue(eng.running)
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
