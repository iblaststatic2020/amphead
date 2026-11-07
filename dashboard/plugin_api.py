"""The amp face's backend.

Everything the pane shows comes from here, so the pane never has to guess and never has to
open REAPER to find out. Read routes are cheap and safe; the two write routes go through the
same tool handlers the agent uses, so a click in the pane and a sentence in chat do the same
thing.
"""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any

from fastapi import APIRouter

router = APIRouter()

# These routes are deliberately plain `def`, not `async def`. Every one of them talks to REAPER
# through a blocking bridge call, and FastAPI runs a sync endpoint in its threadpool: a slow or
# absent bridge then costs a worker thread instead of stalling the event loop the whole desktop
# app shares. An `async def` here blocks every other request in the gateway while it waits.

# The package lives beside this file's parent (dashboard/ sits inside the plugin root).
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from amphead import bridge, demo, launch, tones, verdict  # noqa: E402

FLOOR = verdict.FLOOR  # -60 dB, the same floor the rig boots and parks at
def _device(state: dict) -> dict:
    device = state.get("device") or {}
    return {
        "buffer": device.get("buffer") or "",
        "rate": device.get("rate") or "",
        "out_ms": device.get("latency_out_ms") or 0.0,
        "closed": bool(device.get("closed")),
    }


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
        params = bridge.fx_params(track, 0, keep=4096)
    except bridge.ReaperError:
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
                       # On or Off has two places to be, and the face draws it as a knob that
                       # snaps between them rather than one that can sit at 0.47.
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
        chain = bridge.fx_list(track)
    except bridge.ReaperError:
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
                for fx in bridge.fx_list(track)]
    except bridge.ReaperError:
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
        return bridge.track_info(track_no)
    except bridge.ReaperError:
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
        live = launch.state()
    except launch.NoReaperInstall:
        return {"verdict": "NO REAPER INSTALL", "reason": "REAPER is not installed on this machine",
                "chain": [], "plugin": {}, "knobs": [], "presets": _presets(), "meter": {"peak": 0.0}}

    track_no = 1
    track = _track(track_no) if live.get("bridge") == "live" else {}
    peak, clip = 0.0, False
    if live.get("bridge") == "live" and int(live.get("tracks") or 0) >= 1:
        try:
            reading = bridge.meter(track_no)
            peak = float(max(reading.get("l") or 0.0, reading.get("r") or 0.0))
            clip = bool(reading.get("clip"))
        except bridge.ReaperError:
            peak, clip = 0.0, False

    chain = _chain(track_no) if live.get("bridge") == "live" else None
    master = None
    if live.get("bridge") == "live":
        try:
            master = bridge.master().get("db")
        except bridge.ReaperError:
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
        "master_db": master,
        # Whether REAPER's window is on screen decides if a chain-row click may focus a plugin.
        "ui": "visible" if live.get("bridge") == "live" and launch.ui_visible() else "hidden",
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
        reading = bridge.meter(1)
    except bridge.ReaperError:
        return {"l": 0.0, "r": 0.0, "clip": False, "live": False}
    return {"l": float(reading.get("l") or 0.0), "r": float(reading.get("r") or 0.0),
            "clip": bool(reading.get("clip")), "live": True}


@router.post("/live")
def live_switch(body: dict) -> dict[str, Any]:
    if demo.enabled():
        return demo.live_switch(body)
    from amphead import tools

    on = bool(body.get("on"))
    db = body.get("master_db", -18)
    # Input 0: the Live button never repoints the track's input. Whatever the player chose in
    # REAPER stays chosen, which is the only default that cannot break a DI or a stereo pair.
    out = tools.handle_live({"on": on, "confirm": True, "master_db": db,
                             "input": body.get("input", "0")})
    return {"output": out, "on": on}


@router.post("/panic")
def panic(body: dict) -> dict[str, Any]:
    if demo.enabled():
        return demo.panic(body)
    from amphead import tools

    restore = bool(body.get("restore"))
    db = body.get("master_db", -60)
    out = tools.handle_panic({"restore": restore, "master_db": db})
    return {"output": out, "ok": not out.startswith("NOT PANICKED"), "restore": restore}


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
    except bridge.NoReaper:
        return {"output": "NOT SET: REAPER is not running", "ok": False}
    except rig.WriteRefused as exc:
        return {"output": f"REFUSED: {exc}", "ok": False}
    except (bridge.ReaperError, LookupError, TypeError, ValueError) as exc:
        return {"output": f"NOT SET: {exc}", "ok": False}
    return {"output": done.sentence(), "ok": True, "norm": done.norm, "value": done.fmt,
            "raw": done.written, "clamped": done.clamped, "retried": done.retried,
            "param": done.param_name}


@router.post("/tweak")
def tweak(body: dict) -> dict[str, Any]:
    """One knob, written the same way chat writes it: resolved by name, read back, one undo group."""
    if demo.enabled():
        return demo.tweak(body)
    from amphead import tools

    param = body.get("param")
    if param is None or param == "":
        return {"output": "NO PARAM: say which knob to turn", "ok": False}
    if body.get("value") is None:
        return {"output": "NO VALUE: say what to set it to", "ok": False}
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
    if not launch.ui_visible():
        return {"output": "REAPER's window is not showing, so there is no chain window to bring "
                          "forward. Un-minimize REAPER and click again.",
                "ok": False, "focused": False}
    try:
        done = bridge.fx_focus(track, int(fx))
    except bridge.ReaperError as exc:
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
