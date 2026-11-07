"""The REAPER rig: the contest build's backend.

Every verb here is the bridge call it always was. This file exists so that
nothing above it has to say the word REAPER.
"""

from __future__ import annotations

import os
from typing import Any

from . import bridge, port


class ReaperRig:
    """A rig living inside a REAPER project, reached through the Lua bridge."""

    name = "reaper"

    # reading ------------------------------------------------------------
    def ping(self, **kw: Any) -> dict:
        return bridge.ping(**kw)

    def status(self, **kw: Any) -> dict:
        return bridge.status(**kw)

    def liveness(self) -> tuple[str, str]:
        return bridge.liveness()

    def tabs_list(self, **kw: Any) -> list:
        return bridge.tabs_list(**kw)

    def track_info(self, track: int = 1, **kw: Any) -> dict:
        return bridge.track_info(track=track, **kw)

    def track_names(self, **kw: Any) -> list[str]:
        return bridge.track_names(**kw)

    def fx_list(self, track: int = 1, **kw: Any) -> list:
        return bridge.fx_list(track=track, **kw)

    def fx_params(self, track: int = 1, fx: int = 0, keep: int | None = None,
                  **kw: Any) -> list:
        return bridge.fx_params(track=track, fx=fx, keep=keep, **kw)

    def param_index(self, name: str, track: int = 1, fx: int = 0, **kw: Any) -> int:
        return bridge.param_index(name, track=track, fx=fx, **kw)

    def master(self, **kw: Any) -> dict:
        return bridge.master(**kw)

    def meter(self, track: int = 1, **kw: Any) -> dict:
        return bridge.meter(track=track, **kw)

    def inputs(self, **kw: Any) -> dict:
        return bridge.inputs(**kw)

    def describe_input(self, value: Any) -> str:
        return bridge.describe_input(value)

    def project(self) -> str | None:
        return port.active_project(self)

    # writing ------------------------------------------------------------
    def set_master(self, db: float, expect: str | None = None, **kw: Any) -> dict:
        # One implementation, so a test that stubs the read also sees the write.
        return self.master(db=db, expect=expect, **kw)

    def set_track_level(self, track: int = 1, db: float = port.SAFE_MASTER_DB,
                        expect: str | None = None, **kw: Any) -> dict:
        return bridge.call("TRACK_VOL", track=track, db=db, expect=expect, **kw)

    def set_input(self, track: int = 1, spec: Any = 1, **kw: Any) -> dict:
        return bridge.set_input(track=track, spec=spec, **kw)

    def arm(self, track: int = 1, monitor: bool = True, allow_loud: bool = False,
            **kw: Any) -> dict:
        return bridge.arm(track=track, monitor=monitor, allow_loud=allow_loud, **kw)

    def disarm(self, track: int = 1, **kw: Any) -> dict:
        return bridge.disarm(track=track, **kw)

    def fx_bypass(self, track: int = 1, fx: int = 0, on: bool = True, **kw: Any) -> dict:
        return bridge.fx_bypass(track=track, fx=fx, on=on, **kw)

    def fx_focus(self, track: int = 1, fx: int = 0, **kw: Any) -> dict:
        return bridge.fx_focus(track=track, fx=fx, **kw)

    def undo(self, **kw: Any) -> dict:
        return bridge.undo(**kw)

    def open_rig(self, path: str, **kw: Any) -> dict:
        return bridge.open_rig(path, **kw)

    def close_rig(self, path: str, **kw: Any) -> dict:
        return bridge.call("PROJ_TAB_CLOSE", expect=path, timeout=kw.pop("timeout", 20.0), **kw)

    # project-shaped verbs ----------------------------------------------
    def build(self, *args: Any, **kw: Any) -> Any:
        from . import rig as rig_mod
        return rig_mod.build_rig(*args, **kw)

    def teardown(self, *args: Any, **kw: Any) -> dict:
        from . import rig as rig_mod
        return rig_mod.teardown(*args, **kw)

    # the host ----------------------------------------------------------
    def find(self) -> str:
        from . import launch
        return launch.find_reaper()

    def running(self) -> bool:
        from . import launch
        return launch.running()

    def state(self) -> dict:
        from . import launch
        return launch.state()

    def host_alive(self) -> bool:
        from . import launch
        return launch.bridge_alive()

    def ensure(self, **kw: Any) -> dict:
        from . import launch
        return launch.ensure_running(**kw)

    def ensure_audio(self, **kw: Any) -> Any:
        from . import launch
        return launch.ensure_audio(**kw)

    def stop(self) -> bool:
        from . import launch
        return launch.stop()

    def ui_visible(self) -> bool:
        from . import launch
        return launch.ui_visible()

    def install(self, **kw: Any) -> Any:
        from . import install_bridge
        return install_bridge.install(**kw)

    def prepare_file(self, path: Any, **kw: Any) -> Any:
        from .rigfile import write_template
        return write_template(path, **kw)

    def debug_call(self, cmd: str, **kw: Any) -> dict:
        return bridge.call(cmd, **kw)


def script_path() -> str:
    """Where the Lua bridge lives inside REAPER, for the installer."""
    return os.fspath(bridge.bridge_root())
