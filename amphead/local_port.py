"""The amp, as the plugin sees it.

Hermes is the amp head now: there is no host application to open and no project to make. The
engine in `engine2.py` owns the audio interface, the agent owns the conversation, and neither can
stall the other because the engine runs in its own process. This module is only the phone line
to it: one JSON object per line in, one per line out.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from . import limits, port

LABEL = "AmpHead 2"


def _name(path: Any) -> str:
    """A file as a player says it: the stem, not forty levels of folder above it."""
    if not path:
        return ""
    try:
        return Path(str(path)).stem
    except (OSError, ValueError):
        return str(path)

#: The chain the engine runs, named the way a player reads a signal path.
STAGES = ("Capture", "Tone", "Cabinet")


class EngineNotInstalled(port.NoRig):
    """The engine's interpreter cannot import what it needs, so there is no engine."""


class EngineGone(port.NoRig):
    """The engine process is not running."""


class EngineRefused(port.RigSaidNo):
    """The engine answered and said no."""


class EngineSilent(port.RigTimeout):
    """The engine did not answer in time."""


_ABLE: dict[str, bool] = {}


def _able(python: str) -> bool:
    """Whether this interpreter can host the engine, asked once and remembered.

    A subprocess probe is the only honest answer: the pane's own interpreter may be thin,
    and a configured path may point at a Python that was since removed.
    """
    key = str(python)
    if key not in _ABLE:
        try:
            done = subprocess.run([key, "-c", "import numpy, sounddevice"],
                                  capture_output=True, timeout=30)
            _ABLE[key] = done.returncode == 0
        except (OSError, subprocess.SubprocessError):
            _ABLE[key] = False
    return _ABLE[key]


def engine_python(explicit: str | None = None) -> str:
    """The interpreter that can host the engine, in the order a person would look.

    An explicit argument, then AMPHEAD_ENGINE_PYTHON, then the `engine_python` setting,
    then this process's own interpreter, then a virtualenv kept beside the plugin for
    development (.engine). The process's own interpreter comes first because Hermes
    installs the plugin's `pyproject.toml` dependencies into the environment it runs the
    plugin in: after `hermes plugins install`, the engine needs no second install, no
    script, and no path typed anywhere. Each candidate is asked whether it can import
    numpy and sounddevice before it is trusted, so a stale setting cannot pick a Python
    that has since lost them.
    """
    if explicit:
        return explicit
    said = os.environ.get("AMPHEAD_ENGINE_PYTHON")
    if said:
        return said
    from . import settings

    chosen = str(settings.load().get("engine_python") or "").strip()
    if chosen and Path(chosen).is_file():
        return chosen
    root = Path(__file__).resolve().parent.parent
    candidates = [sys.executable]
    for relative in ("Scripts/python.exe", "bin/python3", "bin/python"):
        candidate = root / ".engine" / relative
        if candidate.is_file():
            candidates.append(str(candidate))
    for candidate in candidates:
        if _able(candidate):
            return candidate
    return sys.executable


class EngineClient:
    """A pipe to ``amphead.engine_main``, started on first use and reused after that."""

    def __init__(self, python: str | None = None, timeout: float = 20.0) -> None:
        self.python = python or engine_python()
        self.timeout = float(timeout)
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._stderr: queue.Queue = queue.Queue(maxsize=200)
        self._last_error: str | None = None

    # lifecycle -------------------------------------------------------------
    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def check_env(self) -> None:
        """Ask the engine's interpreter whether it can host the engine.

        The import has to happen over there, in the interpreter that would run the engine.
        Usually that is the same Python that runs the plugin, but a dev checkout or an
        AMPHEAD_ENGINE_PYTHON override can point somewhere else, and asking the wrong one
        turns a working setup into a false refusal.
        """
        try:
            probe = subprocess.run(
                [self.python, "-c", "import numpy, sounddevice"],
                cwd=str(Path(__file__).resolve().parent.parent),
                capture_output=True, text=True, timeout=60,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise EngineNotInstalled(
                f"{LABEL} cannot run {self.python} ({type(exc).__name__}: {exc}). "
                f"Point AMPHEAD_ENGINE_PYTHON at an interpreter with numpy and sounddevice."
            ) from exc
        if probe.returncode:
            lines = [ln for ln in (probe.stderr or "").strip().splitlines() if ln.strip()]
            reason = lines[-1] if lines else f"exit {probe.returncode}"
            raise EngineNotInstalled(
                f"{LABEL} needs numpy and sounddevice in {self.python}, and that "
                f"interpreter cannot import them ({reason}). Install them there, or point "
                f"AMPHEAD_ENGINE_PYTHON at one that has them."
            )

    def start(self) -> None:
        if self.running:
            return
        self.check_env()
        self._proc = subprocess.Popen(
            [self.python, "-m", "amphead.engine_main"],
            cwd=str(Path(__file__).resolve().parent.parent),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        # Drain stderr on a thread so a chatty engine can never fill a pipe and stall.
        threading.Thread(target=self._drain, daemon=True, name="amphead2-stderr").start()

    def _drain(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        try:
            for line in proc.stderr:
                if self._stderr.full():
                    self._stderr.get_nowait()
                self._stderr.put_nowait(line.rstrip())
        except Exception:
            pass

    def stop(self) -> bool:
        proc = self._proc
        if proc is None:
            return True
        if proc.poll() is None:
            try:
                self.call(cmd="quit", timeout=5.0)
            except port.RigError:
                pass
        try:
            proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            proc.kill()
        self._proc = None
        return True

    # the wire --------------------------------------------------------------
    def call(self, timeout: float | None = None, **request: Any) -> dict:
        if not self.running:
            raise EngineGone(f"{LABEL} is not running. Start it with amphead_setup; if it refuses, "
                             f"the plugin's numpy and sounddevice are missing: reinstall with "
                             f"`hermes plugins install amphead --yes-deps`.")
        proc = self._proc
        assert proc is not None and proc.stdin is not None and proc.stdout is not None
        payload = json.dumps(request) + "\n"
        limit = float(timeout or self.timeout)
        deadline = time.monotonic() + limit
        with self._lock:
            try:
                proc.stdin.write(payload)
                proc.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                self._proc = None
                raise EngineGone(f"{LABEL} stopped answering ({exc}). Start it again with "
                                 f"amphead_setup.") from exc
            line = self._read_line(proc, deadline)
        if line is None:
            raise EngineSilent(f"{LABEL} accepted the request but did not answer within "
                               f"{limit:.1f}s.")
        try:
            reply = json.loads(line)
        except json.JSONDecodeError as exc:
            raise EngineSilent(f"{LABEL} answered with something that is not JSON: "
                               f"{line[:80]!r}") from exc
        if reply.get("ok"):
            return reply.get("result") or {}
        self._last_error = str(reply.get("error") or "no reason given")
        raise EngineRefused(f"{LABEL} refused: {self._last_error}")

    @staticmethod
    def _read_line(proc: subprocess.Popen, deadline: float) -> str | None:
        """Read one line without blocking past the deadline.

        ``readline`` on a pipe cannot be interrupted, so the read happens on a helper
        thread and the caller waits on a queue with a timeout. A stuck engine then
        costs one leaked thread instead of a wedged plugin.
        """
        assert proc.stdout is not None
        out: queue.Queue = queue.Queue(maxsize=1)

        def read() -> None:
            try:
                out.put_nowait(proc.stdout.readline())
            except Exception:
                out.put_nowait("")

        threading.Thread(target=read, daemon=True).start()
        try:
            return out.get(timeout=max(0.05, deadline - time.monotonic())) or None
        except queue.Empty:
            return None

    @property
    def stderr_tail(self) -> list[str]:
        rows = []
        while not self._stderr.empty():
            rows.append(self._stderr.get_nowait())
        return rows


class AmpHead2Rig:
    """AmpHead's own engine: it hosts the chain, and there is no other host."""

    name = "amphead2"
    label = LABEL

    def __init__(self, client: EngineClient | None = None) -> None:
        self.client = client or EngineClient()

    def _call(self, **request: Any) -> dict:
        return self.client.call(**request)

    # reading ---------------------------------------------------------------
    def ping(self, **kw: Any) -> dict:
        return self._call(cmd="status")

    def status(self, **kw: Any) -> dict:
        """The engine's own state, with its files named the way a player says them.

        The engine holds paths; the pane says names and needs the path beside it for a menu
        to show which row is loaded. Both are answered here so no route has to parse a path.
        """
        got = self._call(cmd="status")
        for stage in ("capture", "cab"):
            path = got.get(stage) or ""
            got[f"{stage}_path"] = path
            got[stage] = _name(path)
        return got

    def liveness(self) -> tuple[str, str]:
        if self.client.running:
            return ("live", f"{LABEL} is running")
        return ("absent", f"{LABEL} is not running")


    def track_info(self, track: int = 1, **kw: Any) -> dict:
        got = self._call(cmd="status")
        return {
            "name": LABEL,
            "armed": bool(got.get("armed")),
            "monitor": bool(got.get("armed")),
            "recinput": self._input_spec(got),
            "vol_db": got.get("output_gain_db"),
            "samplerate": got.get("samplerate"),
            "blocksize": got.get("blocksize"),
            "mode": got.get("mode"),
        }


    def master(self, **kw: Any) -> dict:
        got = self._call(cmd="status")
        return {"db": got.get("master_db"), "armed": bool(got.get("armed")),
                "latency_ms": got.get("latency_ms")}

    def meter(self, track: int = 1, **kw: Any) -> dict:
        m = self._call(cmd="meter")
        peak = float(m.get("peak_out") or 0.0)
        return {"l": peak, "r": peak,
                "clip": peak >= 0.999,
                "rms": m.get("rms_out"),
                "overflows": m.get("overflows"), "underflows": m.get("underflows")}

    def inputs(self, **kw: Any) -> dict:
        """The sockets on the interface the engine has open, numbered like its front panel.

        Not the list of Windows audio devices: a switch with a stop per device would offer
        the webcam mic and Stereo Mix as "input 7" and never reach the AudioBox's input 2.
        """
        got = self._call(cmd="status")
        count = int(got.get("input_channels") or 0) if got.get("running") else 0
        rows = [{"i": k, "name": f"IN {k + 1}"} for k in range(count)]
        return {"count": count, "channels": rows}



    def fx_list(self, track: int = 1, **kw: Any) -> list:
        """Three stages, each saying whether it is loaded and whether it is in the path.

        A flat tone knob is loaded and on: it is a stage you can turn, not a missing plug-in.
        A bypassed capture is loaded and off, which is what panic leaves behind.
        """
        got = self._call(cmd="status")
        stages = got.get("stages") or {}
        keys = {"Capture": "capture", "Tone": "tone", "Cabinet": "cabinet"}
        loaded = {
            "Capture": bool(got.get("capture")),
            "Tone": True,
            "Cabinet": bool(got.get("cab")),
        }
        return [{"i": i, "name": name,
                 "enabled": loaded[name] and bool(stages.get(keys[name], True))}
                for i, name in enumerate(STAGES)]

    def fx_params(self, track: int = 1, fx: int = 0, keep: int | None = None,
                  **kw: Any) -> list:
        """The tone stage's three knobs, as the plugin's own parameter rows.

        The capture and the cabinet have no knobs a player turns mid-song, so they
        answer an empty list rather than invented parameters.
        """
        if fx != 1:
            return []
        tone = self._call(cmd="status").get("tone") or {}
        rows = []
        for i, name in enumerate(("Bass", "Mid", "Treble")):
            raw = float(tone.get(name.lower()) or 0.0)
            rows.append({
                "i": i,
                "name": name,
                "fmt": f"{raw:+.1f} dB" if abs(raw) > 1e-9 else "flat",
                "raw": raw,
                "norm": limits.tone(raw) / abs(2 * limits.TONE[1]) + 0.5,
            })
        return rows

    def set_param(self, track: int = 1, fx: int = 0, param: Any = None, value: float = 0.0,
                  unit: str | None = None, mode: str = "auto", **kw: Any) -> dict:
        """Turn one tone knob, then read every knob back and say what the engine holds.

        The pane's write path wants a write it can prove: so this returns the value the
        engine reports after the write, not the value that was asked for.
        """
        if fx != 1:
            raise EngineRefused(f"{LABEL} only has knobs on the tone stage (stage 1); "
                                f"a capture and a cabinet have none to turn.")
        names = ("bass", "mid", "treble")
        if isinstance(param, int) and not isinstance(param, bool):
            if not 0 <= param < len(names):
                raise EngineRefused(f"{LABEL} tone stage has 3 knobs, not {param + 1}")
            key = names[param]
        else:
            key = str(param).strip().lower()
            if key not in names:
                raise EngineRefused(f"{LABEL} has no knob called {param!r}; the tone stage "
                                    f"has Bass, Mid and Treble.")
        wanted = float(value)
        if unit and str(unit).lower() in ("norm", "normalized", "0-1"):
            wanted = (max(0.0, min(1.0, wanted)) - 0.5) * 24.0
        self._call(cmd="tone", **{key: wanted})
        rows = {str(row["name"]).lower(): row for row in self.fx_params(track=track, fx=fx)}
        got = rows[key]
        return {"fx": 1, "fx_name": "Tone", "param": got["i"], "param_name": got["name"],
                "raw": got["raw"], "fmt": got["fmt"], "norm": got["norm"],
                "wanted": wanted, "landed": abs(float(got["raw"]) - wanted) < 0.051,
                "mode": mode, "unit": unit}

    def param_index(self, name: str, track: int = 1, fx: int = 0, **kw: Any) -> int:
        wanted = str(name).strip().lower()
        for row in self.fx_params(track=track, fx=fx):
            if str(row["name"]).lower() == wanted:
                return int(row["i"])
        raise EngineRefused(f"{LABEL} has no knob called {name!r}; the tone stage has "
                            f"Bass, Mid and Treble.")

    # writing ---------------------------------------------------------------
    def set_master(self, db: float, expect: str | None = None, **kw: Any) -> dict:
        return {"db": self._call(cmd="gain", master_db=float(db)).get("master_db")}

    def set_track_level(self, track: int = 1, db: float = port.SAFE_MASTER_DB,
                        expect: str | None = None, **kw: Any) -> dict:
        return {"db": self._call(cmd="gain", output_gain_db=float(db)).get("output_gain_db")}

    def set_input_gain(self, db: float = 0.0, **kw: Any) -> dict:
        """How hard the interface signal hits the capture. Nudging this up is how a clean
        DI is made to drive an amp, which is a tone control as much as a level."""
        return {"db": self._call(cmd="gain", input_gain_db=float(db)).get("input_gain_db")}

    def set_gate(self, db: float = -80.0, **kw: Any) -> dict:
        """Threshold of the noise gate in front of the amp. -80 is off.

        A gate is the one control that has to be reachable without a menu: hiss between
        takes is the first thing anyone wants gone, and the answer is one number.
        """
        return {"db": self._call(cmd="gate", db=float(db)).get("gate_db")}

    # recording --------------------------------------------------------------
    def start_take(self, path: str | None = None, name: str | None = None, **kw: Any) -> dict:
        """Record what the amp is making, to a wav. What you hear is what lands in the file."""
        return self._call(cmd="record_start", path=path or None, name=name or None)

    def stop_take(self, **kw: Any) -> dict:
        return self._call(cmd="record_stop")

    def take_state(self) -> dict:
        if not self.client.running:
            return {"recording": False}
        return self._call(cmd="record_state")

    def tune(self, **kw: Any) -> dict:
        """Measure this chain and reopen the amp on the quickest block size that holds."""
        return self._call(cmd="tune")

    def set_output_gain(self, db: float = 0.0, **kw: Any) -> dict:
        """Level between the cabinet and the master, so balancing two captures never means
        moving the master and losing the number that was safe to be loud at."""
        return {"db": self._call(cmd="gain", output_gain_db=float(db)).get("output_gain_db")}

    def set_input(self, track: int = 1, spec: Any = 1, **kw: Any) -> dict:
        """`spec` is the socket number off the front panel: 1 is IN 1. Never a device index."""
        number = int(spec if spec not in (None, "") else 1)
        if not self._call(cmd="status").get("running"):
            self._call(cmd="start", input_channel=number)
        got = self._call(cmd="input", channel=number)
        return {"recinput": int(got["input_channel"]) - 1,
                "count": got.get("input_channels")}

    def arm(self, track: int = 1, monitor: bool = True, allow_loud: bool = False,
            **kw: Any) -> dict:
        # Arming opens the device if it is closed: a player pressing Live should not
        # also have to know that the stream was closed while the rig sat idle.
        if not self._call(cmd="status").get("running"):
            self._call(cmd="start")
        # Arming moves the master to the measured default. A player who already asked for
        # a level in the same breath meant that level, so put it back afterwards.
        before = self._call(cmd="status").get("master_db")
        got = dict(self._call(cmd="arm"))
        try:
            if before is not None and float(before) > port.SAFE_MASTER_DB + 0.5:
                if abs(float(before) - float(got.get("master_db") or 0.0)) > 0.01:
                    # Keep the arm answer (armed, monitor) and take only the level back.
                    got["master_db"] = self._call(cmd="gain", master_db=float(before)).get(
                        "master_db")
        except (TypeError, ValueError):
            pass
        return {"armed": bool(got.get("armed")), "monitor": bool(got.get("armed")),
                "master_db": got.get("master_db")}

    def disarm(self, track: int = 1, **kw: Any) -> dict:
        got = self._call(cmd="disarm")
        return {"armed": bool(got.get("armed")), "master_db": got.get("master_db")}

    def fx_bypass(self, track: int = 1, fx: int = 0, on: bool = True, **kw: Any) -> dict:
        """``on`` is bypass-on, as the pane means it: true takes the stage out of the path.

        Bypass here is a switch on a stage, not an unload. The capture stays loaded and the
        cabinet stays in memory, so panic can take all three out of the path and the player
        can bring the rig back without finding the file again. Switching on a stage that was
        never loaded is refused by name, because there is nothing there to hear.
        """
        if not 0 <= fx < len(STAGES):
            raise EngineRefused(f"{LABEL} has no stage {fx}")
        name = STAGES[fx]
        key = {"Capture": "capture", "Tone": "tone", "Cabinet": "cabinet"}[name]
        if not on:
            held = self._call(cmd="status")
            if name == "Capture" and not held.get("capture"):
                raise EngineRefused(
                    f"{LABEL} has no capture loaded to switch back on: load one with "
                    f"-m amphead.play --capture, then press Setup."
                )
            if name == "Cabinet" and not held.get("cab"):
                raise EngineRefused(
                    f"{LABEL} has no cabinet loaded to switch back on: load one with "
                    f"-m amphead.play --cab, then press Setup."
                )
        got = self._call(cmd="bypass", stage=key, on=not on)
        if got.get("refused"):
            raise EngineRefused(str(got["refused"]))
        return {"i": fx, "name": name, "enabled": bool(got.get("enabled"))}



    # project-shaped verbs --------------------------------------------------




    # the host it does not have ---------------------------------------------

    def running(self) -> bool:
        return self.client.running

    def state(self) -> dict:
        """What the engine is doing, in the words the judge and the face both read.

        `open` is the only thing that decides whether the switch reads SET UP or GO LIVE. Every
        other field is a fact the engine reported, not a guess made here: if the engine cannot
        answer, nothing below the first line is invented.
        """
        if not self.client.running:
            return {"open": False, "engine": self.name, "armed": False,
                    "device": {"closed": True}}
        got = self._call(cmd="status")
        latency = got.get("latency_ms")
        tone = got.get("tone") or {}
        return {
            "open": True,
            "engine": self.name,
            "armed": bool(got.get("armed")),
            "master_db": got.get("master_db"),
            "parked_by_idle": bool(got.get("parked_by_idle")),
            "take": got.get("take") or {"recording": False},
            "device": {
                "buffer": got.get("blocksize"),
                "rate": got.get("samplerate"),
                "latency_out_ms": round(float(latency or 0.0) / 2.0, 2),
                "latency_rt_ms": latency,
                "closed": not got.get("running"),
                "mode": got.get("mode"),
                "inputs": got.get("input_channels"),
                "name": got.get("device") or got.get("input_device") or "",
            },
            # What is actually in the chain, in the engine's own words. The face cannot show
            # a capture by name if the adapter keeps the name to itself.
            "capture": _name(got.get("capture")),
            "capture_path": got.get("capture") or "",
            "cab": _name(got.get("cab")),
            "cab_path": got.get("cab") or "",
            "tone": {k: float(tone.get(k) or 0.0) for k in ("bass", "mid", "treble")},
            "stages": got.get("stages") or {},
            "input_channel": got.get("input_channel"),
            "input_channels": got.get("input_channels"),
            "samplerate": got.get("samplerate"),
            "blocksize": got.get("blocksize"),
            "input_gain_db": got.get("input_gain_db"),
            "output_gain_db": got.get("output_gain_db"),
            "gate_db": got.get("gate_db"),
            "latency_ms": latency,
            "block_ms": got.get("block_ms"),
            "round_trip_ms": got.get("round_trip_ms"),
            "mode": got.get("mode"),
        }

    def host_alive(self) -> bool:
        return self.client.running

    def ensure(self, **kw: Any) -> dict:
        self.client.start()
        return {"ui": "none", "engine": self.name}


    def stop(self) -> bool:
        return self.client.stop()



    def library(self, **kw: Any) -> dict:
        """What can be loaded, and what is loaded: the pane's amp and cabinet menus.

        A menu is not a device. The folders are on this disk whether or not the engine has
        opened the interface, so a player can see the amp they want before pressing Setup;
        only the *loaded* names need the engine to answer.
        """
        from . import lastchain, library

        held = lastchain.load()
        if not self.client.running:
            return library.listing(_name(held.get("capture")), _name(held.get("cab")))
        return self._call(cmd="library")

    def open_capture(self, path: str, **kw: Any) -> dict:
        """Load one capture, and say the name it will be called from now on.

        With the engine down there is nothing to load into, but the choice is still real:
        it is remembered and Setup plays it. That is what a picker is for.
        """
        from . import lastchain

        if not self.client.running:
            lastchain.save(capture=str(path))
            return {"capture": _name(str(path)), "path": str(path), "on_setup": True}
        got = self._call(cmd="load_capture", path=str(path))
        lastchain.save(capture=str(path))
        return {"capture": _name(got.get("capture")), "path": got.get("capture"),
                "receptive_field": got.get("receptive_field")}

    def open_cab(self, path: str, **kw: Any) -> dict:
        """Load one cabinet, and remember it the same way."""
        from . import lastchain

        if not self.client.running:
            lastchain.save(cab=str(path))
            return {"cab": _name(str(path)), "path": str(path), "on_setup": True}
        got = self._call(cmd="load_cab", path=str(path))
        lastchain.save(cab=str(path))
        return {"cab": _name(got.get("cab")), "path": got.get("cab"),
                "file_rate": got.get("file_rate"), "resampled": got.get("resampled")}

    def load_rig(self, capture: str | None = None, cab: str | None = None,
                 **knobs: Any) -> dict:
        """Put a whole saved sound into the engine, in one call.

        With the engine parked there is nothing to load into, but the choice is still real:
        the capture and cabinet are remembered and Setup plays them, the same as a picker.
        """
        from . import lastchain

        if not self.client.running:
            if capture:
                lastchain.save(capture=str(capture))
            if cab:
                lastchain.save(cab=str(cab))
            return {"pending": True, "capture": _name(capture), "cab": _name(cab),
                    **{k: v for k, v in knobs.items() if v is not None}}
        request: dict[str, Any] = {k: v for k, v in knobs.items() if v is not None}
        if capture:
            request["capture"] = str(capture)
            lastchain.save(capture=str(capture))
        if cab:
            request["cab"] = str(cab)
            lastchain.save(cab=str(cab))
        return self._call(cmd="rig", **request)

    def apply_rig(self, rig: dict) -> dict:
        """Resolve a rig's names against this machine, then load the whole sound.

        A rig travels as names, so loading one has to ask the library where that name lives
        here. A name that does not resolve is not a failure: the rest of the rig still loads
        and the answer says which part was kept, because a moved capture file should cost the
        capture and nothing else.
        """
        from . import rigs

        known = {"captures": [], "cabs": []}
        if self.client.running:
            try:
                known = self.library()
            except Exception:
                known = {"captures": [], "cabs": []}
        capture = rigs.resolve("capture", rig.get("capture", ""), known.get("captures", []))
        cab = rigs.resolve("cab", rig.get("cab", ""), known.get("cabs", []))
        fields = {key: rig[key] for key in
                  ("bass", "mid", "treble", "input_gain_db", "output_gain_db", "gate_db")
                  if key in rig}
        got = self.load_rig(capture=capture, cab=cab, **fields)
        missing = [f"{kind} {rig[kind]!r} is not in the library"
                   for kind in ("capture", "cab")
                   if rig.get(kind) and not (capture if kind == "capture" else cab)]
        got["kept"] = list(got.get("kept") or []) + missing
        return got


    def debug_call(self, cmd: str, **kw: Any) -> dict:
        return self._call(cmd=cmd, **kw)

    # helpers ---------------------------------------------------------------
    def _input_spec(self, got: dict) -> Any:
        """The socket the engine is really reading, counted from zero as the engine does."""
        if not got.get("running"):
            return port.INPUT_NONE
        return max(0, int(got.get("input_channel") or 1) - 1)
