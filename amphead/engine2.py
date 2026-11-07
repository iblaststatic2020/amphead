"""The no-DAW engine: our own device, our own buffer, our own chain.

Two streams, not one duplex stream. PortAudio's WASAPI duplex path pins the input
side at about 20 ms whatever the block size, because it resamples between two
endpoints. Two independent exclusive streams reported 5.8 ms each on an AudioBox
USB, 11.6 ms round trip, and ran three seconds with zero overflows, so that is
the shape this file builds.

Rules that hold whatever the chain is:

* the engine comes up **disarmed** with the master at ``SAFE_MASTER_DB``,
* the audio callback never allocates, never locks for long, and never raises,
* a dropped block is counted and reported, never hidden,
* the same chain code runs offline, so the maths can be tested without a device.

``sounddevice`` is optional: it is imported lazily so the plugin still imports
(and reports "no engine") on a machine that never installed it.
"""

from __future__ import annotations

import math
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field, replace

#: Where the master sits when the chain is disarmed. Quiet enough to be harmless,
#: not muted, so a wrong arm is audible as "too quiet" rather than silent-but-live.
SAFE_MASTER_DB = -60.0

#: Where the master lands when you arm. Measured on a -18 dBFS DI through a lead
#: capture and a 2x12 cabinet: the chain comes out about 16 dB up, so 0 dB clips on
#: double stops. -6 dB put the same DI at -2.8 dBFS peak with no clipped samples,
#: which is the headroom a player actually wants.
ARM_MASTER_DB = -6.0
METERS_HZ = 30.0

#: Blocks of silence pushed into the hand-off queue when the streams open, so the
#: output side starts with slack instead of chasing the input side from empty.
PRIME_BLOCKS = 3

#: Frames per callback. 512 is the measured sweet spot here: the device reports the
#: same 20 ms round trip at 128, 256 and 512 because kernel-streaming latency comes
#: from the driver buffer, not the block, while CPU per second of audio falls from
#: 32% of a core at 128 to 15% at 512 and starved output blocks go from 9 per minute
#: to zero. Going smaller buys nothing and costs reliability.
DEFAULT_BLOCKSIZE = 512


def db_to_gain(db: float) -> float:
    return float(10.0 ** (db / 20.0))


def gain_to_db(gain: float) -> float:
    return float(20.0 * math.log10(max(float(gain), 1e-9)))


def choose_devices(sd) -> tuple[int | None, int | None, str]:
    """Pick the pair that is actually playable on Windows, by measuring nothing but names.

    On this box the AudioBox's WASAPI endpoint advertises 90 ms of output latency and
    refuses exclusive mode outright, while the same hardware under WDM-KS holds 10 ms.
    Kernel streaming wins when it is there; WASAPI is the fallback for cards whose
    drivers only publish a WASAPI endpoint. Non-Windows hosts just use the default pair.
    """
    if not sys.platform.startswith("win"):
        return sd.default.device[0], sd.default.device[1], "default"
    apis = sd.query_hostapis()
    devices = sd.query_devices()

    def hostapi(row) -> str:
        return str(apis[row["hostapi"]]["name"])

    kernel = [(i, d) for i, d in enumerate(devices)
              if "WDM-KS" in hostapi(d)
              and ("audio input" in d["name"].lower() or "audiobox" in d["name"].lower())]
    out = [(i, d) for i, d in enumerate(devices)
           if "WDM-KS" in hostapi(d)
           and ("audio output" in d["name"].lower() or "audiobox" in d["name"].lower())
           and d["max_output_channels"] >= 2]
    if kernel and out:
        return kernel[0][0], out[0][0], "wdm-ks"
    return sd.default.device[0], sd.default.device[1], "default"


@dataclass
class Chain:
    """Everything the callback reads. Replaced whole, never mutated in place."""

    armed: bool = False
    master_db: float = SAFE_MASTER_DB
    input_gain_db: float = 0.0
    output_gain_db: float = 0.0
    amp: object | None = None
    tone: object | None = None
    cab: object | None = None


@dataclass
class Meter:
    """What the pane shows. Written by the callback, read by anyone."""

    peak_in: float = 0.0
    peak_out: float = 0.0
    rms_out: float = 0.0
    overflows: int = 0
    underflows: int = 0
    blocks: int = 0
    stamp: float = field(default_factory=time.time)


def process_chain(x, chain: Chain):
    """One block through the whole chain, in the order a player hears it.

    Input gain, then the capture, then the tone stack, then the cabinet, then the
    two output gains. The master is last and always applied, so a chain that is
    armed but quiet is quiet on purpose rather than by accident.
    """
    import numpy as np

    y = np.asarray(x, dtype=np.float32).reshape(-1) * db_to_gain(chain.input_gain_db)
    if chain.amp is not None:
        y = chain.amp.process(y)
    if chain.tone is not None:
        y = chain.tone.process(y)
    if chain.cab is not None:
        y = chain.cab.process(y)
    y = y * db_to_gain(chain.output_gain_db)
    y = y * db_to_gain(chain.master_db)
    y = np.nan_to_num(y, nan=0.0, posinf=0.0, neginf=0.0)
    np.clip(y, -1.0, 1.0, out=y)
    return y


class Engine:
    """A live instrument chain, alive for as long as the rig is running."""

    def __init__(
        self,
        samplerate: int = 44100,
        blocksize: int = 512,
        input_device: int | None = None,
        output_device: int | None = None,
        channels: int = 1,
    ) -> None:
        self.samplerate = int(samplerate)
        self.blocksize = int(blocksize)
        self.input_device = input_device
        self.output_device = output_device
        self.channels = int(channels)
        self.mode: str | None = None
        self._lock = threading.Lock()
        self._chain = Chain()
        self._meter = Meter()
        self._in_stream = None
        self._out_stream = None
        self._frames: deque = deque(maxlen=8)
        self._frames_lock = threading.Lock()
        self._capture: list = []
        self._capturing = False
        self._last_error: str | None = None

    # state -----------------------------------------------------------------
    @property
    def chain(self) -> Chain:
        return self._chain

    @property
    def meter(self) -> Meter:
        return self._meter

    def update(self, **kwargs) -> Chain:
        """Swap the whole chain atomically. Anything the callback sees is complete."""
        with self._lock:
            self._chain = replace(self._chain, **kwargs)
            return self._chain

    def set_gain(self, name: str, db: float) -> Chain:
        allowed = {"master_db", "input_gain_db", "output_gain_db"}
        if name not in allowed:
            raise ValueError(f"unknown gain {name!r}; expected one of {', '.join(sorted(allowed))}")
        return self.update(**{name: float(db)})

    # lifecycle -------------------------------------------------------------
    def _open(self, sd, exclusive: bool, wasapi: bool = True):
        # WDM-KS and MME devices reject WASAPI-only stream info outright, so the
        # setting is only ever attached to an endpoint that speaks WASAPI.
        settings = sd.WasapiSettings(exclusive=True) if (exclusive and wasapi) else None
        inn = sd.InputStream(
            device=self.input_device,
            samplerate=self.samplerate,
            blocksize=self.blocksize,
            channels=self.channels,
            dtype="float32",
            latency="low",
            callback=self._on_in,
            extra_settings=settings,
        )
        out = sd.OutputStream(
            device=self.output_device,
            samplerate=self.samplerate,
            blocksize=self.blocksize,
            channels=2,
            dtype="float32",
            latency="low",
            callback=self._on_out,
            extra_settings=settings,
        )
        return inn, out

    def start(self) -> str:
        """Open both streams and report which mode took.

        Order is deliberate: kernel streaming when the driver offers it, then WASAPI
        exclusive, then WASAPI shared. On the AudioBox the three are 20 ms, refused,
        and 112 ms round trip, so "it opened" is not the same as "it plays".
        """
        import sounddevice as sd

        apis = sd.query_hostapis()
        if self.input_device is None or self.output_device is None:
            dev_in, dev_out, picked = choose_devices(sd)
            self.input_device = self.input_device if self.input_device is not None else dev_in
            self.output_device = self.output_device if self.output_device is not None else dev_out
            self._last_error = f"devices auto-chosen ({picked})"

        def hostapi(index) -> str:
            return str(apis[sd.query_devices(int(index))["hostapi"]]["name"])

        wasapi = "WASAPI" in hostapi(self.input_device) or "WASAPI" in hostapi(self.output_device)
        named = "wdm-ks" if "WDM-KS" in hostapi(self.output_device) else (
            "wasapi" if wasapi else "other")
        attempts = [(True, wasapi, f"{named}-exclusive" if wasapi else named),
                    (False, wasapi, f"{named}-low" if not wasapi else "wasapi-shared")]
        last: Exception | None = None
        for exclusive, wants_wasapi, label in attempts:
            try:
                self._in_stream, self._out_stream = self._open(
                    sd, exclusive=exclusive, wasapi=wants_wasapi)
            except Exception as exc:
                last = exc
                self._in_stream = self._out_stream = None
                continue
            try:
                # Input first, then cushion, then output: the output callback is the
                # one that cannot wait, so it must never be the first to need a block.
                import numpy as np
                self._in_stream.start()
                with self._frames_lock:
                    self._frames.clear()
                    for _ in range(PRIME_BLOCKS):
                        self._frames.append(np.zeros(self.blocksize, dtype=np.float32))
                self._out_stream.start()
            except Exception as exc:
                last = exc
                self.stop()
                continue
            self.mode = label
            return self.mode
        self._last_error = f"{type(last).__name__}: {last}" if last else "no stream opened"
        raise RuntimeError(self._last_error)

    def stop(self) -> None:
        for stream in (self._in_stream, self._out_stream):
            if stream is not None:
                try:
                    stream.stop()
                    stream.close()
                except Exception:
                    pass
        self._in_stream = self._out_stream = None
        self.mode = None

    @property
    def running(self) -> bool:
        return self._in_stream is not None and self._in_stream.active

    @property
    def latency_ms(self) -> float | None:
        if not self.running:
            return None
        return (self._in_stream.latency + self._out_stream.latency) * 1000.0

    def panic(self) -> Chain:
        """Hard mute now, keep the streams open so the rig does not have to re-open a device."""
        chain = self.update(armed=False, master_db=SAFE_MASTER_DB)
        with self._frames_lock:
            self._frames.clear()
        self._meter.peak_out = 0.0
        self._meter.rms_out = 0.0
        return chain

    # capture ---------------------------------------------------------------
    def begin_capture(self) -> None:
        self._capture = []
        self._capturing = True

    def end_capture(self):
        """Return the recorded blocks as one float32 array, and stop capturing."""
        import numpy as np

        self._capturing = False
        blocks = self._capture
        self._capture = []
        if not blocks:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(blocks)

    # the callback bodies ---------------------------------------------------
    def _on_in(self, indata, frames, time_info, status) -> None:
        try:
            if status:
                self._count(status)
            self._process_block(indata, frames)
        except Exception as exc:                      # never let the callback raise
            self._last_error = f"{type(exc).__name__}: {exc}"
            self._blank_out(frames)

    def _on_out(self, outdata, frames, time_info, status) -> None:
        if status:
            self._count(status)
        try:
            with self._frames_lock:
                block = self._frames.popleft() if self._frames else None
        except Exception:
            block = None
        if block is None or block.size != frames:
            # Starved: the output asked and there was nothing waiting. That is silence
            # in the player's ear, so it counts as an underrun rather than vanishing.
            self._meter.underflows += 1
            outdata[:] = 0.0
            return
        outdata[:, 0] = block
        outdata[:, 1] = block

    def _process_block(self, indata, frames: int) -> None:
        import numpy as np

        chain = self._chain
        x = np.asarray(indata[:frames, 0], dtype=np.float32)
        self._meter.peak_in = float(np.max(np.abs(x))) if frames else 0.0
        self._meter.blocks += 1
        self._meter.stamp = time.time()

        if not chain.armed:
            self._blank_out(frames)
            return

        y = process_chain(x, chain)
        self._meter.peak_out = float(np.max(np.abs(y))) if y.size else 0.0
        self._meter.rms_out = float(np.sqrt(np.mean(np.square(y)))) if y.size else 0.0
        if self._capturing:
            self._capture.append(y.copy())
        self._write_out(y)

    def _write_out(self, y) -> None:
        # Input and output are separate streams, so the processed block is handed
        # over a short bounded queue. Eight blocks is 23 ms of slack at 128 frames;
        # if the queue ever fills the oldest block goes, which is a click, so the
        # underflow counter is the honest signal that it happened.
        with self._frames_lock:
            if len(self._frames) == self._frames.maxlen:
                self._meter.underflows += 1
            self._frames.append(y)

    def _blank_out(self, frames: int) -> None:
        import numpy as np

        self._meter.peak_out = 0.0
        self._meter.rms_out = 0.0
        self._write_out(np.zeros(frames, dtype=np.float32))

    def _count(self, status) -> None:
        if getattr(status, "input_overflow", False):
            self._meter.overflows += 1
        if getattr(status, "output_underflow", False):
            self._meter.underflows += 1

    @property
    def last_error(self) -> str | None:
        return self._last_error

    # offline ---------------------------------------------------------------
    def render(self, x, block: int | None = None):
        """Run the current chain over a buffer with no device at all.

        This is the same code the callback runs, which is the point: the tests can
        check gain staging, clamps and NaN handling on a machine with no audio.
        """
        import numpy as np

        x = np.asarray(x, dtype=np.float32).reshape(-1)
        block = int(block or self.blocksize)
        chain = self._chain
        out = np.zeros_like(x)
        for i in range(0, x.size, block):
            piece = x[i:i + block]
            out[i:i + block] = process_chain(piece, chain) if chain.armed else 0.0
        return out
