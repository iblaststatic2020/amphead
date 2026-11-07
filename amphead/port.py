"""What a rig must do, in words that are not REAPER's.

AmpHead runs on one of two rigs:

``reaper``
    The contest build. A Lua bridge inside a REAPER project.
``amphead2``
    The local engine. No DAW, our own audio device and our own chain.

Everything above this file (tools, verdict, presets, the pane) talks to ``Rig``.
That is the seam: swapping the whole backend is one environment variable,
``AMPHEAD_ENGINE``, and nothing above has to know which one answered.
"""

from __future__ import annotations

import os
from typing import Any, Protocol, runtime_checkable

#: Where the master parks when nobody is playing.
SAFE_MASTER_DB = -60.0
#: REAPER encodes stereo pairs as 1024 + n. Both rigs speak the same input spec.
INPUT_PAIR_BASE = 1024
INPUT_NONE = -1


class RigError(Exception):
    """The rig could not be asked."""


class NoRig(RigError):
    """Nothing is listening. REAPER is closed, or the engine is not running."""


class OldRig(RigError):
    """Something answered, but its protocol is older than ours."""


class RigBlocked(RigError):
    """The rig is mid-write and refused to answer for now."""


class RigTimeout(RigError):
    """The rig did not answer in time."""


class RigSaidNo(RigError):
    """The rig answered and said no."""


@runtime_checkable
class Rig(Protocol):
    """The verbs AmpHead needs. Implementations: reaper, amphead2."""

    name: str

    def ping(self, **kw: Any) -> dict: ...
    def status(self, **kw: Any) -> dict: ...
    def liveness(self) -> tuple[str, str]: ...
    def tabs_list(self, **kw: Any) -> list: ...
    def track_info(self, track: int = 1, **kw: Any) -> dict: ...
    def track_names(self, **kw: Any) -> list[str]: ...
    def fx_list(self, track: int = 1, **kw: Any) -> list: ...
    def fx_params(self, track: int = 1, fx: int = 0,
                  keep: int | None = None, **kw: Any) -> list: ...
    def param_index(self, name: str, track: int = 1, fx: int = 0,
                    **kw: Any) -> int: ...
    def fx_bypass(self, track: int = 1, fx: int = 0, on: bool = True,
                  **kw: Any) -> dict: ...
    def fx_focus(self, track: int = 1, fx: int = 0, **kw: Any) -> dict: ...
    def master(self, **kw: Any) -> dict: ...
    def set_master(self, db: float, expect: str | None = None, **kw: Any) -> dict: ...
    def set_track_level(self, track: int = 1, db: float = SAFE_MASTER_DB,
                        expect: str | None = None, **kw: Any) -> dict: ...
    def meter(self, track: int = 1, **kw: Any) -> dict: ...
    def inputs(self, **kw: Any) -> dict: ...
    def describe_input(self, value: Any) -> str: ...
    def set_input(self, track: int = 1, spec: Any = 1, **kw: Any) -> dict: ...
    def arm(self, track: int = 1, monitor: bool = True,
            allow_loud: bool = False, **kw: Any) -> dict: ...
    def disarm(self, track: int = 1, **kw: Any) -> dict: ...
    def undo(self, **kw: Any) -> dict: ...
    def project(self) -> str | None: ...
    def open_rig(self, path: str, **kw: Any) -> dict: ...
    def close_rig(self, path: str, **kw: Any) -> dict: ...
    def build(self, *args: Any, **kw: Any) -> Any: ...
    def teardown(self, *args: Any, **kw: Any) -> dict: ...

    # the host the rig lives in -----------------------------------------
    def find(self) -> str: ...
    def running(self) -> bool: ...
    def state(self) -> dict: ...
    def host_alive(self) -> bool: ...
    def ensure(self, **kw: Any) -> dict: ...
    def ensure_audio(self) -> Any: ...
    def stop(self) -> bool: ...
    def ui_visible(self) -> bool: ...
    def install(self, **kw: Any) -> Any: ...
    def prepare_file(self, path: Any, **kw: Any) -> Any: ...

    # escape hatch, for the tests' wire stubs only -----------------------
    def debug_call(self, cmd: str, **kw: Any) -> dict: ...


def active_project(rig: Rig) -> str | None:
    """The tab the user is looking at, from the tab list a rig publishes."""
    for tab in rig.tabs_list():
        if isinstance(tab, dict) and tab.get("active") and tab.get("path"):
            return str(tab["path"])
    return None


def _build(name: str) -> Rig:
    if name == "reaper":
        from .reaper_port import ReaperRig
        return ReaperRig()
    if name in ("amphead2", "2"):
        from .local_port import AmpHead2Rig
        return AmpHead2Rig()
    raise RigError(f"no rig called {name!r}; try AMPHEAD_ENGINE=reaper or amphead2")


_cache: dict[str, Rig] = {}


def choose(name: str | None = None) -> Rig:
    """The rig called ``name``, or the one ``AMPHEAD_ENGINE`` names."""
    key = (name or os.environ.get("AMPHEAD_ENGINE") or "reaper").strip().lower()
    if key not in _cache:
        _cache[key] = _build(key)
    return _cache[key]


def active() -> Rig:
    """The rig this process was started against."""
    return choose()
