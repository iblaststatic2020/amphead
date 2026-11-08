"""The seam between this plugin and the amp that answers for it.

There is one rig. The engine in `engine2.py` owns the audio interface and runs in its own
process; `local_port.py` is the phone line to it. This module holds the small amount of
vocabulary the tools, the routes and the tests need to share, and hands back the one rig.
"""

from __future__ import annotations

from typing import Any

#: Where a rig boots and parks: silent, but not off, so the device stays open.
SAFE_MASTER_DB = -60.0

#: The level arming uses. Measured on a -18 dBFS DI: peaks at about -2.9 dBFS, no clipping.
ARM_DB = -6.0


class RigError(Exception):
    """Something about the rig stopped the request, and the player can be told why."""


class NoRig(RigError):
    """There is nothing running to answer: the engine was never started, or it stopped."""


class RigSaidNo(RigError):
    """The engine answered, and the answer was no."""


class RigTimeout(RigError):
    """The engine did not answer in time."""


def choose(name: str | None = None) -> Any:
    """The rig this plugin talks to. There is only one, so a name is only checked."""
    from .local_port import AmpHead2Rig

    wanted = str(name or "").strip().lower()
    if wanted and wanted not in ("amphead2", "engine", "local"):
        raise ValueError(f"there is one rig, the local engine: {name!r} is not one of them")
    return AmpHead2Rig()


def active() -> Any:
    """The rig the tools hold for the life of the process."""
    return choose()
