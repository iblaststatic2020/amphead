"""A canned rig, so the face can be seen working on a machine with no REAPER and no guitar.

The plugin is only impressive when the meter moves. That is also the worst possible thing to hand
a stranger: they clone it, open the pane, and get "REAPER NOT RUNNING" because they do not own a
DAW, an interface or an amp sim. So the pane can swap its source for a demo rig: same payloads,
same judge, same face, no hardware.

Two rules keep it honest:
  1. Demo is never the default and never turns itself on. The pane shows a badge while it is on,
     and one click turns it off.
  2. The demo answers through `verdict.judge` like everything else, so what a visitor sees is the
     real rules being applied to a made-up rig. It cannot drift into a mock-up.
"""

from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path
from typing import Any

from amphead import verdict

# The rig the demo pretends to be. The numbers are the real rig's own numbers, so a screenshot of
# the demo is a fair screenshot of the plugin.
PROJECT = "amphead.rpp (demo rig)"
# Keyed like a real launch.state() device block: verdict.py reads latency_out_ms, so a demo that
# spelled it out_ms would report 11.61 ms of latency as 0.0 ms in the very sentence a player reads.
DEVICE = {"buffer": "512", "rate": "44100", "latency_out_ms": 11.61, "closed": False,
          "inputs": 2, "reopened": False}
# The demo interface's sockets, named the way a front panel names them. The demo has two because
# the rig it is a picture of has two.
SOCKETS = ["IN 1", "IN 2"]
LIVE_MASTER_DB = -20.0
PARKED_MASTER_DB = -60.0
AMP = "VST3i: NAM Universal (Neural Amp Modeler)"

# name, index, the value it starts at (0..1 on the slider)
KNOBS = [
    ("Amplifier Input Gain", 0, 0.62),
    ("Amplifier Bass", 1, 0.48),
    ("Amplifier Mid", 2, 0.60),
    ("Amplifier Treble", 3, 0.55),
    ("Input Gain", 4, 0.37),
    ("Output Gain", 5, 0.50),
    ("Gate Threshold", 6, 0.20),
    ("Sparkle", 7, 0.44),
]

_PRESETS = [("crunch", "a mid-forward crunch"), ("clean", "a bright clean"), ("doom", "a doom stack")]

_START = time.monotonic()
_DEMO: dict[str, Any] = {}


def _path() -> Path:
    """Next to the rig, not in a home of its own.

    This used to answer ``$HERMES_HOME/plugin-data/amphead/demo.json``, a folder nothing else in
    the repo reads, so the pane's switch and the tool's switch could disagree, and on a box where
    HERMES_HOME is unset it wrote into a stale ``~/.hermes``. The demo flag is amphead state, so
    it lives where the rest of amphead's state lives.
    """
    from . import rig as rig_module

    return rig_module.data_dir() / "demo.json"


def enabled() -> bool:
    """On only when asked for: an env var, or the pane's own switch. Never inferred."""
    flag = os.environ.get("AMPHEAD_DEMO", "").strip().lower()
    if flag in ("1", "true", "yes", "on"):
        return True
    try:
        return bool(json.loads(_path().read_text(encoding="utf-8")).get("on"))
    except (OSError, ValueError):
        return False


def set_enabled(on: bool) -> bool:
    try:
        _path().parent.mkdir(parents=True, exist_ok=True)
        _path().write_text(json.dumps({"on": bool(on)}), encoding="utf-8")
    except OSError:
        # A plugin on a read-only install still gets the session it asked for; only the memory of
        # it is lost.
        pass
    _DEMO["on"] = bool(on)
    if on:
        reset()
    return bool(on)


def reset() -> None:
    """Back to the rig as it boots: disarmed, monitoring off, chain loaded, master parked.

    It does not boot armed and it does not boot at a level you can hear. A rig that comes up armed
    is a rig that passes a live guitar into someone's speakers the moment the pane opens, and the
    demo is the first thing a stranger opens. The chain is loaded and the meter moves, so the face
    still shows what it looks like working; you have to press LIVE to arm it.
    """
    _DEMO["knobs"] = {name: value for name, _index, value in KNOBS}
    _DEMO["master_db"] = PARKED_MASTER_DB
    _DEMO["armed"] = False
    _DEMO["monitor"] = False
    _DEMO["mute"] = False
    _DEMO["bypass"] = False
    _DEMO["amp"] = AMP
    _DEMO["preset"] = ""
    # 1 is the first socket, counted the way the front panel counts. The demo used to park this at
    # 1.0 in REAPER's own encoding, which reads as the second socket, the same off-by-one the live
    # rig had.
    _DEMO["input"] = 1


def _rig() -> dict[str, Any]:
    if "knobs" not in _DEMO:
        reset()
    _DEMO.setdefault("input", 1)
    return _DEMO


def _db(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _knobs() -> list[dict]:
    rig = _rig()
    out = []
    for name, index, _start in KNOBS:
        norm = float(rig["knobs"][name])
        out.append({"index": index, "name": name, "section": "amp" if name.lower().startswith("amplifier") else "other",
                    "value": _db(round(norm * 10.0, 2)),
                    "norm": round(norm, 4), "raw": round(norm * 10.0, 4)})
    return out


def _chain() -> list[dict]:
    rig = _rig()
    return [{"name": rig["amp"], "enabled": not rig["bypass"]}]


def _level() -> float:
    """A plucked string, not a sine: an attack every couple of seconds, decaying to a floor."""
    phase = (time.monotonic() - _START) % 2.6
    envelope = math.exp(-2.6 * phase) if phase < 1.4 else 0.0
    db = -34.0 + 21.0 * envelope
    wobble = 0.82 + 0.18 * math.sin((time.monotonic() - _START) * 11.0)
    return 10.0 ** (db / 20.0) * wobble


def meter() -> dict[str, Any]:
    level = _level()
    return {"l": round(level, 6), "r": round(level * 0.94, 6), "clip": False, "live": True}


def _judged() -> tuple[dict, dict, list[dict]]:
    rig = _rig()
    live = {"bridge": "live", "project": PROJECT, "path": "", "tracks": 1, "instances": 1,
            "rig_tab": True, "device": dict(DEVICE)}
    track = {"armed": rig["armed"], "monitor": bool(rig["monitor"]), "mute": rig["mute"],
             "recinput": float(rig["input"] - 1)}
    chain = _chain()
    sample = meter()
    judged = verdict.judge(live, track, {**sample, "master_db": rig["master_db"]}, chain)
    return judged, live, chain


def state() -> dict[str, Any]:
    rig = _rig()
    judged, live, chain = _judged()
    return {
        "verdict": judged["verdict"],
        "reason": judged["reason"],
        "project": PROJECT,
        "device": {"buffer": DEVICE["buffer"], "rate": DEVICE["rate"],
                   "out_ms": DEVICE["latency_out_ms"], "closed": False},
        "master_db": rig["master_db"],
        "ui": "hidden",
        "armed": rig["armed"],
        "monitor": bool(rig["monitor"]),
        "problems": judged["problems"],
        "chain": chain,
        "plugin": {"name": _rig()["amp"], "enabled": not _rig()["bypass"]},
        "knobs": _knobs(),
        "input": _input(),
        "presets": [{"key": key, "title": about} for key, about in _PRESETS],
        "demo": True,
    }


def _input() -> dict:
    """The canned rig's socket, in the same shape the live rig answers with."""
    rig = _rig()
    number = int(rig["input"])
    count = len(SOCKETS)
    return {"value": f"input {number}", "name": SOCKETS[number - 1], "number": number,
            "count": count, "stereo": False,
            "channels": [{"number": i + 1, "name": name} for i, name in enumerate(SOCKETS)],
            "norm": round((number - 1) / (count - 1), 4) if count > 1 else 0.0, "track": 1}


def input_switch(body: dict) -> dict[str, Any]:
    """Turn the demo's socket, so the knob answers on a machine with no interface at all."""
    rig = _rig()
    try:
        number = int(body.get("input"))
    except (TypeError, ValueError):
        return {"output": "NO INPUT: say which socket on the interface", "ok": False}
    if not 1 <= number <= len(SOCKETS):
        return {"output": f"NOT SET: this demo interface has {len(SOCKETS)} sockets", "ok": False}
    rig["input"] = number
    mine = _input()
    return {"output": f"track 1 records from {mine['name']} ({mine['value']})", "ok": True, **mine}


def tweak(body: dict) -> dict[str, Any]:
    rig = _rig()
    param = body.get("param")
    if param is None or param == "":
        return {"output": "NO PARAM: say which knob to turn", "ok": False}
    if body.get("value") is None:
        return {"output": "NO VALUE: say what to set it to", "ok": False}
    name = None
    for knob, index, _start in KNOBS:
        if str(param) in (knob, str(index)):
            name = knob
            break
    if name is None:
        # A name match is friendlier than an index when the pane sends words.
        for knob, _index, _start in KNOBS:
            if str(param).lower() in knob.lower():
                name = knob
                break
    if name is None:
        return {"output": f"NOT SET: no knob called {param!r} on the demo rig", "ok": False}
    try:
        value = float(body.get("value"))
    except (TypeError, ValueError):
        return {"output": f"NOT SET: {body.get('value')!r} is not a number", "ok": False}
    norm = value if body.get("mode", "norm") != "raw" else value / 10.0
    clamped = norm < 0.0 or norm > 1.0
    rig["knobs"][name] = min(1.0, max(0.0, norm))
    landed = float(rig["knobs"][name])
    # The drag path reads `norm` and `value` off the answer, so the demo must answer in the same
    # shape a live plugin does or a knob on the demo rig would only move on the next poll.
    return {"output": f"set {name} to {_db(round(landed * 10.0, 2))} (demo rig)", "ok": True,
            "norm": round(landed, 4), "value": _db(round(landed * 10.0, 2)),
            "raw": round(landed * 10.0, 4), "clamped": clamped, "param": name}


def preset(body: dict) -> dict[str, Any]:
    rig = _rig()
    query = str(body.get("query") or "").strip()
    if not query:
        return {"output": "NO QUERY: name a sound to load", "ok": False}
    hit = next((key for key, _about in _PRESETS if key in query.lower()), None)
    if hit is None:
        return {"output": f"NO MATCH: the demo rig carries {', '.join(k for k, _a in _PRESETS)}",
                "ok": False}
    rig["preset"] = hit
    for knob, _index, start in KNOBS:
        rig["knobs"][knob] = start
    return {"output": f"loaded the {hit} sound into the demo rig; move a knob to hear it move",
            "ok": True}


def live_switch(body: dict) -> dict[str, Any]:
    rig = _rig()
    on = bool(body.get("on"))
    rig["armed"] = on
    rig["monitor"] = on
    rig["master_db"] = LIVE_MASTER_DB if on else PARKED_MASTER_DB
    spoken = ("LIVE: armed and monitoring" if on
              else "STANDBY: armed off, monitoring off, master parked")
    return {"output": f"{spoken} (demo rig)", "on": on}


def panic(body: dict) -> dict[str, Any]:
    rig = _rig()
    restore = bool(body.get("restore"))
    rig["bypass"] = not restore
    rig["master_db"] = LIVE_MASTER_DB if restore else PARKED_MASTER_DB
    if restore:
        return {"output": "RESTORED: the chain is back on and the master is up (demo rig)",
                "ok": True, "restore": True}
    return {"output": "PANICKED: the chain is bypassed and the master is parked (demo rig)",
            "ok": True, "restore": False}


def focus(_body: dict) -> dict[str, Any]:
    return {"output": "the demo rig has no REAPER window to bring forward", "ok": False,
            "focused": False}
