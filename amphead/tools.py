"""AmpHead tools: what an agent calls to play through the amp.

Hermes is the amp now. There is no host application to open, no project file and no track to
build: a module in this plugin (`engine2.py`) owns the audio interface and runs the capture, the
tone stack and the cabinet, and these tools are how the agent starts it, tunes it and silences it.

Nothing here touches the engine at import time. The engine is contacted inside handlers only, so
`hermes plugins validate` can import this plugin with nothing running.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from . import port, verdict
from .answers import call_shape, json_answer, ok as _ok
from .answers import db_level as _db_level, fmt as _fmt
from .answers import FAILURE_WORDS  # re-exported: the tests read the list from here
from .limits import gate as _gate_db, master as _master_db, trim as _trim_db

TOOLSET = "amphead"

#: The one rig: the local engine. There is no backend switch any more.
RIG = port.active()

SAFE_MASTER_DB = port.SAFE_MASTER_DB
STANDBY_DB = port.SAFE_MASTER_DB


def _host() -> str:
    """What to call the amp in a sentence a player reads."""
    return str(getattr(RIG, "label", "the engine"))


def _schema(name: str, description: str, properties: dict[str, Any] | None = None,
            required: list[str] | None = None) -> dict[str, Any]:
    params: dict[str, Any] = {"type": "object", "properties": properties or {}}
    if required:
        params["required"] = required
    params["additionalProperties"] = False
    return {"name": name, "description": description, "parameters": params}


PING_SCHEMA = _schema(
    "amphead_ping",
    "Check whether the amp can answer right now: is the engine installed, is it running, is the "
    "audio device open, which device and how much latency. Read-only and cheap: call it before "
    "anything else when a player says the amp is silent.",
)

STATUS_SCHEMA = _schema(
    "amphead_status",
    "What the amp is doing: the verdict word, whether it is open and armed, which capture and "
    "cabinet are loaded, the tone knobs, master and the two gains, and the device it is using.",
)

SETUP_SCHEMA = _schema(
    "amphead_setup",
    "Open the amp: start the engine, load the last chain, open the audio interface and park the "
    "master at -60 dB so nothing can reach the speakers until a person asks for it. Safe to call "
    "when the engine is already running.",
)

TEARDOWN_SCHEMA = _schema(
    "amphead_teardown",
    "Close the amp: stop the engine and give the audio interface back to the system. The chain is "
    "remembered, so setup brings the same capture and cabinet back.",
)

TONE_SCHEMA = _schema(
    "amphead_tone",
    "Load a capture and a cabinet by describing the sound, or by naming them directly. Searches "
    "the configured capture and cabinet folders, reports what it chose, and loads it into the "
    "running engine. Knobs are left alone unless tone values are given.",
    {"query": {"type": "string",
               "description": "What you want, in words: \"bugera lead\", \"aurora 212\", \"vintage 30\"."},
     "capture": {"type": "string", "description": "Exact capture name or path, if you know it."},
     "cab": {"type": "string", "description": "Exact cabinet name or path, if you know it."},
     "bass": {"type": "number", "description": "Bass knob, 0-10."},
     "mid": {"type": "number", "description": "Mid knob, 0-10."},
     "treble": {"type": "number", "description": "Treble knob, 0-10."},
     "apply": {"type": "boolean",
               "description": "Load it (default true). false only reports what would be chosen."}},
)

LIVE_SCHEMA = _schema(
    "amphead_live",
    "Arm or disarm the amp. Arming starts monitoring and leaves the master exactly where it already "
    "is, so a rig you parked stays parked and a rig you left at a playing level does not jump; pass "
    "master_db to set it in the same call. At the floor it makes no sound. Refuses to arm above the "
    "floor without confirm=true; on=false disarms and parks the master.",
    {"on": {"type": "boolean", "description": "true arms, false disarms (default true)."},
     "confirm": {"type": "boolean",
                 "description": "Required to arm above the floor. This is the one call that can make sound."},
     "master_db": {"type": "number",
                   "description": "Level to arm at. Omitted: keep the rig's current master.",
                   "minimum": -60, "maximum": 0}},
)

PANIC_SCHEMA = _schema(
    "amphead_panic",
    "Silence the amp in one call: park the master at -60 dB, disarm, bypass every stage, then read "
    "all of it back and report what is actually true. The meter keeps working, so the input stays "
    "visible while nothing reaches the speakers. restore=true puts the stages back, still silent.",
    {"restore": {"type": "boolean",
                 "description": "Undo the panic: un-bypass the stages and lift the master "
                                "(default false, which silences the amp)."},
     "master_db": {"type": "number",
                   "description": "Master level used when restoring (default -60, silent).",
                   "minimum": -60, "maximum": 0}},
)

READY_SCHEMA = _schema(
    "amphead_ready",
    "The amp as one answer: a verdict word a player can read (READY, ENGINE OFF, DEVICE CLOSED, "
    "STANDBY, PARKED, NO CHAIN, NO SIGNAL, CLIPPING) and the problems behind it. Listens to the meter for "
    "settle_ms before deciding, so a quiet moment is not mistaken for a dead input.",
    {"settle_ms": {"type": "integer",
                   "description": "How long to watch the meter (default 600).", "minimum": 0,
                   "maximum": 5000}},
)

SET_SCHEMA = _schema(
    "amphead_set",
    "Turn one control and answer with the value that reads back. Controls: master, gate, in, out, bass, "
    "mid, treble, input (which socket of the interface the guitar is on, counted from 1).",
    {"param": {"type": "string",
               "description": "master | gate | in | out | bass | mid | treble | input"},
     "value": {"type": "number", "description": "The value to write."},
     "unit": {"type": "string", "description": "dB for the levels, 0-10 for the tone knobs."},
     "mode": {"type": "string", "description": "auto (default), db or 0-10."}},
    required=["param", "value"],
)

SETTINGS_SCHEMA = _schema(
    "amphead_settings",
    "The machine-specific choices: where captures and cabinets live, which interpreter hosts the "
    "engine, and the audio defaults (device pair, input channel, sample rate, block size, latency "
    "ask). action=show reports the file and every value with where it came from; action=find walks "
    "the usual folders for capture and cabinet libraries, reading file names only, and only when the "
    "user asks for it, never on your own initiative and never as a way of answering a question they "
    "did not ask; action=set writes values.",
    {"action": {"type": "string", "enum": ["show", "find", "set"],
                "description": "show (default), find or set."},
     "capture_dirs": {"type": "array", "items": {"type": "string"},
                      "description": "Folders to scan for .nam captures (set)."},
     "cab_dirs": {"type": "array", "items": {"type": "string"},
                  "description": "Folders to scan for cabinet .wav files (set)."},
     "engine_python": {"type": "string",
                       "description": "Interpreter with numpy and sounddevice (set)."},
     "audio": {"type": "object",
               "description": "Audio overrides (set): input_device, output_device, input_channel "
                              "(counted from 1), samplerate, blocksize, latency_ms.",
               "additionalProperties": True}},
)


def _chain() -> list[dict]:
    """The stages the engine says are in the path, named the way the face names them."""
    try:
        return [{"name": str(fx.get("name")), "enabled": bool(fx.get("enabled", True))}
                for fx in RIG.fx_list(track=1)]
    except port.RigError:
        return []


def handle_ping(args: dict[str, Any], **_kw) -> str:
    if not RIG.running:
        return (f"{_host()} NOT RUNNING: call amphead_setup to open it | "
                f"settings={_settings_path()}")
    try:
        info = RIG.ping()
        state = RIG.state()
    except port.NoRig:
        return f"{_host()} NOT RUNNING: call amphead_setup to open it"
    except port.RigError as exc:
        return f"ENGINE SILENT: {exc}"
    device = state.get("device") or {}
    return _fmt([
        ("state", "OPEN" if state.get("open") else "CLOSED"),
        ("armed", bool(state.get("armed"))),
        ("device", device.get("name") or "engine chosen"),
        ("rate", device.get("rate")),
        ("buffer", device.get("buffer")),
        ("round_trip_ms", round(float(state.get("latency_ms") or 0.0), 1)),
        ("proto", info.get("proto")),
    ])


def handle_status(args: dict[str, Any], **_kw) -> str:
    try:
        state = RIG.state()
    except port.NoRig:
        return f"{_host()} NOT RUNNING | state=ENGINE OFF | next=press SET UP | call amphead_setup"
    if not state.get("open"):
        return f"{_host()} NOT RUNNING | state=ENGINE OFF | next=press SET UP | call amphead_setup"
    device = state.get("device") or {}
    reading: dict = {"l": 0.0, "r": 0.0, "clip": False}
    chain: list[dict] | None = None
    if state.get("open"):
        try:
            sample = RIG.meter(track=1)
            reading = {"l": float(sample.get("l") or 0.0),
                       "r": float(sample.get("r") or 0.0),
                       "clip": bool(sample.get("clip"))}
        except port.RigError:
            pass
        try:
            chain = _chain()
        except port.RigError:
            chain = None
    judged = verdict.judge(state, reading, chain)
    fields: list[tuple[str, Any]] = [
        ("state", judged["state"]),
        ("next", judged["next"]),
        ("health", judged["health"]),
        ("activity", judged["activity"]),
        ("open", True),
        ("armed", bool(state.get("armed"))),
        ("capture", state.get("capture") or "none"),
        ("cab", state.get("cab") or "none"),
    ]
    if judged["notice"]:
        fields.insert(1, ("notice", judged["notice"]))
    tone = state.get("tone") or {}
    fields += [(k, round(float(tone.get(k) or 0.0), 2)) for k in ("bass", "mid", "treble")]
    fields += [
        ("master_db", round(float(state.get("master_db") or 0.0), 2)),
        ("in_db", round(float(state.get("input_gain_db") or 0.0), 2)),
        ("out_db", round(float(state.get("output_gain_db") or 0.0), 2)),
        ("rate", device.get("rate")),
        ("buffer", device.get("buffer")),
        ("round_trip_ms", round(float(state.get("latency_ms") or 0.0), 1)),
        ("mode", device.get("mode") or state.get("mode")),
        ("input_channel", state.get("input_channel")),
    ]
    return _fmt(fields)


def handle_setup(args: dict[str, Any], **_kw) -> str:
    """Open the engine, load the chain, park the master. Boot is silent by construction."""
    try:
        RIG.ensure()
        state = RIG.state()
    except port.NoRig:
        return (f"NO ENGINE INSTALLED: {_install_reason()} | "
                f"the engine ships with this plugin")
    except port.RigError as exc:
        return f"SETUP FAILED: {exc}"

    parked = SAFE_MASTER_DB
    try:
        parked = float(RIG.set_master(db=SAFE_MASTER_DB).get("db"))
        # Disarm last: a rig that boots armed must not reach the speakers before the master does.
        RIG.disarm(track=1)
    except port.RigError as exc:
        return f"SETUP PARTLY DONE: engine open, but the master could not be parked ({exc})"

    now = RIG.state()
    device = now.get("device") or {}
    # SET UP opens the engine but does not take the interface: GO LIVE does. Reporting a 0.0 ms
    # round trip here would be a number for a stream nobody opened.
    opened = not device.get("closed")
    return _fmt([
        ("state", "STANDBY" if opened else "STANDBY (device not open yet)"),
        ("engine", "open"),
        ("device", device.get("name") or ("not opened yet, GO LIVE takes it" if not opened
                                          else "engine chosen")),
        ("rate", device.get("rate") if opened else None),
        ("buffer", device.get("buffer") if opened else None),
        ("round_trip_ms", round(float(now.get("latency_ms") or 0.0), 1) if opened else None),
        ("master_db", parked),
        ("armed", bool(now.get("armed"))),
        ("capture", now.get("capture") or "none"),
        ("cab", now.get("cab") or "none"),
        ("next", "call amphead_live with confirm=true to take the interface; "
                     "the master stays where SET UP parked it"),
        ("was_open", bool(state.get("open"))),
    ])


def handle_teardown(args: dict[str, Any], **_kw) -> str:
    try:
        if not RIG.running:
            return "ALREADY CLOSED: the switch reads SET UP"
        gone = RIG.stop()
    except port.RigError as exc:
        return f"NOT CLOSED: {exc}"
    return _fmt([("engine", "closed" if gone else "still running"),
                 ("device", "released" if gone else "still held"),
                 ("chain", "remembered for next setup")])


def _pick(rows: list[dict], words: list[str]) -> dict | None:
    """Best row by how many words it answers, then by how short its name is."""
    best, best_score = None, 0
    for row in rows:
        name = str(row.get("name") or row.get("path") or "").lower()
        score = sum(1 for w in words if w in name)
        if score > best_score or (score == best_score and score and best is not None
                                  and len(name) < len(str(best.get("name") or ""))):
            best, best_score = row, score
    return best


def handle_tone(args: dict[str, Any], **_kw) -> str:
    from . import library

    apply = args.get("apply")
    apply = True if apply is None else bool(apply)
    query = str(args.get("query") or "").strip()
    words = [w for w in "".join(c if c.isalnum() else " " for c in query.lower()).split() if w]
    state = RIG.state() if RIG.running else {}
    menus = library.listing(state.get("capture_path"), state.get("cab_path"))
    captures = menus.get("captures") or []
    cabs = menus.get("cabs") or []

    chosen_capture = None
    if args.get("capture"):
        chosen_capture = _pick(captures, str(args["capture"]).lower().split())
        chosen_capture = chosen_capture or {"name": f"{args['capture']} (your words, not in the "
                                            f"library)", "path": str(args["capture"])}
    elif words:
        chosen_capture = _pick(captures, words)

    chosen_cab = None
    if args.get("cab"):
        chosen_cab = _pick(cabs, str(args["cab"]).lower().split())
        chosen_cab = chosen_cab or {"name": f"{args['cab']} (your words, not in the library)",
                                    "path": str(args["cab"])}
    elif words:
        chosen_cab = _pick(cabs, words)

    if chosen_capture is None and chosen_cab is None:
        hint = ("; " + ", ".join(str(c.get("name")) for c in captures[:3])) if captures else ""
        return (f"NO MATCH for {query!r}: no capture or cabinet answers those words"
                f"{hint} | available captures={len(captures)} cabs={len(cabs)}")

    lines = [_fmt([("capture", (chosen_capture or {}).get("name") or "unchanged"),
                   ("cab", (chosen_cab or {}).get("name") or "unchanged"),
                   ("apply", apply)])]
    if not apply:
        return "\n".join(lines)
    if not RIG.running:
        return "\n".join(lines + ["not loaded: the engine is not running, call amphead_setup first"])

    try:
        if chosen_capture:
            RIG.open_capture(chosen_capture["path"])
        if chosen_cab:
            RIG.open_cab(chosen_cab["path"])
        knobs: dict[str, float] = {}
        for name in ("bass", "mid", "treble"):
            if args.get(name) is not None:
                knobs[name] = float(args[name])
                RIG.set_param(track=1, fx=1, param=name, value=float(args[name]), mode="auto")
    except port.RigError as exc:
        return "\n".join(lines + [f"not loaded: {exc}"])

    now = RIG.state()
    lines.append(_fmt([("loaded_capture", now.get("capture")),
                       ("loaded_cab", now.get("cab")),
                       ("rate", (now.get("device") or {}).get("rate"))]))
    if knobs:
        lines.append("knobs: " + ", ".join(f"{k}={v:g}" for k, v in knobs.items()))
    return "\n".join(lines)


def handle_live(args: dict[str, Any], **_kw) -> str:
    on = args.get("on")
    on = True if on is None else bool(on)
    if not RIG.running:
        return "NOT LIVE: the engine is not running, call amphead_setup first"

    if not on:
        try:
            RIG.disarm(track=1)
            level = RIG.set_master(db=STANDBY_DB)
        except port.RigError as exc:
            return f"NOT LIVE: {exc}"
        return _fmt([("live", False), ("armed", False), ("master_db", level.get("db"))])

    # What the amp already has, not a fixed level: arming someone's rig louder than they left
    # it is the one mistake this tool must not make. A parked master arms parked, and the
    # player raises the knob.
    now_master = RIG.state().get("master_db")
    db = float(args["master_db"]) if args.get("master_db") is not None else float(
        STANDBY_DB if now_master is None else now_master)
    db = _master_db(db)
    if not args.get("confirm") and db > STANDBY_DB + 0.5:
        return (f"REFUSED: arming at {db:.0f} dB makes sound. Call again with confirm=true, or "
                f"leave the master at the floor and raise it yourself.")
    try:
        RIG.set_master(db=db)
        armed = RIG.arm(track=1, monitor=True, allow_loud=True)
        now = RIG.state()
    except port.RigError as exc:
        return f"NOT LIVE: {exc}"
    return _fmt([("live", True), ("armed", bool(armed.get("armed"))),
                 ("monitor", bool(armed.get("monitor"))),
                 ("master_db", now.get("master_db")),
                 ("round_trip_ms", now.get("round_trip_ms")
                  or round(float(now.get("latency_ms") or 0.0), 1)),
                 ("capture", now.get("capture") or "none"),
                 ("cab", now.get("cab") or "none")])


def handle_panic(args: dict[str, Any], **_kw) -> str:
    """The dead man's switch: silence first, then prove it with read-backs, never assume it."""
    restore = bool(args.get("restore"))
    if not RIG.running:
        return f"NOT PANICKED: {_host()} is not running, so nothing can reach the speakers anyway"
    try:
        if restore:
            db = max(STANDBY_DB, min(0.0, float(args.get("master_db", STANDBY_DB))))
            RIG.set_master(db=db)
        else:
            # Master first: it is the one control that cannot fail because a stage refused.
            RIG.set_master(db=STANDBY_DB)

        chain = _chain()
        refused: list[str] = []
        for fx in RIG.fx_list(track=1):
            try:
                RIG.fx_bypass(track=1, fx=int(fx["i"]), on=not restore)
            except port.RigError as exc:
                refused.append(f"{fx.get('name')} ({exc})")

        if not restore:
            RIG.disarm(track=1)
        level = RIG.master()
        after = [{"name": str(fx.get("name")), "enabled": bool(fx.get("enabled", True))}
                 for fx in RIG.fx_list(track=1)]
    except port.RigError as exc:
        return f"PANIC INCOMPLETE: {exc}. Park the master by hand before you keep playing."

    live_fx = [f["name"] for f in after if f["enabled"]]
    bypassed = [f["name"] for f in after if not f["enabled"]]
    if restore:
        return _fmt([("panicked", False), ("master_db", level.get("db")),
                     ("stages_on", len(live_fx)), ("still_bypassed", len(bypassed)),
                     ("armed", bool(RIG.state().get("armed"))),
                     ("next", "call amphead_live if you want to play")])
    problems: list[str] = []
    heard = level.get("db")
    if heard is None:
        problems.append("the master level could not be read back")
    elif float(heard) > STANDBY_DB + 0.5:
        problems.append(f"master is at {heard} dB")
    if RIG.state().get("armed"):
        problems.append("the engine is still armed")
    if live_fx:
        problems.append("still in the path: " + ", ".join(live_fx))
    for item in refused:
        problems.append(f"refused the bypass: {item}")
    return _fmt([("panicked", not problems), ("master_db", level.get("db")),
                 ("bypassed", len(bypassed)),
                 ("bypassed_names", ", ".join(bypassed) or "none"),
                 ("meter", "still live: the input stays visible with the speakers silent"),
                 ("problems", "; ".join(problems) or "none")])


def handle_ready(args: dict[str, Any], **_kw) -> str:
    """The amp as a single truth: a word a player can read and the problems behind it."""
    settle = int(args.get("settle_ms", 600))
    try:
        state = RIG.state()
    except port.NoRig:
        return f"verdict={verdict.ENGINE_OFF} | reason=the engine is not running | problems=engine off"

    reading: dict = {"l": 0.0, "r": 0.0, "clip": False}
    chain: list[dict] = []
    if state.get("open"):
        chain = _chain()
        deadline = time.monotonic() + max(0, settle) / 1000.0
        while True:
            try:
                sample = RIG.meter(track=1)
            except port.RigError:
                break
            reading["l"] = max(float(reading["l"]), float(sample.get("l") or 0.0))
            reading["r"] = max(float(reading["r"]), float(sample.get("r") or 0.0))
            reading["clip"] = bool(reading["clip"] or sample.get("clip"))
            try:
                reading["master_db"] = RIG.master().get("db")
            except port.RigError:
                pass
            loud = max(reading["l"], reading["r"]) > verdict.FLOOR
            if loud or reading["clip"] or time.monotonic() >= deadline:
                break
            time.sleep(0.05)

    out = verdict.judge(state, reading, chain)
    fields: list[tuple[str, Any]] = [
        ("verdict", out["verdict"]), ("state", out["state"]),
        ("reason", out["reason"]), ("next", out["next"]),
        ("activity", out["activity"]), ("health", out["health"]),
        ("problems", "; ".join(out["problems"]) or "none"),
    ]
    if out["notice"]:
        fields.append(("notice", out["notice"]))
    if state.get("open"):
        fields += [("armed", bool(state.get("armed"))),
                   ("chain", len(chain)),
                   ("live_stages", len([f for f in chain if f["enabled"]])),
                   ("peak_db", _db_level(max(reading["l"], reading["r"]))),
                   ("out_ms", round(float(state.get("latency_ms") or 0.0) / 2.0, 1))]
    return _fmt(fields)


KNOBS = ("bass", "mid", "treble")


def handle_set(args: dict[str, Any], **_kw) -> str:
    """Turn one control, and answer with the value the engine actually reports."""
    param = str(args.get("param") or "").strip().lower()
    if args.get("value") is None:
        return "NOT SET: say param and value"
    try:
        value = float(args["value"])
    except (TypeError, ValueError):
        return f"NOT SET: {args['value']!r} is not a number"
    if not RIG.running:
        return "NOT SET: the engine is not running, call amphead_setup first"

    try:
        if param in ("master", "level", "volume"):
            wrote = RIG.set_master(db=_master_db(value))
            return _fmt([("set", wrote.get("db")), ("param", "master"), ("unit", "dB")])
        if param in ("gate", "noise gate", "noisegate"):
            wrote = RIG.set_gate(db=_gate_db(value))
            return _fmt([("set", wrote.get("db")), ("param", "gate"), ("unit", "dB"),
                         ("off_below", -79.5)])
        if param in ("in", "input_gain", "input gain"):
            wrote = RIG.set_input_gain(db=_trim_db(value))
            return _fmt([("set", wrote.get("db")), ("param", "in"), ("unit", "dB")])
        if param in ("out", "output_gain", "output gain"):
            wrote = RIG.set_output_gain(db=_trim_db(value))
            return _fmt([("set", wrote.get("db")), ("param", "out"), ("unit", "dB")])
        if param in ("input", "channel"):
            wrote = RIG.set_input(track=1, spec=int(value))
            return _fmt([("set", wrote.get("recinput")), ("param", "input"),
                         ("counted_from", 1)])
        if param in KNOBS:
            wrote = RIG.set_param(track=1, fx=1, param=param, value=value,
                                  mode=str(args.get("mode") or "auto"), unit=args.get("unit"))
            return _fmt([("set", wrote.get("landed")), ("param", wrote.get("param_name")),
                         ("reads_back", wrote.get("fmt"))])
    except port.RigError as exc:
        return f"NOT SET: {exc}"
    except (TypeError, ValueError, LookupError) as exc:
        return f"NOT SET: {exc}"
    return (f"NOT SET: {param!r} is not a control on this amp | "
            "controls=master, gate, in, out, bass, mid, treble, input")


def _settings_path() -> str:
    from . import settings
    return str(settings.path())


def _install_reason() -> str:
    """Why the engine cannot start, and the one command that fixes it."""
    from . import local_port
    python = local_port.engine_python(None)
    return (f"the interpreter Hermes runs this plugin in ({python}) cannot import numpy "
            f"and sounddevice | fix: hermes plugins install amphead --enable --yes-deps "
            f"(Hermes installs the two packages this plugin declares)")


def handle_settings(args: dict[str, Any], **_kw) -> str:
    """The machine-specific choices: folder roots, the device pair, the channel, the cushion."""
    from . import library, settings

    action = str(args.get("action") or "show").strip().lower()
    if action == "find":
        found = library.find()
        if not found["candidates"]:
            return (f"NO LIBRARY FOUND: nothing with captures or cabinets under "
                    f"{', '.join(Path(p).name for p in found['scanned']) or 'the usual folders'}"
                    f" | settings={found['settings']}")
        lines = [f"FOUND {len(found['candidates'])} folders that hold captures or cabinets"]
        for row in found["candidates"][:12]:
            lines.append(f"{row['kind']}={row['path']} | captures={row['captures']} | cabs={row['cabs']}")
        lines.append(f"next: amphead_settings action=set capture_dirs=[...] cab_dirs=[...] | "
                     f"settings={found['settings']}")
        return "\n".join(lines)

    if action == "set":
        patch: dict[str, Any] = {}
        for key in ("capture_dirs", "cab_dirs"):
            if key in args:
                value = args[key]
                if isinstance(value, str):
                    value = [v for v in value.split(os.pathsep) if v]
                patch[key] = [str(v) for v in (value or []) if str(v).strip()]
        if args.get("engine_python") is not None:
            patch["engine_python"] = str(args["engine_python"])
        if isinstance(args.get("audio"), dict):
            patch["audio"] = args["audio"]
        if not patch:
            return "NOTHING TO SET: say capture_dirs, cab_dirs, engine_python or audio"
        saved = settings.save(patch)
        changed = ", ".join(sorted(patch))
        return (f"SAVED {changed} | settings={settings.path()} | "
                f"captures={len(saved.get('capture_dirs') or [])} folders | "
                f"cabs={len(saved.get('cab_dirs') or [])} folders")

    said = settings.describe()
    audio = settings.audio()
    lines = [f"SETTINGS at {said['path']} ({'written' if said['exists'] else 'not written yet'})"]
    for row in said["settings"]:
        if row["key"] in ("capture_dirs", "cab_dirs"):
            for folder in row["value"] or []:
                state = "exists" if Path(folder).is_dir() else "MISSING"
                lines.append(f"{row['key']}={folder} | {state} | from={row['from']}")
            if not row["value"]:
                lines.append(f"{row['key']}=none set | from={row['from']}")
        elif row["key"] == "engine_python":
            lines.append(f"engine_python={row['value'] or 'bundled venv'} | from={row['from']}")
        else:
            lines.append("audio=" + " | ".join(f"{k}={v}" for k, v in audio.items()))
    return "\n".join(lines)


RIG_SCHEMA = _schema(
    "amphead_rig",
    "Saved sounds. action=list shows the named rigs on this machine; action=save keeps the "
    "sound loaded right now under a name (capture, cabinet, tone, both trims, gate); "
    "action=load puts a saved one back; action=forget deletes one; action=paste loads a rig "
    "line someone sent you (it starts with amphead:).",
    {"action": {"type": "string", "enum": ["list", "save", "load", "forget", "paste"],
                "description": "list (default), save, load, forget or paste."},
     "name": {"type": "string", "description": "Rig name (save, load, forget)."},
     "text": {"type": "string",
              "description": "A rig line to load (paste), as written by list or save."}},
    required=["action"],
)

AIM_SCHEMA = _schema(
    "amphead_aim",
    "Aim the amp at a sound. Research the rig first (amp family, cabinet, speaker, mic), then pass "
    "those words: this ranks the captures and cabinets already on the machine, reports how close "
    "the library got, and can load the best pair. action=measure compares a reference recording "
    "with your own render in octave bands and reports the distance in dB plus the curve that would "
    "close it. It never claims a tone is an artist's: confidence is exact, family or nearest.",
    {"action": {"type": "string", "enum": ["find", "apply", "measure"],
                "description": "find (default) ranks, apply loads the best pair, measure scores two wavs."},
     "artist": {"type": "string",
                "description": "Label for the answer, e.g. AC/DC. Not used for matching."},
     "amp": {"type": "string",
             "description": "Researched amp family, e.g. plexi, jcm800, 5150, ac30, rectifier."},
     "cab": {"type": "string",
             "description": "Researched speaker or cabinet, e.g. greenback, vintage30, creamback."},
     "mic": {"type": "string", "description": "Optional mic or position, e.g. close, sm57."},
     "reference": {"type": "string", "description": "The recording to measure against (measure)."},
     "take": {"type": "string", "description": "Your own render of the same part (measure)."},
     "input_gain_db": {"type": "number", "description": "Input trim to set while applying."},
     "output_gain_db": {"type": "number", "description": "Output trim to set while applying."},
     "bass": {"type": "number", "description": "Tone knob 0-10 to set while applying."},
     "mid": {"type": "number", "description": "Tone knob 0-10 to set while applying."},
     "treble": {"type": "number", "description": "Tone knob 0-10 to set while applying."}},
)


RECORD_SCHEMA = _schema(
    "amphead_record",
    "Record what the amp is making to a wav, and stop when the riff is done. action=start opens a "
    "take (what you hear is what is written; the file lands in the recordings folder beside the "
    "settings, named for the time); action=stop closes it and reports the path and length; "
    "action=state says whether a take is running. The file is the handoff for anything else: a "
    "DAW, a song model, or a player who just wants the riff.",
    {"action": {"type": "string", "enum": ["start", "stop", "state"],
                "description": "start (default) records, stop saves, state asks."},
     "name": {"type": "string",
              "description": "Optional take name (start), e.g. verse riff take 2."},
     "path": {"type": "string", "description": "Optional exact wav path (start)."}},
    required=["action"],
)


class _NoAnalyser(Exception):
    """The engine interpreter could not run the measurement."""


def _measure(reference: str, take: str) -> str:
    """Run the octave-band comparison in the engine interpreter, which is the one with numpy.

    The plugin process has no numpy by design, and giving it one to save a subprocess would make the
    install heavier than the feature is worth.
    """
    from . import local_port

    try:
        exe = local_port.engine_python(None)
    except local_port.EngineNotInstalled as exc:
        raise _NoAnalyser(str(exc)) from exc

    root = str(Path(__file__).resolve().parent.parent)
    code = (
        "import sys;"
        f"sys.path.insert(0, {root!r});"
        "from amphead import analyse;"
        f"analyse._print_report({reference!r}, {take!r})"
    )
    try:
        done = subprocess.run(
            [str(exe), "-c", code],
            capture_output=True, text=True, timeout=180, cwd=root,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise _NoAnalyser(f"{type(exc).__name__}: {exc}") from exc
    if done.returncode != 0:
        last = (done.stderr or done.stdout or "").strip().splitlines()
        raise _NoAnalyser(last[-1] if last else f"exit {done.returncode}")
    return done.stdout.strip()


def _confidence(caps: list[dict], cabs: list[dict], want: dict) -> str:
    """The weakest link decides: a perfect cab does not rescue a capture that is only a neighbour."""
    from . import target

    asked = [bool(want.get("amp")), bool(want.get("cab"))]
    found = [bool(caps), bool(cabs)]
    exact = [
        bool(caps) and caps[0]["confidence"] == target.CONFIDENCE_EXACT,
        bool(cabs) and cabs[0]["confidence"] == target.CONFIDENCE_EXACT,
    ]
    checks = [want.get("amp") and exact[0], want.get("cab") and exact[1]]
    if all(check for check, asked_here in zip(checks, asked) if asked_here):
        return target.CONFIDENCE_EXACT
    if any(found):
        return target.CONFIDENCE_FAMILY
    return target.CONFIDENCE_NEAREST


def handle_rig(args: dict[str, Any], **_kw) -> str:
    """The saved sounds: list them, keep the one playing, put one back, or take one pasted."""
    from . import rigs

    action = str(args.get("action") or "list").strip().lower()
    cabinet = rigs.Rigs()
    name = str(args.get("name") or "").strip()

    def listing() -> str:
        rows = [cabinet.get(each) for each in cabinet.names()]
        rows = [row for row in rows if row]
        if not rows:
            return "NO RIGS: nothing saved yet; save the sound that is loaded"
        names = ", ".join(str(row["name"]) for row in rows)
        return _fmt([("saved", len(rows)), ("names", names)])

    if action in ("list", "names", ""):
        return listing()

    if action == "save":
        try:
            live = RIG.state()
        except (port.NoRig, port.RigError) as exc:
            return f"NOT SAVED: {exc}"
        current = rigs.from_status(live)
        if not current.get("capture") and not current.get("cab"):
            return "NOT SAVED: nothing is loaded to save"
        try:
            got = cabinet.save(name, current)
        except (ValueError, OSError) as exc:
            return f"NOT SAVED: {exc}"
        word = "REPLACED" if got.pop("replaced", False) else "SAVED"
        return _fmt([(word.lower(), got["name"]), ("sound", rigs.summarise(got)),
                     ("line", rigs.encode(got))])

    if action == "forget":
        if not cabinet.forget(name):
            return f"NOT FOUND: no rig called {name or 'anything'}"
        return _fmt([("forgot", name), ("left", len(cabinet.names()))])

    said = str(args.get("text") or "").strip()
    try:
        wanted = rigs.tidy(rigs.decode(said)) if said else cabinet.get(name)
    except ValueError as exc:
        return f"NOT LOADED: {exc}"
    if not wanted:
        return f"NOT FOUND: no rig called {name or 'anything'}"
    try:
        got = RIG.apply_rig(wanted)
    except (port.NoRig, port.RigError, OSError, ValueError) as exc:
        return f"NOT LOADED: {exc}"
    kept = list(got.get("kept") or [])
    fields = [("loaded", wanted.get("name") or name or "rig"),
              ("sound", rigs.summarise(wanted))]
    if kept:
        fields.append(("kept", "; ".join(kept)))
    if got.get("pending"):
        fields.append(("note", "the engine is parked; Setup plays it"))
    return _fmt(fields)


def handle_aim(args: dict[str, Any], **_kw) -> str:
    """Aim at a researched rig, or measure how far a take landed from a reference."""
    from . import lastchain, library, target

    action = str(args.get("action") or "find").strip().lower()

    if action == "measure":
        reference = str(args.get("reference") or "").strip()
        take = str(args.get("take") or "").strip()
        if not reference or not take:
            return "NEED TWO FILES: reference=<the recording> take=<your own render>"
        for path in (reference, take):
            if not Path(path).is_file():
                return f"MISSING FILE: {path}"
        try:
            return _measure(reference, take)
        except _NoAnalyser as exc:
            return f"CANNOT MEASURE: {exc}"

    want = {
        key: str(args[key]).strip()
        for key in ("amp", "cab", "mic")
        if str(args.get(key) or "").strip()
    }
    if not want:
        return "NEED WORDS: say amp=plexi cab=greenback, the rig you researched"

    label = str(args.get("artist") or "").strip() or want.get("amp", "")
    try:
        live = RIG.state() if RIG.running else {}
    except port.RigError:
        live = {}
    menus = library.listing(live.get("capture_path") or "", live.get("cab_path") or "")
    caps = target.rank(menus.get("captures") or [], want, kind="captures")
    cabs = target.rank(menus.get("cabs") or [], want, kind="cabs")

    cap_line = target.verdict(caps, want, "captures")
    cab_line = target.verdict(cabs, want, "cabs")
    missing: list[str] = []
    if want.get("amp") and not (caps and caps[0]["confidence"] == target.CONFIDENCE_EXACT):
        missing.append(f"amp:{want['amp']}")
    if want.get("cab") and not (cabs and cabs[0]["confidence"] == target.CONFIDENCE_EXACT):
        missing.append(f"cab:{want['cab']}")

    if not caps and not cabs:
        words = " ".join(str(v) for v in want.values())
        return (f"NO MATCH for {label}: nothing in the library answers {words!r} | "
                f"captures={len(menus.get('captures') or [])} cabs={len(menus.get('cabs') or [])} | "
                f"look for a file with {want.get('amp') or want.get('cab')} in its name")

    confidence = _confidence(caps, cabs, want)
    matched = [word for entry in (caps[:1], cabs[:1]) for word in (entry[0]["matched"] if entry else [])]

    lines = [
        f"AIMED AT {label}",
        _fmt([("amp", want.get("amp") or "-"), ("cab", want.get("cab") or "-"),
              ("confidence", confidence), ("matched", ", ".join(matched) or "none"),
              ("missing", ", ".join(missing) or "none")]),
        f"capture: {cap_line}",
        f"cab: {cab_line}",
    ]

    best_capture = caps[0] if caps else None
    best_cab = cabs[0] if cabs else None

    if action != "apply":
        if best_capture:
            lines.append(f"would_load_capture={best_capture['name']}")
        if best_cab:
            lines.append(f"would_load_cab={best_cab['name']}")
        lastchain.save(aim=f"aimed at {label}: {cap_line}; {cab_line}", aim_confidence=confidence)
        return "\n".join(lines)

    if not RIG.running:
        lastchain.save(aim=f"aimed at {label}: {cap_line}; {cab_line}", aim_confidence=confidence)
        return "\n".join(lines + ["not loaded: the engine is not running, call amphead_setup first"])

    try:
        if best_capture:
            RIG.open_capture(best_capture["path"])
        if best_cab:
            RIG.open_cab(best_cab["path"])
        for name, setter in (("input_gain_db", RIG.set_input_gain),
                             ("output_gain_db", RIG.set_output_gain)):
            if args.get(name) is not None:
                setter(float(args[name]))
        for name in ("bass", "mid", "treble"):
            if args.get(name) is not None:
                RIG.set_param(track=1, fx=1, param=name, value=float(args[name]), mode="auto")
    except port.RigError as exc:
        lastchain.save(aim=f"aimed at {label}: {cap_line}; {cab_line}", aim_confidence=confidence)
        return "\n".join(lines + [f"not loaded: {exc}"])

    now = RIG.state()
    lines.append(_fmt([("loaded_capture", now.get("capture") or "unchanged"),
                       ("loaded_cab", now.get("cab") or "unchanged")]))
    lastchain.save(aim=f"aimed at {label}: {cap_line}; {cab_line}", aim_confidence=confidence)
    return "\n".join(lines)


def run_call(cmd: str, **kwargs) -> dict:
    """Small indirection so tests can stub the wire without a live engine."""
    return RIG.debug_call(cmd, **kwargs)


def handle_record(args: dict[str, Any], **_kw) -> str:
    """One take, start to stop, said as a file the player can go and open."""
    action = str(args.get("action") or "start").strip().lower()
    if action in ("state", "status"):
        try:
            live = RIG.take_state()
        except (port.NoRig, port.RigError) as exc:
            return f"NO ENGINE: {exc}"
        if not live.get("recording"):
            return "NOT RECORDING: nothing is being written"
        return _fmt([("recording", "yes"), ("seconds", live.get("seconds")),
                     ("path", live.get("path"))])
    try:
        if action == "stop":
            said = RIG.stop_take()
            seconds = float(said.get("seconds") or 0.0)
            dropped = int(said.get("dropped") or 0)
            # A writer that died mid-take leaves a file that opens and plays: short, and with a
            # jump where the disk fell behind. Saying SAVED over that is the one thing a player
            # cannot check for themselves, so the error is the first field.
            if said.get("error"):
                return _fmt([("saved", "with a problem"), ("error", said.get("error")),
                             ("path", said.get("path")), ("seconds", f"{seconds:.1f}s"),
                             ("dropped", dropped)])
            if seconds <= 0.0:
                return f"NOTHING RECORDED: the take at {said.get('path')} is empty"
            line = [("saved", f"{seconds:.1f}s"), ("path", said.get("path")),
                    ("peak", f"{said.get('peak_db')} dB"), ("dropped", dropped)]
            if dropped:
                line.append(("dropped means", f"{dropped} block(s) of audio are missing from the file"))
            return _fmt(line)
        said = RIG.start_take(path=args.get("path") or None, name=args.get("name") or None)
        rate = said.get("samplerate")
        return _fmt([("recording", "yes"), ("path", said.get("path")),
                     ("rate", f"{rate} Hz" if rate else "rate not reported by the engine"),
                     ("channels", said.get("channels") or "not reported")])
    except (port.NoRig, port.RigError) as exc:
        return f"NOT RECORDED: {exc}"
    except ValueError as exc:
        return f"NOT RECORDED: {exc}"


TOOLS = (
    ("amphead_ping", PING_SCHEMA, handle_ping, "🎸"),
    ("amphead_status", STATUS_SCHEMA, handle_status, "📊"),
    ("amphead_setup", SETUP_SCHEMA, handle_setup, "🎛️"),
    ("amphead_teardown", TEARDOWN_SCHEMA, handle_teardown, "⏏️"),
    ("amphead_tone", TONE_SCHEMA, handle_tone, "🎚️"),
    ("amphead_live", LIVE_SCHEMA, handle_live, "🔊"),
    ("amphead_panic", PANIC_SCHEMA, handle_panic, "⏹️"),
    ("amphead_ready", READY_SCHEMA, handle_ready, "✅"),
    ("amphead_set", SET_SCHEMA, handle_set, "🎚️"),
    ("amphead_settings", SETTINGS_SCHEMA, handle_settings, "⚙️"),
    ("amphead_aim", AIM_SCHEMA, handle_aim, "🎯"),
    ("amphead_rig", RIG_SCHEMA, handle_rig, "💾"),
    ("amphead_record", RECORD_SCHEMA, handle_record, "⏺️"),
)

# ---- the shape the host sees -------------------------------------------------------------
# Shaped in `answers.py`: a handler decides what happened and says it in one line, and the
# wrapper the host registers turns that line into JSON with named fields. The raw handlers stay
# as they are for the panes and the tests.

def json_tools() -> list[tuple[str, dict, Any, str]]:
    """TOOLS, with each handler wrapped. The raw handlers stay as they are for the panes."""
    return [(name, schema, call_shape(name, handler), emoji) for name, schema, handler, emoji in TOOLS]
