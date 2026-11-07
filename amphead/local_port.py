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


class EngineClient:
    """A pipe to ``amphead.engine_main``, started on first use and reused after that."""

    def __init__(self, python: str | None = None, timeout: float = 20.0) -> None:
        self.python = python or os.environ.get("AMPHEAD_ENGINE_PYTHON") or sys.executable
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
        # No tabs: the engine holds one chain, and it is always the active one.
        return []

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
        got = self._call(cmd="arm")
        return {"armed": bool(got.get("armed")), "monitor": bool(got.get("armed")),
                "master_db": got.get("master_db")}

    def disarm(self, track: int = 1, **kw: Any) -> dict:
        got = self._call(cmd="disarm")
        return {"armed": bool(got.get("armed")), "master_db": got.get("master_db")}

    def fx_bypass(self, track: int = 1, fx: int = 0, on: bool = True, **kw: Any) -> dict:
        """Turning a stage off is real; turning one back on needs its file again."""
        if not 0 <= fx < len(STAGES):
            raise EngineRefused(f"{LABEL} has no stage {fx}")
        name = STAGES[fx]
        if on:
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
            "path": str(got.get("capture") or ""),
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
