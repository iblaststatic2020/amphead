"""The amp face's backend.

Everything the pane shows comes from here, so the pane never has to guess and never has to
open REAPER to find out. Read routes are cheap and safe; the two write routes go through the
same tool handlers the agent uses, so a click in the pane and a sentence in chat do the same
thing.
"""

from __future__ import annotations

from pathlib import Path
import math
import sys
import threading
from typing import Any

from contextlib import contextmanager
from contextvars import ContextVar

from fastapi import APIRouter, Depends

router = APIRouter()

# These routes are deliberately plain `def`, not `async def`. Every one of them talks to REAPER
# through a blocking bridge call, and FastAPI runs a sync endpoint in its threadpool: a slow or
# absent bridge then costs a worker thread instead of stalling the event loop the whole desktop
# app shares. An `async def` here blocks every other request in the gateway while it waits.

# The package lives beside this file's parent (dashboard/ sits inside the plugin root).
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from amphead import appdecl, demo, port, tones, verdict  # noqa: E402

#: Which rig a request is talking to. Empty means "the one AMPHEAD_ENGINE names", which is how
#: the tools and the original pane behave. The second pane sends ?engine=amphead2, so the two
#: faces sit side by side on two backends without one process needing to choose for both.
_ENGINE: ContextVar[str] = ContextVar("amphead_engine", default="")


_TOOLS_LOCK = threading.Lock()


class _SelectedRig:
    """A rig picked per request, not per process.

    Every helper in this file already says ``RIG.something``, so the proxy keeps that spelling
    and resolves the actual backend at call time. Patching ``RIG.x`` in a test still works: the
    patch lands on the proxy, which wins over ``__getattr__``.
    """

    def __getattr__(self, name: str) -> Any:
        return getattr(port.choose(_ENGINE.get() or None), name)


RIG = _SelectedRig()


async def _engine_from_query(engine: str | None = None):
    """Read ?engine= on any route and hold it for the length of that request."""
    token = _ENGINE.set((engine or "").strip().lower())
    try:
        yield
    finally:
        _ENGINE.reset(token)


router.dependencies.append(Depends(_engine_from_query))


@contextmanager
def _speaking_to_tools():
    """Point the tool handlers at the rig this request named, then put them back.

    Reads here go through ``RIG``, a proxy that resolves per request. The write routes go
    through the agent-facing tool handlers, and those hold one rig chosen at import, so an
    Amp 2 click was answered by the REAPER rig: "REAPER is not running" while the local engine
    sat open in the next pane. Swapping the module's rig for the length of the call is the
    smallest honest fix; the lock keeps two panes from swapping it under each other.
    """
    from amphead import tools

    with _TOOLS_LOCK:
        held, tools.RIG = tools.RIG, port.choose(_ENGINE.get() or None)
        try:
            yield tools
        finally:
            tools.RIG = held

FLOOR = verdict.FLOOR  # -60 dB, the same floor the rig boots and parks at
def _device(state: dict) -> dict:
    device = state.get("device") or {}
    return {
        "driver": device.get("driver") or "",
        "inputs": int(device.get("inputs") or 0),
        "buffer": device.get("buffer") or "",
        "rate": device.get("rate") or "",
        "out_ms": device.get("latency_out_ms") or 0.0,
        "closed": bool(device.get("closed")),
    }


def _channels() -> dict:
    """The interface's own sockets, in the device's own names, so the knob reads the front panel.

    REAPER numbers these from zero internally; a front panel numbers them from one. Nothing leaves
    this file as a zero, because a zero under a knob reads as "no input" to anyone who is not
    holding REAPER's API docs.
    """
    try:
        got = RIG.inputs()
    except port.RigError:
        return {"count": 0, "channels": []}
    rows = []
    for index, channel in enumerate(got.get("channels") or []):
        number = int(channel.get("i", index)) + 1
        rows.append({"number": number, "name": str(channel.get("name") or f"IN {number}")})
    return {"count": int(got.get("count") or len(rows)), "channels": rows}


def _input(track: dict, live: dict) -> dict:
    """Which socket on the interface the track's record input points at.

    A track number and an input number are two different numbers: a track is a lane in the project,
    an input is a socket on the interface. Nothing here derives one from the other, because a rig
    with three tracks and two inputs would then claim a third socket exists.
    """
    sockets = _channels() if live.get("bridge") == "live" else {"count": 0, "channels": []}
    held = track.get("recinput")
    said = RIG.describe_input(held) if held is not None else "unknown"
    number, stereo = None, False
    try:
        raw = int(float(held))
    except (TypeError, ValueError):
        raw = None
    if raw is not None and raw >= 0:
        if raw >= port.INPUT_PAIR_BASE:
            number, stereo = raw - port.INPUT_PAIR_BASE + 1, True
        else:
            number = raw + 1
    name = next((row["name"] for row in sockets["channels"] if row["number"] == number), said)
    count = sockets["count"]
    norm = round((number - 1) / (count - 1), 4) if number and count > 1 else 0.0
    return {"value": said, "name": name, "number": number, "count": count,
            "channels": sockets["channels"], "stereo": stereo, "norm": norm, "track": 1}


def _knobs(track: int) -> list[dict]:
    """Every control the plugin on the track actually has, in the order the plugin draws them.

    Not a shortlist. The pane is meant to be the plugin's own face, so the knob set on screen is
    the knob set in the VST: two amps can be compared control for control, and nothing the plugin
    exposes is hidden behind a keyword list this file made up. `value` is the plugin's own formatted
    reading, which is the only number worth trusting when the question is "is this the tone".

    `index` is what /knob writes, because two parameters can share a name and a slider must not
    guess which twin it belongs to.
    """
    try:
        params = RIG.fx_params(track, 0, keep=4096)
    except port.RigError:
        return []
    picked = []
    for param in params:
        name = str(param.get("name") or "").strip()
        if not name:
            continue
        reading = _pretty(str(param.get("fmt") or ""))
        picked.append({"index": int(param["i"]), "name": name,
                       "section": _section(name),
                       # The host's own reading decides what is a switch: a parameter that says
                       # On or Off has exactly two positions, and the face draws it as a lever
                       # rather than as a knob that could sit at 0.47.
                       "bool": reading in {"On", "Off"},
                       "value": reading,
                       "norm": round(float(param.get("norm") or 0.0), 4),
                       "raw": param.get("raw")})
    # The amp's own tone controls come first, then the plugin's own switches, in the plugin's order
    # inside each: that is the order the VST face reads in, top left to bottom right.
    order = {"amp": 0, "pedal": 1, "other": 2}
    picked.sort(key=lambda k: order[k["section"]])
    return picked


def _section(name: str) -> str:
    """Which half of the plugin a knob belongs to, decided by the plugin's own prefixes."""
    low = name.lower()
    if low.startswith("amplifier"):
        return "amp"
    if low.startswith("pedal") or "instigator" in low:
        return "pedal"
    return "other"


def _plugin(track: int) -> dict:
    """The plugin the knobs belong to, so the face can name it instead of implying it."""
    try:
        chain = RIG.fx_list(track)
    except port.RigError:
        return {}
    if not chain:
        return {}
    first = chain[0]
    return {"name": str(first.get("name") or ""), "enabled": bool(first.get("enabled", True))}


def _pretty(value: str) -> str:
    """7 decimal places is what the host reports, not what a player wants to read."""
    try:
        number = float(value)
    except ValueError:
        return value
    return f"{number:.2f}".rstrip("0").rstrip(".")


def _chain(track: int) -> list[dict] | None:
    """None means the chain could not be read, which is not the same as an empty chain."""
    try:
        return [{"name": str(fx.get("name")), "enabled": bool(fx.get("enabled", True))}
                for fx in RIG.fx_list(track)]
    except port.RigError:
        return None


def _verdict(state: dict, track: dict, peak: float, clip: bool,
             master_db: float | None = None) -> tuple[str, str]:
    """The pane and the amphead_ready tool answer from the same judge, never from two copies."""
    reading = {"l": peak, "r": peak, "clip": clip}
    if master_db is not None:
        reading["master_db"] = master_db
    out = verdict.judge(state, track, reading)
    return out["verdict"], out["reason"]


def _track(track_no: int) -> dict:
    try:
        return RIG.track_info(track_no)
    except port.RigError:
        return {}


def _presets() -> list[dict]:
    """The three presets the rig carries, which is what a button in the pane can apply in one move.

    The capture library in tones.py answers "make it sound like blink182" and swaps the amp itself;
    it is reachable through chat and /preset with those words, not as a row of buttons.
    """
    from amphead import presets as bank

    return [{"key": row["name"], "title": row.get("about") or row["name"]} for row in bank.catalog()]


@router.get("/state")
def state() -> dict[str, Any]:
    # The demo rig answers first when it is switched on: a visitor with no DAW still gets a face
    # that moves, and the pane paints a badge so it is never mistaken for their own rig.
    if demo.enabled():
        return demo.state()
    try:
        live = RIG.state()
    except port.NoRig:
        reason = appdecl.unavailable_reason() or "REAPER is not installed on this machine"
        return {"verdict": "NO REAPER INSTALL", "reason": reason,
                "chain": [], "plugin": {}, "knobs": [], "presets": _presets(), "meter": {"peak": 0.0}}

    track_no = 1
    track = _track(track_no) if live.get("bridge") == "live" else {}
    peak, clip = 0.0, False
    if live.get("bridge") == "live" and int(live.get("tracks") or 0) >= 1:
        try:
            reading = RIG.meter(track_no)
            peak = float(max(reading.get("l") or 0.0, reading.get("r") or 0.0))
            clip = bool(reading.get("clip"))
        except port.RigError:
            peak, clip = 0.0, False

    chain = _chain(track_no) if live.get("bridge") == "live" else None
    master = None
    if live.get("bridge") == "live":
        try:
            master = RIG.master().get("db")
        except port.RigError:
            master = None

    reading = {"l": peak, "r": peak, "clip": clip}
    if master is not None:
        reading["master_db"] = master
    judged = verdict.judge(live, track, reading, chain)
    word, reason = judged["verdict"], judged["reason"]

    return {
        "verdict": word,
        "reason": reason,
        "project": live.get("project") or "unnamed",
        "device": _device(live),
        # Which socket the track listens to. A separate control from the plugin's knobs, but the
        # same kind of control: it turns, it has a fixed set of places to be, and it says which.
        "input": _input(track, live),
        "master_db": master,
        # Whether REAPER's window is on screen decides if a chain-row click may focus a plugin.
        "ui": "visible" if live.get("bridge") == "live" and RIG.ui_visible() else "hidden",
        # Is the rig open at all. The pane's first button is Setup until this is true, because a
        # Live switch on a rig nobody started can only ever answer with a failure.
        "running": live.get("bridge") == "live",
        "armed": bool(track.get("armed")),
        "monitor": bool(track.get("monitor")),
        "problems": judged["problems"],
        "chain": chain or [],
        # The plugin's own identity and its own full knob set: the face names the amp it is showing
        # and shows every control that amp has, so "true tone" is checkable against the VST.
        "plugin": _plugin(track_no) if live.get("bridge") == "live" else {},
        "knobs": _knobs(track_no) if live.get("bridge") == "live" else [],
        "presets": _presets(),
        "demo": False,
    }


@router.get("/meter")
def meter() -> dict[str, Any]:
    if demo.enabled():
        return demo.meter()
    try:
        reading = RIG.meter(1)
    except port.RigError:
        return {"l": 0.0, "r": 0.0, "clip": False, "live": False}
    return {"l": float(reading.get("l") or 0.0), "r": float(reading.get("r") or 0.0),
            "clip": bool(reading.get("clip")), "live": True}


@router.post("/live")
def live_switch(body: dict) -> dict[str, Any]:
    if demo.enabled():
        return demo.live_switch(body)

    on = bool(body.get("on"))
    # No level means silent. The Live switch arms and monitors; it never hands back a rig that is
    # already loud, because the amp model on the track may be hot and speakers are not free.
    db = body.get("master_db", -60)
    # Input 0: the Live button never repoints the track's input. Whatever the player chose in
    # REAPER stays chosen, which is the only default that cannot break a DI or a stereo pair.
    with _speaking_to_tools() as tools:
        out = tools.handle_live({"on": on, "confirm": True, "master_db": db,
                                 "input": body.get("input", "0")})
    return {"output": out, "on": on, "ok": not out.startswith("NOT LIVE")}


@router.post("/setup")
def setup_rig(body: dict | None = None) -> dict[str, Any]:
    """Open the rig without making a sound, which is the step Amp 2 has no other door to.

    The original pane could assume a human had already opened REAPER. The local engine has no
    REAPER to open, so without this the Live switch was the first control a player could press
    and the engine it woke was never started: every answer was a failure, correctly reported.
    """
    if demo.enabled():
        return {"output": "the demo face is on", "ok": True}
    with _speaking_to_tools() as tools:
        out = tools.handle_setup({})
    return {"output": out, "ok": not out.startswith(("NOT", "NO "))}


@router.post("/panic")
def panic(body: dict) -> dict[str, Any]:
    if demo.enabled():
        return demo.panic(body)
    restore = bool(body.get("restore"))
    db = body.get("master_db", -60)
    with _speaking_to_tools() as tools:
        out = tools.handle_panic({"restore": restore, "master_db": db})
    return {"output": out, "ok": not out.startswith("NOT PANICKED"), "restore": restore}


@router.post("/master")
def master_level(body: dict) -> dict[str, Any]:
    """The player's own volume ramp, one write at a time.

    Boot and Live both leave the master at the floor, so this is the only way the rig gets
    audible. It is deliberately a separate route from /live: raising the level while a track is
    already armed is the step that makes feedback possible, and it should be its own deliberate
    action rather than a side effect of arming.
    """
    if demo.enabled():
        return demo.live_switch({"on": True, "master_db": body.get("db", -60)})
    from amphead import tools

    try:
        asked = float(body.get("db"))
    except (TypeError, ValueError):
        return {"output": "NO LEVEL: say how many dB", "ok": False}
    if not math.isfinite(asked):
        return {"output": "NO LEVEL: say how many dB", "ok": False}
    db = max(-60.0, min(0.0, asked))
    try:
        RIG.set_master(db=db, expect=tools.active_path())
        level = RIG.master().get("db")
    except port.NoRig:
        return {"output": "NOT SET: REAPER is not running", "ok": False}
    except port.RigError as exc:
        return {"output": f"NOT SET: {exc}", "ok": False}
    where = "silent" if float(level or 0) <= -60 else f"master at {level} dB"
    return {"output": f"{where}; raise it in steps and listen", "ok": True, "db": level}


@router.post("/knob")
def knob(body: dict) -> dict[str, Any]:
    """One knob turn, answered in the plugin's own units, and nothing else.

    /tweak is the chat-shaped write: it goes through the tool handler, formats a paragraph and
    then the pane throws the paragraph away. A drag needs the opposite: one bridge write, the
    number the plugin reads back, no chain listing and no meter, because the face asks ten times
    a second while a finger is moving. Nothing here touches the state cache.
    """
    if demo.enabled():
        return demo.tweak(body)
    from amphead import rig

    param = body.get("param")
    if param is None or param == "":
        return {"output": "NO PARAM: say which knob to turn", "ok": False}
    if body.get("value") is None:
        return {"output": "NO VALUE: say what to set it to", "ok": False}
    try:
        done = rig.set_param(
            body.get("fx", 0), param, float(body["value"]),
            track=int(body.get("track") or 1),
            mode=str(body.get("mode") or "norm"),
        )
    except port.NoRig:
        return {"output": "NOT SET: REAPER is not running", "ok": False}
    except rig.WriteRefused as exc:
        return {"output": f"REFUSED: {exc}", "ok": False}
    except (port.RigError, LookupError, TypeError, ValueError) as exc:
        return {"output": f"NOT SET: {exc}", "ok": False}
    return {"output": done.sentence(), "ok": True, "norm": done.norm, "value": done.fmt,
            "raw": done.written, "clamped": done.clamped, "retried": done.retried,
            "param": done.param_name}


@router.post("/input")
def input_switch(body: dict) -> dict[str, Any]:
    """Point the track's record input at one socket on the interface, and prove REAPER stored it.

    This is the only route that changes which hardware channel the rig listens to. It writes the
    track's own record input, never a device setting, so REAPER's audio device block is untouched
    and the change is one undo away from where it was.
    """
    if demo.enabled():
        return demo.input_switch(body)
    spec = body.get("input")
    if spec is None or str(spec).strip() == "":
        return {"output": "NO INPUT: say which socket on the interface", "ok": False}
    track_no = int(body.get("track") or 1)
    try:
        # Every write carries the project the player is looking at as `expect`. Without it the
        # bridge's guard refuses, and rightly: a record input is per project, so a write that does
        # not name a project could land in a tab nobody meant to touch.
        expect = RIG.project()
        got = RIG.inputs()
    except port.NoRig:
        return {"output": "NOT SET: REAPER is not running", "ok": False}
    except port.RigError as exc:
        return {"output": f"NOT SET: {exc}", "ok": False}
    if not expect:
        return {"output": "NOT SET: no project is open, so there is no track to point", "ok": False}
    if int(got.get("count") or 0) < 1:
        # REAPER parks the device closed when idle, and a closed device has no sockets. Opening it
        # is the difference between "your interface has no inputs" and "the device was asleep".
        try:
            got = RIG.ensure_audio(info=got)
        except port.RigError as exc:
            return {"output": f"NOT SET: could not open the audio device: {exc}", "ok": False}
        if int(got.get("count") or 0) < 1:
            return {"output": "NOT SET: the audio device is closed, so there are no sockets to "
                              "point at. Open REAPER's audio device and turn again.", "ok": False}
    try:
        RIG.set_input(track=track_no, spec=spec, inputs=int(got["count"]), expect=expect)
    except port.NoRig:
        return {"output": "NOT SET: REAPER is not running", "ok": False}
    except (port.RigSaidNo, port.RigError, ValueError) as exc:
        return {"output": f"NOT SET: {exc}", "ok": False}
    mine = _input(_track(track_no), {"bridge": "live"})
    mine["track"] = track_no
    return {"output": f"track {track_no} records from {mine['name']} ({mine['value']})",
            "ok": True, **mine}


@router.post("/tweak")
def tweak(body: dict) -> dict[str, Any]:
    """One knob, written the same way chat writes it: resolved by name, read back, one undo group."""
    if demo.enabled():
        return demo.tweak(body)
    param = body.get("param")
    if param is None or param == "":
        return {"output": "NO PARAM: say which knob to turn", "ok": False}
    if body.get("value") is None:
        return {"output": "NO VALUE: say what to set it to", "ok": False}
    with _speaking_to_tools() as tools:
        out = tools.handle_set({
            "track": body.get("track", 1),
            "fx": body.get("fx", 0),
            "param": param,
            "value": body.get("value"),
            "mode": body.get("mode", "norm"),
        })
    return {"output": out, "ok": not out.startswith(("NOT SET", "REFUSED"))}


@router.post("/focus")
def focus(body: dict) -> dict[str, Any]:
    """Bring one plugin to the front, but only while REAPER's window is actually showing."""
    if demo.enabled():
        return demo.focus(body)
    fx = body.get("fx", 0)
    track = int(body.get("track") or 1)
    if not RIG.ui_visible():
        return {"output": "REAPER's window is not showing, so there is no chain window to bring "
                          "forward. Un-minimize REAPER and click again.",
                "ok": False, "focused": False}
    try:
        done = RIG.fx_focus(track, int(fx))
    except port.RigError as exc:
        return {"output": f"the bridge refused the focus: {exc}", "ok": False, "focused": False}
    return {"output": f"brought {done.get('fx') or 'the plugin'} to the front of the chain",
            "ok": True, "focused": True, "fx": done.get("fx")}


@router.post("/preset")
def preset(body: dict) -> dict[str, Any]:
    if demo.enabled():
        return demo.preset(body)
    from amphead import tools

    query = str(body.get("query") or "").strip()
    if not query:
        return {"output": "NO QUERY: name a sound to load", "ok": False}
    out = tools.handle_tone({"query": query, "apply": True})
    # The tool answers with a sentence, and a failure begins with one of these words on the first
    # line. Matching the whole text keeps a working apply whose diff happens to mention "not
    # applied" from being reported as a failure.
    first = out.splitlines()[0].upper() if out else ""
    ok = not first.startswith(("NOT APPLIED", "NO MATCH", "NO QUERY", "NOT SET", "REFUSED"))
    return {"output": out, "ok": ok}


@router.post("/demo")
def demo_switch(body: dict) -> dict[str, Any]:
    """Turn the canned rig on or off. Off hands the pane back to whatever REAPER is doing.

    This is the only route a visitor needs to find: with no REAPER installed the pane offers the
    demo itself, so nothing has to be typed at a terminal or set in an environment variable.
    """
    on = demo.set_enabled(bool(body.get("on")))
    said = ("demo rig on: a made-up session, not your hardware" if on
            else "demo rig off: back to REAPER")
    return {"on": on, "output": said, "demo": on}
