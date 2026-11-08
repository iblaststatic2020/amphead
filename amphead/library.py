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

from . import settings

#: Where captures usually live: the Neural Amp Modeler convention under Documents.
CAPTURE_DEFAULTS = ("Documents", "Guitar Amp Sims", "NAM")
#: Where cabinets usually live: plain IR folders, and Aurora DSP's factory folder.
CAB_DEFAULTS = (("Documents", "Guitar Amp Sims", "IRs"), ("Documents", "Aurora DSP"))


def _folders(key: str, defaults: list[Path], current: str) -> list[Path]:
    """Every folder worth scanning, in order, without duplicates.

    Settings first, because a folder the player chose is a better guess than a folder
    this code was told about: the environment variable, then the settings file, then the
    usual places, then wherever the file already loaded came from.
    """
    found: list[Path] = settings.folders(key)
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


#: Where a person keeps a library they downloaded: the usually-synced folders.
SEARCH_ROOTS = ("Documents", "Downloads", "Desktop", "Music", "OneDrive",
                "OneDrive/Documents", "Dropbox", "Google Drive")
#: Words in a folder name that say "this is a tone library", and words that say "these are
#: recordings". A render folder can hold five hundred wavs and still be the last place a
#: player keeps a cabinet, so the name is worth more than the count.
LIBRARY_WORDS = ("ir", "irs", "impulse", "cab", "cabs", "nam", "capture", "captures", "amp",
                 "amps", "tone", "tones", "preset", "presets", "kemper", "neural")
RECORDING_WORDS = ("media", "render", "renders", "bounce", "mixes", "stems", "samples",
                   "loops", "backup", "projectdata", "recordings")

#: Deep enough to clear "Documents/OneDrive/Guitar/NAM", shallow enough to stay quick.
FIND_DEPTH = 4
FIND_LIMIT = 40000


def _library_score(row: dict) -> int:
    """How much a folder's name argues for it being a tone library rather than a session."""
    parts = re.split(r"[^a-z0-9]+", row["path"].lower())
    words = [p for p in parts if p]
    good = any(w.startswith(LIBRARY_WORDS) for w in words)
    bad = any(w.startswith(RECORDING_WORDS) for w in words)
    return (1000 if good else 0) - (1000 if bad else 0) + row["captures"] + row["cabs"]


def find(depth: int = FIND_DEPTH, limit: int = FIND_LIMIT, max_rows: int = 40) -> dict:
    """Folders that look like a capture or cabinet library, for the agent to offer.

    Nobody knows where a player unzipped their pack, but it is almost always in one of the
    folders that sync: Documents, Downloads, Desktop, Music, and the cloud folders that
    appear inside them. Walk those a few levels down, count the files that matter, and
    report the folders that hold them, biggest first. The caller decides; this only looks.
    """
    home = Path.home()
    roots: list[Path] = []
    for name in SEARCH_ROOTS:
        candidate = home.joinpath(*name.split("/"))
        if candidate.is_dir() and candidate not in roots:
            roots.append(candidate)
    # Already-configured folders count too: a library can live anywhere.
    for key in ("capture_dirs", "cab_dirs"):
        for folder in settings.folders(key):
            if folder.is_dir() and folder not in roots:
                roots.append(folder)

    tally: dict[Path, list[int]] = {}
    visits = 0
    for root in roots:
        base_depth = len(root.parts)
        for base, subdirs, files in os.walk(root):
            visits += len(files)
            if visits > limit:
                break
            here = Path(base)
            subdirs[:] = [d for d in subdirs if not d.startswith((".", "$"))
                          and len(here.parts) - base_depth < depth]
            nam = sum(1 for n in files if n.lower().endswith(".nam"))
            wav = sum(1 for n in files if n.lower().endswith(".wav"))
            if nam or wav:
                tally[here] = [nam, wav]
        if visits > limit:
            break

    rows = []
    for folder, (nam, wav) in tally.items():
        rows.append({
            "path": str(folder),
            "captures": nam,
            "cabs": wav,
            "kind": "captures" if nam > wav else ("cabs" if wav > nam else "both"),
        })
    rows.sort(key=lambda r: (-_library_score(r), r["path"].lower()))
    return {
        "candidates": rows[:max_rows],
        "scanned": [str(r) for r in roots],
        "configured": {"capture_dirs": [str(p) for p in settings.folders("capture_dirs")],
                       "cab_dirs": [str(p) for p in settings.folders("cab_dirs")]},
        "settings": str(settings.path()),
    }


def listing(capture: str = "", cab: str = "") -> dict:
    """Both menus, plus the two names currently loaded (if any)."""
    home = Path.home()
    capture_roots = _folders("capture_dirs", [home.joinpath(*CAPTURE_DEFAULTS)], capture)
    cab_roots = _folders("cab_dirs", [home.joinpath(*each) for each in CAB_DEFAULTS], cab)
    return {
        "captures": scan(capture_roots, (".nam",)),
        "cabs": scan(cab_roots, (".wav",)),
        "capture": capture,
        "cab": cab,
        "capture_dirs": [str(p) for p in capture_roots],
        "cab_dirs": [str(p) for p in cab_roots],
    }
