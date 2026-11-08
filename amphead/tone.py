"""The tone stack, so the knobs on the pane are real knobs.

Three bands: bass shelf, mid peak, treble shelf. Each one is an RBJ biquad run in
transposed direct form II, which is the numerically boring choice and therefore the
right one for a state that runs for hours. Blocks carry their filter state, so the
stage is streaming-exact: the tests compare a streamed run against one block and
against a hand-computed biquad response.
"""

from __future__ import annotations

import math

import numpy as np

BANDS = ("bass", "mid", "treble")

#: A knob at 0 dB must be a true bypass, so a flat stack costs nothing and changes nothing.
BYPASS_DB = 0.0


def _cookbook(kind: str, freq: float, q: float, db: float, samplerate: int) -> tuple[float, float, float, float, float]:
    """RBJ audio EQ cookbook coefficients, normalised by a0."""
    a = 10.0 ** (db / 40.0)
    w0 = 2.0 * math.pi * freq / samplerate
    cos_w0 = math.cos(w0)
    sin_w0 = math.sin(w0)
    alpha = sin_w0 / (2.0 * q)

    if kind == "peak":
        b0 = 1.0 + alpha * a
        b1 = -2.0 * cos_w0
        b2 = 1.0 - alpha * a
        a0 = 1.0 + alpha / a
        a1 = -2.0 * cos_w0
        a2 = 1.0 - alpha / a
    elif kind == "low_shelf":
        sqrt_a = math.sqrt(a)
        two_sqrt_a_alpha = 2.0 * sqrt_a * alpha
        b0 = a * ((a + 1) - (a - 1) * cos_w0 + two_sqrt_a_alpha)
        b1 = 2.0 * a * ((a - 1) - (a + 1) * cos_w0)
        b2 = a * ((a + 1) - (a - 1) * cos_w0 - two_sqrt_a_alpha)
        a0 = (a + 1) + (a - 1) * cos_w0 + two_sqrt_a_alpha
        a1 = -2.0 * ((a - 1) + (a + 1) * cos_w0)
        a2 = (a + 1) + (a - 1) * cos_w0 - two_sqrt_a_alpha
    elif kind == "high_shelf":
        sqrt_a = math.sqrt(a)
        two_sqrt_a_alpha = 2.0 * sqrt_a * alpha
        b0 = a * ((a + 1) + (a - 1) * cos_w0 + two_sqrt_a_alpha)
        b1 = -2.0 * a * ((a - 1) + (a + 1) * cos_w0)
        b2 = a * ((a + 1) + (a - 1) * cos_w0 - two_sqrt_a_alpha)
        a0 = (a + 1) - (a - 1) * cos_w0 + two_sqrt_a_alpha
        a1 = 2.0 * ((a - 1) - (a + 1) * cos_w0)
        a2 = (a + 1) - (a - 1) * cos_w0 - two_sqrt_a_alpha
    else:  # pragma: no cover - programming error, not user input
        raise ValueError(f"unknown biquad kind: {kind}")

    return b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0


class Biquad:
    """One band, streaming, with state carried across blocks."""

    def __init__(self, kind: str, freq: float, q: float, db: float, samplerate: int) -> None:
        self.kind = kind
        self.freq = float(freq)
        self.q = float(q)
        self.db = float(db)
        self.samplerate = int(samplerate)
        self.set_coeffs(_cookbook(kind, self.freq, self.q, self.db, self.samplerate))
        self.z1 = 0.0
        self.z2 = 0.0

    def set_coeffs(self, cooked) -> None:
        """Publish one block's coefficients as a single value.

        The callback unpacks five numbers. Written as five separate attributes, a knob
        turned mid-block can be read as a mix of the old and new coefficients, and a shelf
        at +-12 dB with mismatched numerator and denominator rings or bursts. One tuple
        assignment is atomic under the GIL, so the callback always gets a matched set.
        """
        self.coeffs: tuple[float, float, float, float, float] = tuple(float(v) for v in cooked)

    @property
    def bypassed(self) -> bool:
        return abs(self.db - BYPASS_DB) < 1e-9

    def reset(self) -> None:
        self.z1 = self.z2 = 0.0

    def process(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=np.float32).reshape(-1)
        if self.bypassed or x.size == 0:
            return x
        b0, b1, b2, a1, a2 = self.coeffs
        z1, z2 = self.z1, self.z2
        y = np.empty_like(x)
        # A python loop over 128 samples is ~20 us, an order of magnitude under the
        # block budget, and it keeps the state exact. Vectorising this would mean
        # lfilter (scipy) or an unrolled recurrence that is harder to trust.
        for i in range(x.size):
            xi = float(x[i])
            yi = b0 * xi + z1
            z1 = b1 * xi - a1 * yi + z2
            z2 = b2 * xi - a2 * yi
            y[i] = yi
        self.z1, self.z2 = z1, z2
        return y


class ToneStack:
    """Bass, mid and treble, in the order a player expects them to sit."""

    DEFAULTS = {"bass": 200.0, "mid": 700.0, "treble": 3000.0, "mid_q": 0.9}

    def __init__(self, samplerate: int = 44100, bass: float = 0.0, mid: float = 0.0, treble: float = 0.0) -> None:
        self.samplerate = int(samplerate)
        f = self.DEFAULTS
        self.bass_band = Biquad("low_shelf", f["bass"], 0.7, bass, self.samplerate)
        self.mid_band = Biquad("peak", f["mid"], f["mid_q"], mid, self.samplerate)
        self.treble_band = Biquad("high_shelf", f["treble"], 0.7, treble, self.samplerate)

    @property
    def bands(self) -> tuple[Biquad, Biquad, Biquad]:
        return (self.bass_band, self.mid_band, self.treble_band)

    def set(self, name: str, db: float) -> None:
        """Move one knob. Coefficients are rebuilt, state is kept, so it does not click."""
        if name not in BANDS:
            raise ValueError(f"unknown band {name!r}; expected one of {', '.join(BANDS)}")
        band = getattr(self, f"{name}_band")
        band.db = float(db)
        band.set_coeffs(_cookbook(band.kind, band.freq, band.q, band.db, band.samplerate))

    def reset(self) -> None:
        for band in self.bands:
            band.reset()

    def process(self, x: np.ndarray) -> np.ndarray:
        y = x
        for band in self.bands:
            y = band.process(y)
        return y
