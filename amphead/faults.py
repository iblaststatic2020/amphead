"""What to do about a file that would not load.

The engine raises the truth: an exception with a type name and a message. That is the right thing
for a log and the wrong thing for a player, who is looking at a sentence on a pane and needs the
next step, not the exception class. This module turns the common failures into one sentence that
names the fix, and stays silent when it has nothing to add rather than padding the error.

Pure: it reads an exception, a kind and a path. It touches no file.
"""

from __future__ import annotations

#: The two kinds of file the amp loads, named the way the rest of the plugin names them.
CAPTURE = "capture"
CAB = "cab"


def _said(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


def _short(path: str) -> str:
    """The file name, not the whole path: the player knows which folder they picked."""
    if not path:
        return "that file"
    name = str(path).replace("\\", "/").rsplit("/", 1)[-1]
    return name or "that file"


def explain(exc: BaseException, kind: str = "", path: str = "") -> str:
    """One sentence naming the fix, or "" when the message already says everything."""
    said = _said(exc)
    lower = said.lower()
    name = _short(path)
    kind = str(kind or "").strip().lower()

    if isinstance(exc, FileNotFoundError) or "no such file" in lower:
        where = "captures" if kind == CAPTURE else "cabinets" if kind == CAB else "files"
        return (f"{name} is not at that path any more. `amphead_settings action=find` locates the "
                f"{where} folder, and `amphead_settings action=set` points the amp at it.")

    if isinstance(exc, PermissionError) or "permission" in lower or "access is denied" in lower:
        return (f"Windows would not let the amp read {name}. Close whatever has it open, or copy "
                f"it somewhere else and load that copy.")

    if isinstance(exc, MemoryError):
        return (f"{name} is larger than this machine can hold in memory. Try a shorter capture "
                f"or a shorter cabinet.")

    if kind == CAPTURE:
        if "only wavenet captures are supported" in lower:
            model = said.rsplit("is", 1)[-1].strip() if " is " in said else "another architecture"
            return (f"{name} is {model}, and this engine runs NAM's WaveNet captures only. Export "
                    f"it as WaveNet in the trainer, or pick another capture from the folder.")
        if "not supported" in lower:
            return (f"{name} uses part of the NAM format this engine does not implement. A plain "
                    f"WaveNet capture from the same folder will load.")
        if "json" in lower or isinstance(exc, (KeyError, TypeError, UnicodeDecodeError)):
            return (f"{name} opened but does not look like a .nam capture. Check the picker landed "
                    f"on the capture file and not on a shortcut or a text note.")
        if "weights ended" in lower:
            return (f"{name} is truncated: the file ends before its weights do. Copy it again from "
                    f"wherever you got it.")
        if "configuration" in lower or "config" in lower:
            return (f"{name} has no model configuration in it, so it is not a complete NAM capture.")

    if kind == CAB:
        if "sample width" in lower:
            return (f"{name} is not PCM audio the amp can read. Export the impulse as a 16 or "
                    f"24 bit wav, mono or stereo, and load that.")
        if "at least two samples" in lower:
            return (f"{name} has no audio in it. Pick a cabinet impulse with a real tail.")
        if "wav" in lower or "riff" in lower or "fmt" in lower:
            return (f"{name} is not a wav file the reader understands. Impulses must be uncompressed "
                    f"PCM wav, not mp3 or a compressed container with a .wav name.")

    if kind == "tune" and "device" in lower:
        return "The device would not open for a measurement. Check no other app has it in exclusive mode."

    return ""
