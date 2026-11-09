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
    # Interpreter with numpy and sounddevice, when it is not the one running the plugin.
    "engine_python": "",
    # Noise gate threshold in dBFS in front of the amp. -80 is off, which is the default:
    # a gate that arrives switched on is a gate that eats a quiet riff on the first take.
    "gate_db": -80.0,
    # The click. Monitoring only, never in the take. 120 is a neutral starting count;
    # a player recording to a song changes it once and the file remembers.
    "bpm": 120.0,
    "metronome": False,
    "metronome_db": -18.0,
    "audio": {
        "input_device": None,
        "output_device": None,
        # Counted from 1, the way an interface labels its sockets. 1 is the first
        # input, which is where most interfaces put the instrument jack.
        "input_channel": 1,
        "samplerate": 44100,
        # 256: the block is felt on top of the driver buffer, and 256 holds realtime on the
        # measured rig. The tune control soaks each size and writes back whichever the chain
        # actually feeds. See amphead/tune.py.
        "blocksize": 256,
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


#: Set when the file on disk could not be read, so the pane can say so instead of
#: silently playing on defaults.
_BROKEN = ""


def problem() -> str:
    """Why the settings file is not being used, or "" when it is fine."""
    return _BROKEN


def _keep_aside(target: Path, text: str) -> Path:
    """Copy a file we cannot read to the side, so the next save cannot destroy it."""
    aside = target.with_suffix(target.suffix + ".broken")
    n = 2
    while aside.exists():
        aside = target.with_suffix(target.suffix + f".broken{n}")
        n += 1
    aside.write_text(text, encoding="utf-8")
    return aside


def load() -> dict:
    """The settings file, filled in with defaults. A broken file never stops the rig.

    A file that will not parse is copied to `settings.json.broken` instead of being
    dropped: every value in it was a choice the player made by hand, and the next
    `save()` writes the whole merged object, so using the defaults in memory and then
    saving would erase the lot on the first knob turn.
    """
    global _BROKEN
    _BROKEN = ""
    target = path()
    try:
        text = target.read_text(encoding="utf-8")
    except OSError:
        return _merge(DEFAULTS, {})
    try:
        raw = json.loads(text)
    except ValueError as exc:
        try:
            aside = _keep_aside(target, text)
        except OSError:
            _BROKEN = f"{target.name} could not be read ({exc}) and could not be copied aside"
        else:
            _BROKEN = (f"{target.name} could not be read ({exc}). It was kept as "
                       f"{aside.name} and the built-in settings are in use")
        return _merge(DEFAULTS, {})
    if not isinstance(raw, dict):
        _BROKEN = f"{target.name} is not a JSON object, so the built-in settings are in use"
        try:
            _keep_aside(target, text)
        except OSError:
            pass
        return _merge(DEFAULTS, {})
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


def _raw() -> dict:
    """Just the keys the file itself holds, with no defaults filled in."""
    try:
        raw = json.loads(path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def describe() -> dict:
    """What the file says, what the environment says, and what is built in.

    The pane shows this instead of a blank form: a player (or their agent) can see which
    folder is actually being scanned and whether it came from the file or from the older
    environment variable, without reading code.
    """
    file_says = load()
    written = _raw()
    rows = []
    for key in ("capture_dirs", "cab_dirs", "engine_python"):
        env = FOLDER_ENV.get(key)
        env_said = os.environ.get(env) if env else (os.environ.get("AMPHEAD_ENGINE_PYTHON") if key == "engine_python" else None)
        rows.append({
            "key": key,
            "value": file_says.get(key) or [],
            "env": env_said or "",
            "from": "environment" if env_said else ("settings" if written.get(key) else "built in"),
        })
    rows.append({"key": "audio", "value": audio(), "env": "",
                 "from": "settings" if written.get("audio") else "built in"})
    said = {"path": str(path()), "exists": path().exists(), "settings": rows}
    if _BROKEN:
        said["problem"] = _BROKEN
    return said
