"""The engine: our own device, our own buffer, our own chain, no host application.

One duplex stream, not two. Two streams have to hand blocks over a queue, and every
block standing in that queue is a block of delay between the string and the speaker:
at 512 frames a six block cushion is 70 ms, which is enough to make a player feel
late and blame their hands. A duplex stream reads and writes in the same callback,
so there is nowhere for a queue to live and the latency is the device's alone. The
two-stream path stays as a fallback for a driver that refuses a duplex open, and it
says so in its mode name, because it costs delay a player can hear.

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
import os
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field, replace

from .gate import FLOOR_DB as GATE_FLOOR_DB, Gate
from .limits import SILENT_DB
from .park import AutoPark
from .record import Take
from .limits import master as clamp_master, gate as clamp_gate, trim as clamp_trim

#: Where the master sits when the chain is disarmed. Quiet enough to be harmless,
#: not muted, so a wrong arm is audible as "too quiet" rather than silent-but-live.
SAFE_MASTER_DB = SILENT_DB

#: Where the master lands when you arm. Measured on a -18 dBFS DI through a lead
#: capture and a 2x12 cabinet: the chain comes out about 16 dB up, so 0 dB clips on
#: double stops. -6 dB put the same DI at -2.8 dBFS peak with no clipped samples,
#: which is the headroom a player actually wants.
ARM_MASTER_DB = -6.0
METERS_HZ = 30.0

#: Blocks of silence pushed into the hand-off queue on the two-stream fallback only.
# The queue sits in the signal path, so this number *is* added latency: two blocks of
# cushion is 23 ms at 512 frames, and six was 70 ms. It is only ever paid when the
# driver refuses a duplex open, which the mode name reports out loud.
PRIME_BLOCKS = 2

#: Latency asked of each direction, in milliseconds. The driver's own "low" on this
#: machine is 10 ms per side, which is 20 ms round trip and audible enough to make a
#: player feel late. The same device honours a smaller request (it reports back the
#: figure asked for), and every millisecond here is a millisecond between the string
#: and the speaker. AMPHEAD_LATENCY_MS overrides it for a driver that cannot hold it.
TARGET_LATENCY_MS = float(os.environ.get("AMPHEAD_LATENCY_MS") or 5.0)

#: Frames per callback, measured rather than guessed. The felt round trip is the driver
#: buffer plus one block, so 256 is 5.8 ms of block on top of ~10 ms of buffer, and 128
#: buys 2.9 ms at the price of 0.7 ms of headroom, which overruns in practice. A chain
#: that gets heavier than the box can render at 256 asks the amp to measure again rather
#: than hoping (see amphead/tune.py).
DEFAULT_BLOCKSIZE = 256


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

    def virtual(name: str) -> bool:
        """Cables and loopbacks advertise themselves as an interface; they are not one.

        VB-Audio's virtual point, Steam's streaming pair and any "cable" device will open,
        play nothing into a guitar, and otherwise look exactly like the real card.
        """
        low = name.lower()
        return any(word in low for word in ("vb-audio", "cable", "virtual", "steam streaming",
                                            "voicemeeter", "loopback"))

    kernel = [(i, d) for i, d in enumerate(devices)
              if "WDM-KS" in hostapi(d) and not virtual(d["name"])]

    def stem(name: str) -> str:
        """The interface a kernel endpoint belongs to, with the endpoint word taken out.

        Kernel streaming publishes each direction as its own device ("AudioBox 96 Audio
        Input", "AudioBox 96 Audio Output"), so the two halves of one interface are found
        by their common beginning, not by this plugin knowing the vendor.
        """
        low = name.lower()
        if "(" in low and ")" in low:
            low = low[low.index("(") + 1:low.rindex(")")]
        for word in ("audio input", "audio output", "input", "output"):
            low = low.replace(word, " ")
        return "".join(ch for ch in low if ch.isalnum())

    ins = [(i, d) for i, d in kernel if d["max_input_channels"] >= 1]
    outs = [(i, d) for i, d in kernel if d["max_output_channels"] >= 2]
    for i, d in ins:
        for j, e in outs:
            shared = stem(d["name"])
            if i != j and len(shared) >= 3 and shared == stem(e["name"]):
                return i, j, "wdm-ks"
    if ins and outs:
        return ins[0][0], outs[0][0], "wdm-ks"
    return sd.default.device[0], sd.default.device[1], "default"


@dataclass
class Chain:
    """Everything the callback reads. Replaced whole, never mutated in place."""

    armed: bool = False
    master_db: float = SAFE_MASTER_DB
    input_gain_db: float = 0.0
    output_gain_db: float = 0.0
    # A gate is a stage like any other: present, switchable, off by default, because
    # silence is a choice the player makes rather than one the amp makes for them.
    gate: object | None = None
    gate_db: float = GATE_FLOOR_DB
    amp: object | None = None
    tone: object | None = None
    cab: object | None = None
    # Loaded and switched on are different things: panic takes a stage out of the path
    # without forgetting it, so the rig can be brought back without finding the file again.
    amp_on: bool = True
    tone_on: bool = True
    cab_on: bool = True

    def prime(self, frames: int) -> None:
        """Build every stage's transform bank for this block size, off the audio thread.

        The cabinet's partition set depends on the block size, and building it inside the
        first callback is 11 ms of work in a 5.8 ms slot: the load itself is a dropout, and
        a block size change measured against the old set is measured wrong. Anything that
        knows the size calls this before the stream opens.
        """
        if self.cab is not None:
            prime = getattr(self.cab, "prime", None)
            if callable(prime):
                prime(int(frames))


@dataclass
class Meter:
    """What the pane shows. Written by the callback, read by anyone."""

    peak_in: float = 0.0
    peak_out: float = 0.0
    rms_out: float = 0.0
    overflows: int = 0
    underflows: int = 0
    # A callback that took longer than its block is already a miss even when the driver
    # never complains: on kernel streaming a gap is silent until someone hears it.
    late: int = 0
    #: What the last callback cost, and the worst one since the counters were reset. The
    #: median says whether the chain fits on average; the peak says whether it ever did
    #: not, which is the difference between "looks fine" and "the amp hiccupped".
    work_ms: float = 0.0
    peak_ms: float = 0.0
    blocks: int = 0
    stamp: float = field(default_factory=time.time)


def process_chain(x, chain: Chain):
    """One block through the whole chain, in the order a player hears it.

    Input gain, then the capture, then the tone stack, then the cabinet, then the
    two output gains. The master is last and always applied, so a chain that is
    armed but quiet is quiet on purpose rather than by accident.
    """
    import numpy as np

    y = np.asarray(x, dtype=np.float32).reshape(-1)
    # The gate hears the guitar, not the amp: in front of the drive, so the threshold means
    # the same thing whatever the input gain is doing.
    if chain.gate is not None:
        y = chain.gate.process(y)
    y = y * db_to_gain(chain.input_gain_db)
    if chain.amp is not None and chain.amp_on:
        y = chain.amp.process(y)
    if chain.tone is not None and chain.tone_on:
        y = chain.tone.process(y)
    if chain.cab is not None and chain.cab_on:
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
        # 256, not 512: kernel streaming gives about 10 ms of driver buffer either way,
        # and the block is what the player actually feels on top of it. At 512 the felt
        # round trip was about 21 ms; at 256 it is about 13 ms with 3.3 ms of render
        # headroom left with a WaveNet capture and a 262k-tap cabinet. At 128 that
        # headroom is 0.7 ms and the output buffer starts overflowing.
        blocksize: int = 256,
        input_device: int | None = None,
        output_device: int | None = None,
        channels: int = 1,
        input_channel: int = 0,
    ) -> None:
        self.samplerate = int(samplerate)
        self.blocksize = int(blocksize)
        self.input_device = input_device
        self.output_device = output_device
        self.channels = int(channels)
        # Which socket on the interface the amp hears, counted from zero. The stream opens every
        # socket the device has, so switching sockets is one number, not a device reopen.
        self.input_channel = int(input_channel)
        self.input_channels = 0
        self.mode: str | None = None
        self._lock = threading.Lock()
        self._gate = Gate(threshold_db=GATE_FLOOR_DB, rate=self.samplerate)
        self._chain = Chain(gate=self._gate)
        self._meter = Meter()
        self._duplex = None
        self._in_stream = None
        self._out_stream = None
        self._frames: deque = deque(maxlen=8)
        self._frames_lock = threading.Lock()
        self._capture: list = []
        self._capturing = False
        self._last_error: str | None = None
        #: Errors thrown out of the audio callback. Never cleared by a later success: the
        #: count is how a player sees that the amp has been glitching, which no single
        #: message can convey.
        self._cb_errors = 0
        # Nobody plays forever in one sitting. After a long quiet stretch the amp parks its own
        # master, so an interface left live overnight is not still listening to the room.
        self._park = AutoPark()
        self._parked_by_idle = False
        # The take in progress, if any. A block is queued and a writer thread does the disk work,
        # so a slow drive costs a counted dropped block rather than a hole in the audio.
        self._take: Take | None = None
        # PortAudio calls finished_callback for an intentional close too. Without this flag
        # every calibration and every STOP wrote "the device closed" into the status, so a
        # clean restart read as an unplugged interface.
        self._closing = False

    # state -----------------------------------------------------------------
    # recording --------------------------------------------------------------
    def start_take(self, path, channels: int = 2) -> dict:
        """Start writing the amp's output to a wav. Refuses rather than stacking takes.

        The file is opened before the lock is taken, because opening it means a mkdir, a
        wave.open and a thread start: tens of milliseconds on a slow or network folder, and
        the audio callback waits behind this lock. Holding it across the open is a guaranteed
        burst of dropouts at the moment the player presses record.
        """
        if self._take is not None:
            raise ValueError(f"a take is already recording to {self._take.path}")
        take = Take(path, self.samplerate, channels=channels).open()
        with self._lock:
            if self._take is not None:
                take.stop()
                raise ValueError("two takes raced for the same engine; the second was not started")
            self._take = take
            # Pressing record is the player reaching for the amp, so the silence is over.
            self._park.reset()
            self._parked_by_idle = False
        return take.state()

    def stop_take(self) -> dict:
        with self._lock:
            take, self._take = self._take, None
        if take is None:
            raise ValueError("nothing is recording")
        return take.stop()

    def take_state(self) -> dict:
        take = self._take
        return take.state() if take is not None else {"recording": False}

    @property
    def parked_by_idle(self) -> bool:
        """True when the amp parked its own master because nobody has played for a while."""
        return self._parked_by_idle

    @property
    def chain(self) -> Chain:
        return self._chain

    @property
    def meter(self) -> Meter:
        return self._meter

    def reopen(self, samplerate: int) -> str:
        """Open the same device at another rate, keeping the chain where it is.

        A capture is weights fitted at one rate, so the stream has to meet it, not the
        other way round: most .nam files are 48 kHz while 44.1 kHz was the old default,
        and refusing those turned the plugin's whole library into "incompatible". The
        device is asked for the capture's rate; if the driver will not open there, the
        old rate is restored and the failure is raised so the caller can name both.
        """
        samplerate = int(samplerate)
        if samplerate == self.samplerate:
            return self.mode or ""
        old = self.samplerate
        was_running = self.running
        if was_running:
            self.stop()
        self.samplerate = samplerate
        self._gate = Gate(threshold_db=self._gate.threshold_db, rate=samplerate)
        with self._lock:
            self._chain = replace(self._chain, gate=self._gate,
                                  gate_db=self._gate.threshold_db)
        if not was_running:
            return ""
        try:
            return self.start()
        except Exception:
            self.samplerate = old
            self._gate = Gate(threshold_db=self._gate.threshold_db, rate=old)
            with self._lock:
                self._chain = replace(self._chain, gate=self._gate,
                                      gate_db=self._gate.threshold_db)
            try:
                self.start()
            except Exception:
                pass
            raise

    def update(self, **kwargs) -> Chain:
        """Swap the whole chain atomically. Anything the callback sees is complete."""
        cab = kwargs.get("cab")
        if cab is not None:
            # Build the cabinet's partition bank before the callback can see it. Doing it
            # inside the first block turns loading a long IR into a dropout at the exact
            # moment the player expects to hear the cabinet, and leaves the old bank in
            # place when the block size shrinks.
            prime = getattr(cab, "prime", None)
            if callable(prime):
                prime(self.blocksize)
        with self._lock:
            self._chain = replace(self._chain, **kwargs)
            # A player reaching for the master or the switch is the end of the silence.
            if "master_db" in kwargs or "armed" in kwargs:
                self._park.reset()
                self._parked_by_idle = False
            return self._chain

    def set_gain(self, name: str, db: float) -> Chain:
        allowed = {"master_db", "input_gain_db", "output_gain_db"}
        if name not in allowed:
            raise ValueError(f"unknown gain {name!r}; expected one of {', '.join(sorted(allowed))}")
        if name in ("input_gain_db", "output_gain_db"):
            # Trim either side of the amp, in dB. 24 dB up is already enough to push a clean DI
            # into the capture as hard as an overdrive pedal would; past that the knob stops
            # being a level and becomes a distortion control that clips the master anyway.
            db = clamp_trim(db)
        return self.update(**{name: float(db)})

    def reset_counters(self) -> None:
        """Counters belong to one stream. Carrying them across opens turns a 10 s soak into a
        running total and makes a fresh block size look busy when it is not."""
        self._meter.blocks = self._meter.underflows = self._meter.overflows = 0
        self._meter.late = 0
        self._meter.work_ms = self._meter.peak_ms = 0.0
        # `_last_error` is not kept here either: a successful open below is a clean slate.
        # The callback error count is, because that glitch did happen.


    @property
    def counters(self) -> dict:
        """Starved and dropped blocks since the stream opened. The only honest latency check."""
        return {"underflows": self._meter.underflows, "overflows": self._meter.overflows,
                "late": self._meter.late, "blocks": self._meter.blocks,
                "work_ms": round(self._meter.work_ms, 2),
                "peak_ms": round(self._meter.peak_ms, 2),
                # Failures inside the callback, which are otherwise invisible: the stream is
                # still open and the pane still says LIVE, but the output has been silence.
                "callback_errors": self._cb_errors}

    def set_gate(self, db: float) -> Chain:
        """Move the gate threshold. The bottom of the range is off, not -80 dB."""
        db = clamp_gate(db)
        self._gate.set_threshold(db)
        return self.update(gate_db=db)

    # lifecycle -------------------------------------------------------------
    def _open_split(self, sd, exclusive: bool, wasapi: bool = True):
        """Two independent streams: the fallback, and the one that costs a queue."""
        # WDM-KS and MME devices reject WASAPI-only stream info outright, so the
        # setting is only ever attached to an endpoint that speaks WASAPI.
        settings = sd.WasapiSettings(exclusive=True) if (exclusive and wasapi) else None
        inn = sd.InputStream(
            device=self.input_device,
            samplerate=self.samplerate,
            blocksize=self.blocksize,
            channels=max(self.channels, self.input_channels, self.input_channel + 1),
            dtype="float32",
            latency="low",
            callback=self._on_in,
            extra_settings=settings,
            finished_callback=self._device_closed,
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
            finished_callback=self._device_closed,
        )
        return inn, out

    def _open_duplex(self, sd, exclusive: bool, wasapi: bool = True, latency=None):
        """One stream, both directions.

        The input device and the output device are two endpoints of one interface, so
        the stream is opened with a device pair. Nothing is buffered between them: the
        block the input hands over is the block the output is waiting for, which is why
        this path has no prime and no queue depth to argue about.
        """
        settings = sd.WasapiSettings(exclusive=True) if (exclusive and wasapi) else None
        return sd.Stream(
            device=(self.input_device, self.output_device),
            samplerate=self.samplerate,
            blocksize=self.blocksize,
            channels=(max(self.channels, self.input_channels, self.input_channel + 1), 2),
            dtype="float32",
            latency="low" if latency is None else latency,
            callback=self._on_duplex,
            extra_settings=settings,
            finished_callback=self._device_closed,
        )

    def start(self) -> str:
        """Open the device and report which mode took.

        Order is deliberate: duplex first, because a single stream cannot hold the
        signal; then the two-stream fallback, which can and therefore does. Then which
        timing model: kernel streaming when the driver offers it, then WASAPI exclusive,
        then WASAPI shared. On the AudioBox those are 20 ms, refused and 112 ms, so
        "it opened" is not the same as "it plays".
        """
        self.reset_counters()
        # A new open is a new stream. Keeping yesterday's "device closed" after a clean
        # restart made every status read like a fault that was still happening.
        self._last_error = None
        # Prime for the size this stream will actually use. Calibration opens a new stream
        # for every candidate, so this is also what makes each candidate's measurement its
        # own: without it, a smaller block runs padded to the old, larger filter and looks
        # exactly as expensive as the size it is supposed to beat.
        self._chain.prime(self.blocksize)
        import sounddevice as sd

        apis = sd.query_hostapis()
        if self.input_device is None or self.output_device is None:
            dev_in, dev_out, picked = choose_devices(sd)
            self.input_device = self.input_device if self.input_device is not None else dev_in
            self.output_device = self.output_device if self.output_device is not None else dev_out
            self._last_error = f"devices auto-chosen ({picked})"

        def hostapi(index) -> str:
            return str(apis[sd.query_devices(int(index))["hostapi"]]["name"])

        try:
            self.input_channels = int(sd.query_devices(int(self.input_device))["max_input_channels"])
        except Exception:
            self.input_channels = self.channels
        if self.input_channels and self.input_channel >= self.input_channels:
            self.input_channel = 0

        wasapi = "WASAPI" in hostapi(self.input_device) or "WASAPI" in hostapi(self.output_device)
        named = "wdm-ks" if "WDM-KS" in hostapi(self.output_device) else (
            "wasapi" if wasapi else "other")
        timing = [(True, f"{named}-exclusive" if not wasapi else "wasapi-exclusive"),
                  (False, "wasapi-shared" if wasapi else named)]
        # Duplex before split at each timing model, and never the other way round: the
        # fallback works, so it would win every time and quietly cost a queue. Inside
        # duplex, the tight request comes first and the driver's own "low" is the
        # safety net, so a driver that refuses 5 ms still gets a queue-free stream.
        asked = TARGET_LATENCY_MS / 1000.0
        attempts = []
        for exclusive, label in timing:
            attempts.append(("duplex", exclusive, label, asked))
            attempts.append(("duplex", exclusive, label + "-low", None))
            attempts.append(("split", exclusive, label + "-queue", None))

        last: Exception | None = None
        for kind, exclusive, label, latency in attempts:
            try:
                if kind == "duplex":
                    stream = self._open_duplex(sd, exclusive=exclusive, wasapi=wasapi,
                                               latency=latency)
                    stream.start()
                    self._duplex = stream
                    self._in_stream = self._out_stream = None
                else:
                    import numpy as np

                    inn, out = self._open_split(sd, exclusive=exclusive, wasapi=wasapi)
                    # Input first, then cushion, then output: the output callback is the
                    # one that cannot wait, so it must never be the first to need a block.
                    inn.start()
                    with self._frames_lock:
                        self._frames.clear()
                        for _ in range(PRIME_BLOCKS):
                            self._frames.append(np.zeros(self.blocksize, dtype=np.float32))
                    out.start()
                    self._in_stream, self._out_stream = inn, out
                    self._duplex = None
                    label = f"{label}+queue"
            except Exception as exc:
                last = exc
                self.stop()
                continue
            self.mode = label
            self._last_error = None
            return self.mode
        self._last_error = f"{type(last).__name__}: {last}" if last else "no stream opened"
        raise RuntimeError(self._last_error)

    def stop(self) -> None:
        take, self._take = self._take, None
        if take is not None:
            take.stop()
        self._closing = True
        try:
            for stream in (self._duplex, self._in_stream, self._out_stream):
                if stream is not None:
                    try:
                        stream.stop()
                        stream.close()
                    except Exception:
                        pass
        finally:
            self._closing = False
        self._duplex = self._in_stream = self._out_stream = None
        self.mode = None

    @property
    def running(self) -> bool:
        """Whether audio is flowing. A device that was pulled out mid-take makes the question
        itself raise, so this answers False and keeps the reason instead of taking `status()`,
        and every tool that reads it, down with it."""
        stream = self._duplex if self._duplex is not None else self._in_stream
        if stream is None:
            return False
        try:
            return bool(stream.active)
        except Exception as exc:  # PortAudioError once the interface is gone
            if not self._last_error:
                self._last_error = f"{type(exc).__name__}: {exc}"
            return False

    @property
    def latency_ms(self) -> float | None:
        """What the device charges, in and out. A queue on the fallback is extra."""
        if not self.running:
            return None
        if self._duplex is not None:
            said = self._duplex.latency
            pair = said if isinstance(said, (tuple, list)) else (said, said)
            return (float(pair[0]) + float(pair[1])) * 1000.0
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
            self._fail_callback(exc)
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

    def _render(self, indata, frames: int):
        """One block in, one block out. The same code on both device paths."""
        import numpy as np

        chain = self._chain
        col = min(self.input_channel, indata.shape[1] - 1) if getattr(indata, "ndim", 2) > 1 else 0
        x = np.asarray(indata[:frames, col] if getattr(indata, "ndim", 2) > 1 else indata[:frames],
                       dtype=np.float32)
        self._meter.peak_in = float(np.max(np.abs(x))) if frames else 0.0
        self._meter.blocks += 1
        # Count the quiet on the input, and park the master when it has gone on long enough.
        # The chain stays armed: this is a level move, not a disarm, so raising the master is
        # all it takes to hear the amp again.
        if chain.armed and self._park.hear(self._meter.peak_in):
            # Parked from the audio thread, so no lock and no update(): the chain is a frozen
            # dataclass, and building the replacement here then storing it once is atomic
            # under the GIL. update() takes the lock the control thread holds while a take's
            # file opens, which is an xrun at the worst possible moment.
            self._chain = chain = replace(chain, master_db=SAFE_MASTER_DB)
            self._parked_by_idle = True
        self._meter.stamp = time.time()

        # The chain runs while parked or disarmed too. Skipping it freezes the cabinet's ring
        # buffer, the model's layer histories and the biquad state, so the first block after
        # GO LIVE convolves new input against minutes-old spectra: a burst of the wrong tail
        # on the first note, which the player blames on the capture. The output is discarded
        # instead, which is silence without the stale state behind it.
        started = time.perf_counter()
        y = process_chain(x, chain)
        if frames:
            work_ms = (time.perf_counter() - started) * 1000.0
            self._meter.work_ms = work_ms
            if work_ms > self._meter.peak_ms:
                self._meter.peak_ms = work_ms
            if work_ms > 1000.0 * frames / float(self.samplerate):
                self._meter.late += 1
        if not chain.armed:
            # A take that is running keeps the time it was promised: every block still lands
            # in the file, as silence. Dropping them makes a wav shorter than the performance,
            # with a jump where the gap was, while the state still says recording.
            y = np.zeros(frames, dtype=np.float32)
        self._meter.peak_out = float(np.max(np.abs(y))) if y.size else 0.0
        self._meter.rms_out = float(np.sqrt(np.mean(np.square(y)))) if y.size else 0.0
        if self._capturing:
            self._capture.append(y.copy())
        take = self._take
        if take is not None:
            take.push(y)                              # queued, never written in the callback
        return y

    def _on_duplex(self, indata, outdata, frames, time_info, status) -> None:
        """Read, process, write: one callback, so no block can stand in a queue."""
        try:
            if status:
                self._count(status)
            y = self._render(indata, frames)
        except Exception as exc:                      # never let the callback raise
            self._fail_callback(exc)
            outdata[:] = 0.0
            return
        outdata[:, 0] = y
        outdata[:, 1] = y

    def _process_block(self, indata, frames: int) -> None:
        """The two-stream path: render, then hand the block over the queue."""
        self._write_out(self._render(indata, frames))

    def _write_out(self, y) -> None:
        # Input and output are separate streams, so the processed block is handed
        # over a short bounded queue. Eight blocks is 23 ms of slack at 128 frames;
        # if the queue ever fills the oldest block goes, which is a click, so the
        # underflow counter is the honest signal that it happened.
        with self._frames_lock:
            if len(self._frames) == self._frames.maxlen:
                self._meter.underflows += 1
            self._frames.append(y)

    def _device_closed(self) -> None:
        """PortAudio says the stream ended on its own: unplugged, sleeping, or taken over.

        PortAudio runs this off the audio thread. Without it a vanished interface leaves the
        stream object looking open, so `running` stays true and every readout says LIVE while
        the device is gone. Setting the reason here makes the next status say what happened.
        """
        if self._closing:
            return
        self._last_error = "the audio device closed the stream (unplugged, asleep, or taken over)"
        self._parked_by_idle = False
        self._chain = replace(self._chain, armed=False, master_db=SAFE_MASTER_DB)

    def _fail_callback(self, exc: BaseException) -> None:
        """Record a callback failure and make the silence visible.

        Swallowing the exception and returning zeros leaves `armed` true, so every readout
        says the amp is live while the player hears nothing, and the only clue is a string
        buried in a status field. Dropping the master and the arm flag means the next status
        reads NOT LIVE with a reason, which is what a player needs to stop looking at cables.
        """
        self._cb_errors += 1
        self._last_error = f"{type(exc).__name__}: {exc}"
        try:
            self._chain = replace(self._chain, armed=False, master_db=SAFE_MASTER_DB)
        except Exception:
            pass

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
