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

Partitioning makes the cost flat per audible tap, not free: every partition is another
multiply-accumulate in every block. IR packs often pad a 0.25 s cabinet to a power of two,
which turns 51 quiet partitions into 1024 busy ones and is enough to stutter a block that
was comfortable before. So a loaded wav is trimmed to its last audible sample first; the
padding was never part of the cabinet.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np


#: A cabinet's usable tail is its first few hundred milliseconds. Everything after that is
#: room, noise, or dither, and a file padded to a power of two can be seconds of near-silence.
#: Below this level the tail is inaudible even with the master up, but it still costs a
#: partition per block and is the difference between a smooth amp and a stuttering one.
TRIM_BELOW_DB = -80.0
TRIM_KEEP_MS = 50.0


def trim_tail(ir: np.ndarray, samplerate: int) -> np.ndarray:
    """Drop trailing near-silence, keeping the last audible decay and a short grace tail.

    A "5.9 second" cabinet whose last audible sample is at 0.24 s is not a long cabinet; it
    is a short cabinet in a padded file. Feeding the padding through the partition bank adds
    work to every block for sound nobody can hear. The cut is relative to the IR's own peak
    and 80 dB down, well below the noise floor of any guitar recording.
    """
    ir = np.asarray(ir, dtype=np.float32).reshape(-1)
    if ir.size < 2:
        return ir
    peak = float(np.max(np.abs(ir)))
    if peak <= 0.0:
        return ir
    floor = peak * (10.0 ** (TRIM_BELOW_DB / 20.0))
    above = np.flatnonzero(np.abs(ir) > floor)
    if above.size == 0:
        return ir
    keep = max(2, int(round(samplerate * TRIM_KEEP_MS / 1000.0)))
    end = min(ir.size, int(above[-1]) + 1 + keep)
    if end >= ir.size:
        return ir
    return np.ascontiguousarray(ir[:end])


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
        #: The file's length before trailing near-silence was removed, for a caller that
        #: wants to mention the saving. Zero means the file was already tight.
        self.trimmed_from = 0
        self.samplerate = samplerate
        #: The rate the impulse response was captured at, before any resampling.
        self.file_rate = int(file_rate or samplerate or 0)
        self.gain = float(10.0 ** (gain_db / 20.0))
        self._block: int | None = None
        self._partitions = 0
        self._ir_parts: np.ndarray | None = None
        self._ring: np.ndarray | None = None
        self._hist: np.ndarray | None = None
        self._hist_pos = 0
        self._pos = 0
        self._tail = np.zeros(0, dtype=np.float32)

    @classmethod
    def from_wav(
        cls,
        path: str | Path,
        gain_db: float = 0.0,
        normalize: bool = True,
        rate: int | None = None,
        trim: bool = True,
    ) -> "Cab":
        """Read a 16, 24 or 32 bit PCM wav with the standard library only.

        scipy would do this in one line, but it is a 40 MB dependency for one file
        format. ``rate`` is the rate the engine runs at, and the file is resampled
        to it, because a 44.1 kHz cabinet in a 48 kHz chain is a different cabinet.

        ``trim`` drops trailing near-silence before the IR reaches the partition bank.
        A padded cabinet costs a full partition for every silent block, and one padded to
        a power of two can be seconds of it. Pass ``trim=False`` to keep the file exact.
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
        original_length = int(data.size)
        if trim:
            data = trim_tail(data, file_rate)
        trimmed_from = original_length if data.size < original_length else 0
        if rate is not None and int(rate) != file_rate:
            data = resample(data, file_rate, int(rate))
        cab = cls(
            data,
            samplerate=int(rate or file_rate),
            gain_db=gain_db,
            normalize=normalize,
            file_rate=file_rate,
        )
        cab.trimmed_from = trimmed_from
        return cab

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
        parts = max(1, min(self.MAX_PARTITIONS, -(-self.length // block)))
        room = parts * block
        padded = np.zeros(room, dtype=np.float64)
        # A cabinet longer than the partition cap cannot be stored in full, so it is cut
        # here rather than at assignment time: `padded[: self.length] = self.ir` raises when
        # the IR outruns the buffer, and inside an audio callback that is silence forever.
        padded[: min(self.length, room)] = self.ir[:room]
        self._ir_parts = np.fft.rfft(padded.reshape(parts, block), 2 * block, axis=1)
        # Two copies of the ring let the callback read the last P spectra as one
        # contiguous slice instead of gathering them with a modulo index array.
        self._ring = np.zeros((2 * parts, block + 1), dtype=np.complex128)
        self._frame = np.empty(2 * block, dtype=np.float32)
        self._acc = np.empty(block + 1, dtype=np.complex128)
        # One block of time-domain history per partition, plus one extra block: a
        # ring spectrum is the transform of [previous block, this block], so the
        # oldest window needs the block before it. A short block and a block size
        # change both read it, because either one moves the grid the partitions
        # were cut on.
        self._hist = np.zeros((parts + 1) * block, dtype=np.float32)
        self._hist_pos = 0
        # Oldest partition first, to match that slice's order.
        self._ir_parts_rev = self._ir_parts[::-1]
        self._block = block
        self._partitions = parts
        self._pos = 0
        self.truncated_at = 0 if parts * block == self.length else parts * block
        # The frame is always [previous block, this block], so the history has to be exactly
        # one block wide. Leaving it short would frame the signal as [this block, silence],
        # which is the same convolution arriving one block late.
        self._tail = np.zeros(block, dtype=np.float32)

    def prime(self, frames: int) -> None:
        """Build the transform bank now instead of in the audio callback.

        `_prepare` is ~11 ms for a five second cabinet at 256 frames, which is two blocks
        of work landing in one slot: the load itself costs a dropout. Callers that know
        the block size (the loader, a block size change) hand it over here, off the audio
        thread.
        """
        block = int(frames)
        if block > 0 and block != self._block:
            self._prepare(block)
            self._pos = 0

    def reset(self) -> None:
        if self._ring is not None:
            self._ring[:] = 0
        self._pos = 0
        if self._hist is not None:
            self._hist[:] = 0
            self._hist_pos = 0
        self._tail = np.zeros(self._block or 0, dtype=np.float32)

    def _push_hist(self, samples: np.ndarray) -> None:
        """Append the real stream to the time-domain history, wrapping if needed."""
        size = int(samples.size)
        total = self._hist.size
        pos = self._hist_pos
        if pos + size <= total:
            self._hist[pos:pos + size] = samples
        else:
            first = total - pos
            self._hist[pos:] = samples[:first]
            self._hist[:size - first] = samples[first:]
        self._hist_pos = (pos + size) % total

    def _hist_stream(self) -> np.ndarray:
        """The history buffer in chronological order."""
        pos = self._hist_pos
        if pos == 0:
            return self._hist.copy()
        return np.concatenate((self._hist[pos:], self._hist[:pos]))

    def _resegment_ring(self) -> None:
        """Rebuild the ring spectra on the block grid the stream just moved to."""
        block = self._block
        parts = self._partitions
        stream = self._hist_stream()
        # The last P + 1 blocks of the real stream are needed: the oldest of them is
        # only there to frame the one after it, because a ring spectrum is the
        # transform of [previous block, this block], not of the block alone.
        segments = stream.reshape(parts + 1, block)
        real_ring = self._ring
        self._ring = np.zeros_like(real_ring)
        self._tail = np.zeros(block, dtype=np.float32)
        self._pos = 0
        out = None
        for seg in segments:
            out = self._run(seg)
        real_ring[:] = self._ring
        self._ring = real_ring
        return out

    def process(self, block: np.ndarray) -> np.ndarray:
        x = np.asarray(block, dtype=np.float32).reshape(-1)
        frames = int(x.size)
        if frames == 0:
            return x
        if self._block is None:
            self._prepare(frames)
            self._push_hist(x)
        elif frames > self._block:
            # The block grew: rebuild the partition set, then re-derive the ring from
            # the real stream's history on the new grid. The old grid's spectra are
            # worthless after the block size moves, and the carried tail alone is not
            # enough for a cabinet that spans more than one partition.
            old_stream = self._hist_stream()
            self._prepare(frames)
            seed = min(self._hist.size, old_stream.size)
            if seed:
                self._push_hist(old_stream[-seed:])
            self._push_hist(x)
            return self._resegment_ring()
        elif frames < self._block:
            # A short last block is padded up to the block the filter is built for. The
            # padded tail is what the output wants for those frames, but the history for
            # the next block must be the last block of the REAL stream: leaving the pad
            # in the tail puts block - frames zeros between two notes and clicks.
            old_tail = self._tail.copy()
            padded = np.zeros(self._block, dtype=np.float32)
            padded[:frames] = x
            y = self._run(padded)[:frames]
            self._push_hist(x)
            self._resegment_ring()
            return y
        else:
            self._push_hist(x)
        return self._run(x)

    def _run(self, x: np.ndarray) -> np.ndarray:
        """One full block through the partitions: frame, transform, accumulate, inverse."""
        frames = self._block
        frame = self._frame
        frame[:frames] = self._tail
        frame[frames:] = x
        spectrum = np.fft.rfft(frame, 2 * frames)
        pos = self._pos
        parts = self._partitions
        # Write the current spectrum into both halves. The P spectra ending at this
        # block are then one contiguous slice, oldest first, so no modulo gather and
        # no temporary index array are needed.
        self._ring[pos] = spectrum
        self._ring[pos + parts] = spectrum
        acc = self._acc
        np.einsum("ij,ij->j", self._ring[pos + 1:pos + parts + 1],
                  self._ir_parts_rev, out=acc)
        out = np.fft.irfft(acc, 2 * frames)
        y = out[frames:].astype(np.float32)
        np.copyto(self._tail, x)
        self._pos = (pos + 1) % parts
        if self.gain != 1.0:
            y = y * self.gain
        return y
