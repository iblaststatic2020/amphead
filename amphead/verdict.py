"""One judge for the rig, shared by the tool and the amp face.

The pane and the agent must never disagree about whether the amp is playable, so the rules live
here once. Everything in this module is pure: hand it what REAPER said and it returns a word, a
sentence a player can act on, and the list of things that are wrong.
"""

from __future__ import annotations

from typing import Any

PARKED_DB = -59.0  # the floor setup and PANIC park the master at
FLOOR = 0.001            # -60 dB, the same floor the rig boots and parks at
LATENCY_LIMIT_MS = 40.0  # beyond this, playing feels late even when the meter is moving

# Words are the whole vocabulary. A new one needs a colour in the pane, because a verdict the
# pane cannot paint is a verdict the player cannot read.
REAPER_NOT_RUNNING = "REAPER NOT RUNNING"
REAPER_BUSY = "REAPER BUSY"
NO_REAPER_INSTALL = "NO REAPER INSTALL"
DEVICE_CLOSED = "DEVICE CLOSED"
NO_INPUT = "NO INPUT"
NO_CHAIN = "NO CHAIN"
STANDBY = "STANDBY"
NO_SIGNAL = "NO SIGNAL"
CLIPPING = "CLIPPING"
READY = "READY"


def _device(state: dict) -> dict:
    device = state.get("device") or {}
    return {
        "buffer": device.get("buffer") or "",
        "rate": device.get("rate") or "",
        "out_ms": float(device.get("latency_out_ms") or 0.0),
        "closed": bool(device.get("closed")),
    }


def clip_of(reading: dict) -> bool:
    """The bridge latches the clip flag and clears it on read, so one true sample is the truth."""
    return bool(reading.get("clip"))


def _peak(reading: dict) -> float:
    return float(max(reading.get("l") or 0.0, reading.get("r") or 0.0))


def judge(state: dict, track: dict, reading: dict,
          chain: list[dict] | None = None) -> dict[str, Any]:
    """Return {verdict, reason, problems}. `reading` is one METER sample, already the loudest.

    chain=None means the caller did not look at the chain, so the chain rules are skipped rather
    than guessed at. recinput missing means the same about the input.
    """
    problems: list[str] = []

    if state.get("bridge") != "live":
        word = REAPER_BUSY if state.get("bridge") == "down" else REAPER_NOT_RUNNING
        return {"verdict": word, "reason": str(state.get("reason") or "the bridge has not answered"),
                "problems": problems}

    device = _device(state)
    if device["closed"]:
        return {"verdict": DEVICE_CLOSED,
                "reason": "REAPER has no audio device open. Flip LIVE and it opens one for you.",
                "problems": ["the audio device is closed"]}

    if not track.get("armed"):
        return {"verdict": STANDBY, "reason": "the track is not armed, flip LIVE to play",
                "problems": ["the track is not armed"]}

    if "recinput" in track and int(float(track.get("recinput") or 0)) == 0:
        return {"verdict": NO_INPUT,
                "reason": "the track is armed but pointed at no input, so nothing can arrive",
                "problems": ["recinput is 0 (no input selected)"]}

    peak = _peak(reading)
    if clip_of(reading):
        return {"verdict": CLIPPING,
                "reason": "the input is hitting the ceiling, back your guitar volume off",
                "problems": ["the input is clipping"]}

    live_fx: list[str] = []
    if chain is not None:
        live_fx = [str(fx.get("name")) for fx in chain if fx.get("enabled", True)]
    if chain is not None and not chain:
        return {"verdict": NO_CHAIN,
                "reason": "the track is live but nothing is loaded on it, so you hear the raw DI",
                "problems": ["the chain is empty"]}
    if chain is not None and not live_fx:
        return {"verdict": NO_CHAIN,
                "reason": "every plugin on the track is bypassed, so you hear the raw DI. "
                          "That is what PANIC leaves behind; RESTORE puts it back.",
                "problems": ["every plugin in the chain is bypassed"]}

    bypassed = [str(fx.get("name")) for fx in (chain or []) if not fx.get("enabled", True)]
    if bypassed:
        problems.append("still bypassed: " + ", ".join(bypassed))

    if device["out_ms"] > LATENCY_LIMIT_MS:
        problems.append(f"latency {device['out_ms']} ms is above {LATENCY_LIMIT_MS} ms")

    if peak <= FLOOR:
        return {"verdict": NO_SIGNAL,
                "reason": "armed and monitoring, but nothing is arriving on this input",
                "problems": problems + ["the meter never rose above -60 dB"]}

    master_db = reading.get("master_db")
    if master_db is not None and float(master_db) <= PARKED_DB:
        # Signal is arriving and the chain is live, but the master is parked: the player would
        # hear nothing, so this is not "ready". It is the same STANDBY the snapshot boots into.
        return {"verdict": STANDBY,
                "reason": f"the track is armed and the input is arriving, but the master is parked "
                          f"at {float(master_db):.0f} dB. Flip LIVE to hear it.",
                "problems": problems + ["the master is parked at the silent floor"]}

    reason = f"armed, monitoring, {device['out_ms']} ms out"
    if chain is not None:
        count = len(live_fx)
        reason += f", {count} plugin{'s' if count != 1 else ''} live"
    return {"verdict": READY, "reason": reason, "problems": problems}


