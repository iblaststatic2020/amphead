"""Headless proof of the audio device decision path.

PortAudio needs real hardware, so the live stream open itself cannot run in CI.
Everything *around* that open can, and it is exactly where the per-machine
behavior lives: picking a usable pair, re-picking when a saved slot is stale or
the two devices speak different host APIs, falling back to a split stream, and
retrying once when the list changes between the check and the open. These tests
drive `Engine.start(_sd=fake)` with an injected fake sounddevice so every one of
those branches is exercised on every CI OS, leaving only the literal
`sd.Stream()` syscall unproven.

The Windows device heuristics run on every OS by patching `sys.platform` to
win32; the off-Windows default-pair branch is a dedicated pure test.
"""
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

import numpy as np

from amphead.engine2 import Engine, choose_devices


class InvalidDevice(RuntimeError):
    pass


class FakeStream:
    """Enough of a sounddevice stream for the decision logic to drive."""

    def __init__(self):
        self._active = True
        self.started = False

    def start(self):
        self.started = True

    def stop(self):
        self._active = False

    def close(self):
        self._active = False

    @property
    def active(self):
        return self._active


class FakeSD:
    """Minimal sounddevice surface: query_* plus the three open calls.

    `fail_open` is a hook the test sets: it gets (kind, kwargs) and either
    returns a FakeStream or raises. That is how a test tells this machine which
    streams fail, which is the whole point of the suite.
    """

    class WasapiSettings:
        def __init__(self, exclusive=False):
            self.exclusive = exclusive

    def __init__(self, hostapis, devices, default=(-1, -1)):
        self.hostapis = hostapis
        self.devices = devices
        self.default = SimpleNamespace(device=default)
        self.opens = []            # (kind, kwargs) in open order
        self.fail_open = None      # callable(kind, kwargs) -> FakeStream or raise

    def query_hostapis(self):
        return self.hostapis

    def query_devices(self, index=-1):
        if index == -1:
            return self.devices
        return self.devices[int(index)]

    def _open(self, kind, kwargs):
        self.opens.append((kind, kwargs))
        if self.fail_open is not None:
            return self.fail_open(kind, kwargs)
        return FakeStream()

    def Stream(self, **kwargs):
        return self._open("Stream", kwargs)

    def InputStream(self, **kwargs):
        return self._open("InputStream", kwargs)

    def OutputStream(self, **kwargs):
        return self._open("OutputStream", kwargs)


def dev(name, hostapi, i, o):
    return {"name": name, "hostapi": hostapi,
            "max_input_channels": i, "max_output_channels": o}


# Two host-API sets used across the tests.
WDM = [{"name": "Windows WASAPI"}, {"name": "Windows MME"},
       {"name": "Windows WDM-KS"}]
MSAME = [{"name": "Windows WASAPI"}, {"name": "Windows WDM-KS"}]

# One matched AudioBox-ish interface under WDM-KS: the pair `choose_devices` finds.
BOX_PAIR = [dev("AudioBox 96 Audio Input", 2, 1, 0),
            dev("AudioBox 96 Audio Output", 2, 0, 2)]
# Same interface but hostapi indexes that exist under the 2-entry MSAME set,
# for the off-Windows default-pair path.
DWDEPTH = [dev("AudioBox 96 Audio Input", 1, 1, 0),
           dev("AudioBox 96 Audio Output", 1, 0, 2)]
# That same interface plus a WASAPI-only input and an MME-only output, for the
# host-API mismatch test (the input at 2 and output at 3 must be re-picked).
MIXED = BOX_PAIR + [dev("USB Mic WASAPI", 0, 1, 0), dev("DAC MME", 1, 0, 2)]


class ChooseDevicesTest(unittest.TestCase):
    def test_non_windows_uses_the_default_pair(self):
        sd = FakeSD(MSAME, BOX_PAIR, default=(7, 8))
        with mock.patch("sys.platform", "linux"):
            self.assertEqual(choose_devices(sd), (7, 8, "default"))

    def test_windows_prefers_a_matched_kernel_pair(self):
        sd = FakeSD(WDM, BOX_PAIR, default=(9, 9))
        with mock.patch("sys.platform", "win32"):
            self.assertEqual(choose_devices(sd), (0, 1, "wdm-ks"))

    def test_windows_skips_virtual_endpoints(self):
        virtual = [dev("CABLE Input (VB-Audio Virtual Cable)", 2, 1, 0),
                   dev("CABLE Output (VB-Audio Virtual Cable)", 2, 0, 2),
                   dev("Line In", 2, 1, 0), dev("Line Out", 2, 0, 2)]
        sd = FakeSD(WDM, virtual, default=(9, 9))
        with mock.patch("sys.platform", "win32"):
            # The virtual cable is skipped; the real Line In/Out pair still wins.
            self.assertEqual(choose_devices(sd), (2, 3, "wdm-ks"))

    def test_windows_with_no_kernel_pair_uses_the_default(self):
        only = [dev("USB Mic WASAPI", 0, 1, 0)]
        sd = FakeSD(WDM, only, default=(5, 6))
        with mock.patch("sys.platform", "win32"):
            self.assertEqual(choose_devices(sd), (5, 6, "default"))


class StartDevicesTest(unittest.TestCase):
    def _engine(self, **kw):
        e = Engine(samplerate=48000, blocksize=128)
        return e

    def test_auto_picks_a_pair_when_nothing_is_saved(self):
        e = self._engine()
        sd = FakeSD(WDM, BOX_PAIR)
        with mock.patch("sys.platform", "win32"):
            mode = e.start(_sd=sd)
        self.assertEqual(mode, "wdm-ks-exclusive")
        self.assertEqual((e.input_device, e.output_device), (0, 1))
        self.assertIsNotNone(e._duplex)
        self.assertIsNone(e._last_error)
        # Duplex opens first and wins: only Stream calls, no split fallback.
        self.assertTrue(all(k == "Stream" for k, _ in sd.opens))
        self.assertTrue(sd.opens)

    def test_a_stale_saved_slot_is_repicked(self):
        e = self._engine()
        e.input_device, e.output_device = 99, 100
        sd = FakeSD(WDM, BOX_PAIR)
        with mock.patch("sys.platform", "win32"):
            mode = e.start(_sd=sd)
        self.assertEqual(mode, "wdm-ks-exclusive")
        self.assertEqual((e.input_device, e.output_device), (0, 1))

    def test_host_api_mismatch_is_repicked_to_a_matched_pair(self):
        e = self._engine()
        e.input_device, e.output_device = 2, 3    # WASAPI in, MME out: no duplex
        sd = FakeSD(WDM, MIXED)
        with mock.patch("sys.platform", "win32"):
            mode = e.start(_sd=sd)
        self.assertEqual(mode, "wdm-ks-exclusive")
        self.assertEqual((e.input_device, e.output_device), (0, 1))

    def test_split_stream_is_the_fallback_when_duplex_is_refused(self):
        e = self._engine()

        def fail_duplex(kind, kw):
            if kind == "Stream":
                raise RuntimeError("Host error -9983: host refusing duplex")
            return FakeStream()

        sd = FakeSD(WDM, BOX_PAIR)
        sd.fail_open = fail_duplex
        with mock.patch("sys.platform", "win32"):
            mode = e.start(_sd=sd)
        self.assertEqual(mode, "wdm-ks-exclusive+queue")
        self.assertIsNone(e._duplex)
        self.assertIsNotNone(e._in_stream)
        self.assertIsNotNone(e._out_stream)

    def test_no_stream_at_all_raises_without_leaking(self):
        e = self._engine()

        def fail_everything(kind, kw):
            raise RuntimeError("no device")

        sd = FakeSD(WDM, BOX_PAIR)
        sd.fail_open = fail_everything
        with mock.patch("sys.platform", "win32"):
            with self.assertRaisesRegex(RuntimeError, "no device"):
                e.start(_sd=sd)
        self.assertIsNone(e._duplex)
        self.assertIsNone(e._in_stream)

    def test_invalid_device_after_the_check_retries_once_with_a_fresh_pair(self):
        """The list changed between the check and the open, so open fails -9996.

        The retry should re-pick. The fake mutates its device list on the very
        first open (simulating the card appearing), so the second pick differs
        from the first and the reopen succeeds.
        """
        e = self._engine()
        sd = FakeSD(WDM, BOX_PAIR, default=(6, 5))
        swapped = False

        def flaky(kind, kw):
            nonlocal swapped
            if not swapped:
                # Card A (chosen at 0,1) is there for the first pick; swap it for
                # card B before the first open, so the reopen sees a genuinely
                # different pair. A WASAPI dummy at index 0 shifts card B's kernel
                # pair to 1,2, which differs from the saved (0,1).
                swapped = True
                sd.devices = [dev("USB Mic WASAPI", 0, 1, 0),
                              dev("Scarlett Solo In", 2, 1, 0),
                              dev("Scarlett Solo Out", 2, 0, 2)]
            # Refuse every request on card A's pair; only the re-picked pair (1,2)
            # may open. First call requests (0,1) throughout and fails all of its
            # fallbacks; the -9996 retry re-picks to (1,2), whose duplex then opens.
            if kind == "Stream" and kw.get("device") == (1, 2):
                return FakeStream()
            raise InvalidDevice("Error opening: Invalid device -9996")

        sd.fail_open = flaky
        with mock.patch("sys.platform", "win32"):
            mode = e.start(_sd=sd)
        # The reopen picked card B's pair at 1,2 and opened a duplex on it.
        self.assertEqual(mode, "wdm-ks-exclusive")
        self.assertEqual((e.input_device, e.output_device), (1, 2))
        self.assertIsNotNone(e._duplex)

    def test_invalid_device_with_no_second_pick_raises_cleanly(self):
        e = self._engine()

        def always_invalid(kind, kw):
            raise InvalidDevice("Invalid device -9996")

        sd = FakeSD(WDM, BOX_PAIR, default=(6, 5))
        sd.fail_open = always_invalid
        with mock.patch("sys.platform", "win32"):
            with self.assertRaisesRegex(RuntimeError, "Invalid device"):
                e.start(_sd=sd)

    def test_off_windows_start_uses_the_saved_default_pair(self):
        e = self._engine()
        e.input_device, e.output_device = 0, 1
        sd = FakeSD(MSAME, DWDEPTH, default=(0, 1))
        with mock.patch("sys.platform", "linux"):
            mode = e.start(_sd=sd)
        # Off Windows the engine trusts the default pair and still opens duplex.
        self.assertIsNotNone(mode)
        self.assertIsNotNone(e._duplex)


if __name__ == "__main__":
    unittest.main()
