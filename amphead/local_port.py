"""The local rig: AmpHead 2, no DAW.

Same verbs the REAPER rig answers, so the pane, the tools, the verdict and the
presets cannot tell which one is behind them. The only difference a player sees is
that no window opens.

The engine is a separate process on purpose: it owns the audio device, the agent
owns the conversation, and neither can stall the other. Here we are only the phone
line to it -- one JSON object per line in, one per line out.
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

from . import port

LABEL = "AmpHead 2"

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


def engine_python(explicit: str | None = None) -> str:
    """The interpreter that can host the engine, in the order a person would look.

    An explicit argument, then AMPHEAD_ENGINE_PYTHON, then the virtualenv this repo keeps
    beside itself (.engine), then this process's own interpreter. The venv step is the one
    that matters: Hermes runs the plugin in its own Python, which has no numpy and no
    PortAudio, so "press Setup in the pane" would otherwise answer "install numpy" to a
    player who already has an engine that runs.
    """
    if explicit:
        return explicit
    said = os.environ.get("AMPHEAD_ENGINE_PYTHON")
    if said:
        return said
    root = Path(__file__).resolve().parent.parent
    for relative in ("Scripts/python.exe", "bin/python3", "bin/python"):
        candidate = root / ".engine" / relative
        if candidate.is_file():
            return str(candidate)
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

    def start(self) -> None:
        if self.running:
            return
        try:
            import numpy  # noqa: F401
            import sounddevice  # noqa: F401
        except Exception as exc:
            raise EngineNotInstalled(
                f"{LABEL} needs numpy and sounddevice in {self.python}, and this one cannot "
                f"import them ({type(exc).__name__}: {exc}). Install them there, or point "
                f"AMPHEAD_ENGINE_PYTHON at an interpreter that has them."
            ) from exc
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
            raise EngineGone(f"{LABEL} is not running. Start it with amphead_setup, or play "
                             f"through REAPER with AMPHEAD_ENGINE=reaper.")
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
    """AmpHead's own engine, hosting the chain without a DAW."""

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
        return self._call(cmd="status")

    def liveness(self) -> tuple[str, str]:
        if self.client.running:
            return ("live", f"{LABEL} is running")
        return ("absent", f"{LABEL} is not running")

    def tabs_list(self, **kw: Any) -> list:
        """One chain, always open, always the one you are listening to.

        The pane asks a rig which tab is active and refuses to write without an answer,
        so the engine answers with itself instead of an empty list: the guard exists to
        stop a stale handle landing on somebody's song, and there is no song here.
        """
        return [{"active": True, "path": str(self.project() or LABEL), "index": 0}]

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

    def track_names(self, **kw: Any) -> list[str]:
        return [LABEL]

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
        got = self._call(cmd="devices")
        rows = [{"index": row["index"], "name": row["name"], "channels": 1}
                for row in got.get("inputs", [])]
        return {"count": len(rows), "channels": rows}

    def describe_input(self, value: Any) -> str:
        if value is None or value == "":
            return "no input"
        if isinstance(value, int):
            if value >= port.INPUT_PAIR_BASE:
                return f"input {value - port.INPUT_PAIR_BASE + 1} (stereo)"
            if value == port.INPUT_NONE:
                return "no input"
            return f"input {value + 1}"
        return str(value)

    def project(self) -> str | None:
        got = self._call(cmd="status")
        return str(got.get("capture") or "") or None

    def fx_list(self, track: int = 1, **kw: Any) -> list:
        got = self._call(cmd="status")
        present = {
            "Capture": bool(got.get("capture")),
            "Tone": any(abs(float(v)) > 1e-9 for v in (got.get("tone") or {}).values()),
            "Cabinet": bool(got.get("cab")),
        }
        return [{"i": i, "name": name, "enabled": present[name]} for i, name in enumerate(STAGES)]

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
                "norm": max(-12.0, min(12.0, raw)) / 24.0 + 0.5,
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

    def set_input(self, track: int = 1, spec: Any = 1, **kw: Any) -> dict:
        index = int(spec if spec not in (None, "") else 0)
        got = self._call(cmd="start", input_device=index)
        return {"recinput": index, "mode": got.get("mode"),
                "latency_ms": got.get("latency_ms")}

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

        Taking a stage out is honest and immediate. Putting it back is not: the engine
        holds a capture and an IR, not a rack of plug-ins, so a stage that was bypassed
        has to be loaded again rather than switched back on. Saying so beats a chain that
        reports itself restored while it is still clean.
        """
        if not 0 <= fx < len(STAGES):
            raise EngineRefused(f"{LABEL} has no stage {fx}")
        name = STAGES[fx]
        if not on:
            raise EngineRefused(
                f"{LABEL} cannot switch {name} back on without loading it again: "
                f"run amphead_capture or amphead_cab, or set the tone knobs."
            )
        if name == "Capture":
            self._call(cmd="clear_capture")
        elif name == "Cabinet":
            self._call(cmd="clear_cab")
        else:
            self._call(cmd="tone", bass=0.0, mid=0.0, treble=0.0)
        return {"i": fx, "name": name, "enabled": False}

    def fx_focus(self, track: int = 1, fx: int = 0, **kw: Any) -> dict:
        return {"focused": None, "note": f"{LABEL} draws no plugin windows"}

    def undo(self, **kw: Any) -> dict:
        raise EngineRefused(f"{LABEL} has no undo history; the chain is three stages and no edits")

    # project-shaped verbs --------------------------------------------------
    def open_rig(self, path: str, **kw: Any) -> dict:
        return {"path": path, "mode": "engine", "note": f"{LABEL} keeps no project file"}

    def close_rig(self, path: str, **kw: Any) -> dict:
        self._call(cmd="panic")
        return {"mode": "engine", "tracks_removed": 0, "path": path}

    def build(self, *args: Any, **kw: Any) -> Any:
        raise EngineRefused(
            f"{LABEL} needs no build step: load a capture with amphead_capture, a cabinet with "
            f"amphead_cab, and arm. There is no track to create."
        )

    def teardown(self, *args: Any, **kw: Any) -> dict:
        self._call(cmd="stop")
        return {"closed": "engine stopped", "host": "no host to stop"}

    # the host it does not have ---------------------------------------------
    def find(self) -> str:
        return f"{self.client.python} (engine interpreter)"

    def running(self) -> bool:
        return self.client.running

    def state(self) -> dict:
        if not self.client.running:
            return {"bridge": "gone", "rig_tab": True, "project": LABEL,
                    "path": "", "tracks": 1, "device": {"closed": True}, "engine": self.name}
        got = self._call(cmd="status")
        latency = got.get("latency_ms")
        return {
            "bridge": "live",
            "rig_tab": True,
            "project": LABEL,
            "path": str(got.get("capture") or f"{LABEL} (no project file)"),
            "tracks": 1,
            "device": {
                "buffer": got.get("blocksize"),
                "rate": got.get("samplerate"),
                "latency_out_ms": round(float(latency or 0.0) / 2.0, 2),
                "latency_rt_ms": latency,
                "closed": not got.get("running"),
                "mode": got.get("mode"),
            },
            "engine": self.name,
        }

    def host_alive(self) -> bool:
        return self.client.running

    def ensure(self, **kw: Any) -> dict:
        self.client.start()
        return {"ui": "none", "engine": self.name}

    def ensure_audio(self, **kw: Any) -> Any:
        got = self._call(cmd="start")
        devices = self._call(cmd="devices")
        return {
            "inputs": len(devices.get("inputs") or []),
            "outputs": len(devices.get("outputs") or []),
            "mode": got.get("mode"),
            "output_latency_ms": round(float(got.get("latency_ms") or 0) / 2, 2),
        }

    def stop(self) -> bool:
        return self.client.stop()

    def ui_visible(self) -> bool:
        return False

    def install(self, **kw: Any) -> Any:
        try:
            import sounddevice  # noqa: F401
        except Exception as exc:
            raise EngineNotInstalled(
                f"{LABEL} has no installer: it needs numpy and sounddevice in "
                f"{self.client.python} ({type(exc).__name__}: {exc}). "
                f"pip install numpy sounddevice, then run setup."
            ) from exc
        return type("Installed", (), {"bridge_path": self.client.python,
                                      "startup_action": "nothing to install"})()

    def prepare_file(self, path: Any, **kw: Any) -> Any:
        return None

    def debug_call(self, cmd: str, **kw: Any) -> dict:
        return self._call(cmd=cmd, **kw)

    # helpers ---------------------------------------------------------------
    def _input_spec(self, got: dict) -> Any:
        """The engine opens whatever device it was given, so a live rig has an input."""
        return 0 if got.get("running") else port.INPUT_NONE
