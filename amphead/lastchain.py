"""The chain that was played last, kept where both entry points can read it.

The CLI writes it when you play, and the pane reads it when it starts, so pressing
Setup plays the amp you last chose instead of a clean DI. A capture is a file and a
cabinet is a file; remembering two paths is the whole job, and guessing one is worse
than starting quiet.
"""

import json
from pathlib import Path

STATE = Path.home() / ".amphead2.json"


def load() -> dict:
    """Whatever was remembered, or an empty dict if nothing was or it is unreadable."""
    try:
        got = json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return got if isinstance(got, dict) else {}


def save(**kw) -> None:
    """Remember the non-empty parts; a closed path must not overwrite a good one."""
    state = load()
    state.update({k: v for k, v in kw.items() if v})
    try:
        STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError:
        pass


def clear(*keys) -> None:
    """Forget fields that are no longer true, e.g. an aim a later load replaced."""
    state = load()
    for key in keys:
        state.pop(key, None)
    try:
        STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError:
        pass


def usable(path) -> str | None:
    """The remembered path, but only if the file is still there."""
    if not path:
        return None
    try:
        return str(path) if Path(path).is_file() else None
    except (OSError, ValueError):
        return None
