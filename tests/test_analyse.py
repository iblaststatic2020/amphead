"""The measurement has to be trustworthy before anything is allowed to aim with it.

These tests use signals whose spectrum is known exactly, so a profile that drifts shows up as a
failing number rather than a vague impression.
"""

import math
import tempfile
import unittest
import wave
from pathlib import Path

import numpy as np

from amphead import analyse


def shaped(seconds=2.0, rate=48000, slope=0.0, lowpass=None, highpass=None, seed=3):
    """Noise with a known tilt, optionally band-limited, so the measurement has an answer key."""
    rng = np.random.default_rng(seed)
    n = int(seconds * rate)
    noise = rng.standard_normal(n)
    spectrum = np.fft.rfft(noise)
    freqs = np.fft.rfftfreq(n, 1.0 / rate)
    gain = np.ones_like(freqs)
    if slope:
        gain *= (np.maximum(freqs, 20.0) / 1000.0) ** (slope / 6.0206)  # slope in dB/octave
    if lowpass:
        gain *= 1.0 / np.sqrt(1.0 + (freqs / lowpass) ** 4)
    if highpass:
        gain *= 1.0 / np.sqrt(1.0 + (highpass / np.maximum(freqs, 1.0)) ** 4)
    out = np.fft.irfft(spectrum * gain, n)
    out = out / max(np.max(np.abs(out)), 1e-9) * 0.5
    return out.astype(np.float32), rate


class TestProfiling(unittest.TestCase):
    def test_a_signal_is_zero_distance_from_itself(self):
        x, rate = shaped()
        a = analyse.profile(x, rate)
        b = analyse.profile(x, rate)
        self.assertEqual(analyse.distance(a, b)["db"], 0.0)

    def test_a_darker_candidate_reads_as_less_high_end(self):
        flat, rate = shaped()
        dark, _ = shaped(lowpass=1200.0)
        gap = analyse.distance(analyse.profile(flat, rate), analyse.profile(dark, rate))
        self.assertGreater(gap["db"], 3.0, "a 1.2 kHz ceiling should not measure as a match")
        self.assertLess(gap["per_band"]["8000"], -6.0, "8 k must come out darker than the target")
        self.assertLess(gap["tilt"], -0.5, "the whole spectrum must read as a downward tilt")

    def test_the_curve_points_at_the_band_that_is_missing(self):
        flat, rate = shaped()
        dark, _ = shaped(lowpass=1200.0)
        curve = analyse.eq_curve(analyse.profile(flat, rate), analyse.profile(dark, rate))
        self.assertGreater(curve[8000], 0.0, "8 k needs adding to reach the target")
        self.assertGreater(curve[4000], 0.0)
        self.assertLess(abs(curve[125]), 4.0, "the low end was never the problem")

    def test_a_curve_is_clamped_and_smoothed(self):
        flat, rate = shaped()
        hole, _ = shaped(lowpass=300.0)
        curve = analyse.eq_curve(analyse.profile(flat, rate), analyse.profile(hole, rate))
        self.assertLessEqual(max(curve.values()), analyse.MAX_MOVE_DB)
        self.assertGreaterEqual(min(curve.values()), -analyse.MAX_MOVE_DB)
        values = [curve[c] for c in analyse.BANDS]
        steps = [abs(b - a) for a, b in zip(values, values[1:])]
        self.assertLess(max(steps), 8.0, "a cabinet curve cannot jump between octaves")

    def test_a_declared_tilt_is_measured_back(self):
        for slope in (-4.5, 0.0, 3.0):
            x, rate = shaped(slope=slope)
            got = analyse.profile(x, rate)["tilt"]
            self.assertAlmostEqual(got, slope, delta=0.8, msg=f"declared {slope} dB/oct")

    def test_crest_separates_a_squashed_sound_from_a_dynamic_one(self):
        rate = 48000
        t = np.arange(rate, dtype=np.float32) / rate
        sine = (0.5 * np.sin(2 * math.pi * 220 * t)).astype(np.float32)
        self.assertAlmostEqual(analyse.crest(sine), 3.01, delta=0.2)

        burst = np.zeros(rate, dtype=np.float32)
        burst[1000:1040] = 0.9  # a pick attack and then nothing
        self.assertGreater(analyse.crest(burst), 15.0)

    def test_silence_profiles_without_dividing_by_zero(self):
        quiet = np.zeros(48000, dtype=np.float32)
        p = analyse.profile(quiet, 48000)
        self.assertEqual(p["bands"][str(1000)], 0.0)
        self.assertEqual(p["crest"], 0.0)

    def test_rate_mismatch_is_stated_and_not_hidden(self):
        a, _ = shaped(rate=44100)
        b, _ = shaped(rate=48000)
        gap = analyse.distance(analyse.profile(a, 44100), analyse.profile(b, 48000))
        self.assertIn("different rates", gap["note"])


class TestReadingFiles(unittest.TestCase):
    def test_a_stereo_16_bit_wav_is_read_as_mono_at_its_own_rate(self):
        rate = 44100
        t = np.arange(rate, dtype=np.float32) / rate
        left = 0.5 * np.sin(2 * math.pi * 440 * t)
        right = 0.5 * np.sin(2 * math.pi * 440 * t)
        interleaved = np.empty(rate * 2, dtype="<i2")
        interleaved[0::2] = (left * 32767).astype("<i2")
        interleaved[1::2] = (right * 32767).astype("<i2")

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pair.wav"
            with wave.open(str(path), "wb") as handle:
                handle.setnchannels(2)
                handle.setsampwidth(2)
                handle.setframerate(rate)
                handle.writeframes(interleaved.tobytes())

            x, got = analyse.load(path)
            self.assertEqual(got, rate)
            self.assertEqual(x.size, rate, "two channels must fold to one")
            self.assertLess(float(np.max(np.abs(x))), 0.6)

    def test_a_file_can_be_profiled_by_path(self):
        x, rate = shaped()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "take.wav"
            with wave.open(str(path), "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(rate)
                handle.writeframes((x * 32767).astype("<i2").tobytes())
            p = analyse.profile(path)
            self.assertEqual(p["name"], "take.wav")
            self.assertEqual(p["rate"], rate)
            self.assertGreater(p["seconds"], 1.0)


if __name__ == "__main__":
    unittest.main()
