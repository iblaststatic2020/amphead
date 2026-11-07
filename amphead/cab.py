"""Cabinet impulse responses as a streaming filter.

An IR is a finite impulse response, so the correct operation is a plain linear
convolution and the only question is doing it without a per-block allocation
storm. This is overlap-save: keep the last ``L - 1`` input samples, transform the
block plus that history once, multiply by the IR transform computed at load time,
inverse transform, and take the valid middle.

It is exact. The test compares a streamed signal against
``scipy.signal.fftconvolve`` over the whole signal and requires a match to 1e-5,
because "sounds right" is not a test and a truncated overlap sounds like a click
every block.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np


def resample(ir: np.ndarray, sr_from: int, sr_to: int) -> np.ndarray:
    """Band-limited resample of a short signal, frequency domain, no scipy.

    A 44.1 kHz cabinet played at 48 kHz is not a subtle error: every resonance moves
    by 8.8% and the top end gets brighter, which is exactly the kind of "why does it
    not sound like the plugin" that is invisible in a level meter.
    """
    ir = np.asarray(ir, dtype=np.float64).reshape(-1)
    if sr_from == sr_to or ir.size == 0:
        return ir.astype(np.float32)
    n_out = int(round(ir.size * sr_to / float(sr_from)))
    spectrum = np.fft.rfft(ir)
    # Keep the low half band and pad or truncate the top, then scale for the length change.
    keep = n_out // 2 + 1
    if keep <= spectrum.size:
        out = spectrum[:keep]
    else:
        out = np.concatenate((spectrum, np.zeros(keep - spectrum.size, dtype=spectrum.dtype)))
    y = np.fft.irfft(out, n_out)
    return (y * (n_out / float(ir.size))).astype(np.float32)


class Cab:
    """One impulse response, applied block by block."""

    def __init__(
        self,
        ir: np.ndarray,
        samplerate: int | None = None,
        gain_db: float = 0.0,
        normalize: bool = True,
        file_rate: int | None = None,
    ) -> None:
        ir = np.asarray(ir, dtype=np.float64).reshape(-1)
        if ir.size < 2:
            raise ValueError("an impulse response needs at least two samples")
        # Cabinets arrive at wildly different levels. Normalising to a unit peak makes
        # cabinet loudness depend on the gain knob and nothing else, so two IRs can be
        # swapped without also moving the master.
        self.peak = float(np.max(np.abs(ir)))
        if normalize and self.peak > 0:
            ir = ir / self.peak
        self.ir = ir.astype(np.float32)
        self.length = int(self.ir.size)
        self.samplerate = samplerate
        #: The rate the impulse response was captured at, before any resampling.
        self.file_rate = int(file_rate or samplerate or 0)
        self.gain = float(10.0 ** (gain_db / 20.0))
        self._history = np.zeros(self.length - 1, dtype=np.float32)
        self._size: int | None = None
        self._ir_fft: np.ndarray | None = None

    @classmethod
    def from_wav(
        cls,
        path: str | Path,
        gain_db: float = 0.0,
        normalize: bool = True,
        rate: int | None = None,
    ) -> "Cab":
        """Read a 16, 24 or 32 bit PCM wav with the standard library only.

        scipy would do this in one line, but it is a 40 MB dependency for one file
        format. ``rate`` is the rate the engine runs at, and the file is resampled
        to it, because a 44.1 kHz cabinet in a 48 kHz chain is a different cabinet.
        """
        import wave

        with wave.open(str(path), "rb") as wav:
            channels = wav.getnchannels()
            width = wav.getsampwidth()
            file_rate = int(wav.getframerate())
            raw = wav.readframes(wav.getnframes())

        if width == 2:
            data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
        elif width == 3:
            b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
            joined = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
            joined = np.where(joined & 0x800000, joined - 0x1000000, joined)
            data = joined.astype(np.float32) / 8388608.0
        elif width == 4:
            data = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
        elif width == 1:
            data = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
        else:
            raise ValueError(f"unsupported wav sample width: {width} bytes")
        if channels > 1:
            data = data.reshape(-1, channels).mean(axis=1)
        if rate is not None and int(rate) != file_rate:
            data = resample(data, file_rate, int(rate))
        return cls(
            data,
            samplerate=int(rate or file_rate),
            gain_db=gain_db,
            normalize=normalize,
            file_rate=file_rate,
        )

    def _prepare(self, frames: int) -> None:
        size = 1
        while size < frames + self.length - 1:
            size <<= 1
        self._size = size
        self._ir_fft = np.fft.rfft(self.ir, size)
        # History is NOT cleared here: a block size change must not click.

    def reset(self) -> None:
        self._history = np.zeros(self.length - 1, dtype=np.float32)

    def process(self, block: np.ndarray) -> np.ndarray:
        x = np.asarray(block, dtype=np.float32).reshape(-1)
        frames = int(x.size)
        if frames == 0:
            return x
        if self._size is None or frames + self.length - 1 > self._size:
            self._prepare(frames)
        joined = np.concatenate((self._history, x))
        spectrum = np.fft.rfft(joined, self._size)
        out = np.fft.irfft(spectrum * self._ir_fft, self._size)
        start = self.length - 1
        y = out[start:start + frames].astype(np.float32)
        self._history = joined[-(self.length - 1):]
        if self.gain != 1.0:
            y = y * self.gain
        return y
