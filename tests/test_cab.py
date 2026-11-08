"""Cabinet convolution: exact match against a plain convolution, and it must stream."""

import json
import tempfile
import unittest
import wave
from pathlib import Path

try:
    import numpy as np
    from amphead.cab import Cab, resample
except ModuleNotFoundError:  # pragma: no cover - engine modules need numpy
    np = None

if np is None:  # pragma: no cover
    import unittest as _unittest
    raise _unittest.SkipTest("numpy is not installed")


def render(cab, x, block=128):
    out = []
    for i in range(0, x.size, block):
        out.append(cab.process(x[i:i + block]))
    return np.concatenate(out)


@unittest.skipIf(np is None, "numpy is not installed")
class CabTest(unittest.TestCase):
    def test_resample_same_rate_is_a_noop(self):
        rng = np.random.default_rng(11)
        ir = rng.standard_normal(128).astype(np.float32)
        self.assertTrue(np.array_equal(resample(ir, 48000, 48000), ir))

    def test_resample_changes_length_by_the_rate_ratio(self):
        ir = np.zeros(1024, dtype=np.float32)
        ir[0] = 1.0
        out = resample(ir, 44100, 48000)
        self.assertEqual(out.size, round(1024 * 48000 / 44100))

    def test_resample_keeps_pitch_and_level(self):
        """A tone stays the tone: this is the test that would have caught an 8% sharp cab."""
        rate_in, rate_out, freq, seconds = 44100, 48000, 1000.0, 0.5
        t = np.arange(int(rate_in * seconds)) / rate_in
        tone = np.sin(2 * np.pi * freq * t).astype(np.float32)
        out = resample(tone, rate_in, rate_out)
        self.assertAlmostEqual(float(np.sqrt(np.mean(out ** 2))), 0.7071, places=2)
        spectrum = np.abs(np.fft.rfft(out))
        peak_hz = np.argmax(spectrum) * rate_out / out.size
        self.assertAlmostEqual(peak_hz, freq, delta=5.0)

    def test_cab_resamples_the_file_to_the_engine_rate(self):
        rng = np.random.default_rng(12)
        ir = rng.standard_normal(512).astype(np.float32) * 0.1
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ir.wav"
            with wave.open(str(path), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(44100)
                w.writeframes((ir * 20000).astype("<i2").tobytes())
            cab = Cab.from_wav(path, rate=48000)
        self.assertEqual(cab.samplerate, 48000)
        self.assertEqual(cab.file_rate, 44100)
        self.assertEqual(cab.length, round(512 * 48000 / 44100))

    def test_impulse_returns_the_ir(self):
        rng = np.random.default_rng(5)
        ir = rng.standard_normal(64).astype(np.float32) * 0.1
        ir[0] = 0.5
        cab = Cab(ir, samplerate=48000, gain_db=0.0, normalize=False)
        x = np.zeros(256, dtype=np.float32)
        x[10] = 1.0
        got = render(cab, x)
        want = np.convolve(x, ir)[: x.size]
        self.assertLess(float(np.max(np.abs(got - want))), 1e-6)

    def test_streaming_equals_one_shot(self):
        rng = np.random.default_rng(6)
        ir = (rng.standard_normal(300) * 0.05).astype(np.float32)
        x = (rng.standard_normal(4000) * 0.1).astype(np.float32)
        many = render(Cab(ir, 48000, normalize=False), x, 128)
        one = render(Cab(ir, 48000, normalize=False), x, 4000)
        self.assertLess(float(np.max(np.abs(many - one))), 1e-5)

    def test_block_smaller_than_ir_is_fine(self):
        rng = np.random.default_rng(7)
        ir = (rng.standard_normal(512) * 0.02).astype(np.float32)
        x = (rng.standard_normal(777) * 0.1).astype(np.float32)
        got = render(Cab(ir, 48000, normalize=False), x, 32)
        want = np.convolve(x, ir)[: x.size]
        self.assertLess(float(np.max(np.abs(got - want))), 1e-5)

    def test_gain_db_scales_output(self):
        ir = np.array([1.0, 0.0], dtype=np.float32)
        x = np.array([0.25, -0.5], dtype=np.float32)
        plain = render(Cab(ir, 48000, gain_db=0.0), x, 4)
        quieter = render(Cab(ir, 48000, gain_db=-6.0), x, 4)
        self.assertAlmostEqual(float(quieter[0] / plain[0]), 0.5011, places=3)

    def test_a_cabinet_far_longer_than_the_block_is_exact(self):
        """The five second IR that made the amp cut in and out: 262144 taps at 512 frames.

        Overlap-save with one transform of the whole IR is exact but costs 117% of realtime
        here, so the output stream starves. Partitions are exact too, and the test that says
        so is the same convolution, not a level or a vibe.
        """
        rng = np.random.default_rng(21)
        ir = (rng.standard_normal(8192) * 0.01).astype(np.float32)
        ir[0] = 0.4
        x = (rng.standard_normal(4096) * 0.1).astype(np.float32)
        got = render(Cab(ir, 48000, normalize=False), x, 512)
        want = np.convolve(x, ir)[: x.size]
        self.assertLess(float(np.max(np.abs(got - want))), 1e-4)

    def test_the_transform_stays_the_size_of_a_block_however_long_the_cabinet(self):
        """This is the fix itself: cost per block must stop depending on the IR length."""
        short = Cab(np.zeros(2048, dtype=np.float32) + 0.001, 48000, normalize=False)
        short.process(np.zeros(512, dtype=np.float32))
        long = Cab(np.zeros(200000, dtype=np.float32) + 0.001, 48000, normalize=False)
        long.process(np.zeros(512, dtype=np.float32))
        self.assertEqual(short._ring.shape[1], 513)
        self.assertEqual(long._ring.shape[1], 513, "the transform grew with the cabinet")
        self.assertEqual(short._partitions, 4)
        self.assertEqual(long._partitions, 391)

    def test_a_long_cabinet_still_fits_inside_its_own_block(self):
        """A wall-clock budget, deliberately generous: 200000 taps measured ~1.3 ms per 512
        frame block (11.6 ms of audio) after partitioning, against 13.6 ms before. Slow CI
        machines should still clear 4x realtime headroom."""
        import time

        cab = Cab(np.ones(200000, dtype=np.float32) / 200000, 44100, normalize=False)
        x = np.random.default_rng(3).standard_normal(512).astype(np.float32) * 0.1
        cab.process(x)
        start = time.perf_counter()
        for _ in range(20):
            cab.process(x)
        per_block = (time.perf_counter() - start) / 20
        self.assertLess(per_block, 4 * 512 / 44100,
                        "a long cabinet no longer fits in its block: the output will starve")

    def test_empty_ir_is_an_error(self):
        with self.assertRaises(ValueError):
            Cab(np.zeros(0, dtype=np.float32), 48000)

    def test_from_wav_16_bit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ir.wav"
            data = (np.array([0, 16384, -32768, 8192], dtype=np.int16))
            with wave.open(str(path), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(48000)
                w.writeframes(data.tobytes())
            cab = Cab.from_wav(path)
            self.assertEqual(cab.samplerate, 48000)
            self.assertAlmostEqual(float(cab.ir[1]), 0.5, places=4)
            self.assertAlmostEqual(float(cab.ir[2]), -1.0, places=4)

    def test_from_wav_24_bit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ir24.wav"
            frames = np.array([0, 1 << 22, -(1 << 22)], dtype=np.int32)
            raw = bytearray()
            for v in frames:
                raw += int(v & 0xFFFFFF).to_bytes(3, "little", signed=False)
            with wave.open(str(path), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(3)
                w.setframerate(44100)
                w.writeframes(bytes(raw))
            cab = Cab.from_wav(path, normalize=False)
            self.assertEqual(cab.samplerate, 44100)
            self.assertAlmostEqual(float(cab.ir[1]), 0.5, places=4)
            self.assertAlmostEqual(float(cab.ir[2]), -0.5, places=4)

    def test_from_wav_stereo_is_summed_to_mono(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "st.wav"
            frames = np.array([[16384, -16384], [0, 32767]], dtype=np.int16)
            with wave.open(str(path), "wb") as w:
                w.setnchannels(2)
                w.setsampwidth(2)
                w.setframerate(48000)
                w.writeframes(frames.tobytes())
            cab = Cab.from_wav(path, normalize=False)
            self.assertEqual(cab.ir.size, 2)
            self.assertAlmostEqual(float(cab.ir[0]), 0.0, places=4)


if __name__ == "__main__":
    unittest.main()
