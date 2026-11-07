"""Where REAPER keeps its files, per operating system.

REAPER is one application on three platforms (Windows XP-11, macOS, Linux on Intel and ARM),
and its resource directory sits somewhere different on each. Everything else in AmpHead is
platform neutral: the Lua bridge is ReaScript, the request channel is files, the parameters
come back over the same calls. These two functions are the whole platform surface.

``REAPER_RESOURCE_PATH`` still wins, because a portable install keeps its resource directory
next to the binary and only the user knows where that is.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def resource_path(explicit: str | os.PathLike | None = None) -> Path:
    """REAPER's resource directory.

    Windows keeps it under ``%APPDATA%\\REAPER``, macOS under
    ``~/Library/Application Support/REAPER``, and Linux under ``$XDG_CONFIG_HOME/REAPER``,
    which is ``~/.config/REAPER`` by default.
    """
    if explicit is not None:
        return Path(explicit)
    env = os.environ.get("REAPER_RESOURCE_PATH")
    if env:
        return Path(env)
    home = Path.home()
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or (home / "AppData" / "Roaming")
        return Path(base) / "REAPER"
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "REAPER"
    base = os.environ.get("XDG_CONFIG_HOME") or (home / ".config")
    return Path(base) / "REAPER"


def bridge_dir(resource: str | os.PathLike | None = None) -> Path:
    """Where the Lua bridge scripts live inside the resource directory."""
    return resource_path(resource) / "Scripts" / "hermes_bridge"
