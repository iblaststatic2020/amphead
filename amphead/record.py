"""A take: the amp's own output written to a wav while the player plays.

The callback must never wait on a disk. A block is handed to a queue and a thread of its own
does the writing, so a slow spin of the drive costs a dropped block (which is counted and
reported) rather than a hole in the audio. Pure apart from the file it writes: it takes blocks,
it does not know where they came from.
"""

from __future__ import annotations

import math
import queue
import threading
import time
import wave
from pathlib import Path

#: 24 bit is the smallest width that does not throw away what a guitar capture produces, and it
#: opens in every DAW without a codec. 16 bit at a hot input is audible on a clean amp.
SAMPLE_WIDTH = 3

#: Around two seconds of blocks at 128 frames. Deep enough that a slow disk does not cost a
#: block on a normal machine, shallow enough that a stuck writer is noticed quickly.
QUEUE_BLOCKS = 256


def _pcm24(block) -> bytes:
    """Interleaved 24-bit little endian from a float block in -1..1, clipped, not wrapped."""
    import numpy as np

    a = np.asarray(block, dtype=np.float32)
    a = np.clip(a, -1.0, 1.0)
    scale = float((1 << (8 * SAMPLE_WIDTH - 1)) - 1)
    as_int = np.rint(a * scale).astype(np.int32)
    raw = as_int.astype("<i4").view(np.uint8).reshape(-1, 4)[:, :SAMPLE_WIDTH]
    return raw.tobytes()


def latest(folder) -> dict:
    """The newest take already on disk, so a face opened later still has a file to play.

    A take lives in the engine's memory only while the engine runs. The file is the durable
    part, so the answer to "what did I just play" has to come from the folder, not the process.
    """
    folder = Path(folder)
    try:
        files = [p for p in folder.glob("*.wav") if p.is_file()]
        if not files:
            return {}
        newest = max(files, key=lambda p: p.stat().st_mtime)
    except OSError:
        return {}
    seconds = 0.0
    try:
        with wave.open(str(newest), "rb") as wav:
            rate = wav.getframerate()
            if rate:
                seconds = wav.getnframes() / float(rate)
    except (OSError, EOFError, wave.Error):
        seconds = 0.0
    return {"recording": False,
            "path": str(newest),
            "name": newest.name,
            "seconds": round(seconds, 2),
            "peak_db": None,
            "dropped": 0,
            "saved": True}


def _db(peak: float) -> float:
    peak = float(peak or 0.0)
    if peak <= 0.0:
        return -math.inf
    return 20.0 * math.log10(peak)


def default_name(samplerate: int, when: float | None = None) -> str:
    """A name a player can find later: the date and time they played, nothing clever."""
    stamp = time.strftime("%Y-%m-%d %H%M%S", time.localtime(when if when else time.time()))
    return f"take {stamp}"


class Take:
    """One recording, from the first block to the last."""

    def __init__(self, path, samplerate: int, channels: int = 2,
                 width: int = SAMPLE_WIDTH, queue_blocks: int = QUEUE_BLOCKS) -> None:
        self.path = Path(path)
        self.samplerate = int(samplerate)
        self.channels = int(channels)
        self.width = int(width)
        self._queue: queue.Queue = queue.Queue(maxsize=queue_blocks)
        self._writer: threading.Thread | None = None
        self._wave: wave.Wave_write | None = None
        self._frames = 0
        self._peak = 0.0
        self._dropped = 0
        self._started = 0.0
        self._stopped = 0.0
        self._error = ""
        self._lock = threading.Lock()

    # opening and closing -----------------------------------------------------
    def open(self) -> "Take":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._wave = wave.open(str(self.path), "wb")
        self._wave.setnchannels(self.channels)
        self._wave.setsampwidth(self.width)
        self._wave.setframerate(self.samplerate)
        self._started = time.time()
        self._writer = threading.Thread(target=self._drain, name="amphead-take", daemon=True)
        self._writer.start()
        return self

    def _drain(self) -> None:
        import numpy as np

        while True:
            block = self._queue.get()
            if block is None:
                break
            try:
                self._wave.writeframes(_pcm24(block))
                with self._lock:
                    self._frames += int(np.size(block)) // self.channels
            except Exception as exc:                  # a full disk must not look like audio
                with self._lock:
                    self._error = f"{type(exc).__name__}: {exc}"
                break

    # pushing and closing -----------------------------------------------------
    def push(self, block) -> None:
        """One block of the amp's output. Never blocks: a full queue costs a dropped block."""
        import numpy as np

        a = np.asarray(block, dtype=np.float32)
        if a.ndim > 1:                                # an interleaved block arrives as is
            peak = float(np.max(np.abs(a))) if a.size else 0.0
        else:
            peak = float(np.max(np.abs(a))) if a.size else 0.0
            if self.channels > 1:
                a = np.repeat(a.reshape(-1, 1), self.channels, axis=1).reshape(-1)
        with self._lock:
            self._peak = max(self._peak, peak)
        try:
            self._queue.put_nowait(a)
        except queue.Full:
            with self._lock:
                self._dropped += 1

    def stop(self) -> dict:
        """Close the file and say what was written. Safe to call twice.

        A writer that died on a full disk leaves a queue nobody drains, so the closing
        sentinel has to be dropped in without waiting for room. Waiting there is how a
        failed take turns into a hung engine.
        """
        if self._writer is None:
            return self.summary()
        if self._writer.is_alive():
            try:
                self._queue.put(None, timeout=10.0)
            except queue.Full:
                self._writer.join(timeout=1.0)
        else:
            try:
                while True:
                    self._queue.get_nowait()
            except queue.Empty:
                pass
            self._queue.put_nowait(None)
        self._writer.join(timeout=10.0)
        if self._writer.is_alive():
            with self._lock:
                self._error = self._error or "the writer thread did not finish in 10 s"
        else:
            self._writer = None
        try:
            self._wave.close()
        except Exception:
            pass
        self._stopped = time.time()
        return self.summary()

    # what happened -----------------------------------------------------------
    @property
    def dropped(self) -> int:
        return self._dropped

    def summary(self) -> dict:
        with self._lock:
            frames, peak, dropped = self._frames, self._peak, self._dropped
        seconds = frames / float(self.samplerate) if self.samplerate else 0.0
        said = {
            "path": str(self.path),
            "name": self.path.name,
            "seconds": round(seconds, 2),
            "frames": frames,
            "samplerate": self.samplerate,
            "peak_db": None if peak <= 0.0 else round(_db(peak), 1),
            "dropped": dropped,
            "recording": False,
        }
        if self._error:
            said["error"] = self._error
        return said

    def state(self) -> dict:
        with self._lock:
            frames, peak, dropped, error = self._frames, self._peak, self._dropped, self._error
        recording = self._writer is not None and self._writer.is_alive() and not error
        said = {"recording": recording,
                "path": str(self.path),
                "name": self.path.name,
                "seconds": round(frames / float(self.samplerate), 2) if self.samplerate else 0.0,
                "peak_db": None if peak <= 0.0 else round(_db(peak), 1),
                "dropped": dropped}
        if error:
            said["error"] = error
        return said
