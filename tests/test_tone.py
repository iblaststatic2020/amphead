"""Tone knobs: they must do what they say, and a flat knob must be a wire."""

import unittest

try:
    import numpy as np
    from amphead.tone import Biquad, ToneStack
except ModuleNotFoundError:  # pragma: no cover - the engine is an optional extra
    np = None

if np is None:  # pragma: no cover
    raise unittest.SkipTest("numpy is not installed")


def amp(response, freq, samplerate):
    """Steady-state amplitude of a sine at ``freq`` after it passes through ``response``."""
    n = samplerate  # one second, so every whole-hz test tone is a bin frequency
    t = np.arange(n) / samplerate
    x = np.sin(2.0 * np.pi * freq * t).astype(np.float32)
    y = response(x)
    tail = y[n // 2:]
    return float(np.sqrt(2.0) * np.sqrt(np.mean(np.square(tail))))


def render(stack, x, block=128):
    return np.concatenate([stack.process(x[i:i + block]) for i in range(0, x.size, block)])


@unittest.skipIf(np is None, "numpy is not installed")
class ToneTest(unittest.TestCase):
    SR = 48000

    def test_flat_stack_is_a_wire(self):
        stack = ToneStack(self.SR)
        rng = np.random.default_rng(2)
        x = (rng.standard_normal(512) * 0.2).astype(np.float32)
        self.assertTrue(np.array_equal(render(stack, x), x))

    def test_bass_shelf_boosts_lows_and_leaves_highs(self):
        stack = ToneStack(self.SR, bass=6.0)
        low = amp(stack.process, 50, self.SR)
        stack.reset()
        high = amp(stack.process, 8000, self.SR)
        self.assertAlmostEqual(20 * np.log10(low), 6.0, delta=0.4)
        self.assertAlmostEqual(20 * np.log10(high), 0.0, delta=0.5)

    def test_bass_cut_removes_lows_and_leaves_highs(self):
        # A cut is negative dB. This pins that the band really attenuates instead of
        # treating a minus sign as an instruction to boost.
        stack = ToneStack(self.SR, bass=-6.0)
        low = amp(stack.process, 50, self.SR)
        stack.reset()
        high = amp(stack.process, 8000, self.SR)
        self.assertAlmostEqual(20 * np.log10(low), -6.0, delta=0.4)
        self.assertAlmostEqual(20 * np.log10(high), 0.0, delta=0.5)

    def test_treble_shelf_boosts_highs_and_leaves_lows(self):
        stack = ToneStack(self.SR, treble=6.0)
        high = amp(stack.process, 10000, self.SR)
        stack.reset()
        low = amp(stack.process, 80, self.SR)
        self.assertAlmostEqual(20 * np.log10(high), 6.0, delta=0.5)
        self.assertAlmostEqual(20 * np.log10(low), 0.0, delta=0.5)

    def test_mid_peak_is_centred(self):
        stack = ToneStack(self.SR, mid=6.0)
        centre = amp(stack.process, 700, self.SR)
        stack.reset()
        away = amp(stack.process, 60, self.SR)
        self.assertAlmostEqual(20 * np.log10(centre), 6.0, delta=0.4)
        self.assertAlmostEqual(20 * np.log10(away), 0.0, delta=0.6)

    def test_cut_is_the_mirror_of_boost(self):
        up = ToneStack(self.SR, mid=6.0)
        down = ToneStack(self.SR, mid=-6.0)
        a = amp(up.process, 700, self.SR)
        b = amp(down.process, 700, self.SR)
        self.assertAlmostEqual(20 * np.log10(a / b), 12.0, delta=0.3)

    def test_streaming_equals_one_block(self):
        stack = ToneStack(self.SR, bass=3.0, mid=-2.0, treble=4.0)
        rng = np.random.default_rng(4)
        x = (rng.standard_normal(6000) * 0.1).astype(np.float32)
        many = render(stack, x, 128)
        stack.reset()
        one = render(stack, x, x.size)
        self.assertLess(float(np.max(np.abs(many - one))), 1e-5)

    def test_knob_move_does_not_click(self):
        stack = ToneStack(self.SR)
        x = np.full(1024, 0.1, dtype=np.float32)
        out = render(stack, x, 128)
        stack.set("bass", 6.0)
        out = np.concatenate([out, render(stack, x, 128)])
        # The shelf takes a few milliseconds to settle, which is fine. What must not
        # happen is a jump at the seam that is larger than that settling itself.
        seam = abs(float(out[1024]) - float(out[1023]))
        settling = float(np.max(np.abs(np.diff(out[1024:1024 + 256]))))
        self.assertLessEqual(seam, settling + 1e-6)

    def test_set_rejects_unknown_band(self):
        with self.assertRaises(ValueError):
            ToneStack(self.SR).set("presence", 3.0)

    def test_band_bypass_flag(self):
        self.assertTrue(Biquad("peak", 700, 0.9, 0.0, self.SR).bypassed)
        self.assertFalse(Biquad("peak", 700, 0.9, 0.1, self.SR).bypassed)


if __name__ == "__main__":
    unittest.main()
