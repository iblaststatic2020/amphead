"""The amp's one state machine: every implicit mode, named once.

Before this module the amp had one visible word (`verdict`) and a dozen hidden modes behind it:
open but deviceless, armed but parked, bypassed by panic, glitching under a healthy meter,
playing a capture at the wrong rate, recording, tuning. Each caller inferred a different subset
from raw booleans, so the pane, the tools and the agent could disagree without either being
wrong about its own facts.

Now there is one pure function. It takes what the engine said and returns a closed set of axes:

``state``
    The same word the face paints (READY, STANDBY, ...). Kept as the headline because it is
    already the vocabulary players and tests know.
``engine``
    off, open, closed, fault.
``signal``
    live, standby, parked, silent, bypassed, fault.
``activity``
    idle or recording. Activity is orthogonal: a take can run while the amp is live.
``health``
    ok, warn, bad. A tone notice (rate mismatch) is a warning, not a failed amp.
``next``
    One imperative for the player. This is what turns a diagnosis into a control.
``notice``
    A short fact the face can print in place, separate from the full ``problems`` list.

Nothing here touches the engine. ``verdict.judge`` is a thin alias for this function, so every
existing caller keeps its signature and gains the axes for free.
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
GLITCH = "GLITCH"
READY = "READY"

#: What the player should do next, per verdict. One imperative, no hedging.
NEXT = {
    ENGINE_OFF: "press SET UP",
    DEVICE_CLOSED: "press GO LIVE",
    DEVICE_UNPLUGGED: "plug the interface back in",
    DEVICE_REOPENED: "play",
    NO_CHAIN: "press SILENCE again to restore the stages",
    NO_SIGNAL: "play, or check the cable and input",
    NO_INPUT: "pick an input on the face",
    MUTED: "raise the master",
    PARKED: "raise the master or press GO LIVE",
    STANDBY: "press GO LIVE",
    CLIPPING: "lower IN",
    GLITCH: "run tune",
    READY: "play",
}

IDLE = "idle"
RECORDING = "recording"

OK = "ok"
WARN = "warn"
BAD = "bad"


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


def _mismatch(state: dict) -> tuple[str, str]:
    """Return (full problem sentence, short notice) for a capture/engine rate split."""
    mismatch = state.get("rate_mismatch") or {}
    capture_hz = mismatch.get("capture_hz")
    stream_hz = mismatch.get("stream_hz")
    if not capture_hz or not stream_hz:
        return "", ""
    capture_hz, stream_hz = int(capture_hz), int(stream_hz)
    delta = round(abs(100 * (1 - stream_hz / capture_hz)))
    direction = "dark" if stream_hz < capture_hz else "bright"
    problem = (f"this capture was trained at {capture_hz} Hz and the engine runs at "
               f"{stream_hz} Hz, so it plays about {delta}% {direction}: set the interface to "
               f"{capture_hz} Hz to hear it exactly")
    notice = (f"{capture_hz / 1000:g} kHz capture in a {stream_hz / 1000:g} kHz engine: "
              f"{delta}% {direction}. Set the interface to {capture_hz} Hz.")
    return problem, notice


def _activity(state: dict) -> str:
    """Recording is a verb, not a verdict. A take can run while the amp is live."""
    take = state.get("take") or {}
    if take.get("recording") or state.get("recording"):
        return RECORDING
    return IDLE


def _stage_state(chain: list[dict] | None) -> dict:
    if chain is None:
        return {"live": None, "bypassed": []}
    live = [str(stage.get("name")) for stage in chain if stage.get("enabled", True)]
    bypassed = [str(stage.get("name")) for stage in chain if not stage.get("enabled", True)]
    return {"live": len(live), "bypassed": bypassed}


def snapshot(state: dict, reading: dict | None = None,
             chain: list[dict] | None = None) -> dict[str, Any]:
    """Return the amp's explicit state from the facts the engine reported.

    ``chain=None`` means the caller did not look at the stages, so the stage rules are skipped
    rather than guessed at. The precedence is deliberate: a named broken thing beats a symptom,
    and a failed callback beats "not armed", because that is why it is not armed.
    """
    reading = reading or {}
    problems: list[str] = []
    mismatch_problem, notice = _mismatch(state)
    if mismatch_problem:
        problems.append(mismatch_problem)

    callback_errors = int(state.get("callback_errors") or 0)
    if callback_errors:
        problems.append(f"the audio callback failed {callback_errors} time(s) and the amp "
                        f"disarmed itself")
    late = int(state.get("late") or 0)
    if late:
        problems.append(f"{late} block(s) reached the driver too late, so playback cracks and "
                        f"drags: run tune, or raise blocksize in settings.json")
    underflows = int(state.get("underflows") or 0)
    if underflows:
        problems.append(f"{underflows} block(s) underran: the device asked for audio that was "
                        f"not ready, which is a click or a gap")
    overflows = int(state.get("overflows") or 0)
    if overflows:
        problems.append(f"{overflows} input block(s) overran: the driver dropped input audio")

    activity = _activity(state)
    stages = _stage_state(chain)
    health = BAD if (callback_errors or late) else WARN if (mismatch_problem or underflows or
                                                           overflows) else OK

    def said(verdict: str, reason: str, engine: str, signal: str,
             next_action: str | None = None) -> dict[str, Any]:
        return {
            "state": verdict,
            "verdict": verdict,
            "reason": reason,
            "next": next_action or NEXT.get(verdict, ""),
            "problems": problems,
            "engine": engine,
            "signal": signal,
            "activity": activity,
            "health": health,
            "notice": notice,
            "stages": stages,
        }

    if not state.get("open"):
        return said(ENGINE_OFF,
                    "the engine is not running: the switch on the face reads SET UP",
                    "off", "off")

    device = _device(state)
    if device["closed"]:
        return said(DEVICE_CLOSED,
                    "the engine has no audio device open. Press GO LIVE and it opens one.",
                    "closed", "fault")

    if device["inputs"] is not None and int(device["inputs"]) == 0:
        return said(DEVICE_UNPLUGGED,
                    "the interface reports no inputs, so nothing can arrive: check it is "
                    "connected and powered.",
                    "fault", "fault")

    # A callback that threw is the reason the amp is not armed. Saying STANDBY first would
    # send the player to the switch for a failure the switch did not cause.
    if callback_errors:
        return said(GLITCH,
                    "the audio callback failed and the amp disarmed itself. Run tune; if it "
                    "happens again, raise the block size.",
                    "open", "fault")

    if not state.get("armed"):
        master = state.get("master_db")
        where = (f"parked at {float(master):.0f} dB" if master is not None
                 and float(master) <= PARKED_DB else "on")
        return said(STANDBY,
                    f"the engine is open and silent, master {where}: GO LIVE opens the amp "
                    f"with the master wherever the knob is, so it cannot arrive loud",
                    "open", "standby")

    if state.get("parked_by_idle"):
        return said(PARKED,
                    "the amp parked its own master after a long silence. Raise the master "
                    "(or press GO LIVE) and it is back where you left it.",
                    "open", "parked")

    peak = _peak(reading)
    if clip_of(reading):
        return said(CLIPPING,
                    "the output is hitting the ceiling, back the input gain off",
                    "open", "live")

    if chain is not None and stages["live"] == 0:
        return said(NO_CHAIN,
                    "nothing is in the path, so you would hear the raw interface "
                    "signal. That is what SILENCE leaves behind; GO LIVE re-engages it.",
                    "open", "bypassed")

    if peak <= FLOOR:
        return said(NO_SIGNAL,
                    "armed and monitoring, but nothing is arriving on this input",
                    "open", "silent")

    master_db = reading.get("master_db", state.get("master_db"))
    if master_db is not None and float(master_db) <= PARKED_DB:
        return said(STANDBY,
                    f"armed and hearing the input, but the master is parked at "
                    f"{float(master_db):.0f} dB. Raise the master to hear it.",
                    "open", "standby", "raise the master")

    if late:
        return said(GLITCH,
                    "the amp is live but blocks are reaching the driver late, so playback "
                    "cracks and drags. Run tune.",
                    "open", "live")

    reason = f"armed, monitoring, {device['out_ms']:.1f} ms round trip"
    if stages["live"]:
        reason += f", {stages['live']} stage{'s' if stages['live'] != 1 else ''} live"
    if device["reopened"]:
        return said(DEVICE_REOPENED,
                    "playable, and the audio device had to be reopened: " + reason,
                    "open", "live")
    return said(READY, reason, "open", "live")
