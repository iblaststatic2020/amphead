"""Cabinet impulse responses as a streaming filter.

An IR is a finite impulse response, so the correct operation is a plain linear
convolution. The only question is doing it fast enough inside an audio callback.

Overlap-save with one transform the length of the whole IR is exact but scales with the
wrong number: a five second cabinet at 44.1 kHz is 262144 taps, which forces a 524288
point transform every 512 frames. Measured here that is 13.6 ms of work for 11.6 ms of
audio, 117% of realtime, and the output stream starves: the amp cuts in and out when a
long cabinet is chosen.

So the IR is cut into partitions and each partition is transformed once, at load time,
with a transform only twice the block size. Per block the cost is one forward transform,
one multiply-accumulate across the partitions, and one inverse transform. The arithmetic
is the same convolution, spread so the work per block stays small and flat however long
the cabinet is.

It is exact. The tests compare a streamed signal against transform convolution over the
whole signal and require a match to 1e-5, for a short cabinet and for one much longer than
the block, because "sounds right" is not a test and a truncated overlap clicks every block.
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
        self._block: int | None = None
        self._partitions = 0
        self._ir_parts: np.ndarray | None = None
        self._ring: np.ndarray | None = None
        self._pos = 0
        self._tail = np.zeros(0, dtype=np.float32)

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

    #: A cabinet is not worth more partitions than this before the rest is noise floor.
    MAX_PARTITIONS = 4096
    #: Set only if an IR was longer than the cap, so a caller can say so out loud.
    truncated_at = 0

    def _prepare(self, frames: int) -> None:
        """Cut the IR into partitions of one block and transform each one, once.

        With ``P`` partitions the filter is exact up to ``P * frames`` taps, which is why
        the transform stays small and the length of the cabinet stops mattering to the cost
        of a block. A five second IR at 512 frames is 512 partitions, not a 524288 point
        transform.
        """
        block = int(frames)
        parts = min(self.MAX_PARTITIONS, -(-self.length // block))
        padded = np.zeros(parts * block, dtype=np.float64)
        padded[: self.length] = self.ir
        self._ir_parts = np.fft.rfft(padded.reshape(parts, block), 2 * block, axis=1)
        self._ring = np.zeros((parts, block + 1), dtype=np.complex128)
        self._block = block
        self._partitions = parts
        self._pos = 0
        self.truncated_at = 0 if parts * block == self.length else parts * block
        # The frame is always [previous block, this block], so the history has to be exactly
        # one block wide. Leaving it short would frame the signal as [this block, silence],
        # which is the same convolution arriving one block late.
        self._tail = np.zeros(block, dtype=np.float32)

    def reset(self) -> None:
        if self._ring is not None:
            self._ring[:] = 0
        self._pos = 0
        self._tail = np.zeros(self._block or 0, dtype=np.float32)

    def process(self, block: np.ndarray) -> np.ndarray:
        x = np.asarray(block, dtype=np.float32).reshape(-1)
        frames = int(x.size)
        if frames == 0:
            return x
        if self._block is None:
            self._prepare(frames)
        elif frames > self._block:
            # The block grew: rebuild the partition set, carrying the last full block of input
            # over as the new frame's history so the seam stays continuous.
            carry = self._tail
            self._prepare(frames)
            keep = min(carry.size, frames)
            if keep:
                self._tail[:keep] = carry[-keep:]
        elif frames < self._block:
            # A short last block is padded up to the block the filter is built for. Processing it
            # at a different size would throw away the history the partitions are holding, and the
            # tail of a cabinet is exactly what that history is for.
            padded = np.zeros(self._block, dtype=np.float32)
            padded[:frames] = x
            return self._run(padded)[:frames]
        return self._run(x)

    def _run(self, x: np.ndarray) -> np.ndarray:
        """One full block through the partitions: frame, transform, accumulate, inverse."""
        frames = self._block
        frame = np.concatenate((self._tail, x))
        spectrum = np.fft.rfft(frame, 2 * frames)
        self._ring[self._pos] = spectrum
        order = (self._pos - np.arange(self._partitions)) % self._partitions
        acc = np.einsum("ij,ij->j", self._ring[order], self._ir_parts)
        out = np.fft.irfft(acc, 2 * frames)
        y = out[frames:].astype(np.float32)
        self._tail = np.asarray(x, dtype=np.float32).copy()
        self._pos = (self._pos + 1) % self._partitions
        if self.gain != 1.0:
            y = y * self.gain
        return y
