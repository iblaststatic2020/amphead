"""Measure a guitar sound, so an agent can aim at it and say how far off it landed.

A name is not a target and a guess is not an error. This module turns audio into both: an
eight-band profile of a recording (or of the amp's own output), a distance between two profiles
that ignores loudness, and the curve that would close the gap.

What it deliberately does not do is invent distortion a capture does not have. It measures and it
shapes. The honesty of the feature lives here: every claim the face makes about "aimed at X" is
backed by one of these numbers.

Bands are octave centres because guitar tone lives in octaves: 63 Hz is the cab thump, 125 to 250
the body, 500 to 1 k the bite, 2 to 4 k the presence, 8 k the fizz that a close mic either keeps or
loses. Narrower bands look precise and measure nothing but the material.

numpy only, like `cab.py` and `nam.py`: this runs in the engine interpreter, never in the plugin's.
"""

from __future__ import annotations

import math
import sys
import wave
from pathlib import Path

import numpy as np

BANDS = (63, 125, 250, 500, 1000, 2000, 4000, 8000)

# Below this a band is room noise or converter hiss, and comparing it is comparing nothing.
SILENT_DB = -78.0

# An EQ move bigger than this stops being a shape and starts being a different rig.
MAX_MOVE_DB = 12.0


def load(path: str | Path) -> tuple[np.ndarray, int]:
    """Read a wav into mono float32 at its own rate. 8, 16, 24 and 32 bit integer PCM."""
    with wave.open(str(path), "rb") as handle:
        frames = handle.getnframes()
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        rate = handle.getframerate()
        raw = handle.readframes(frames)

    if width == 1:
        x = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif width == 2:
        x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 3:
        # 24 bit is stored little-endian with no container: widen, sign-extend, scale
        bytes_ = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        packed = bytes_[:, 0] | (bytes_[:, 1] << 8) | (bytes_[:, 2] << 16)
        packed = np.where(packed >= 1 << 23, packed - (1 << 24), packed)
        x = packed.astype(np.float32) / float(1 << 23)
    elif width == 4:
        x = np.frombuffer(raw, dtype="<i4").astype(np.float32) / float(1 << 31)
    else:
        raise ValueError(f"{path}: {width * 8}-bit wav is not something to measure")

    if channels > 1:
        x = x.reshape(-1, channels).mean(axis=1)
    return x, rate


def psd(x: np.ndarray, rate: int, size: int = 8192, hop: int | None = None):
    """Averaged power spectrum over Hann windows. Welch, without the dependency."""
    hop = hop or size // 2
    if x.size < size:
        x = np.pad(x, (0, size - x.size))
    window = np.hanning(size).astype(np.float32)
    total = np.zeros(size // 2 + 1, dtype=np.float64)
    frames = 0
    for start in range(0, x.size - size + 1, hop):
        segment = x[start:start + size] * window
        total += np.abs(np.fft.rfft(segment)) ** 2
        frames += 1
    power = total / max(frames, 1)
    freqs = np.fft.rfftfreq(size, 1.0 / rate)
    return freqs, power


def _db(value: float) -> float:
    return 10.0 * math.log10(max(value, 1e-20))


def bands(freqs: np.ndarray, power: np.ndarray) -> dict[int, float]:
    """Octave band energies in dB, each normalised by its own width so wide bands are not louder."""
    out: dict[int, float] = {}
    for centre in BANDS:
        low, high = centre / math.sqrt(2.0), centre * math.sqrt(2.0)
        mask = (freqs >= low) & (freqs < high)
        if not mask.any():
            out[centre] = SILENT_DB
            continue
        out[centre] = _db(float(power[mask].mean()))
    return out


def tilt(freqs: np.ndarray, power: np.ndarray, low: float = 100.0, high: float = 8000.0) -> float:
    """Least-squares slope of the spectrum in dB per octave over the guitar's range.

    One number for "dark or bright" that survives a change of key, pickup or riff.
    """
    mask = (freqs >= low) & (freqs <= high) & (power > 1e-18)
    if mask.sum() < 8:
        return 0.0
    f = np.log2(freqs[mask])
    y = 10.0 * np.log10(power[mask])
    a, _ = np.polyfit(f, y, 1)
    return float(a)


def centroid(freqs: np.ndarray, power: np.ndarray) -> float:
    """Energy-weighted mean frequency. Rises with gain and presence, falls with a dark cab."""
    total = float(power.sum())
    if total <= 0.0:
        return 0.0
    return float((freqs * power).sum() / total)


def crest(x: np.ndarray) -> float:
    """Peak over RMS in dB. Low means squashed, high means dynamics left in the playing."""
    rms = float(np.sqrt(np.mean(np.square(x, dtype=np.float64))))
    peak = float(np.max(np.abs(x))) if x.size else 0.0
    if rms <= 1e-9 or peak <= 0.0:
        return 0.0
    return 20.0 * math.log10(peak / rms)


def profile(source: str | Path | np.ndarray, rate: int | None = None) -> dict:
    """Everything measurable about one sound, in a dict that is safe to put in JSON."""
    if isinstance(source, (str, Path)):
        x, rate = load(source)
        name = Path(source).name
    else:
        x = np.asarray(source, dtype=np.float32)
        if rate is None:
            raise ValueError("rate is required when profiling an array")
        name = "signal"

    freqs, power = psd(x, rate)
    band_db = bands(freqs, power)
    # Normalise to the loudest band so the shape is comparable across takes and levels.
    # Keys are strings because this dict is written to JSON, and a key that changes type on the
    # way to disk and back is a bug waiting for the first caller who does not guess right.
    loudest = max(band_db.values())
    shape = {str(centre): round(db - loudest, 2) for centre, db in band_db.items()}
    rms = float(np.sqrt(np.mean(np.square(x, dtype=np.float64)))) if x.size else 0.0
    return {
        "name": name,
        "rate": int(rate),
        "seconds": round(x.size / float(rate), 3) if rate else 0.0,
        "bands": shape,
        "tilt": round(tilt(freqs, power), 3),
        "centroid": round(centroid(freqs, power), 1),
        "crest": round(crest(x), 2),
        "rms_db": round(20.0 * math.log10(max(rms, 1e-9)), 2),
    }


def distance(target: dict, candidate: dict) -> dict:
    """How far apart two profiles are, in dB, with loudness taken out of the question.

    Band differences are weighted towards the bands a player actually hears a cabinet in. The
    total is the RMS of those differences: a single number that can be printed, compared and
    improved, instead of the word "close".
    """
    if target.get("rate") != candidate.get("rate"):
        # A profile at another rate was measured on another band grid; say so rather than pretend.
        note = "profiles were measured at different rates"
    else:
        note = ""

    per_band: dict[str, float] = {}
    squares: list[float] = []
    for centre in BANDS:
        a = target["bands"].get(str(centre), target["bands"].get(centre, SILENT_DB))
        b = candidate["bands"].get(str(centre), candidate["bands"].get(centre, SILENT_DB))
        if a <= SILENT_DB and b <= SILENT_DB:
            per_band[str(centre)] = 0.0
            continue
        difference = float(b) - float(a)
        per_band[str(centre)] = round(difference, 2)
        squares.append(difference * difference)

    total = math.sqrt(sum(squares) / len(squares)) if squares else 0.0
    return {
        "db": round(total, 2),
        "per_band": per_band,
        "tilt": round(float(candidate["tilt"]) - float(target["tilt"]), 3),
        "crest": round(float(candidate["crest"]) - float(target["crest"]), 2),
        "note": note,
    }


def eq_curve(target: dict, candidate: dict, limit: float = MAX_MOVE_DB) -> dict[int, float]:
    """The band gains that would take `candidate` toward `target`, smoothed and clamped.

    Positive means add, negative means cut. Clamped because a 20 dB move is not a match, it is a
    warning that the capture is the wrong one.
    """
    raw: dict[int, float] = {}
    for centre in BANDS:
        a = target["bands"].get(str(centre), target["bands"].get(centre, SILENT_DB))
        b = candidate["bands"].get(str(centre), candidate["bands"].get(centre, SILENT_DB))
        if a <= SILENT_DB or b <= SILENT_DB:
            raw[centre] = 0.0
            continue
        raw[centre] = max(-limit, min(limit, float(a) - float(b)))

    # A three-band smoothing pass: a curve that steps 6 dB between neighbours cannot be a cab.
    values = [raw[c] for c in BANDS]
    smoothed = []
    for i in range(len(values)):
        left = values[max(i - 1, 0)]
        right = values[min(i + 1, len(values) - 1)]
        smoothed.append(round((left + 2.0 * values[i] + right) / 4.0, 2))
    return dict(zip(BANDS, smoothed))


def _print_report(target_path: str, candidate_path: str) -> None:
    target = profile(target_path)
    candidate = profile(candidate_path)
    spacing = distance(target, candidate)
    curve = eq_curve(target, candidate)

    print(f"target    {target['name']}  tilt {target['tilt']:+.2f} dB/oct  crest {target['crest']:.1f} dB")
    print(f"candidate {candidate['name']}  tilt {candidate['tilt']:+.2f} dB/oct  crest {candidate['crest']:.1f} dB")
    print()
    print(f"{'band':>7} {'target':>8} {'cand':>8} {'diff':>7} {'move':>7}")
    for centre in BANDS:
        a = target["bands"].get(str(centre), target["bands"].get(centre, SILENT_DB))
        b = candidate["bands"].get(str(centre), candidate["bands"].get(centre, SILENT_DB))
        diff = spacing["per_band"].get(str(centre), 0.0)
        print(f"{centre:>7} {a:>8.1f} {b:>8.1f} {diff:>+7.1f} {curve[centre]:>+7.1f}")
    print()
    print(f"distance {spacing['db']:.2f} dB RMS  tilt {spacing['tilt']:+.2f} dB/oct  crest {spacing['crest']:+.1f} dB")
    if spacing["note"]:
        print(spacing["note"])


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("usage: python -m amphead.analyse <target.wav> <candidate.wav>")
        raise SystemExit(2)
    _print_report(sys.argv[1], sys.argv[2])
