"""The chain without a device: gain staging, clamps, and the safety floor."""

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

    def test_armed_at_safe_master_is_effectively_silent(self):
        engine = Engine()
        engine.update(armed=True, amp=FakeAmp(), master_db=SAFE_MASTER_DB)
        y = engine.render(ramp())
        # The safe floor is -60 dB, so the loudest thing that can come out is that
        # quiet: audible only if you put your ear to the speaker, and never a shock.
        self.assertLessEqual(gain_to_db(float(np.max(np.abs(y)))), SAFE_MASTER_DB + 0.1)

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


if __name__ == "__main__":
    unittest.main()
