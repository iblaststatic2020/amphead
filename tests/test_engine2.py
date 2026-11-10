"""The chain without a device: gain staging, clamps, and the safety floor."""

import time
import unittest

try:
    import numpy as np
    from amphead.engine2 import (ARM_MASTER_DB, SAFE_MASTER_DB, Chain, Engine,
                                 db_to_gain, gain_to_db, process_chain)
except ModuleNotFoundError:  # pragma: no cover - the engine is an optional extra
    np = None

if np is None:  # pragma: no cover
    raise unittest.SkipTest("numpy is not installed")


class FakeAmp:
    """A stand-in for a capture: gain of two, plus a one-sample delay."""

    def __init__(self, gain: float = 2.0) -> None:
        self.gain = gain
        self.previous = 0.0

    def process(self, x):
        out = np.concatenate(([self.previous], x[:-1])) * self.gain
        if x.size:
            self.previous = float(x[-1])
        return out.astype(np.float32)


class FakeCab:
    def __init__(self, gain: float = 0.5) -> None:
        self.gain = gain

    def process(self, x):
        return (x * self.gain).astype(np.float32)


def ramp(n=256):
    return np.linspace(-0.5, 0.5, n, dtype=np.float32)


@unittest.skipIf(np is None, "numpy is not installed")
class BypassTest(unittest.TestCase):
    def test_a_switched_out_stage_is_skipped_not_forgotten(self):
        """The point of a switch: take the amp out of the path, keep the amp."""
        import numpy as np

        from amphead.engine2 import Chain, process_chain

        chain = Chain(amp=FakeAmp(), amp_on=False, master_db=0.0)
        x = np.linspace(-0.2, 0.2, 64).astype(np.float32)
        np.testing.assert_allclose(process_chain(x, chain), x, atol=1e-6)
        chain.amp_on = True
        self.assertFalse(np.allclose(process_chain(x, chain), x))

    def test_the_chain_is_wired_capture_then_tone_then_cabinet(self):
        import numpy as np

        from amphead.engine2 import Chain, process_chain

        x = np.linspace(-0.2, 0.2, 64).astype(np.float32)
        both = process_chain(x, Chain(amp=FakeAmp(), cab=FakeCab(), master_db=0.0))
        amp_first = process_chain(process_chain(x, Chain(amp=FakeAmp(), master_db=0.0)),
                                  Chain(cab=FakeCab(), master_db=0.0))
        np.testing.assert_allclose(amp_first[1:], both[1:], atol=1e-6)


class ChainTest(unittest.TestCase):
    def test_disarmed_is_silence(self):
        engine = Engine()
        engine.update(amp=FakeAmp(), master_db=0.0)
        self.assertTrue(np.all(engine.render(ramp()) == 0.0))

    def test_armed_at_the_master_floor_is_true_silence(self):
        engine = Engine()
        engine.update(armed=True, amp=FakeAmp(), master_db=SAFE_MASTER_DB)
        y = engine.render(ramp())
        # -60 dB is the bottom of the fader, and the bottom has to be off. A fader that
        # still passes -60 dB leaves a ghost note on a loud amp with the knob all the way down.
        self.assertTrue(np.all(y == 0.0))

    def test_master_just_above_the_floor_still_passes_signal(self):
        engine = Engine()
        engine.update(armed=True, amp=FakeAmp(), master_db=SAFE_MASTER_DB + 0.1)
        y = engine.render(ramp())
        self.assertGreater(float(np.max(np.abs(y))), 0.0,
                           "only the exact floor is off; the rest of the fader still works")

    def test_gain_applies_in_the_documented_order(self):
        chain = Chain(armed=True, master_db=0.0, input_gain_db=6.0, output_gain_db=-6.0,
                      amp=FakeAmp(2.0), cab=FakeCab(0.5))
        x = ramp()
        y = process_chain(x, chain)
        expected = x * db_to_gain(6.0) * 2.0 * 0.5 * db_to_gain(-6.0)
        # FakeAmp delays by one sample, so compare against the same delay.
        self.assertLess(float(np.max(np.abs(y[1:] - expected[:-1]))), 1e-6)

    def test_input_gain_drives_the_amp_and_output_gain_only_sets_the_level(self):
        """The two new knobs have to be in different places, or they are the same knob.

        A hard clipper stands in for a capture: driven from a quiet DI it is nearly linear,
        and +12 dB into it flattens the peaks. Undoing that with -12 dB after it leaves a
        different waveform at the same level, which is what "input gain changes tone" means.
        """
        class Clipper:
            def process(self, x):
                return np.clip(x * 8.0, -1.0, 1.0).astype(np.float32)

        x = ramp()
        clean = process_chain(x, Chain(armed=True, master_db=0.0, amp=Clipper()))
        driven = process_chain(
            x, Chain(armed=True, master_db=0.0, amp=Clipper(),
                     input_gain_db=12.0, output_gain_db=-12.0))
        self.assertFalse(np.allclose(clean, driven, atol=1e-3),
                         "input and output gain are in the same place in the chain")
        # Both are still a level: neither may push the block past the clamp on its own.
        self.assertLessEqual(float(np.max(np.abs(driven))), 1.0)

    def test_the_trim_knobs_stop_at_24_db(self):
        """Past 24 dB the knob is a distortion control, so it stops answering like a level."""
        engine = Engine()
        engine.set_gain("input_gain_db", 60.0)
        engine.set_gain("output_gain_db", -60.0)
        self.assertEqual(engine.chain.input_gain_db, 24.0)
        self.assertEqual(engine.chain.output_gain_db, -24.0)

    def test_output_is_clamped(self):
        chain = Chain(armed=True, master_db=24.0, amp=FakeAmp(20.0))
        y = process_chain(ramp(), chain)
        self.assertLessEqual(float(np.max(np.abs(y))), 1.0)

    def test_nan_and_inf_never_escape(self):
        chain = Chain(armed=True, master_db=0.0, amp=FakeAmp(1.0))
        x = np.array([0.1, np.nan, np.inf, -np.inf], dtype=np.float32)
        y = process_chain(x, chain)
        self.assertTrue(np.all(np.isfinite(y)))

    def test_panic_mutes_and_disarms(self):
        engine = Engine()
        engine.update(armed=True, master_db=0.0, amp=FakeAmp())
        engine.panic()
        self.assertFalse(engine.chain.armed)
        self.assertEqual(engine.chain.master_db, SAFE_MASTER_DB)
        self.assertTrue(np.all(engine.render(ramp()) == 0.0))

    def test_panic_is_adopted_by_the_next_render_not_by_the_control_thread(self):
        """Hard mute is the one move that cannot depend on a control-thread write.

        The control thread asks; the audio thread answers. If panic wrote the gains
        itself it would race a callback already holding the old chain, so the event
        is set and the next block adopts the muted chain atomically.
        """
        engine = Engine(samplerate=44100, blocksize=128)
        engine.update(armed=True, master_db=0.0, amp=FakeAmp(1.0))
        engine.panic()
        self.assertTrue(engine._gain_sync.is_set(),
                        "panic must hand the gain reset to the audio thread")
        y = engine._render(ramp(256).reshape(-1, 1), 256)
        self.assertTrue(np.all(y == 0.0))
        self.assertFalse(engine._gain_sync.is_set())
        self.assertEqual(engine._gain_last, engine._linear_gains(engine.chain))

    def test_a_park_that_hits_a_busy_lock_is_retried_not_lost(self):
        """The quiet trigger must survive a capture load that owns the update lock."""
        engine = Engine(samplerate=48000, blocksize=128)
        engine.update(armed=True, master_db=0.0, amp=FakeAmp(1.0))
        park = engine._park
        park._last = park._clock() - (park.idle_seconds + 1)
        with engine._lock:
            engine._render(np.zeros((128, 1), dtype=np.float32), 128)
            self.assertTrue(engine._park_pending, "the park should wait its turn")
            self.assertEqual(engine.chain.master_db, 0.0)
        engine._render(np.zeros((128, 1), dtype=np.float32), 128)
        self.assertFalse(engine._park_pending)
        self.assertEqual(engine.chain.master_db, SAFE_MASTER_DB)
        self.assertTrue(engine._parked_by_idle)

    def test_the_first_arm_fades_in_too(self):
        """P2: the fade-in only ran after a fade-out on the same stream.

        The very first arm of a session was a full-gain step in one block, the
        exact click the fade exists to prevent. Every arm edge must fade.
        """
        engine = Engine(samplerate=44100, blocksize=256)
        engine.update(armed=True, master_db=0.0, cab=FakeCab(1.0))
        # A fresh stream has never been armed, so _was_armed is False already.
        x = np.full((256, 1), 0.25, dtype=np.float32)
        first = engine._render(x, 256)
        self.assertEqual(float(first[0]), 0.0, "the arm edge must start at silence")
        second = engine._render(x, 256)
        self.assertGreater(float(second[0]), 0.05,
                           "the settled block must not still be faded in")

    def test_a_close_under_a_busy_lock_mutes_on_the_next_block(self):
        """P2: a mute that cannot take the lock is retried on a later block, not dropped."""
        engine = Engine(samplerate=48000, blocksize=128)
        engine.update(armed=True, master_db=0.0, amp=FakeAmp(1.0))
        with engine._lock:
            self.assertFalse(engine._mute_chain())
            self.assertTrue(engine._mute_pending, "the mute should wait its turn")
            self.assertTrue(engine.chain.armed, "it must not write from a stale chain")
        y = engine._render(np.zeros((128, 1), dtype=np.float32), 128)
        self.assertTrue(np.all(y == 0.0))
        self.assertEqual(engine.chain.master_db, SAFE_MASTER_DB)
        self.assertFalse(engine._mute_pending)
        self.assertFalse(engine.chain.armed)

    def test_arm_does_not_move_the_master_by_itself(self):
        engine = Engine()
        engine.update(armed=True)
        self.assertEqual(engine.chain.master_db, SAFE_MASTER_DB)

    def test_bad_gain_name_is_refused(self):
        with self.assertRaises(ValueError):
            Engine().set_gain("volume", 3.0)

    def test_db_round_trip(self):
        for db in (-60.0, -6.0, 0.0, 3.5):
            self.assertAlmostEqual(gain_to_db(db_to_gain(db)), db, places=9)

    def test_render_block_size_does_not_change_the_result(self):
        engine = Engine(blocksize=128)
        engine.update(armed=True, master_db=-12.0, amp=FakeAmp(1.5), cab=FakeCab(0.9))
        x = ramp(4096)
        a = engine.render(x, 128)
        engine.update(amp=FakeAmp(1.5))
        b = engine.render(x, 512)
        self.assertLess(float(np.max(np.abs(a - b))), 1e-5)


    def test_render_refuses_to_share_the_live_chain(self):
        """Offline render and the callback share gate, model, biquad and cab state."""
        engine = Engine(blocksize=128)
        engine._duplex = type("Live", (), {"active": True})()
        with self.assertRaises(RuntimeError):
            engine.render(ramp())


class TimingTest(unittest.TestCase):
    def test_late_counts_work_after_process_chain(self):
        """A click-path stall is inside the callback even though the chain was fast."""
        engine = Engine(samplerate=48000, blocksize=128)
        engine.update(armed=False, metronome=True)

        def slow_click(frames, chain):
            time.sleep(0.004)
            return None

        engine._click_block = slow_click
        indata = np.zeros((128, 2), dtype=np.float32)
        outdata = np.zeros((128, 2), dtype=np.float32)
        engine._on_duplex(indata, outdata, 128, None, None)
        self.assertEqual(engine.counters["late"], 1)
        self.assertGreaterEqual(engine.counters["peak_ms"], 4.0)
        self.assertGreater(engine.counters["chain_ms"], 0.0)
        self.assertEqual(engine.counters["blocks"], 1)

    def test_start_late_uses_a_proportional_allowance(self):
        """P2: a fixed 2 ms threshold false-positives on bursty hosts at big blocks.

        At a 4096 sample block @ 48 kHz the period is ~85 ms, so a scheduler that
        wakes tens of milliseconds behind the previous callback was flagged though
        it was on time. The allowance is max(2 ms, 25% of the period), so only a
        genuinely late block counts; worst_gap_ms keeps the actual deficit either way.
        """
        engine = Engine(samplerate=48000, blocksize=4096)
        period_ms = 4096 / 48000 * 1000.0
        allow_ms = max(2.0, 0.25 * period_ms)
        self.assertGreater(allow_ms, 2.0, "the proportional allowance must dominate here")

        # Woke 60 ms after the deadline: comfortably on time at this period (~85 ms).
        engine._last_cb_start = time.perf_counter() - 0.06
        engine._mark_start(4096)
        self.assertEqual(engine._meter.start_late, 0)
        self.assertEqual(engine._meter.worst_gap_ms, 0.0)

        # Woke 30 ms *past* the deadline: genuinely late, and the deficit is tracked.
        engine._last_cb_start = time.perf_counter() - (period_ms / 1000.0 + 0.03)
        engine._mark_start(4096)
        self.assertEqual(engine._meter.start_late, 1)
        self.assertGreater(engine._meter.worst_gap_ms, allow_ms)


class GainRampTest(unittest.TestCase):
    def test_a_master_step_is_spread_over_one_block(self):
        """A -6 dB to 0 dB move must not put a full-scale step in the waveform."""
        engine = Engine(samplerate=44100, blocksize=128)
        engine.update(armed=True, master_db=-6.0, amp=FakeAmp(1.0))
        engine._sync_gains()
        block = 128
        t0 = np.arange(block, dtype=np.float32) / 44100.0
        t1 = np.arange(block, block * 2, dtype=np.float32) / 44100.0
        first = engine._render(0.3 * np.sin(2 * np.pi * 220 * t0)[:, None], block)
        engine.update(master_db=0.0)
        second = engine._render(0.3 * np.sin(2 * np.pi * 220 * t1)[:, None], block)
        natural = 0.3 * 2 * np.pi * 220 / 44100
        jump = abs(float(second[0]) - float(first[-1]))
        self.assertLessEqual(jump, 1.1 * natural)
        # At the seam the ramp still starts at -6 dB, not at the new 0 dB gain.
        self.assertLess(abs(float(second[0])), 0.3 * 0.6)


class FakeTake:
    """A take that keeps its blocks in memory, so the click can be checked against it."""

    def __init__(self) -> None:
        self.blocks = []

    def push(self, block) -> None:
        self.blocks.append(np.array(block, copy=True))


class TempoTest(unittest.TestCase):
    def test_the_click_is_in_the_monitor_and_not_in_the_take(self):
        engine = Engine(samplerate=48000, blocksize=512)
        engine.update(armed=False)
        engine._take = FakeTake()
        engine.set_tempo(bpm=120, metronome=True)
        heard = []
        for _ in range(100):
            heard.append(engine._render(np.zeros((512, 1), dtype=np.float32), 512))
        written = np.concatenate(engine._take.blocks)
        self.assertEqual(float(np.max(np.abs(written))), 0.0)
        self.assertGreater(float(np.max(np.abs(np.concatenate(heard)))), 0.0)

    def test_the_click_lands_on_the_beat(self):
        engine = Engine(samplerate=48000, blocksize=512)
        engine.set_tempo(bpm=120, metronome=True)
        engine._click_reset.set()
        block = engine._click_block(48000, engine.chain)
        lit = np.flatnonzero(np.abs(block) > 0.0)
        self.assertLess(int(lit[0]), 10)
        second = int(lit[lit > 20000][0])
        self.assertAlmostEqual(second, 24000, delta=10)

    def test_bpm_stops_at_the_ends_of_the_range(self):
        engine = Engine()
        self.assertEqual(engine.set_tempo(bpm=900).bpm, 300.0)
        self.assertEqual(engine.set_tempo(bpm=10).bpm, 40.0)

    def test_changing_tempo_restarts_the_grid(self):
        engine = Engine(samplerate=48000, blocksize=512)
        engine.set_tempo(bpm=120, metronome=True)
        engine._render(np.zeros((512, 1), dtype=np.float32), 512)
        engine.set_tempo(bpm=90)
        self.assertTrue(engine._click_reset.is_set())
        click = engine._click_block(1, engine.chain)
        self.assertIsNotNone(click)


class FakeHost:
    """Just enough of sounddevice to answer the question "which pair would you open?"."""

    WDM = 0

    def __init__(self, devices, default=(5, 6)):
        self.devices = devices
        self.default = type("Defaults", (), {"device": default})()

    def query_hostapis(self):
        return [{"name": "Windows WDM-KS"}, {"name": "MME"}]

    def query_devices(self):
        return [dict(d, hostapi=d.get("hostapi", self.WDM)) for d in self.devices]


def row(name, ins=0, outs=0, hostapi=0):
    return {"name": name, "max_input_channels": ins, "max_output_channels": outs,
            "hostapi": hostapi, "default_samplerate": 44100.0}


class TheBlockSizeIsAPromise(unittest.TestCase):
    """A smaller block is felt latency, and it has to leave the callback time to finish."""

    def test_the_default_block_is_a_quarter_of_the_old_one(self):
        engine = Engine()
        self.assertEqual(engine.blocksize, 256, "512 frames is 11.6 ms of felt delay on top")

    def test_a_256_block_leaves_room_for_a_capture_and_a_long_cabinet(self):
        import time

        import numpy as _np

        from amphead.cab import Cab

        ir = _np.zeros(262144, dtype=_np.float32)
        ir[0] = 1.0
        cab = Cab(ir, 44100)
        chain = Chain(armed=True, master_db=0.0, cab=cab)
        x = (_np.random.randn(256) * 0.05).astype(_np.float32)
        budget = 1000.0 * 256 / 44100
        # The first block after a load pays for buffers and first-touch pages. What has to
        # fit in the budget is the steady state, which is what a player hears.
        for _ in range(5):
            process_chain(x, chain)
        seen = []
        for _ in range(50):
            t = time.perf_counter()
            process_chain(x, chain)
            seen.append((time.perf_counter() - t) * 1000)
        seen.sort()
        median = seen[len(seen) // 2]
        # The median, not the worst: a whole-box hiccup is the OS, not the chain, and a
        # test that fails on it teaches people to ignore the suite.
        self.assertLess(median, budget,
                        f"{median:.2f} ms of render in a {budget:.2f} ms block")


class TestChoosingTheDevice(unittest.TestCase):
    """The pair is found by shape, not by this plugin knowing one vendor's names."""

    def choose(self, devices, default=(5, 6)):
        import sys
        from amphead.engine2 import choose_devices
        from unittest import mock

        with mock.patch.object(sys, "platform", "win32"):
            return choose_devices(FakeHost(devices, default))

    def test_the_two_halves_of_one_interface_are_paired_by_their_common_name(self):
        got = self.choose([
            row("Speakers (Realtek HD Audio output)", outs=8),
            row("Line (Focusrite USB Audio Input)", ins=2),
            row("Speakers (Focusrite USB Audio Output)", outs=2),
        ])
        self.assertEqual(got, (1, 2, "wdm-ks"))

    def test_a_virtual_cable_is_never_the_interface(self):
        got = self.choose([
            row("Input (VB-Audio Point)", ins=16),
            row("Output (VB-Audio Point)", outs=16),
            row("Line (Audio Input)", ins=2),
            row("Speakers (Audio Output)", outs=2),
        ])
        self.assertEqual(got[:2], (2, 3), "the real card is the one without 'virtual' on it")

    def test_an_unnamed_pair_still_opens_something(self):
        got = self.choose([
            row("Line (Audio Input)", ins=2),
            row("Speakers (Audio Output)", outs=2),
        ])
        self.assertEqual(got, (0, 1, "wdm-ks"))

    def test_without_kernel_streaming_it_uses_the_system_default_and_says_so(self):
        got = self.choose([row("Speakers", outs=2, hostapi=1)], default=(3, 4))
        self.assertEqual(got, (3, 4, "default"))

    def test_a_device_that_cannot_take_two_outputs_is_not_the_output(self):
        got = self.choose([
            row("Mono (Some Input)", ins=2),
            row("Mono (Some Output)", outs=1),
            row("Stereo (Other Output)", outs=2),
        ])
        self.assertEqual(got, (0, 2, "wdm-ks"), "mono output cannot carry a stereo cabinet")


if __name__ == "__main__":
    unittest.main()


class A_vanished_device_is_not_an_exception(unittest.TestCase):
    """Pulling the interface out makes `stream.active` raise, not return False.

    That question is asked by every status answer, so raising there used to blank the pane with
    a traceback instead of saying the device is gone.
    """

    def test_reading_active_that_raises_says_not_running(self):
        from amphead.engine2 import Engine

        class Gone:
            @property
            def active(self):
                raise RuntimeError("PortAudioError: device unavailable")

        engine = object.__new__(Engine)
        engine._duplex = Gone()
        engine._in_stream = engine._out_stream = None
        engine._last_error = None

        self.assertFalse(engine.running)
        self.assertIn("device unavailable", engine._last_error)
