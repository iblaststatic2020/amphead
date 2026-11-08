"""Named sounds, and a way to hand one to somebody else.

A rig is not a preset the way a plugin models one. There are no hidden parameters to snapshot:
it is the capture, the cabinet, the three tone knobs, both trims and the gate. Eight numbers and
two names. So a rig is a small dict, stored as JSON beside the settings file, and it can also be
written as one line a person can paste into a chat.

Two decisions worth knowing:

* Rigs are stored by **name**, and the path is kept alongside as a convenience. Names travel,
  paths do not: your capture lives in a different folder than mine, and a rig that only worked
  on the machine that made it would be useless to share.
* Nothing here opens a device or talks to the engine. This module is a filing cabinet, which is
  why it tests with a temporary folder and no audio at all.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import limits, settings

#: What a rig remembers. Master is deliberately absent: a saved sound should not turn the room
#: up when it loads, and arming is a choice about the moment, not about the tone.
FIELDS = ("capture", "cab", "bass", "mid", "treble", "input_gain_db", "output_gain_db", "gate_db")

#: The short names a shared line uses, so it stays readable in a chat window.
SHORT = {
    "capture": "capture",
    "cab": "cab",
    "bass": "bass",
    "mid": "mid",
    "treble": "treble",
    "input_gain_db": "in",
    "output_gain_db": "out",
    "gate_db": "gate",
}
LONG = {short: long for long, short in SHORT.items()}
ALIASES = {"input": "input_gain_db", "in_gain": "input_gain_db", "output": "output_gain_db",
           "out_gain": "output_gain_db", "noisegate": "gate_db", "noise_gate": "gate_db"}

PREFIX = "amphead:"


def path() -> Path:
    """Where rigs live: beside the settings file, so one uninstall removes both."""
    return settings.path().parent / "rigs.json"


def _clean(name: str) -> str:
    """A name a shared line can carry: no separators, no shouting about it."""
    return " ".join(str(name).replace(";", " ").replace("=", " ").split())


def from_status(status: dict) -> dict:
    """The eight fields, taken from a live status payload."""
    tone = status.get("tone") or {}
    return {
        "capture": _clean(status.get("capture") or ""),
        "cab": _clean(status.get("cab") or ""),
        "bass": round(float(tone.get("bass", 0.0)), 2),
        "mid": round(float(tone.get("mid", 0.0)), 2),
        "treble": round(float(tone.get("treble", 0.0)), 2),
        "input_gain_db": round(float(status.get("input_gain_db", 0.0)), 2),
        "output_gain_db": round(float(status.get("output_gain_db", 0.0)), 2),
        "gate_db": round(float(status.get("gate_db", limits.GATE[0])), 2),
    }


def tidy(raw: dict) -> dict:
    """A rig that a layer above cannot break: known fields only, numbers inside their ranges."""
    out: dict[str, Any] = {}
    if raw.get("name"):
        out["name"] = _clean(raw["name"])
    for key in FIELDS:
        if key not in raw:
            continue
        if key in ("capture", "cab"):
            out[key] = _clean(raw.get(key) or "")
            continue
        try:
            value = float(raw[key])
        except (TypeError, ValueError):
            continue
        if key in ("bass", "mid", "treble"):
            value = limits.tone(value)
        elif key == "gate_db":
            value = limits.gate(value)
        else:
            value = limits.trim(value)
        out[key] = round(value, 2)
    return out


def encode(rig: dict) -> str:
    """One readable line: `amphead:lead | capture=Bugera;cab=Vintage 30;bass=3;...`."""
    rig = tidy(rig)
    parts = [f"{SHORT[key]}={rig[key]}" for key in FIELDS if key in rig]
    name = _clean(rig.get("name") or "")
    head = PREFIX + (name + " | " if name else "")
    return head + ";".join(parts)


def decode(text: str) -> dict:
    """The rig a pasted line describes. Raises ValueError for anything else."""
    line = " ".join(str(text or "").split())
    if not line.lower().startswith(PREFIX):
        raise ValueError("that is not an AmpHead rig line (it should start with amphead:)")
    body = line[len(PREFIX):]
    # A line with no name has no pipe, so the whole body is the fields, not a name.
    head, pipe, rest = body.partition("|")
    name, body = (head, rest) if pipe else ("", head)
    raw: dict[str, Any] = {}
    if name.strip():
        raw["name"] = _clean(name)
    for part in body.replace(",", ";").split(";"):
        key, sep, value = part.partition("=")
        key = key.strip().lower().replace("-", "_")
        if not sep:
            continue
        key = LONG.get(ALIASES.get(key, key), ALIASES.get(key, key))
        if key in FIELDS:
            raw[key] = value.strip()
    if not any(key in FIELDS for key in raw):
        raise ValueError("that rig line has no fields in it")
    return raw


class Rigs:
    """The filing cabinet: read, write, forget. Tolerant of a broken file."""

    def __init__(self, where: Path | str | None = None) -> None:
        self.where = Path(where) if where else path()

    def _read(self) -> dict:
        try:
            raw = json.loads(self.where.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(raw, dict):
            return {}
        rigs = raw.get("rigs")
        return rigs if isinstance(rigs, dict) else {}

    def _write(self, rigs: dict) -> None:
        self.where.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.where.with_suffix(".tmp")
        tmp.write_text(json.dumps({"version": 1, "rigs": rigs}, indent=2, ensure_ascii=False),
                       encoding="utf-8")
        tmp.replace(self.where)

    def names(self) -> list[str]:
        return list(self._read().keys())

    def get(self, name: str) -> dict | None:
        want = _clean(name).lower()
        for key, rig in self._read().items():
            if key.lower() == want:
                out = tidy(rig)
                out["name"] = key
                return out
        return None

    def save(self, name: str, rig: dict) -> dict:
        name = _clean(name)
        if not name:
            raise ValueError("a rig needs a name")
        rigs = self._read()
        existing = next((key for key in rigs if key.lower() == name.lower()), None)
        kept = tidy(rig)
        if not kept:
            raise ValueError("nothing to save yet: there is no capture or cabinet loaded")
        rigs[existing or name] = kept
        self._write(rigs)
        return {"name": existing or name, "replaced": existing is not None, **kept}

    def forget(self, name: str) -> bool:
        rigs = self._read()
        key = next((key for key in rigs if key.lower() == _clean(name).lower()), None)
        if key is None:
            return False
        del rigs[key]
        self._write(rigs)
        return True


def summarise(rig: dict) -> str:
    """One line a player reads instead of eight numbers."""
    bits = []
    if rig.get("capture"):
        bits.append(str(rig["capture"]))
    if rig.get("cab"):
        bits.append(f"into {rig['cab']}")
    knobs = [f"{word} {rig[word]:+g}" for word in ("bass", "mid", "treble") if word in rig]
    if knobs:
        bits.append(", ".join(knobs))
    if "gate_db" in rig:
        bits.append("gate off" if limits.gate_off(rig["gate_db"]) else f"gate {rig['gate_db']:+g}")
    return ", ".join(bits) if bits else "nothing loaded"


def resolve(kind: str, name: str, rows: list[dict]) -> str | None:
    """A file path for a rig's name, from what the engine's library knows.

    A rig travels as a name, so loading one has to ask this machine where that name lives. No
    match is not an error: the rest of the rig still loads, and the caller says what was kept.
    """
    want = _clean(name).lower()
    if not want:
        return None
    for row in rows or []:
        got = _clean(row.get("name") or "").lower()
        if got == want or Path(str(row.get("path") or "")).stem.lower() == want:
            return str(row.get("path") or "") or None
    return None
