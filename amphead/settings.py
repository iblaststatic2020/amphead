"""The few things no code can guess about the machine it runs on.

AmpHead works out most of its own world: it finds the audio interface, it remembers
the capture and cabinet played last.  What it cannot
work out is where a player keeps their captures, which interface input is the guitar,
and how hard this particular driver wants to be pushed. Those are choices, so they live
in a settings file, one JSON object, in the same place on every install:

    Windows   %APPDATA%\\amphead\\settings.json
    macOS     ~/Library/Application Support/amphead/settings.json
    Linux     $XDG_CONFIG_HOME/amphead/settings.json  (~/.config by default)

Nothing has to be set for the plugin to work: every key falls back to the value that was
measured on a real rig, and the pane still reads the folders a player is most likely to
have. The file exists so those guesses can be corrected without editing code or learning
an environment variable.

Precedence, highest first: an explicit argument, the environment variable that predates
this file (``AMPHEAD_CAPTURE_DIR`` and friends, still honoured so existing installs and
the tests keep working), the settings file, then the built-in default.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

#: Point this at a file to run against a scratch settings file (tests, portable installs).
SETTINGS_ENV = "AMPHEAD_SETTINGS"

DEFAULTS: dict[str, Any] = {
    # Folders scanned for captures (.nam) and cabinets (.wav). Empty means "use the
    # usual folders", which library.py knows how to find.
    "capture_dirs": [],
    "cab_dirs": [],
    # Interpreter with numpy and sounddevice, if it is not the bundled engine venv.
    "engine_python": "",
    "audio": {
        "input_device": None,
        "output_device": None,
        # Counted from 1, the way an interface labels its sockets. 1 is the first
        # input, which is where most interfaces put the instrument jack.
        "input_channel": 1,
        "samplerate": 44100,
        "blocksize": 512,
        # Per direction. 5 ms is the tightest that held realtime on the measured rig.
        "latency_ms": 5.0,
    },
}

#: The environment variable each folder key honours, for installs that predate this file.
FOLDER_ENV = {"capture_dirs": "AMPHEAD_CAPTURE_DIR", "cab_dirs": "AMPHEAD_CAB_DIR"}


def path() -> Path:
    """Where the settings file lives on this platform."""
    said = os.environ.get(SETTINGS_ENV)
    if said:
        return Path(said)
    home = Path.home()
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or (home / "AppData" / "Roaming")
        return Path(base) / "amphead" / "settings.json"
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "amphead" / "settings.json"
    base = os.environ.get("XDG_CONFIG_HOME") or (home / ".config")
    return Path(base) / "amphead" / "settings.json"


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for key, value in (over or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load() -> dict:
    """The settings file, filled in with defaults. A broken file never stops the rig."""
    try:
        raw = json.loads(path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    return _merge(DEFAULTS, raw)


def save(patch: dict) -> dict:
    """Merge ``patch`` into the file and return what the file now says.

    Written to a neighbouring temp file and moved into place, so a crash mid-write
    leaves the previous settings readable rather than a truncated file.
    """
    if not isinstance(patch, dict):
        raise TypeError("settings must be a JSON object")
    merged = _merge(load(), patch)
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(merged, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, target)
    return merged


def audio() -> dict:
    """The audio block, ready to hand to ``Engine`` / ``start``."""
    block = load().get("audio") or {}
    out = {}
    for key, default in DEFAULTS["audio"].items():
        value = block.get(key, default)
        if key in ("input_channel", "samplerate", "blocksize"):
            try:
                out[key] = int(value)
            except (TypeError, ValueError):
                out[key] = int(default)
        elif key == "latency_ms":
            try:
                out[key] = float(value)
            except (TypeError, ValueError):
                out[key] = float(default)
        elif key in ("input_device", "output_device"):
            try:
                out[key] = None if value in (None, "") else int(value)
            except (TypeError, ValueError):
                out[key] = None
    return out


def folders(key: str) -> list[Path]:
    """The folders to scan for one kind of file, in order, existing ones only.

    ``key`` is ``"capture_dirs"`` or ``"cab_dirs"``. The environment variable wins over
    the file because it is the older, more specific override. Callers add the usual
    folders after these, so a machine with no settings file still has a library.
    """
    said: list[Path] = []
    env = FOLDER_ENV.get(key)
    for raw in (os.environ.get(env) or "").split(os.pathsep) if env else []:
        if raw:
            said.append(Path(raw))
    for raw in load().get(key) or []:
        if raw:
            said.append(Path(str(raw)))
    return said


def describe() -> dict:
    """What the file says, what the environment says, and what is built in.

    The pane shows this instead of a blank form: a player (or their agent) can see which
    folder is actually being scanned and whether it came from the file or from the older
    environment variable, without reading code.
    """
    file_says = load()
    rows = []
    for key in ("capture_dirs", "cab_dirs", "engine_python"):
        env = FOLDER_ENV.get(key)
        env_said = os.environ.get(env) if env else (os.environ.get("AMPHEAD_ENGINE_PYTHON") if key == "engine_python" else None)
        rows.append({
            "key": key,
            "value": file_says.get(key) or [],
            "env": env_said or "",
            "from": "environment" if env_said else ("settings" if file_says.get(key) else "built in"),
        })
    rows.append({"key": "audio", "value": audio(), "env": "",
                 "from": "settings" if (file_says.get("audio") or {}) else "built in"})
    return {"path": str(path()), "exists": path().exists(), "settings": rows}
