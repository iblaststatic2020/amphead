"""What amp files are on this machine, and where people keep them.

Two kinds of file make a tone: a capture (a ``.nam`` model of an amp) and a cabinet (a ``.wav``
impulse response of a speaker). Neither has a standard install location, so the pane cannot
guess one. This module is the one place that knows the usual folders, and it is stdlib-only on
purpose: the pane's own interpreter asks it too, before the engine is running, so a player
sees the menu they are about to choose from instead of an empty box.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

#: Where captures usually live: the Neural Amp Modeler convention under Documents.
CAPTURE_DEFAULTS = ("Documents", "Guitar Amp Sims", "NAM")
#: Where cabinets usually live: plain IR folders, and Aurora DSP's factory folder.
CAB_DEFAULTS = (("Documents", "Guitar Amp Sims", "IRs"), ("Documents", "Aurora DSP"))


def _folders(env: str, defaults: list[Path], current: str) -> list[Path]:
    """Every folder worth scanning, in order, without duplicates."""
    found: list[Path] = []
    for raw in (os.environ.get(env) or "").split(os.pathsep):
        if raw:
            found.append(Path(raw))
    found.extend(defaults)
    if current:
        found.append(Path(current).parent)
    seen, out = set(), []
    for path in found:
        key = str(path).lower()
        if key not in seen and path.is_dir():
            seen.add(key)
            out.append(path)
    return out


def scan(dirs: list[Path], suffixes: tuple[str, ...], limit: int = 2000) -> list[dict]:
    """Every matching file under ``dirs``, named by its folder below the root.

    The folder under the root is what tells two Bugera captures apart; "NAM" alone names a
    whole library and says nothing to a player.
    """
    rows, seen = [], set()
    for root in dirs:
        for base, _sub, files in os.walk(root):
            for name in sorted(files):
                if not name.lower().endswith(suffixes):
                    continue
                path = Path(base) / name
                key = str(path).lower()
                if key in seen:
                    continue
                seen.add(key)
                try:
                    below = Path(base).relative_to(root).parts[-2:]
                except ValueError:
                    below = ()
                rows.append({"path": str(path), "name": path.stem,
                             "folder": " / ".join(below) if below else root.name})
                if len(rows) >= limit:
                    return sorted(rows, key=lambda r: (r["folder"].lower(), r["name"].lower()))
    return sorted(rows, key=lambda r: (r["folder"].lower(), r["name"].lower()))


def tone_name(capture: str = "", cab: str = "") -> str:
    """A short name for the two files together, the way a player would say it.

    "Phillipe P Bugera6262-Lead-M77-ESR0,020" is a file name, not a tone. The words that
    matter survive; the capture rig's parameter tails and the vendor's own prefix do not.
    """
    def words(stem: str) -> list[str]:
        kept = []
        for part in re.split(r"[-_,\s]+", stem):
            if not part:
                continue
            low = part.lower()
            if low.startswith("esr") or re.fullmatch(r"0\d*|\d", low):
                continue
            if low in ("phillipe", "p"):
                continue
            if part.lower().startswith("bugera6262"):
                part = "Bugera 6262"
            kept.append(part)
        return kept

    halves = []
    for raw in (capture, cab):
        stem = Path(str(raw)).stem if raw else ""
        kept = words(stem)
        if kept:
            halves.append(" ".join(kept))
    return " / ".join(halves) if halves else "nothing loaded"


def listing(capture: str = "", cab: str = "") -> dict:
    """Both menus, plus the two names currently loaded (if any)."""
    home = Path.home()
    capture_roots = _folders("AMPHEAD_CAPTURE_DIR", [home.joinpath(*CAPTURE_DEFAULTS)], capture)
    cab_roots = _folders("AMPHEAD_CAB_DIR", [home.joinpath(*each) for each in CAB_DEFAULTS], cab)
    return {
        "captures": scan(capture_roots, (".nam",)),
        "cabs": scan(cab_roots, (".wav",)),
        "capture": capture,
        "cab": cab,
        "capture_dirs": [str(p) for p in capture_roots],
        "cab_dirs": [str(p) for p in cab_roots],
    }
