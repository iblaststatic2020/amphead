"""The engine process: one JSON object per line on stdin, one per line on stdout.

It runs out of process on purpose. The audio callback then shares nothing with the
agent, the pane, or a long tool call, and ``panic`` can stop a note even if the
caller is wedged. Everything it can do is a verb here, so the client in
``local_port.py`` is a thin pipe and the pane keeps talking to verbs it already
knows.

Run it with the interpreter that has numpy and sounddevice:

    python -m amphead.engine_main --samplerate 44100 --blocksize 512
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import traceback
from pathlib import Path

from . import faults
from . import record as recording
from . import settings
from . import tune
from .engine2 import ARM_MASTER_DB, SAFE_MASTER_DB, Engine


class TuneCancelled(Exception):
    """A tune that was asked to stop between blocks, not a tune that failed."""


def engine_from_settings(**over) -> Engine:
    """An engine built from the settings file, so the pane and the file agree.

    The device pair, the guitar channel, the rate and the block are all choices this
    machine had to be measured to learn; another machine answers differently. Settings
    first, keyword arguments last, because the caller who names a value means it.
    """
    from . import engine2

    audio = settings.audio()
    audio.update({k: v for k, v in over.items() if v is not None})
    engine2.TARGET_LATENCY_MS = float(audio.get("latency_ms") or engine2.TARGET_LATENCY_MS)
    saved = settings.load()
    return Engine(
        samplerate=int(audio["samplerate"]),
        blocksize=int(audio["blocksize"]),
        input_device=audio.get("input_device"),
        output_device=audio.get("output_device"),
        input_channel=max(0, int(audio["input_channel"]) - 1),
        bpm=float(saved.get("bpm") or 120.0),
        # The click is a monitor the player switches on for this session, not a setting.
        # Restoring it here is how every GO LIVE started ticking without being asked.
        metronome=False,
        metronome_db=float(-18.0 if saved.get("metronome_db") is None else saved.get("metronome_db")),
    )


def _kind_of(verb: str) -> str:
    """Which kind of file a failing verb was holding, in the words the fixes module uses."""
    if verb in ("load_capture", "clear_capture"):
        return faults.CAPTURE
    if verb in ("load_cab", "clear_cab"):
        return faults.CAB
    if verb == "tune":
        return "tune"
    return ""


class Server:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.capture_name: str | None = None
        self.cab_name: str | None = None
        # A capture whose training rate the device will not run. The amp still plays it, because
        # a library that loads with a named tone error beats a library that refuses to load, but
        # the pane has to carry the reason.
        self.rate_mismatch: dict | None = None
        self.tone = {"bass": 0.0, "mid": 0.0, "treble": 0.0}
        #: The last take this engine finished, so asking what was just recorded answers with
        #: that take rather than with whatever wav happens to be newest in the folder.
        self.last_take: dict | None = None
        # A tune is a long, device-owning job. Running it on the request loop made every other
        # verb wait behind it, so the pane looked dead for the whole measurement. It lives on a
        # thread now and reports its phase; the loop keeps answering status while it works.
        self._tune_lock = threading.Lock()
        self._tune_stop = threading.Event()
        self._tune_thread: threading.Thread | None = None
        self._tuning = {
            "running": False, "phase": "", "started_at": None, "finished_at": None,
            "seconds": 0.0, "cancelled": False, "error": "", "result": None,
        }

    # every verb returns plain json ----------------------------------------
    # recording --------------------------------------------------------------
    def recordings_dir(self) -> Path:
        """Beside the settings file, so the player already knows where it is."""
        return settings.path().parent / "recordings"

    def record_start(self, path: str | None = None, name: str | None = None) -> dict:
        """Open a take on the amp's output. The name defaults to when the player pressed record."""
        stamp = recording.default_name(self.engine.samplerate)
        folder = self.recordings_dir()
        if path:
            target = Path(path)
            if target.is_dir():
                target = target / f"{stamp}.wav"
            elif not target.parent.is_dir():
                # A take never makes a tree. If the folder the caller named is not there, the
                # path is a typo or a guess, and the answer is the folder that does exist.
                raise ValueError(f"the folder {target.parent} does not exist, so a take cannot "
                                 f"go there; name a folder that exists, or leave path out to "
                                 f"record to {folder}")
        else:
            # A take name arrives from an agent, so it is a name, not a path: keep the last
            # component and drop what a filename cannot hold. "..\..\x" must not walk out of
            # the recordings folder.
            clean = str(name or "").replace("\\", "/").split("/")[-1]
            clean = "".join(ch for ch in clean if ch.isprintable() and ch not in '<>:"|?*').strip(" .")
            target = folder / f"{clean or stamp}.wav"
        # Never over a file that already exists: a second take in the same minute used to
        # replace the first, and the first is the one thing here that cannot be made again.
        target = recording.free_path(target)
        # A take is recorded after the master, so a master at the bottom writes silence. Refuse
        # with the fix rather than hand back a wav full of nothing.
        live = self.status()
        if not live.get("armed"):
            raise ValueError("the amp is not live: press GO LIVE, then record")
        if float(live.get("master_db") or SAFE_MASTER_DB) <= -59.0:
            raise ValueError("the master is silent, so the take would be: raise the master first")
        self.last_take = None
        said = self.engine.start_take(target)
        said["dir"] = str(target.parent)
        return said

    def record_stop(self) -> dict:
        said = self.engine.stop_take()
        if said.get("path"):
            self.last_take = said
        return said

    def take_state(self) -> dict:
        """The live take, else the one this engine finished, else the newest wav on disk.

        The disk fallback is a guess, so it says so: a wav that was already in the folder
        before this engine started is not evidence that anything was recorded now, and the
        tools used to report it as a fresh take.
        """
        live = self.engine.take_state()
        if live.get("path"):
            return live
        if self.last_take is not None:
            return dict(self.last_take, this_session=True)
        found = recording.latest(self.recordings_dir())
        return dict(found, this_session=False) if found else live

    def status(self) -> dict:
        meter = self.engine.meter
        chain = self.engine.chain
        return {
            "engine": "amphead2",
            "running": self.engine.running,
            "mode": self.engine.mode,
            "samplerate": self.engine.samplerate,
            "blocksize": self.engine.blocksize,
            "latency_ms": self.engine.latency_ms,
            # What a player actually feels: the driver buffer either way, plus the block
            # the callback has to fill before anyone hears it. Reporting only the driver
            # figure made a 512-frame build look as quick as a 256-frame one.
            "block_ms": round(1000.0 * self.engine.blocksize / self.engine.samplerate, 2),
            "round_trip_ms": (None if self.engine.latency_ms is None else
                              round(self.engine.latency_ms +
                                    1000.0 * self.engine.blocksize / self.engine.samplerate, 1)),
            # Starved and dropped blocks since the stream opened: the honest latency check.
            "underflows": self.engine.counters["underflows"],
            "overflows": self.engine.counters["overflows"],
            "late": self.engine.counters["late"],
            # Failures inside the callback. The stream stays open and every other field says
            # the amp is working, so without this a glitching amp looks like a quiet guitar.
            "callback_errors": self.engine.counters["callback_errors"],
            "input_device": self.engine.input_device,
            "input_channel": self.engine.input_channel + 1,
            "input_channels": self.engine.input_channels,
            "armed": chain.armed,
            "master_db": chain.master_db,
            # Distinguishes "the player parked it" from "the amp parked itself": the verdict
            # says which, because the fix is different (raise the master vs press GO LIVE).
            "parked_by_idle": self.engine.parked_by_idle,
            "take": self.engine.take_state(),
            "input_gain_db": chain.input_gain_db,
            "output_gain_db": chain.output_gain_db,
            "gate_db": chain.gate_db,
            "bpm": chain.bpm,
            "metronome": chain.metronome,
            "metronome_db": chain.metronome_db,
            "capture": self.capture_name,
            "cab": self.cab_name,
            # Not an error, a tone fact: the pane has to be able to say it where the player
            # reads the amp, not only in the reply to the load call.
            "rate_mismatch": dict(self.rate_mismatch) if self.rate_mismatch else None,
            "stages": {"capture": chain.amp_on, "tone": chain.tone_on,
                       "cabinet": chain.cab_on},
            "tone": dict(self.tone),
            "meter": {
                "peak_in": meter.peak_in,
                "peak_out": meter.peak_out,
                "rms_out": meter.rms_out,
                "overflows": meter.overflows,
                "underflows": meter.underflows,
                "blocks": meter.blocks,
            },
            "last_error": self.engine.last_error,
            # A long measurement is a visible state, not a frozen page. Small shape only.
            "tuning": self.tuning_summary(),
        }

    def devices(self) -> dict:
        import sounddevice as sd

        rows = sd.query_devices()
        return {
            "default_input": sd.default.device[0],
            "default_output": sd.default.device[1],
            "inputs": [{"index": i, "name": r["name"]} for i, r in enumerate(rows) if r["max_input_channels"] > 0],
            "outputs": [{"index": i, "name": r["name"]} for i, r in enumerate(rows) if r["max_output_channels"] > 0],
        }

    def library(self, **kw) -> dict:
        """The captures and cabinets on this machine, so the pane can offer a choice.

        Two kinds of file, two places people keep them, and no way for the pane to guess
        either. The loaded file's own folder is scanned too, so a capture that lives
        somewhere unusual is still reachable after it has been played once. The scanning
        itself lives in ``amphead.library`` because the pane asks the same question before
        the engine is running.
        """
        from amphead import library

        return library.listing(self.capture_name, self.cab_name)

    def start(self, **kw) -> dict:
        engine = self.engine
        if engine.running:
            return {"mode": engine.mode, "already": True}
        if kw.get("latency_ms") is not None:
            from . import engine2

            engine2.TARGET_LATENCY_MS = float(kw["latency_ms"])
        for key in ("samplerate", "blocksize", "input_device", "output_device"):
            if kw.get(key) is not None:
                setattr(engine, key, int(kw[key]))
        if kw.get("input_channel") is not None:
            engine.input_channel = max(0, int(kw["input_channel"]) - 1)
        mode = engine.start()
        # Started means an open device, not a live amp: the master stays at the floor
        # until arm() asks for a level, so "armed: false" is silent rather than quiet.
        engine.update(master_db=SAFE_MASTER_DB, armed=False)
        # The gate is a preference, not a performance setting: it comes back the way the
        # player left it, and the settings file is where "left it" is written down.
        gate_db = kw.get("gate_db", settings.load().get("gate_db"))
        if gate_db is not None:
            engine.set_gate(float(gate_db))
        played = self._remembered_chain(kw)
        return {"mode": mode, "latency_ms": engine.latency_ms,
                "last_error": engine.last_error, "chain": played}

    def _remembered_chain(self, kw: dict) -> dict:
        """Load what the CLI was playing last, so Setup is not a clean DI by accident.

        The pane has no file picker, and the player has already chosen a capture once.
        A path that no longer exists is skipped in silence rather than loaded as an error:
        the engine still starts, it just has nothing in front of the input.
        """
        from . import lastchain

        want = {"capture": kw.get("capture") or lastchain.load().get("capture"),
                "cab": kw.get("cab") or lastchain.load().get("cab")}
        done = {}
        for key, path in want.items():
            found = lastchain.usable(path)
            if not found:
                continue
            try:
                if key == "capture":
                    done["capture"] = self.load_capture(found)["capture"]
                else:
                    done["cab"] = self.load_cab(found).get("cab", found)
            except Exception as exc:  # a bad file must not take the device down with it
                fix = faults.explain(exc, key, found)
                done[key] = f"failed: {type(exc).__name__}: {exc}" + (f". {fix}" if fix else "")
        return done

    def stop(self) -> dict:
        self.engine.stop()
        return {"running": False}

    def set_input(self, channel: int = 1) -> dict:
        """Point the amp at one socket on the interface, numbered the way the front panel is.

        The stream already carries every socket, so this is live: no reopen, no gap.
        """
        number = int(channel)
        count = self.engine.input_channels
        if number < 1 or (count and number > count):
            raise ValueError(f"input {number} does not exist; this interface has "
                             f"{count or 'an unknown number of'} inputs")
        self.engine.input_channel = number - 1
        return {"input_channel": number, "input_channels": count}

    def _forget_tune(self) -> None:
        """A measured block size belongs to the chain it measured, not to the next one."""
        with self._tune_lock:
            if not self._tuning.get("running"):
                self._tuning.update(result=None, phase="", started_at=None, finished_at=None,
                                    seconds=0.0, cancelled=False, error="")

    def load_capture(self, path: str) -> dict:
        from .nam import WaveNet

        self._forget_tune()
        model = WaveNet.load(path)
        # A capture is not just weights: it is weights for one sample rate. Playing an 8 kHz
        # trained model on a 48 kHz stream runs its filters at six times the frequency they
        # were fit to, which reads as a thin, fizzy amp rather than as an error, and the player
        # blames the capture. So the stream follows the capture: most .nam files are 48 kHz and
        # the old default was 44.1, and refusing those made the whole library look broken.
        rate = getattr(model, "sample_rate", None)
        followed = None
        if rate and int(rate) != int(self.engine.samplerate):
            if self.engine.running and self.engine.take_state().get("recording"):
                raise ValueError(
                    f"this capture was trained at {int(rate)} Hz and the engine is running at "
                    f"{self.engine.samplerate} Hz. Changing rate would end the take that is "
                    f"recording, so stop the take and load it again")
            try:
                mode = self.engine.reopen(int(rate))
            except Exception as exc:
                # The device will not run at the capture's rate. Refusing the load left 1009 of
                # this rig's 2928 captures unplayable, which is not "safe", it is the plugin
                # being wrong about the library. It plays, and it says what it is doing: the
                # filters were fitted at one rate and are being driven at another, so the top
                # end sits a little lower than the capture intends.
                self.rate_mismatch = {"capture_hz": int(rate),
                                      "stream_hz": int(self.engine.samplerate),
                                      "why": str(exc).split(": ")[0]}
                followed = None
                mode = None
            else:
                followed = int(rate)
                self.rate_mismatch = None
                # The cabinet and the tone stack were built for the old rate: a 44.1 kHz cabinet
                # in a 48 kHz chain is a different cabinet, so both are rebuilt at the new one.
                if self.cab_name:
                    self.load_cab(self.cab_name)
                if self.engine.chain.tone is not None:
                    self._rebuild_tone(int(rate))
        else:
            self.rate_mismatch = None
        self.engine.update(amp=model)
        self.capture_name = str(path)
        said = {"capture": self.capture_name, "receptive_field": model.receptive_field,
                "samplerate": self.engine.samplerate}
        if followed:
            said["followed_capture_rate"] = followed
            said["mode"] = mode
        if self.rate_mismatch:
            said["rate_mismatch"] = dict(self.rate_mismatch)
            said["warning"] = (
                f"this capture was trained at {self.rate_mismatch['capture_hz']} Hz and this "
                f"device only opens at {self.rate_mismatch['stream_hz']} Hz, so its tone is "
                f"about {round(100 * (1 - self.rate_mismatch['stream_hz'] / self.rate_mismatch['capture_hz']))}% "
                f"dark. Set the interface to {self.rate_mismatch['capture_hz']} Hz in its own "
                f"control panel (Windows sound settings cannot) to hear it exactly")
        return said

    def _rebuild_tone(self, rate: int) -> None:
        """Move a dialled-in tone stack to another sample rate without losing the settings."""
        from .tone import ToneStack

        stack = ToneStack(rate)
        for name, value in self.tone.items():
            stack.set(name, float(value))
        self.engine.update(tone=stack)

    def bypass(self, stage: str = "", on: bool = True) -> dict:
        """Switch a stage out of the path, or back in, without unloading it.

        A capture is not a plug-in: there is nothing to click in a rack. But the chain has
        three stages and each can be skipped, so panic can silence the rig and the player
        can undo it without hunting for the .nam file again.
        """
        key = str(stage or "").strip().lower()
        names = {"capture": "amp_on", "amp": "amp_on", "cab": "cab_on", "cabin": "cab_on",
                 "cabinet": "cab_on", "tone": "tone_on"}
        field = names.get(key)
        if field is None:
            return {"refused": f"no stage called {stage!r}; the chain has capture, tone, cabinet"}
        self.engine.update(**{field: bool(on)})
        chain = self.engine.chain
        return {"stage": key, "enabled": bool(getattr(chain, field))}

    def clear_capture(self) -> dict:
        self._forget_tune()
        self.engine.update(amp=None)
        self.capture_name = None
        self.rate_mismatch = None
        return {"capture": None}

    def load_cab(self, path: str, gain_db: float | None = None) -> dict:
        from .cab import Cab

        self._forget_tune()
        cab = Cab.from_wav(
            path,
            gain_db=0.0 if gain_db is None else float(gain_db),
            rate=self.engine.samplerate,
        )
        self.engine.update(cab=cab)
        self.cab_name = str(path)
        return {
            "cab": self.cab_name,
            "taps": int(cab.length),
            "trimmed_from": int(cab.trimmed_from),
            "samplerate": cab.samplerate,
            "resampled": cab.samplerate != cab.file_rate,
            "file_rate": cab.file_rate,
        }

    def clear_cab(self) -> dict:
        self._forget_tune()
        self.engine.update(cab=None)
        self.cab_name = None
        return {"cab": None}

    def tone_set(self, **kw) -> dict:
        from .tone import ToneStack

        chain = self.engine.chain
        stack = chain.tone
        if stack is None:
            stack = ToneStack(self.engine.samplerate)
            self.engine.update(tone=stack)
        for name in ("bass", "mid", "treble"):
            if kw.get(name) is not None:
                stack.set(name, float(kw[name]))
                self.tone[name] = float(kw[name])
        return dict(self.tone)

    def gain(self, **kw) -> dict:
        for name in ("master_db", "input_gain_db", "output_gain_db"):
            if kw.get(name) is not None:
                self.engine.set_gain(name, float(kw[name]))
        chain = self.engine.chain
        return {
            "master_db": chain.master_db,
            "input_gain_db": chain.input_gain_db,
            "output_gain_db": chain.output_gain_db,
        }

    def gate(self, db: float | None = None) -> dict:
        """Threshold for the noise gate in front of the amp. The floor is off."""
        if db is not None:
            self.engine.set_gate(float(db))
            # Remembered, because a gate someone dialled in is a decision, not a session detail.
            try:
                settings.save({"gate_db": float(self.engine.chain.gate_db)})
            except Exception:
                pass
        chain = self.engine.chain
        return {"gate_db": chain.gate_db, "open": chain.gate is not None and not chain.gate.off}

    def tempo(self, bpm: float | None = None, metronome: bool | None = None,
              metronome_db: float | None = None) -> dict:
        """Set the count and the click. The click is monitor-only and remembered."""
        if bpm is not None or metronome is not None or metronome_db is not None:
            chain = self.engine.set_tempo(
                bpm=None if bpm is None else float(bpm),
                metronome=metronome,
                metronome_db=None if metronome_db is None else float(metronome_db))
            try:
                settings.save({"bpm": float(chain.bpm), "metronome": bool(chain.metronome),
                               "metronome_db": float(chain.metronome_db)})
            except Exception:
                pass
        chain = self.engine.chain
        return {"bpm": chain.bpm, "metronome": chain.metronome,
                "metronome_db": chain.metronome_db}

    def rig(self, capture: str | None = None, cab: str | None = None, **knobs) -> dict:
        """Land a whole sound in one command: capture, cabinet, tone, trims, gate.

        One command rather than five because half a rig is worse than none: the new capture
        with the old cabinet is a sound nobody asked for, and the player who hears it has no
        way to tell which half arrived. A stage that fails is reported and the rest still
        lands, so a moved capture file costs the capture and not the tone.
        """
        loaded: dict = {}
        kept: list[str] = []
        if capture:
            try:
                loaded["capture"] = self.load_capture(str(capture))["capture"]
            except Exception as exc:
                fix = faults.explain(exc, faults.CAPTURE, str(capture))
                kept.append(f"capture: {exc}" + (f". {fix}" if fix else ""))
        if cab:
            try:
                loaded["cab"] = self.load_cab(str(cab))["cab"]
            except Exception as exc:
                fix = faults.explain(exc, faults.CAB, str(cab))
                kept.append(f"cabinet: {exc}" + (f". {fix}" if fix else ""))
        tone_kw = {name: knobs[name] for name in ("bass", "mid", "treble")
                   if knobs.get(name) is not None}
        if tone_kw:
            loaded["tone"] = self.tone_set(**tone_kw)
        gains = {name: knobs[name] for name in ("input_gain_db", "output_gain_db")
                 if knobs.get(name) is not None}
        if gains:
            loaded.update(self.gain(**gains))
        if knobs.get("gate_db") is not None:
            loaded["gate_db"] = self.gate(db=float(knobs["gate_db"]))["gate_db"]
        return {"loaded": loaded, "kept": kept}

    def arm(self, master_db: float | None = None) -> dict:
        # No level means the master the amp already has. Lifting it to a fixed -6 dB here is
        # how a caller that said only "arm" got a rig that came up louder than the knob.
        # The click is never part of going live: a rig that kept ticking from the last
        # session is a rig the player did not ask to hear.
        self.engine.update(
            armed=True,
            metronome=False,
            master_db=self.engine.chain.master_db if master_db is None else float(master_db),
        )
        chain = self.engine.chain
        return {"armed": chain.armed, "master_db": chain.master_db}

    def disarm(self) -> dict:
        chain = self.engine.update(armed=False, metronome=False)
        return {"armed": chain.armed, "master_db": chain.master_db}

    def panic(self) -> dict:
        chain = self.engine.panic()
        return {"armed": chain.armed, "master_db": chain.master_db}

    def meter(self) -> dict:
        m = self.engine.meter
        return {
            "peak_in": m.peak_in,
            "peak_out": m.peak_out,
            "rms_out": m.rms_out,
            "overflows": m.overflows,
            "underflows": m.underflows,
            "blocks": m.blocks,
        }

    def render(self, source: str, target: str, block: int | None = None) -> dict:
        """Offline render through the live chain. Same code as the callback."""
        import numpy as np
        import wave

        with wave.open(source, "rb") as wav:
            channels = wav.getnchannels()
            width = wav.getsampwidth()
            rate = wav.getframerate()
            raw = wav.readframes(wav.getnframes())
        if width != 2:
            raise ValueError("render source must be a 16 bit wav")
        x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
        if channels > 1:
            x = x.reshape(-1, channels).mean(axis=1)
        y = self.engine.render(x, block)
        pcm = np.clip(y, -1.0, 1.0)
        pcm = (pcm * 32767.0).astype("<i2")
        with wave.open(target, "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(rate)
            out.writeframes(pcm.tobytes())
        return {
            "target": target,
            "samplerate": rate,
            "seconds": round(x.size / float(rate), 3),
            "peak_out": float(np.max(np.abs(pcm)) / 32768.0) if pcm.size else 0.0,
        }

    def tune(self, blocksize: int | None = None, apply: bool | None = None,
             live: bool = True, seconds: float = 6.0,
             progress=None, cancel=None) -> dict:
        """Find the quickest block size this rig can actually feed, and reopen on it.

        The measurement is a soak on the real device, because a render stopwatch without a
        driver deadline flatters small blocks. Arming survives the trip: this changes how the
        rig is timed, not whether the player is holding a guitar.

        ``progress`` is a callback the long soak uses to say where it is. ``cancel`` is checked
        between blocks, so a player who changes their mind gets their amp back at the next
        boundary instead of after the last candidate.
        """
        def step(phase: str) -> None:
            if cancel is not None and cancel():
                raise TuneCancelled()
            if progress:
                progress(phase)

        def say(phase: str, *_) -> None:
            # soak_all reports between candidates; this is the only place a cancel can land
            # during the sweep without tearing a device open in the middle of a block.
            if cancel is not None and cancel():
                raise TuneCancelled()
            if progress:
                progress(phase)

        step("starting")
        # An offline sweep is a stopwatch, not a device measurement, so it must not save
        # the size it guessed. Only a live soak earns the right to change the setting.
        if apply is None:
            apply = bool(live)
        if not live:
            apply = False
        was_running = self.engine.running
        was_blocksize = self.engine.blocksize
        armed = self.engine.chain.armed
        if was_running:
            self.engine.stop()
        rows: list[dict] = []
        live_error: Exception | None = None
        applied = False
        try:
            if live:
                try:
                    if blocksize:
                        # An explicit size is a request to measure that size, even when it
                        # is below the safe floor the automatic sweep starts at. It is not
                        # permission to save a size the soak just refused.
                        asked = int(blocksize)
                        rows = [tune.soak(self.engine, asked, seconds=seconds)]
                        if rows[0]["ok"]:
                            picked = asked
                        else:
                            picked = None
                            words = tune.soak_verdict(rows, asked)
                    else:
                        rows = tune.soak_all(self.engine, seconds=seconds, progress=say)
                        picked = tune.choose_soak(rows)
                    # A six second pass is still a snapshot. Whatever it picked gets a longer
                    # run before the amp is left at that size, and if the longer run drops
                    # blocks the next size up is tried instead.
                    confirmed = True
                    if not blocksize and picked:
                        confirmed = False
                        for _ in range(2):
                            step(f"checking {picked} frames")
                            confirm = tune.soak(self.engine, picked, seconds=max(10.0, seconds * 2))
                            if confirm["ok"]:
                                confirmed = True
                                break
                            bigger = [r for r in rows if r["blocksize"] > picked and r["ok"]]
                            if not bigger:
                                break
                            picked = int(min(bigger, key=lambda r: r["blocksize"])["blocksize"])
                        if not confirmed:
                            # The short soak passed this size, the long one did not, and
                            # there is no larger clean size to fall back to. Saving it
                            # would make the next start choppier than the one before the
                            # tune; leave the engine's size alone and say so.
                            picked = None
                            words = ("the device dropped blocks in the long check; "
                                     "the block size was left unchanged")
                        else:
                            words = tune.soak_verdict(rows, picked)
                    elif not blocksize:
                        words = tune.soak_verdict(rows, picked)
                except TuneCancelled:
                    raise
                except Exception as exc:
                    # A render stopwatch is not a device measurement. If the soak could not
                    # open the device, saving the size it would have guessed is how a
                    # calibration makes a rig choppier than it found it.
                    live_error = exc
                    rows, picked, words = [], None, ""
            else:
                words = ""
            if not rows:
                if live_error is not None:
                    raise RuntimeError(f"the device would not open: {live_error}") from live_error
                step("measuring this chain")
                rows = tune.measure(self.engine.chain, self.engine.samplerate)
                picked = int(blocksize) if blocksize else tune.choose(rows)
                words = tune.verdict(rows, picked)
            if apply and picked:
                step(f"saving {picked} frames")
                self.engine.blocksize = int(picked)
                # Write it down: a measured size that evaporates on the next start is a
                # measurement the player has to repeat every session.
                try:
                    settings.save({"audio": {"blocksize": int(picked)}})
                except Exception:
                    pass
                applied = True
            changed = applied and picked != was_blocksize
        finally:
            # A failed, cancelled or measure-only tune must not leave the amp at a size
            # nobody chose. Restore the size it arrived with unless this tune saved one.
            if not applied:
                self.engine.blocksize = was_blocksize
            # Only reopen what was open. Measuring a parked amp must not switch it on.
            if was_running:
                self.engine.start()
                if armed:
                    self.engine.update(armed=True)
        if progress:
            progress("done")
        return {
            "blocksize": self.engine.blocksize,
            "picked": picked,
            "changed": changed,
            "rows": rows,
            "live": bool(rows and "underflows" in rows[0]),
            "verdict": words,
            "running": self.engine.running,
            "armed": self.engine.chain.armed,
            "block_ms": round(1000.0 * self.engine.blocksize / self.engine.samplerate, 2),
            "round_trip_ms": (None if self.engine.latency_ms is None else
                              round(self.engine.latency_ms +
                                    1000.0 * self.engine.blocksize / self.engine.samplerate, 1)),
        }

    # tuning, off the request loop -----------------------------------------
    def is_tuning(self) -> bool:
        with self._tune_lock:
            return bool(self._tuning["running"])

    def tuning_summary(self) -> dict:
        """The small truthful shape the pane and the judge can carry every poll.

        The rows stay behind ``tune_status``: a status payload with ten soak tables in it is
        a status payload that costs more than the thing it describes.
        """
        with self._tune_lock:
            state = dict(self._tuning)
        result = state.pop("result", None) or {}
        if state["running"] and state["started_at"] is not None:
            state["seconds"] = round(time.monotonic() - float(state["started_at"]), 1)
        state["verdict"] = result.get("verdict") or ""
        state["blocksize"] = result.get("blocksize")
        state["changed"] = result.get("changed")
        state["round_trip_ms"] = result.get("round_trip_ms")
        state["finished"] = state["finished_at"] is not None
        return state

    def tune_start(self, **kwargs) -> dict:
        """Start the measurement and answer at once; the caller polls ``tune_status``."""
        with self._tune_lock:
            if self._tuning["running"]:
                already = True
            else:
                already = False
                self._tune_stop.clear()
                self._tuning = {
                    "running": True, "phase": "starting", "started_at": time.monotonic(),
                    "finished_at": None, "seconds": 0.0, "cancelled": False,
                    "error": "", "result": None,
                }
                self._tune_thread = threading.Thread(
                    target=self._tune_worker, args=(dict(kwargs),),
                    daemon=True, name="amphead-tune")
        if not already:
            self._tune_thread.start()
        said = self.tuning_summary()
        said["already"] = already
        return said

    def _tune_worker(self, kwargs: dict) -> None:
        def progress(phase: str, *_) -> None:
            if self._tune_stop.is_set():
                raise TuneCancelled()
            with self._tune_lock:
                self._tuning["phase"] = phase

        result = None
        error = ""
        cancelled = False
        try:
            result = self.tune(progress=progress, cancel=self._tune_stop.is_set, **kwargs)
        except TuneCancelled:
            cancelled = True
        except Exception as exc:                     # a tune must not kill the engine
            error = f"{type(exc).__name__}: {exc}"
        finally:
            with self._tune_lock:
                self._tuning.update(
                    running=False, cancelled=cancelled, error=error, result=result,
                    finished_at=time.monotonic(),
                    phase="cancelled" if cancelled else ("failed" if error else "done"))
                self._tune_thread = None

    def tune_status(self) -> dict:
        with self._tune_lock:
            state = dict(self._tuning)
        if state["running"] and state["started_at"] is not None:
            state["seconds"] = round(time.monotonic() - float(state["started_at"]), 1)
        return state

    def tune_cancel(self) -> dict:
        """Ask the worker to stop at the next candidate boundary and keep the amp."""
        self._tune_stop.set()
        return self.tuning_summary()

    def shutdown_tune(self) -> None:
        self._tune_stop.set()

    # dispatch -------------------------------------------------------------
    VERBS = {
        "status": "status",
        "devices": "devices",
        "library": "library",
        "start": "start",
        "stop": "stop",
        "input": "set_input",
        "load_capture": "load_capture",
        "clear_capture": "clear_capture",
        "bypass": "bypass",
        "load_cab": "load_cab",
        "clear_cab": "clear_cab",
        "tone": "tone_set",
        "gain": "gain",
        "gate": "gate",
        "tempo": "tempo",
        "rig": "rig",
        "arm": "arm",
        "disarm": "disarm",
        "panic": "panic",
        "meter": "meter",
        "render": "render",
        "tune": "tune",
        "tune_start": "tune_start",
        "tune_status": "tune_status",
        "tune_cancel": "tune_cancel",
        "record_start": "record_start",
        "record_stop": "record_stop",
        "record_state": "take_state",
    }

    #: Verbs that may run while a tune owns the device. Everything else waits, because a
    #: capture load or a start in the middle of a soak would measure the wrong chain.
    TUNE_SAFE = {"status", "meter", "library", "devices", "take_state", "tempo",
                 "tune_start", "tune_status", "tune_cancel"}

    def handle(self, request: dict) -> dict:
        verb = request.get("cmd") or request.get("verb")
        if verb in ("quit", "exit"):
            self.shutdown_tune()
            return {"ok": True, "stop": True}
        name = self.VERBS.get(verb or "")
        if name is None:
            return {"ok": False, "error": f"unknown command {verb!r}"}
        if self.is_tuning() and verb not in self.TUNE_SAFE:
            return {"ok": False, "error": "the amp is measuring block sizes; the knobs and "
                                          "the chain wait until the reading is done"}
        kwargs = {k: v for k, v in request.items() if k not in ("cmd", "verb", "id")}
        try:
            return {"ok": True, "result": getattr(self, name)(**kwargs)}
        except Exception as exc:
            said = f"{type(exc).__name__}: {exc}"
            fix = faults.explain(exc, _kind_of(name), str(kwargs.get("path") or ""))
            return {"ok": False, "error": f"{said}. {fix}" if fix else said}


def serve(stdin=None, stdout=None, server: "Server | None" = None) -> int:
    """The whole wire: read a request, answer it with its id echoed back.

    One loop, and `main` runs this one. It used to have a near-copy of its own, which is
    where the request id stopped being echoed: the client then dropped every reply as stale
    and reported timeouts on a pipe that was working perfectly.
    """
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    server = server or Server(engine_from_settings())
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            reply = {"ok": False, "error": f"bad json: {exc}"}
            request = {}
        else:
            try:
                reply = server.handle(request)
            except Exception as exc:                  # a verb must never kill the engine
                reply = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                traceback.print_exc(file=sys.stderr)
        # Echo the request id so the client can tell its own reply from one that arrived late.
        if isinstance(request, dict) and "id" in request:
            reply["id"] = request["id"]
        stdout.write(json.dumps(reply) + "\n")
        stdout.flush()
        if reply.get("stop"):
            break
    server.shutdown_tune()
    server.engine.stop()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AmpHead 2 engine process")
    # Defaults are None on purpose: unset means "whatever the settings file says",
    # which is how another machine gets its own rate and block without a flag.
    parser.add_argument("--samplerate", type=int, default=None)
    parser.add_argument("--blocksize", type=int, default=None,
                        help="frames per callback (512 is the measured default: same round "
                             "trip as 128 on kernel streaming, less CPU, no starved blocks)")
    parser.add_argument("--input", type=int, default=None)
    parser.add_argument("--output", type=int, default=None)
    args = parser.parse_args(argv)
    return serve(server=Server(engine_from_settings(
        samplerate=args.samplerate, blocksize=args.blocksize,
        input_device=args.input, output_device=args.output)))


if __name__ == "__main__":
    raise SystemExit(main())
