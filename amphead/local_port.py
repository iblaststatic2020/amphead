"""The local rig: AmpHead 2, no DAW.

This is the seam's second implementation, and right now it is a shape, not an
engine. Every verb answers honestly: either "not running" or "not built yet",
in one sentence, because a rig that raises a traceback is worse than a rig that
says what is missing.

When the engine lands it fills these methods in. Nothing above this file
changes: the pane, the tools, the verdict and the presets keep working, because
they only ever asked for verbs.
"""

from __future__ import annotations

from typing import Any

from . import port

LABEL = "AmpHead 2"


class NotBuilt(port.RigError):
    """AmpHead 2 has no engine yet for this verb."""


class AmpHead2Rig:
    """AmpHead's own engine, hosting the chain without a DAW."""

    name = "amphead2"
    label = LABEL
    #: What this rig will be able to do, once the engine lands.
    planned = (
        "params", "set", "master", "meter", "arm", "disarm",
        "snapshot", "restore", "record", "presets", "focus",
    )

    def _not_built(self, verb: str) -> NotBuilt:
        return NotBuilt(
            f"{LABEL} has no {verb} yet. It is the no-DAW engine, and only its "
            f"shape exists so far; run AMPHEAD_ENGINE=reaper to play through REAPER."
        )

    # answers that are true today --------------------------------------
    def ping(self, **kw: Any) -> dict:
        raise port.NoRig(f"{LABEL} is not running. Start it, or use AMPHEAD_ENGINE=reaper.")

    def status(self, **kw: Any) -> dict:
        raise port.NoRig(f"{LABEL} is not running.")

    def liveness(self) -> tuple[str, str]:
        return ("absent", f"{LABEL} is not running; the engine is not built yet")

    def tabs_list(self, **kw: Any) -> list:
        return []

    def project(self) -> str | None:
        return None

    def track_info(self, track: int = 1, **kw: Any) -> dict:
        raise self._not_built("track")

    def track_names(self, **kw: Any) -> list[str]:
        return []

    def inputs(self, **kw: Any) -> dict:
        raise self._not_built("input list")

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

    # verbs that need the engine ---------------------------------------
    def fx_list(self, track: int = 1, **kw: Any) -> list:
        raise self._not_built("chain")

    def fx_params(self, track: int = 1, fx: int = 0, keep: int | None = None,
                  **kw: Any) -> list:
        raise self._not_built("knobs")

    def param_index(self, name: str, track: int = 1, fx: int = 0, **kw: Any) -> int:
        raise self._not_built("knob lookup")

    def fx_bypass(self, track: int = 1, fx: int = 0, on: bool = True, **kw: Any) -> dict:
        raise self._not_built("bypass")

    def fx_focus(self, track: int = 1, fx: int = 0, **kw: Any) -> dict:
        raise self._not_built("FX window")

    def master(self, **kw: Any) -> dict:
        raise self._not_built("output level")

    def set_master(self, db: float, expect: str | None = None, **kw: Any) -> dict:
        raise self._not_built("output level")

    def meter(self, track: int = 1, **kw: Any) -> dict:
        raise self._not_built("meter")

    def set_track_level(self, track: int = 1, db: float = port.SAFE_MASTER_DB,
                        expect: str | None = None, **kw: Any) -> dict:
        raise self._not_built("track level")

    def set_input(self, track: int = 1, spec: Any = 1, **kw: Any) -> dict:
        raise self._not_built("input")

    def arm(self, track: int = 1, monitor: bool = True, allow_loud: bool = False,
            **kw: Any) -> dict:
        raise self._not_built("arm")

    def disarm(self, track: int = 1, **kw: Any) -> dict:
        raise self._not_built("disarm")

    def undo(self, **kw: Any) -> dict:
        raise self._not_built("undo")

    def open_rig(self, path: str, **kw: Any) -> dict:
        raise self._not_built("project file")

    def close_rig(self, path: str, **kw: Any) -> dict:
        raise self._not_built("project file")

    def build(self, *args: Any, **kw: Any) -> Any:
        raise self._not_built("rig builder")

    def teardown(self, *args: Any, **kw: Any) -> dict:
        raise self._not_built("rig teardown")

    # the host it has none of -------------------------------------------
    def find(self) -> str:
        raise self._not_built("DAW install")

    def running(self) -> bool:
        return False

    def state(self) -> dict:
        return {"running": False, "ui": None, "engine": self.name}

    def host_alive(self) -> bool:
        return False

    def ensure(self, **kw: Any) -> dict:
        raise self._not_built("DAW launch")

    def ensure_audio(self, **kw: Any) -> Any:
        # AmpHead 2 brings its own device; there is no DAW audio to prepare.
        return "any"

    def stop(self) -> bool:
        return True

    def ui_visible(self) -> bool:
        return False

    def install(self, **kw: Any) -> Any:
        raise self._not_built("installer")

    def prepare_file(self, path: Any, **kw: Any) -> Any:
        # No project file exists to write.
        return None

    def debug_call(self, cmd: str, **kw: Any) -> dict:
        raise self._not_built("wire")
