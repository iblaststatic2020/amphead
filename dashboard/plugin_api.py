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

# The package lives beside this file's parent (dashboard/ sits inside the plugin root).
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from amphead import bridge, launch, tones, verdict  # noqa: E402

FLOOR = verdict.FLOOR  # -60 dB, the same floor the rig boots and parks at
WANTED = ("gain", "bass", "mid", "treble", "oomph", "sparkle", "blend", "gate", "volume")


def _device(state: dict) -> dict:
    device = state.get("device") or {}
    return {
        "buffer": device.get("buffer") or "",
        "rate": device.get("rate") or "",
        "out_ms": device.get("latency_out_ms") or 0.0,
        "closed": bool(device.get("closed")),
    }


def _knobs(track: int) -> list[dict]:
    """The controls worth showing, in the amp's own words and values."""
    try:
        params = bridge.fx_params(track, 0)
    except bridge.ReaperError:
        return []
    picked = []
    for param in params:
        name = str(param.get("name") or "")
        low = name.lower()
        if "pedal" in low or "instigator" in low:
            continue
        if any(word in low for word in WANTED):
            picked.append({"name": name, "value": _pretty(str(param.get("fmt") or ""))})
    return picked[:8]


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


def _verdict(state: dict, track: dict, peak: float, clip: bool) -> tuple[str, str]:
    """The pane and the amphead_ready tool answer from the same judge, never from two copies."""
    out = verdict.judge(state, track, {"l": peak, "r": peak, "clip": clip})
    return out["verdict"], out["reason"]


def _track(track_no: int) -> dict:
    try:
        return bridge.track_info(track_no)
    except bridge.ReaperError:
        return {}


def _presets() -> list[dict]:
    return [{"key": arch.key, "title": arch.title} for arch in tones.ARCHETYPES]


@router.get("/state")
async def state() -> dict[str, Any]:
    try:
        live = launch.state()
    except launch.NoReaperInstall:
        return {"verdict": "NO REAPER INSTALL", "reason": "REAPER is not installed on this machine",
                "chain": [], "knobs": [], "presets": _presets(), "meter": {"peak": 0.0}}

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
    judged = verdict.judge(live, track, {"l": peak, "r": peak, "clip": clip}, chain)
    word, reason = judged["verdict"], judged["reason"]
    master = None
    if live.get("bridge") == "live":
        try:
            master = bridge.master().get("db")
        except bridge.ReaperError:
            master = None

    return {
        "verdict": word,
        "reason": reason,
        "project": live.get("project") or "unnamed",
        "device": _device(live),
        "master_db": master,
        "armed": bool(track.get("armed")),
        "monitor": bool(track.get("monitor")),
        "problems": judged["problems"],
        "chain": chain or [],
        "knobs": _knobs(track_no) if live.get("bridge") == "live" else [],
        "presets": _presets(),
    }


@router.get("/meter")
async def meter() -> dict[str, Any]:
    try:
        reading = bridge.meter(1)
    except bridge.ReaperError:
        return {"l": 0.0, "r": 0.0, "clip": False, "live": False}
    return {"l": float(reading.get("l") or 0.0), "r": float(reading.get("r") or 0.0),
            "clip": bool(reading.get("clip")), "live": True}


@router.post("/live")
async def live_switch(body: dict) -> dict[str, Any]:
    from amphead import tools

    on = bool(body.get("on"))
    db = body.get("master_db", -18)
    # Input 0: the Live button never repoints the track's input. Whatever the player chose in
    # REAPER stays chosen, which is the only default that cannot break a DI or a stereo pair.
    out = tools.handle_live({"on": on, "confirm": True, "master_db": db,
                             "input": body.get("input", "0")})
    return {"output": out, "on": on}


@router.post("/panic")
async def panic(body: dict) -> dict[str, Any]:
    from amphead import tools

    restore = bool(body.get("restore"))
    db = body.get("master_db", -60)
    out = tools.handle_panic({"restore": restore, "master_db": db})
    return {"output": out, "ok": not out.startswith("NOT PANICKED"), "restore": restore}


@router.post("/preset")
async def preset(body: dict) -> dict[str, Any]:
    from amphead import tools

    query = str(body.get("query") or "").strip()
    if not query:
        return {"output": "NO QUERY: name a sound to load", "ok": False}
    out = tools.handle_tone({"query": query, "apply": True})
    return {"output": out, "ok": not out.startswith("not applied")}
