"""The amp face's backend.

Everything the pane shows comes from here, so the pane never has to guess and never has to look
at another application to find out. Hermes is the amp: the engine owns the audio device, and this
module is the HTTP face over the same tools the agent calls, so a click in the pane and a sentence
in chat do the same thing.

These routes are deliberately plain `def`, not `async def`. The engine client is a blocking
request/response over a pipe, and FastAPI runs a sync endpoint in its threadpool: a slow engine
then costs one worker thread instead of stalling the event loop the whole desktop app shares.
"""

from __future__ import annotations

import math
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from fastapi import APIRouter

router = APIRouter()

# The package lives beside this file's parent (dashboard/ sits inside the plugin root).
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from amphead import lastchain, library as amp_library, limits, port, rigs, verdict  # noqa: E402

RIG = port.active()

FLOOR = verdict.FLOOR


@contextmanager
def _speaking_to_tools():
    """Point the tool handlers at this module's rig for the length of one call.

    The write routes go through the agent-facing handlers so a click and a sentence cannot drift
    apart. Those handlers hold one rig chosen at import; handing them the same one this module
    uses keeps the pane and chat on the same engine.
    """
    from amphead import tools

    tools.RIG = RIG
    yield tools


def _device(state: dict) -> dict:
    device = state.get("device") or {}
    return {
        "driver": device.get("mode") or device.get("driver") or "",
        "name": device.get("name") or "",
        "inputs": device.get("inputs"),
        "buffer": device.get("buffer") or "",
        "rate": device.get("rate") or "",
        "out_ms": device.get("latency_out_ms") or 0.0,
        "round_trip_ms": state.get("round_trip_ms") or state.get("latency_ms") or 0.0,
        "closed": bool(device.get("closed")),
    }


def _channels() -> dict:
    """The interface's own sockets, numbered from one the way a front panel numbers them."""
    try:
        got = RIG.inputs()
    except port.RigError:
        return {"count": 0, "channels": []}
    rows = []
    for index, channel in enumerate(got.get("channels") or []):
        number = int(channel.get("i", index)) + 1
        name = str(channel.get("name") or f"IN {number}")
        rows.append({"number": number, "name": name})
    return {"count": int(got.get("count") or len(rows)), "channels": rows}


def _input(live: dict) -> dict:
    """Which socket the amp is listening to, named the way the interface names it."""
    sockets = _channels() if live.get("open") else {"count": 0, "channels": []}
    held = live.get("input_channel")
    rows = sockets["channels"]
    if rows:
        number = int(held or 1)
        chosen = next((r for r in rows if r["number"] == number), rows[0])
        return {"value": number, "name": chosen["name"], "options": rows,
                "count": sockets["count"]}
    return {"value": int(held or 1), "name": f"IN {int(held or 1)}", "options": [], "count": 0}


def _chain() -> list[dict]:
    try:
        return [{"i": int(fx.get("i", 0)), "name": str(fx.get("name")),
                 "enabled": bool(fx.get("enabled", True))}
                for fx in RIG.fx_list(track=1)]
    except port.RigError:
        return []


def _knobs(live: dict) -> list[dict]:
    """The three tone knobs, in the order the face paints them, plus what they read."""
    tone = live.get("tone") or {}
    return [{"name": name.upper(), "key": name, "value": float(tone.get(name) or 0.0),
             "min": 0.0, "max": 10.0}
            for name in ("bass", "mid", "treble")]


def _asked_db(body: dict) -> float | None:
    """The dB a knob asked for, or None if it did not ask for a number at all.

    Three routes need the same two guards (a number, and a finite one). One copy of them,
    because a route that forgot the second guard would post an inf into the engine.
    """
    try:
        asked = float(body.get("db"))
    except (TypeError, ValueError):
        return None
    return asked if math.isfinite(asked) else None


def _presets() -> list[dict]:
    """The captures the pane offers, so a player can change the amp without a file dialog."""
    try:
        rows = amp_library.listing("", "").get("captures") or []
    except OSError:
        return []
    return [{"key": row.get("path"), "title": row.get("name")} for row in rows]


@router.get("/state")
def state() -> dict[str, Any]:
    try:
        live = RIG.state()
    except port.NoRig:
        return {"verdict": verdict.ENGINE_OFF,
                "reason": "the engine is not running: the switch reads SET UP",
                "running": False, "armed": False, "chain": [], "knobs": [],
                "device": {"closed": True}, "meter": {"peak": 0.0}, "presets": [],
                "capture": "", "cab": "", "tone": {}, "tone_name": "",
                "aim": lastchain.load().get("aim") or "",
                "aim_confidence": lastchain.load().get("aim_confidence") or ""}

    peak, clip = 0.0, False
    master = live.get("master_db")
    if live.get("open"):
        try:
            reading = RIG.meter(1)
            peak = float(max(reading.get("l") or 0.0, reading.get("r") or 0.0))
            clip = bool(reading.get("clip"))
        except port.RigError:
            peak, clip = 0.0, False
        try:
            master = RIG.master().get("db", master)
        except port.RigError:
            pass

    reading = {"l": peak, "r": peak, "clip": clip}
    if master is not None:
        reading["master_db"] = master
    chain = _chain() if live.get("open") else []
    judged = verdict.judge(live, reading, chain)

    return {
        "verdict": judged["verdict"],
        "reason": judged["reason"],
        "problems": judged["problems"],
        "running": bool(live.get("open")),
        "armed": bool(live.get("armed")),
        "monitor": bool(live.get("armed")),
        "master_db": master,
        "device": _device(live),
        "input": _input(live),
        "input_channel": live.get("input_channel"),
        "chain": chain,
        "knobs": _knobs(live),
        "plugin": {"name": "AmpHead", "vendor": "Hermes", "host": "engine"},
        "presets": _presets(),
        "engine": live.get("engine") or "engine",
        "capture": live.get("capture") or "",
        "capture_path": live.get("capture_path") or "",
        "cab": live.get("cab") or "",
        "cab_path": live.get("cab_path") or "",
        "tone": live.get("tone") or {},
        "tone_name": amp_library.tone_name(live.get("capture_path") or "",
                                           live.get("cab_path") or ""),
        "input_gain_db": live.get("input_gain_db"),
        "output_gain_db": live.get("output_gain_db"),
        "gate_db": live.get("gate_db"),
        "aim": lastchain.load().get("aim") or "",
        "aim_confidence": lastchain.load().get("aim_confidence") or "",
        "stages": live.get("stages") or {},
        "samplerate": live.get("samplerate"),
        "blocksize": live.get("blocksize"),
        "latency_ms": live.get("latency_ms"),
        "block_ms": live.get("block_ms"),
        "round_trip_ms": live.get("round_trip_ms"),
        "mode": live.get("mode"),
        "meter": {"peak": peak, "clip": clip},
        "demo": False,
    }


@router.get("/meter")
def meter() -> dict[str, Any]:
    try:
        reading = RIG.meter(1)
    except port.RigError:
        return {"l": 0.0, "r": 0.0, "clip": False, "live": False}
    return {"l": float(reading.get("l") or 0.0), "r": float(reading.get("r") or 0.0),
            "clip": bool(reading.get("clip")), "live": True}


def _rejoin_stages(track: int = 1) -> list[str]:
    """Put back any stage PANIC switched out, so arming never hands back a dead chain."""
    back: list[str] = []
    for fx in RIG.fx_list(track=track):
        if not fx.get("enabled", True):
            try:
                RIG.fx_bypass(track=track, fx=int(fx["i"]), on=False)
                back.append(str(fx.get("name")))
            except port.RigError:
                pass
    return back


@router.post("/live")
def live_switch(body: dict) -> dict[str, Any]:
    on = bool(body.get("on"))
    # No master in the request means the amp keeps the one it has. The old default was a
    # fixed -6 dB, so any caller that forgot the field armed the rig louder than the knob.
    db = body.get("master_db")
    with _speaking_to_tools() as tools:
        back = _rejoin_stages() if on and RIG.running else []
        out = tools.handle_live({"on": on, "confirm": True, "master_db": db})
    if back:
        out = f"{out} | re-engaged={', '.join(back)}"
    return {"output": out, "on": on, "ok": not out.startswith("NOT LIVE"), "reengaged": back}


@router.post("/setup")
def setup_rig(body: dict | None = None) -> dict[str, Any]:
    """Open the engine without making a sound: the first press of the one switch."""
    with _speaking_to_tools() as tools:
        out = tools.handle_setup({})
    return {"output": out, "ok": not out.startswith(("NOT", "NO ", "SETUP FAILED"))}


@router.post("/teardown")
def teardown(body: dict | None = None) -> dict[str, Any]:
    """Close the engine, so the face has a way back to SET UP."""
    if not RIG.running:
        return {"output": "the engine is already closed: the switch reads SET UP", "ok": True}
    gone = bool(RIG.stop())
    return {"output": ("engine closed: the switch reads SET UP again" if gone
                       else "the engine did not stop cleanly: press SET UP to try again"),
            "ok": gone}


@router.get("/library")
def library() -> dict[str, Any]:
    """The captures and cabinets this machine has, so the pane can offer a menu."""
    try:
        return RIG.library()
    except port.NoRig as exc:
        return {"captures": [], "cabs": [], "capture": "", "cab": "",
                "why": str(exc) or "the engine is not running"}


@router.post("/capture")
def choose_capture(body: dict) -> dict[str, Any]:
    path = (body or {}).get("path")
    if not path:
        return {"output": "NO CAPTURE: say which file", "ok": False}
    try:
        got = RIG.open_capture(str(path))
    except (port.RigError, OSError, ValueError) as exc:
        return {"output": f"NOT LOADED: {exc}", "ok": False}
    return {"output": f"amp: {got['capture']}", "ok": True, **got}


@router.post("/cab")
def choose_cab(body: dict) -> dict[str, Any]:
    path = (body or {}).get("path")
    if not path:
        return {"output": "NO CABINET: say which file", "ok": False}
    try:
        got = RIG.open_cab(str(path))
    except (port.RigError, OSError, ValueError) as exc:
        return {"output": f"NOT LOADED: {exc}", "ok": False}
    return {"output": f"cabinet: {got['cab']}", "ok": True, **got}


@router.post("/panic")
def panic(body: dict) -> dict[str, Any]:
    restore = bool(body.get("restore"))
    db = body.get("master_db", port.SAFE_MASTER_DB)
    with _speaking_to_tools() as tools:
        out = tools.handle_panic({"restore": restore, "master_db": db})
    return {"output": out, "ok": not out.startswith("NOT PANICKED"), "restore": restore}


@router.post("/master")
def master_level(body: dict) -> dict[str, Any]:
    """The player's own volume ramp, one write at a time."""
    asked = _asked_db(body)
    if asked is None:
        return {"output": "NO LEVEL: say how many dB", "ok": False}
    db = limits.master(asked)
    try:
        RIG.set_master(db=db)
        level = RIG.master().get("db")
    except port.NoRig:
        return {"output": "NOT SET: the engine is not running", "ok": False}
    except port.RigError as exc:
        return {"output": f"NOT SET: {exc}", "ok": False}
    where = "silent" if float(level or 0) <= -59 else f"master at {level} dB"
    return {"output": f"{where}; raise it in steps and listen", "ok": True, "db": level}


def _trim(kind: str, body: dict) -> dict[str, Any]:
    """Move one of the two gain knobs either side of the amp, and say where it landed."""
    asked = _asked_db(body)
    if asked is None:
        return {"output": "NO LEVEL: say how many dB", "ok": False}
    db = limits.trim(asked)
    setter = getattr(RIG, "set_input_gain" if kind == "input" else "set_output_gain", None)
    if setter is None:
        return {"output": f"NOT SET: the engine has no {kind} gain", "ok": False}
    try:
        wrote = setter(db=db)
    except port.NoRig:
        return {"output": "NOT SET: the engine is not running", "ok": False}
    except port.RigError as exc:
        return {"output": f"NOT SET: {exc}", "ok": False}
    return {"output": f"{kind} gain at {wrote.get('db')} dB", "ok": True,
            "db": wrote.get("db"), "kind": kind}


@router.post("/input_gain")
def input_gain(body: dict) -> dict[str, Any]:
    return _trim("input", body)


@router.post("/output_gain")
def output_gain(body: dict) -> dict[str, Any]:
    return _trim("output", body)


@router.post("/gate")
def gate(body: dict) -> dict[str, Any]:
    """Threshold for the gate in front of the amp. The bottom of the range is off."""
    asked = _asked_db(body)
    if asked is None:
        return {"output": "NO LEVEL: say how many dB", "ok": False}
    db = limits.gate(asked)
    try:
        wrote = RIG.set_gate(db=db)
    except port.NoRig:
        return {"output": "NOT SET: the engine is not running", "ok": False}
    except port.RigError as exc:
        return {"output": f"NOT SET: {exc}", "ok": False}
    landed = wrote.get("db")
    word = "off" if limits.gate_off(landed) else f"{float(landed):.0f} dB"
    return {"output": f"gate {word}", "ok": True, "db": landed}


@router.post("/tune")
def tune(body: dict) -> dict[str, Any]:
    """Measure this chain and reopen the amp on the quickest block that holds it."""
    try:
        wrote = RIG.tune()
    except port.NoRig:
        return {"output": "NOT MEASURED: the engine is not running", "ok": False}
    except port.RigError as exc:
        return {"output": f"NOT MEASURED: {exc}", "ok": False}
    return {"output": wrote.get("verdict") or "measured", "ok": True,
            "blocksize": wrote.get("blocksize"), "changed": wrote.get("changed"),
            "round_trip_ms": wrote.get("round_trip_ms")}


@router.post("/knob")
def knob(body: dict) -> dict[str, Any]:
    """One tone knob, by index into the three the engine really has."""
    tone = ("bass", "mid", "treble")
    try:
        index = int(body.get("param"))
    except (TypeError, ValueError):
        return {"output": "NO PARAM: say which knob", "ok": False}
    if not 0 <= index < len(tone):
        return {"output": f"NO PARAM: {index} is not one of the three tone knobs", "ok": False}
    try:
        value = float(body.get("value"))
    except (TypeError, ValueError):
        return {"output": "NO VALUE: say what to set it to", "ok": False}
    if str(body.get("mode") or "auto").lower() == "norm":
        value *= 10.0
    try:
        wrote = RIG.set_param(track=1, fx=1, param=tone[index],
                              value=max(0.0, min(10.0, value)), mode="auto")
    except port.NoRig:
        return {"output": "NOT SET: the engine is not running", "ok": False}
    except (port.RigError, LookupError, ValueError) as exc:
        return {"output": f"NOT SET: {exc}", "ok": False}
    return {"output": f"{wrote.get('param_name')} at {wrote.get('fmt')}", "ok": True,
            "param": tone[index], "value": float(wrote.get("landed") or value)}


@router.post("/tweak")
def tweak(body: dict) -> dict[str, Any]:
    """One knob, written the same way chat writes it."""
    param = body.get("param")
    if param is None or param == "":
        return {"output": "NO PARAM: say which knob to turn", "ok": False}
    with _speaking_to_tools() as tools:
        out = tools.handle_set({"param": param, "value": body.get("value"),
                                "unit": body.get("unit"), "mode": body.get("mode", "auto")})
    return {"output": out, "ok": not out.startswith(("NOT SET", "REFUSED"))}


@router.post("/input")
def input_switch(body: dict) -> dict[str, Any]:
    """Which socket of the interface the guitar is on, counted from one."""
    try:
        number = int(body.get("channel") or body.get("value") or 1)
    except (TypeError, ValueError):
        return {"output": "NO CHANNEL: say which input, counted from 1", "ok": False}
    try:
        RIG.set_input(track=1, spec=number)
        live = RIG.state()
    except port.NoRig:
        return {"output": "NOT SET: the engine is not running", "ok": False}
    except port.RigError as exc:
        return {"output": f"NOT SET: {exc}", "ok": False}
    mine = _input(live)
    return {"output": f"listening on {mine['name']} (input {mine['value']})", "ok": True, **mine}


@router.get("/rigs")
def rigs_list() -> dict[str, Any]:
    """Every saved sound, with the one line it can be shared as."""
    cabinet = rigs.Rigs()
    rows = []
    for name in cabinet.names():
        rig = cabinet.get(name)
        if not rig:
            continue
        rows.append({"name": rig["name"], "summary": rigs.summarise(rig),
                     "line": rigs.encode(rig)})
    return {"rigs": rows, "current": _current_rig()}


@router.post("/rig")
def rig_action(body: dict) -> dict[str, Any]:
    """Save the sound that is loaded, load one by name, paste one, or forget one."""
    body = body or {}
    action = str(body.get("action") or "load").strip().lower()
    cabinet = rigs.Rigs()

    if action in ("list", "names"):
        return rigs_list()

    if action == "forget":
        name = str(body.get("name") or "").strip()
        if not cabinet.forget(name):
            return {"output": f"NOT FOUND: no rig called {name or 'anything'}", "ok": False}
        return {"output": f"FORGOT: {name}", "ok": True, "rigs": rigs_list()["rigs"]}

    if action == "save":
        name = str(body.get("name") or "").strip()
        try:
            current = _current_rig()
            if not current.get("capture") and not current.get("cab"):
                return {"output": "NOT SAVED: nothing is loaded to save", "ok": False}
            got = cabinet.save(name, current)
        except (ValueError, OSError) as exc:
            return {"output": f"NOT SAVED: {exc}", "ok": False}
        verb = "REPLACED" if got.pop("replaced", False) else "SAVED"
        return {"output": f"{verb}: {got['name']} | {rigs.summarise(got)}", "ok": True,
                "rig": got, "line": rigs.encode(got), "rigs": rigs_list()["rigs"]}

    # load, from a name or from a pasted line
    said = str(body.get("text") or "").strip()
    name = str(body.get("name") or "").strip()
    try:
        wanted = rigs.tidy(rigs.decode(said)) if said else cabinet.get(name)
    except ValueError as exc:
        return {"output": f"NOT LOADED: {exc}", "ok": False}
    if not wanted:
        return {"output": f"NOT FOUND: no rig called {name or 'anything'}", "ok": False}

    try:
        got = RIG.apply_rig(wanted)
        live = RIG.state()
    except (port.NoRig, port.RigError, OSError, ValueError) as exc:
        return {"output": f"NOT LOADED: {exc}", "ok": False}

    # Say what arrived and what did not. `apply_rig` reports a capture or cabinet whose name
    # is not in this machine's library, so the sentence never claims more than landed.
    kept = list(got.get("kept") or [])
    tail = "" if not kept else " | kept the old " + "; ".join(kept)
    label = wanted.get("name") or name or "rig"
    return {"output": f"LOADED: {label} | {rigs.summarise(wanted)}{tail}",
            "ok": True, "rig": wanted, "state": live, "pending": bool(got.get("pending")),
            "kept": kept}


def _current_rig() -> dict:
    """The sound in the amp right now, as a rig. Empty if the engine cannot answer."""
    try:
        return rigs.from_status(RIG.state())
    except (port.NoRig, port.RigError):
        return {}


@router.post("/preset")
def preset(body: dict) -> dict[str, Any]:
    query = str(body.get("query") or "").strip()
    if not query:
        return {"output": "NO QUERY: name a sound to load", "ok": False}
    with _speaking_to_tools() as tools:
        out = tools.handle_tone({"query": query, "apply": True})
    first = out.splitlines()[0].upper() if out else ""
    ok = not first.startswith(("NOT APPLIED", "NO MATCH", "NO QUERY", "NOT SET", "REFUSED"))
    return {"output": out, "ok": ok}
