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
import traceback
from pathlib import Path

from . import faults
from . import record as recording
from . import settings
from . import tune
from .engine2 import ARM_MASTER_DB, SAFE_MASTER_DB, Engine


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
    return Engine(
        samplerate=int(audio["samplerate"]),
        blocksize=int(audio["blocksize"]),
        input_device=audio.get("input_device"),
        output_device=audio.get("output_device"),
        input_channel=max(0, int(audio["input_channel"]) - 1),
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
        self.tone = {"bass": 0.0, "mid": 0.0, "treble": 0.0}

    # every verb returns plain json ----------------------------------------
    # recording --------------------------------------------------------------
    def recordings_dir(self) -> Path:
        """Beside the settings file, so the player already knows where it is."""
        return settings.path().parent / "recordings"

    def record_start(self, path: str | None = None, name: str | None = None) -> dict:
        """Open a take on the amp's output. The name defaults to when the player pressed record."""
        if path:
            target = Path(path)
            if target.is_dir():
                target = target / f"{recording.default_name(self.engine.samplerate)}.wav"
        else:
            target = self.recordings_dir() / f"{name or recording.default_name(self.engine.samplerate)}.wav"
        # A take is recorded after the master, so a master at the bottom writes silence. Refuse
        # with the fix rather than hand back a wav full of nothing.
        live = self.status()
        if not live.get("armed"):
            raise ValueError("the amp is not live: press GO LIVE, then record")
        if float(live.get("master_db") or SAFE_MASTER_DB) <= -59.0:
            raise ValueError("the master is silent, so the take would be: raise the master first")
        said = self.engine.start_take(target)
        said["dir"] = str(target.parent)
        return said

    def record_stop(self) -> dict:
        return self.engine.stop_take()

    def take_state(self) -> dict:
        """The live take, or the newest one on disk if the engine has not recorded since start."""
        live = self.engine.take_state()
        if live.get("path"):
            return live
        return recording.latest(self.recordings_dir()) or live

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
            "capture": self.capture_name,
            "cab": self.cab_name,
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

    def load_capture(self, path: str) -> dict:
        from .nam import WaveNet

        model = WaveNet.load(path)
        self.engine.update(amp=model)
        self.capture_name = str(path)
        return {"capture": self.capture_name, "receptive_field": model.receptive_field}

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
        self.engine.update(amp=None)
        self.capture_name = None
        return {"capture": None}

    def load_cab(self, path: str, gain_db: float | None = None) -> dict:
        from .cab import Cab

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
            "samplerate": cab.samplerate,
            "resampled": cab.samplerate != cab.file_rate,
            "file_rate": cab.file_rate,
        }

    def clear_cab(self) -> dict:
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
        self.engine.update(
            armed=True,
            master_db=self.engine.chain.master_db if master_db is None else float(master_db),
        )
        chain = self.engine.chain
        return {"armed": chain.armed, "master_db": chain.master_db}

    def disarm(self) -> dict:
        chain = self.engine.update(armed=False)
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

    def tune(self, blocksize: int | None = None, apply: bool = True,
             live: bool = True, seconds: float = 2.0) -> dict:
        """Find the quickest block size this rig can actually feed, and reopen on it.

        The measurement is a soak on the real device, because a render stopwatch without a
        driver deadline flatters small blocks. Arming survives the trip: this changes how the
        rig is timed, not whether the player is holding a guitar.
        """
        was_running = self.engine.running
        armed = self.engine.chain.armed
        if was_running:
            self.engine.stop()
        rows: list[dict] = []
        try:
            if live:
                try:
                    rows = tune.soak_all(self.engine, seconds=seconds)
                    picked = int(blocksize) if blocksize else tune.choose_soak(rows)
                    # A two second pass is a screenshot, not a verdict. Whatever it picked gets
                    # a longer run before the amp is left at that size, and if the longer run
                    # drops blocks the next size up is tried instead.
                    if not blocksize and picked:
                        for _ in range(2):
                            confirm = tune.soak(self.engine, picked, seconds=max(5.0, seconds * 2))
                            if confirm["ok"]:
                                break
                            bigger = [r for r in rows if r["blocksize"] > picked and r["ok"]]
                            if not bigger:
                                break
                            picked = int(min(bigger, key=lambda r: r["blocksize"])["blocksize"])
                    words = tune.soak_verdict(rows, picked)
                except Exception:
                    rows, picked, words = [], None, ""
            else:
                words = ""
            if not rows:
                rows = tune.measure(self.engine.chain, self.engine.samplerate)
                picked = int(blocksize) if blocksize else tune.choose(rows)
                words = tune.verdict(rows, picked)
            changed = picked is not None and picked != self.engine.blocksize
            if apply and picked:
                self.engine.blocksize = int(picked)
                # Write it down: a measured size that evaporates on the next start is a
                # measurement the player has to repeat every session.
                try:
                    settings.save({"audio": {"blocksize": int(picked)}})
                except Exception:
                    pass
        finally:
            # Only reopen what was open. Measuring a parked amp must not switch it on.
            if was_running:
                self.engine.start()
                if armed:
                    self.engine.update(armed=True)
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
        "rig": "rig",
        "arm": "arm",
        "disarm": "disarm",
        "panic": "panic",
        "meter": "meter",
        "render": "render",
        "tune": "tune",
        "record_start": "record_start",
        "record_stop": "record_stop",
        "record_state": "take_state",
    }

    def handle(self, request: dict) -> dict:
        verb = request.get("cmd") or request.get("verb")
        if verb in ("quit", "exit"):
            return {"ok": True, "stop": True}
        name = self.VERBS.get(verb or "")
        if name is None:
            return {"ok": False, "error": f"unknown command {verb!r}"}
        kwargs = {k: v for k, v in request.items() if k not in ("cmd", "verb")}
        try:
            return {"ok": True, "result": getattr(self, name)(**kwargs)}
        except Exception as exc:
            said = f"{type(exc).__name__}: {exc}"
            fix = faults.explain(exc, _kind_of(name), str(kwargs.get("path") or ""))
            return {"ok": False, "error": f"{said}. {fix}" if fix else said}


def serve(stdin=None, stdout=None) -> int:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    server = Server(engine_from_settings())
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            reply = {"ok": False, "error": f"bad json: {exc}"}
        else:
            try:
                reply = server.handle(request)
            except Exception as exc:                  # a verb must never kill the engine
                reply = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                traceback.print_exc(file=sys.stderr)
        stdout.write(json.dumps(reply) + "\n")
        stdout.flush()
        if reply.get("stop"):
            break
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
    server = Server(engine_from_settings(samplerate=args.samplerate, blocksize=args.blocksize,
                                         input_device=args.input, output_device=args.output))
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            reply = {"ok": False, "error": f"bad json: {exc}"}
        else:
            try:
                reply = server.handle(request)
            except Exception as exc:
                reply = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        sys.stdout.write(json.dumps(reply) + "\n")
        sys.stdout.flush()
        if reply.get("stop"):
            break
    server.engine.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
