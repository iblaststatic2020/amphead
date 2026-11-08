"""One judge for the amp, shared by the tools and the face.

The pane and the agent must never disagree about whether the amp is playable, so the rules live
here once. Everything in this module is pure: hand it what the engine said and it returns a word,
a sentence a player can act on, and the list of things that are wrong.

The words are the whole vocabulary the face can paint. A new one needs a colour in
`desktop/plugin.js`, because a verdict the pane cannot paint is a verdict the player cannot read.
"""

from __future__ import annotations

from typing import Any

PARKED_DB = -59.0       # the floor setup and PANIC park the master at
ARM_DB = -6.0           # the level arming uses, measured so a -18 dBFS DI does not clip
FLOOR = 0.001           # -60 dB: below this the engine is open and hearing nothing
LATENCY_LIMIT_MS = 40.0  # beyond this, playing feels late even when the meter is moving

ENGINE_OFF = "ENGINE OFF"
DEVICE_CLOSED = "DEVICE CLOSED"
DEVICE_UNPLUGGED = "DEVICE UNPLUGGED"
DEVICE_REOPENED = "REOPENED DEVICE"
NO_INPUT = "NO INPUT"
NO_CHAIN = "NO CHAIN"
MUTED = "MUTED"
STANDBY = "STANDBY"
PARKED = "PARKED"
NO_SIGNAL = "NO SIGNAL"
CLIPPING = "CLIPPING"
READY = "READY"


def _device(state: dict) -> dict:
    device = state.get("device") or {}
    return {
        "buffer": device.get("buffer") or "",
        "rate": device.get("rate") or "",
        "mode": device.get("mode") or state.get("mode") or "",
        "out_ms": float(device.get("latency_out_ms") or 0.0),
        "closed": bool(device.get("closed")),
        # None means nobody looked, which is not the same as zero inputs on either socket.
        "inputs": device.get("inputs"),
        "reopened": bool(device.get("reopened")),
    }


def clip_of(reading: dict) -> bool:
    """The engine reports the peak it saw; one sample at the ceiling is the truth."""
    return bool(reading.get("clip"))


def _peak(reading: dict) -> float:
    return float(max(reading.get("l") or 0.0, reading.get("r") or 0.0))


def judge(state: dict, reading: dict, chain: list[dict] | None = None) -> dict[str, Any]:
    """Return {verdict, reason, problems}. `reading` is one meter sample, already the loudest.

    chain=None means the caller did not look at the stages, so the stage rules are skipped rather
    than guessed at. Each word names the broken thing rather than the symptom: NO SIGNAL is what a
    closed device, an empty input and a parked master all look like, and a pane that says NO SIGNAL
    for all three teaches the player to ignore it.
    """
    problems: list[str] = []

    if not state.get("open"):
        return {"verdict": ENGINE_OFF,
                "reason": "the engine is not running: the switch on the face reads SET UP",
                "problems": problems}

    device = _device(state)
    if device["closed"]:
        return {"verdict": DEVICE_CLOSED,
                "reason": "the engine has no audio device open. Press GO LIVE and it opens one.",
                "problems": ["the audio device is closed"]}

    if device["inputs"] is not None and int(device["inputs"]) == 0:
        return {"verdict": DEVICE_UNPLUGGED,
                "reason": "the interface reports no inputs, so nothing can arrive: check it is "
                          "connected and powered.",
                "problems": ["the device reports 0 inputs"]}

    if not state.get("armed"):
        master = state.get("master_db")
        where = (f"parked at {float(master):.0f} dB" if master is not None
                 and float(master) <= PARKED_DB else "on")
        return {"verdict": STANDBY,
                "reason": f"the engine is open and silent, master {where}: GO LIVE opens the amp "
                          f"with the master wherever the knob is, so it cannot arrive loud",
                "problems": ["the engine is open but nothing is live"]}

    if state.get("parked_by_idle"):
        return {"verdict": PARKED,
                "reason": "the amp parked its own master after a long silence. Raise the master "
                          "(or press GO LIVE) and it is back where you left it.",
                "problems": ["the master was parked because nobody played for a while"]}

    peak = _peak(reading)
    if clip_of(reading):
        return {"verdict": CLIPPING,
                "reason": "the output is hitting the ceiling, back the input gain off",
                "problems": ["the output is clipping"]}

    live: list[str] = []
    if chain is not None:
        live = [str(stage.get("name")) for stage in chain if stage.get("enabled", True)]
        if not live:
            return {"verdict": NO_CHAIN,
                    "reason": "nothing is in the path, so you would hear the raw interface "
                              "signal. That is what SILENCE leaves behind; GO LIVE re-engages it.",
                    "problems": ["every stage is out of the path"]}

    if peak <= FLOOR:
        return {"verdict": NO_SIGNAL,
                "reason": "armed and monitoring, but nothing is arriving on this input",
                "problems": problems + ["the meter never rose above -60 dB"]}

    master_db = reading.get("master_db", state.get("master_db"))
    if master_db is not None and float(master_db) <= PARKED_DB:
        return {"verdict": STANDBY,
                "reason": f"armed and hearing the input, but the master is parked at "
                          f"{float(master_db):.0f} dB. Raise the master to hear it.",
                "problems": problems + ["the master is parked at the silent floor"]}

    reason = f"armed, monitoring, {device['out_ms']:.1f} ms round trip"
    if live:
        reason += f", {len(live)} stage{'s' if len(live) != 1 else ''} live"
    if device["reopened"]:
        return {"verdict": DEVICE_REOPENED,
                "reason": "playable, and the audio device had to be reopened: " + reason,
                "problems": problems + ["the device had to be reopened"]}
    return {"verdict": READY, "reason": reason, "problems": problems}
