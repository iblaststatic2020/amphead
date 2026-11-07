"""AmpHead tools: the four entry points an agent actually calls.

Nothing here touches REAPER at import time: the bridge is contacted inside handlers only, so
``hermes plugins validate`` can import this plugin with the host closed.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import math
import time

from . import appdecl, port, verdict

TOOLSET = "amphead"

#: The rig this process talks to. Swap AMPHEAD_ENGINE to swap the backend.
RIG = port.active()


def _host() -> str:
    """What to call the thing that hosts the chain, in the player's words.

    Every sentence below used to say REAPER, which is a lie the moment the engine is the
    backend: a player told "REAPER is not running" while their rig is silent will look
    for a window that does not exist.
    """
    return str(getattr(RIG, "label", "REAPER"))

# Below this the master is quiet enough that arming cannot howl. Same floor the bridge enforces.
SAFE_MASTER_DB = port.SAFE_MASTER_DB


def rig_path() -> Path:
    """The rig tab's path, answered by :func:`amphead.rig.rig_path` and nowhere else."""
    from . import rig as rig_module

    return rig_module.rig_path()


def _schema(name: str, description: str, properties: dict[str, Any] | None = None,
            required: list[str] | None = None) -> dict[str, Any]:
    params: dict[str, Any] = {"type": "object", "properties": properties or {}}
    if required:
        params["required"] = required
    params["additionalProperties"] = False
    return {"name": name, "description": description, "parameters": params}


PING_SCHEMA = _schema(
    "amphead_ping",
    "Check whether the amp rig can answer right now: is REAPER installed, is it running, is "
    "the bridge alive, which project tab is the rig, and is the audio device open. Read-only, "
    "cheap, safe to call before anything else.",
)

STATUS_SCHEMA = _schema(
    "amphead_status",
    "Report the rig as it actually is: REAPER state, rig tab path, track count, arm and monitor "
    "state, master level in dB, and audio device buffer, rate and latency. Read-only; never "
    "starts REAPER and never writes.",
)

SETUP_SCHEMA = _schema(
    "amphead_setup",
    "Bring the rig up: start REAPER if it is not running, minimize it to the taskbar, open a rig "
    "tab that Hermes owns, and park the master at -60 dB. The track is left in standby: it is never "
    "armed by this tool, because going live is the player's decision.",
    {"tempo": {"type": "number", "description": "Project tempo in BPM (default 120).",
               "minimum": 20, "maximum": 300},
     "hide": {"type": "boolean",
              "description": "Launch REAPER on a hidden desktop so it has no window at all "
                             "(default false: taskbar icon only)."}},
)

TEARDOWN_SCHEMA = _schema(
    "amphead_teardown",
    "Put the rig away: clear and close the rig tab Hermes owns, leaving any song project alone. "
    "Set stop_host to also quit REAPER when the rig tab was the only thing open.",
    {"stop_host": {"type": "boolean",
                   "description": "Also quit REAPER after closing the rig tab (default false)."}},
)


TONE_SCHEMA = _schema(
    "amphead_tone",
    "Turn a plain-language tone request into sound on the rig. A request the rig's own presets "
    "answer (\"make it crunchier\", \"cleaner\") applies that preset in one undo group and reports "
    "every knob that moved in the plugin's units; a request for a different amp "
    "(\"an amp that sounds like blink182\", \"drop C metal\") loads a saved NAM preset from the "
    "capture library instead, since amp choice lives in the plugin's state chunk and not on a knob.",
    {"query": {"type": "string",
               "description": "What the player asked for, in their own words."},
     "apply": {"type": "boolean",
               "description": "Write the preset into the rig now (default true). False is a dry "
                              "run that only reports the choice."}},
    required=["query"],
)


LIVE_SCHEMA = _schema(
    "amphead_live",
    "Open or close the guitar's path to the speakers. on=true points the track at the input, arms "
    "it and turns monitoring on, but leaves the master parked at -60 dB: the player raises it "
    "themselves, so nothing can howl or blow a speaker the moment the amp comes on. on=false "
    "disarms and parks the master at -60 dB. Going live is audible, so on=true requires confirm=true.",
    {"on": {"type": "boolean", "description": "true goes live, false returns to standby."},
     "confirm": {"type": "boolean",
                 "description": "Required for on=true. Confirms the player wants the amp audible."},
     "master_db": {"type": "number",
                   "description": "Master level in dB for live playing (default -60, i.e. silent: "
                                  "the player ramps it up; floor -60, ceiling 0).",
                   "minimum": -60, "maximum": 0},
     "input": {"type": "string",
               "description": "Track input, counted from 1: \"1\" is the first channel on the "
                              "interface. Omit to leave whatever input REAPER already has, \"stereo\" "
                              "or \"1-2\" for a stereo pair, \"none\" to clear it."},
     "track": {"type": "integer", "description": "Track index, 1 based (default 1).", "minimum": 1}},
)

PANIC_SCHEMA = _schema(
    "amphead_panic",
    "Silence the rig in one call: park the master at -60 dB, disarm the track and bypass every "
    "plugin in the chain, then read all three back and report what is actually true. The meter "
    "keeps working, so the input is still visible while nothing reaches the speakers. restore=true "
    "puts the chain back, still silent at -60 dB unless a level is given, and does not arm the track.",
    {"restore": {"type": "boolean",
                 "description": "Undo the panic: un-bypass the chain and lift the master "
                                "(default false, which silences the rig)."},
     "master_db": {"type": "number",
                   "description": "Master level used when restoring (default -60, silent).",
                   "minimum": -60, "maximum": 0},
     "track": {"type": "integer", "description": "Track index, 1 based (default 1).",
               "minimum": 1}},
)

STANDBY_DB = -60.0


def check_reaper_available() -> bool:
    """True when this backend has a host to point at. Deliberately does not require it to be
    running: setup's whole job is to start it, so requiring a live host here would hide the very
    tool that starts it.

    Catches every rig error, not just NoRig: a backend that hosts its own chain raises NotBuilt
    from find(), and a check that raises is a check that breaks tool registration rather than
    reporting the tool as unavailable.
    """
    try:
        RIG.find()
        return True
    except port.RigError:
        return False


def no_install_reason() -> str:
    """Why there is no host, in the host's own words (the REAPER application declaration).

    One sentence for the three states that mean "nothing to launch": missing, too old, wrong OS.
    Falls back to the old line when hermes_platform is not importable, so a bare checkout still
    explains itself.
    """
    return (appdecl.unavailable_reason()
            or "neither REAPER_EXE nor a standard install path resolved")


def _db_level(amplitude: float) -> str:
    """A player reads dB, not a raw amplitude like 0.014."""
    if amplitude <= 0:
        return "-inf"
    return f"{20 * math.log10(amplitude):.1f}"


def _whole(value: Any) -> Any:
    """REAPER reports I_RECINPUT as a float, and input=1.0 reads like a bug next to input=1."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return value
    return int(number) if number.is_integer() else value


def _no_input(value: Any) -> bool:
    """True when the track is pointed at nothing. REAPER stores that as a negative number, and a
    missing reading is the same thing as far as a player is concerned."""
    try:
        return int(float(value)) < 0
    except (TypeError, ValueError):
        return True


def _fmt(fields: list[tuple[str, Any]]) -> str:
    return " | ".join(f"{k}={v}" for k, v in fields)


def _rig_find() -> Any:
    """Where the host is installed, or ``None`` when the backend has no install to find.

    Asked only on the failure path, and it must never raise there: a backend that hosts its own
    chain has no DAW install, so an unguarded ``find()`` turned "not running" into a traceback.
    """
    try:
        return RIG.find()
    except Exception:
        return None


def _rig_running() -> bool:
    """Is the host process alive? Same rule: a failure here must not replace the sentence."""
    try:
        return bool(RIG.running())
    except Exception:
        return False


def handle_ping(args: dict[str, Any], **_kw) -> str:
    try:
        info = RIG.ping()
    except port.NoRig:
        found = _rig_find()
        where = f"installed at {found}" if found else "no install found"
        return f"{_host()} NOT RUNNING: {where}; call amphead_setup to start it"
    except port.OldRig as exc:
        return f"OLD BRIDGE: {exc}"
    except port.RigBlocked as exc:
        # A heartbeat can outlive the process by a few seconds. Ask the OS before blaming a dialog.
        if not _rig_running():
            found = _rig_find()
            where = f"installed at {found}" if found else "no install found"
            return f"{_host()} NOT RUNNING: {where}; call amphead_setup to start it"
        return f"{_host()} BLOCKED: {exc}"
    except port.RigError as exc:
        if not _rig_running():
            return f"{_host()} NOT RUNNING: call amphead_setup to start it"
        return f"BRIDGE ERROR: {exc}"
    return _fmt([("ok", info.get("ok")), ("proto", info.get("proto")),
                 ("reaper", info.get("version")), ("bridge", info.get("bridge"))])


def handle_status(args: dict[str, Any], **_kw) -> str:
    try:
        state = RIG.state()
    except port.NoRig:
        return f"NO REAPER INSTALL: {no_install_reason()}"
    if state.get("bridge") != "live":
        label = (f"{_host()} BLOCKED" if state.get("bridge") == "down"
                 else f"{_host()} NOT RUNNING")
        return (f"{label} | reason={state.get('reason') or 'no heartbeat'} | "
                f"rig={rig_path().as_posix()} | call amphead_setup")
    fields: list[tuple[str, Any]] = [
        ("project", state.get("project") or "unnamed"),
        ("path", state.get("path") or "unnamed"),
        ("tracks", state.get("tracks")),
    ]
    device = state.get("device") or {}
    fields += [("buffer", device.get("buffer")), ("rate", device.get("rate")),
               ("out_ms", device.get("latency_out_ms")),
               ("device", "closed" if device.get("closed") else "open")]
    try:
        fields.append(("master_db", RIG.master().get("db")))
        if int(state.get("tracks") or 0) >= 1:
            info = RIG.track_info(1)
            fields += [("armed", info.get("armed")), ("monitor", info.get("monitor"))]
        else:
            fields.append(("rig", "empty"))
    except port.RigError:
        pass
    return _fmt(fields)


def handle_setup(args: dict[str, Any], **_kw) -> str:
    path = rig_path()
    try:
        RIG.find()
    except port.RigError as exc:
        # Any rig error means there is no host to set up. Letting NotBuilt out of here would
        # replace the sentence with a traceback in the middle of a tool call.
        return f"NO REAPER INSTALL: {exc}"
    tempo = float(args.get("tempo") or 120.0)
    hide = bool(args.get("hide"))
    try:
        # Setup owns the install as well as the rig: a pane on a machine whose resource folder
        # was rebuilt, or whose REAPER updated and dropped the startup script, should repair
        # itself rather than tell the player to run a command line.
        installed = RIG.install()
    except port.RigError as exc:
        return f"INSTALL FAILED: {exc}"
    try:
        running = RIG.ensure(hidden_desktop=hide)
        if not RIG.host_alive():
            return _fmt([
                ("installed", f"{installed.bridge_path} ({installed.startup_action})"),
                ("state", "BRIDGE NOT ANSWERING"),
                ("fix", "quit REAPER and run setup again: __startup.lua only runs at launch"),
            ])
        if not path.is_file():
            path.parent.mkdir(parents=True, exist_ok=True)
            RIG.prepare_file(path, tempo=tempo)
        tabs_before = len(RIG.tabs_list())
        opened = RIG.open_rig(path.as_posix(), new="1")
        rig = opened.get("path") or path.as_posix()
        if getattr(RIG, "name", "reaper") != "reaper":
            # No tab, no .rpp: naming one would send the player looking for a file that
            # was never written.
            rig = str(RIG.project() or getattr(RIG, "label", "the rig"))
        # The guard is on for file-backed tabs, our own rig included: a write must name the tab it
        # intends to touch, so a stale handle can never land on somebody's song.
        lowered = RIG.set_master(SAFE_MASTER_DB, expect=rig)
        # Boot is silent by construction: the master and the track fader both come up at the
        # floor, so a rig that boots armed still cannot pass signal until a person raises a level.
        track_db = lowered.get("db", SAFE_MASTER_DB)
        try:
            RIG.set_track_level(1, SAFE_MASTER_DB, expect=rig)
        except port.RigError as exc:
            track_db = f"not parked ({exc})"
        tabs_after = len(RIG.tabs_list())
        # Report the track as it actually is, not as the plan says it should be: the template arms
        # and monitors so the chain is testable, and the parked master is what keeps that silent.
        info = RIG.track_info(1)
        # A track pointed at no input reads in the verdict as a dead cable, and looks to a player
        # like a broken lead. Setup points a rig that has none at the first input, and reports the
        # input the way a person counts it, never as the zero REAPER stores for channel one.
        held = info.get("recinput")
        input_line = RIG.describe_input(held)
        repaired = False
        if _no_input(held):
            try:
                RIG.set_input(track=1, spec=1, expect=rig)
                input_line = RIG.describe_input(RIG.track_info(1).get("recinput"))
                repaired = True
            except port.RigError as exc:
                input_line = f"none (refused: {exc})"
    except port.RigError as exc:
        return f"SETUP FAILED: {exc}"
    except port.NoRig as exc:
        return f"NO REAPER INSTALL: {exc}"
    return _fmt([
        ("state", "STANDBY"), ("ui", running.get("ui")), ("rig", rig),
        ("tabs", f"{tabs_before}->{tabs_after}"),
        ("master_db", lowered.get("db")),
        ("track_db", track_db),
        ("input", f"{input_line} (setup pointed it at the first input)" if repaired else input_line),
        ("armed", f"{info.get('armed')} (silent: master parked at {lowered.get('db')} dB)"),
        ("monitor", info.get("monitor")),
        ("next", "the player flips Live; setup never raises the master"),
    ])


def handle_teardown(args: dict[str, Any], **_kw) -> str:
    stop_host = bool(args.get("stop_host"))
    path = rig_path()
    closed = "no rig tab (REAPER not running)"
    try:
        if RIG.host_alive():
            payload = RIG.close_rig(path.as_posix(), timeout=20.0)
            closed = f"mode={payload.get('mode')} tracks_removed={payload.get('tracks_removed')}"
    except port.RigError as exc:
        closed = f"refused: {exc}"
    if stop_host:
        gone = RIG.stop()
        return _fmt([("rig", closed), ("host", "stopped" if gone else "still running")])
    return _fmt([("rig", closed), ("host", "leave running")])


def handle_tone(args: dict[str, Any], **_kw) -> str:
    from . import tones

    query = str(args.get("query") or "").strip()
    if not query:
        return "NO QUERY: say what sound you want, for example \"an amp that sounds like blink182\""
    apply = args.get("apply")
    apply = True if apply is None else bool(apply)

    # A request that names a sound the rig's own presets already carry is answered by applying that
    # preset: one undo group, every knob read back, a diff in the plugin's units. The library path
    # below changes the capture itself and only runs when the preset does not fit this rig.
    from . import presets

    hit = presets.intent(query)
    if hit:
        name, reason = hit
        if not apply:
            return _fmt([("preset", name), ("why", reason), ("apply", "false (dry run)")])
        try:
            applied = presets.apply(name)
        except port.RigError:
            applied = None
        except (LookupError, ValueError):
            applied = None
        if applied is not None and applied.changes:
            head = applied.sentence()
            if applied.refused:
                head += " | refused: " + ", ".join(c.param for c in applied.refused)
            return "\n".join([f"why: {reason}", head] + [c.line() for c in applied.changes])
        # Everything the preset names is missing: fall through to the capture library, which can
        # load a whole amp rather than turn knobs that are not there.
        if applied is not None and not applied.missing:
            return f"why: {reason}\nNOT APPLIED: {name} touched no knob on this rig"

    arch, ranked = tones.best(query)
    if arch is None:
        options = "; ".join(a.key for _, a in ranked[:4])
        return f"NO MATCH for {query!r} | closest={options}"

    try:
        root = tones.root_of(arch)
    except (OSError, KeyError) as exc:
        return f"NOT APPLIED: the preset library is not readable ({exc})"
    lines = [
        _fmt([("archetype", arch.key), ("preset", arch.preset), ("capture", root["amp"]),
              ("ir", root["ir"])]),
        "why: " + arch.why,
    ]
    if arch.tweaks:
        lines.append("moves: " + ", ".join(f"{k} {v:+.1f}" for k, v in arch.tweaks.items()))
    if not apply:
        return "\n".join(lines) + "\ndry run: apply=false"

    try:
        applied = tones.apply_to_live(arch)
    except port.NoRig:
        return "\n".join(lines) + "\nnot applied: REAPER is not running, call amphead_setup first"
    except tones.NoRig as exc:
        return "\n".join(lines) + f"\nnot applied: {exc}"
    except port.RigError as exc:
        return "\n".join(lines) + f"\nnot applied: {exc}"

    lines.append(_fmt([("loaded", Path(applied.project).name), ("track", applied.track),
                       ("capture_now", applied.amp),
                       ("matches", applied.amp == applied.expected_amp)]))
    lines.append("knobs: " + ", ".join(f"{k}={v}" for k, v in applied.knobs.items()))
    return "\n".join(lines)


def active_path() -> str | None:
    """The project the user is looking at. Every write carries it as `expect`."""
    return port.active_project(RIG)


def handle_live(args: dict[str, Any], **_kw) -> str:
    on = args.get("on")
    on = True if on is None else bool(on)
    track = int(args.get("track") or 1)
    try:
        expect = active_path()
    except port.NoRig:
        return "NOT LIVE: REAPER is not running, call amphead_setup first"
    if expect is None:
        return "NOT LIVE: no project is open, call amphead_setup first"

    if not on:
        try:
            payload = RIG.disarm(track=track, expect=expect)
            level = RIG.set_master(db=STANDBY_DB, expect=expect)
        except port.NoRig:
            return "NOT LIVE: REAPER is not running, call amphead_setup first"
        except port.RigError as exc:
            return f"NOT LIVE: {exc}"
        return _fmt([("live", False), ("armed", payload.get("armed")),
                     ("monitor", payload.get("monitor")), ("master_db", level.get("db"))])

    if not args.get("confirm"):
        return ("REFUSED: going live arms the rig. Call again with confirm=true. "
                "The master stays parked at -60 dB; raise it yourself once you hear nothing wrong.")

    db = float(args.get("master_db", STANDBY_DB))
    db = max(STANDBY_DB, min(0.0, db))
    # Input 0 means "leave the track's input alone". Pointing a player's track at input 1 behind
    # their back is the one write that breaks a rig silently, so it only happens when asked.
    spec = str(args.get("input") if args.get("input") is not None else "0").strip().lower()
    retarget = spec not in {"", "0", "keep", "as-is", "unchanged"}

    # REAPER can park the device closed while it sits idle, and a closed device has zero inputs,
    # so the input write would fail for a reason that has nothing to do with the player's rig.
    try:
        device = RIG.ensure_audio()
    except port.RigError as exc:
        return f"NOT LIVE: could not open the audio device: {exc}"
    if int(device.get("inputs") or 0) < 1:
        return ("NOT LIVE: REAPER's audio device is closed, so the track has no input to point at. "
                "Open it once in Options > Preferences > Audio > Device, then call this again.")

    try:
        RIG.set_master(db=db, expect=expect)
        if retarget:
            RIG.set_input(track=track, spec=spec, expect=expect)
        armed = RIG.arm(track=track, monitor=True, allow_loud=True, expect=expect)
        level = RIG.master()
        now = RIG.track_info(track=track, expect=expect)
    except port.NoRig:
        return "NOT LIVE: REAPER is not running, call amphead_setup first"
    except port.RigError as exc:
        return f"NOT LIVE: {exc}"

    return _fmt([("live", True), ("track", track), ("armed", armed.get("armed")),
                 ("monitor", armed.get("monitor")), ("master_db", level.get("db")),
                 ("input", RIG.describe_input(now.get("recinput", spec if retarget else ""))),
                 ("input_changed", retarget),
                 ("clips_at", device.get("output_latency_ms"))])


READY_SCHEMA = _schema(
    "amphead_ready",
    "Judge the whole rig in one call and answer with a single verdict word plus the reasons: is "
    "REAPER alive, is the rig tab there, is the audio device open, is the track armed and "
    "monitoring, is its input selected, is the chain loaded and passing audio, is signal actually "
    "arriving, and is the round trip short enough to play. Read-only; never arms, never writes.",
    {"track": {"type": "integer", "description": "Track index, 1 based (default 1).",
               "minimum": 1},
     "settle_ms": {"type": "integer",
                   "description": "How long to listen for signal before calling it silent "
                                  "(default 600, so one quiet frame cannot report a live rig dead).",
                   "minimum": 0, "maximum": 3000}},
)


def handle_ready(args: dict[str, Any], **_kw) -> str:
    """The rig as a single truth: a word a player can read and the problems behind it."""
    track = int(args.get("track") or 1)
    settle = int(args.get("settle_ms", 600))
    try:
        state = RIG.state()
    except port.NoRig:
        return (f"verdict=NO REAPER INSTALL | reason={no_install_reason()} | "
                "problems=REAPER is missing")

    chain: list[dict] = []
    info: dict = {}
    reading: dict = {}
    if state.get("bridge") == "live":
        try:
            info = RIG.track_info(track=track)
            chain = [{"name": str(fx.get("name")), "enabled": bool(fx.get("enabled", True))}
                     for fx in RIG.fx_list(track=track)]
            # Listen for a moment: a single frame of silence is not the same as a dead input.
            deadline = time.monotonic() + max(0, settle) / 1000.0
            while True:
                sample = RIG.meter(track=track)
                reading = {"l": max(float(reading.get("l") or 0.0), float(sample.get("l") or 0.0)),
                           "r": max(float(reading.get("r") or 0.0), float(sample.get("r") or 0.0)),
                           "clip": bool(reading.get("clip") or sample.get("clip"))}
                # The master belongs in the verdict: an armed rig parked at -60 dB is silent.
                try:
                    master_db = RIG.master().get("db")
                except port.RigError:
                    master_db = None
                if master_db is not None:
                    reading["master_db"] = master_db
                loud = max(reading["l"], reading["r"]) > verdict.FLOOR
                if loud or reading["clip"] or time.monotonic() >= deadline:
                    break
                time.sleep(0.05)
        except port.NoRig:
            # Nothing answered mid-read. Ask the client which silence this is: a quit, a dialog,
            # or a bridge that is not loaded at all. Guessing here is how the pane ends up saying
            # REAPER NOT RUNNING to somebody staring at an open REAPER.
            kind, sentence = RIG.liveness()
            state = {"bridge": {"not-loaded": "gone", "not-running": "not-running"}.get(kind, "down"),
                     "reason": sentence}
        except port.RigError as exc:
            # A read can fail two ways: the wire is wedged (BUSY), or the project in front of
            # REAPER has no track 1 because it is not the rig. state() already decided which one.
            if state.get("rig_tab") is False:
                return _fmt([("verdict", verdict.RIG_TAB_CLOSED),
                             ("reason", f"the project in front of REAPER has no track {track} "
                                        f"({exc}). Run setup to bring the rig tab forward."),
                             ("problems", "the active project is not the rig")])
            return f"verdict=REAPER BUSY | reason={exc} | problems=the bridge refused the read"

    out = verdict.judge(state, info, reading, chain)
    fields = [("verdict", out["verdict"]), ("reason", out["reason"]),
              ("problems", "; ".join(out["problems"]) or "none")]
    if state.get("bridge") == "live":
        fields += [("track", track), ("armed", bool(info.get("armed"))),
                   ("monitor", bool(info.get("monitor"))),
                   ("input", RIG.describe_input(info.get("recinput"))),
                   ("chain", len(chain)), ("live_fx", len([f for f in chain if f["enabled"]])),
                   ("peak_db", _db_level(max(reading.get("l") or 0.0, reading.get("r") or 0.0))),
                   ("out_ms", (state.get("device") or {}).get("latency_out_ms") or 0.0)]
    return _fmt(fields)


def handle_panic(args: dict[str, Any], **_kw) -> str:
    """The dead man's switch: silence first, then prove it with read-backs, never assume it."""
    track = int(args.get("track") or 1)
    restore = bool(args.get("restore"))
    try:
        expect = active_path()
    except port.NoRig:
        return "NOT PANICKED: REAPER is not running, so nothing can reach the speakers anyway"
    if expect is None:
        return "NOT PANICKED: no project is open, so there is no rig to silence"

    try:
        if restore:
            db = max(STANDBY_DB, min(0.0, float(args.get("master_db", STANDBY_DB))))
            RIG.set_master(db=db, expect=expect)
        else:
            # Master first: it is the one control that cannot fail because a plugin refused it.
            RIG.set_master(db=STANDBY_DB, expect=expect)

        # One stubborn plugin must not stop the others from closing: keep going and report the
        # stragglers by name, because "panicked, except the amp" is the sentence that saves ears.
        chain = RIG.fx_list(track=track, expect=expect)
        refused: list[str] = []
        for fx in chain:
            try:
                RIG.fx_bypass(track=track, fx=int(fx["i"]), on=not restore, expect=expect)
            except port.RigError as exc:
                refused.append(f"{fx.get('name')} ({exc})")

        if restore:
            armed = RIG.track_info(track=track, expect=expect)
        else:
            RIG.disarm(track=track, expect=expect)
            armed = RIG.track_info(track=track, expect=expect)

        level = RIG.master()
        after = RIG.fx_list(track=track, expect=expect)
    except port.NoRig:
        return "NOT PANICKED: REAPER stopped answering mid-call"
    except port.RigError as exc:
        return f"PANIC INCOMPLETE: {exc}. Park the master by hand before you keep playing."

    bypassed = [fx.get("name") for fx in after if not fx.get("enabled")]
    live_fx = [fx.get("name") for fx in after if fx.get("enabled")]
    if restore:
        return _fmt([
            ("panicked", False), ("master_db", level.get("db")),
            ("plugins_on", len(live_fx)), ("still_bypassed", len(bypassed)),
            ("armed", armed.get("armed")),
            ("next", "flip Live if you want to play; restore never arms the track for you"),
        ])
    problems = []
    if float(level.get("db") or 0.0) > STANDBY_DB + 0.5:
        problems.append(f"master is at {level.get('db')} dB")
    if armed.get("armed") or armed.get("monitor"):
        problems.append("track is still armed or monitoring")
    if live_fx:
        problems.append("still passing: " + ", ".join(live_fx))
    for item in refused:
        problems.append(f"refused the bypass: {item}")
    return _fmt([
        ("panicked", not problems), ("master_db", level.get("db")),
        ("bypassed", len(bypassed)), ("bypassed_names", ", ".join(bypassed) or "none"),
        ("armed", armed.get("armed")), ("monitor", armed.get("monitor")),
        ("meter", "still live: the input stays visible even with the speakers silent"),
        ("problems", "; ".join(problems) or "none"),
    ])


SET_SCHEMA = _schema(
    "amphead_set",
    "Turn one knob on the amp and prove it moved: resolves the plugin and the parameter by name "
    "(refusing to guess when a name matches several), writes it as one undo group, reads it back, "
    "and only reports success when the value actually landed and the read-back is a real number. "
    "A plugin that accepts a write and ignores it, or reports -inf, comes back as a refusal with "
    "both attempts shown.",
    {"track": {"type": "integer", "description": "Track index, 1 based (default 1).",
               "minimum": 1},
     "fx": {"type": ["string", "integer"],
            "description": "Plugin name, or its index on the track. Names beat indices."},
     "param": {"type": ["string", "integer"],
               "description": "Parameter name, or its index on the plugin."},
     "value": {"type": "number", "description": "The new value."},
     "mode": {"type": "string", "enum": ["auto", "norm", "raw"],
              "description": "auto (default) writes the plugin's normalized 0..1 value, raw "
                             "writes the plugin's own units, norm forces normalized."},
     "unit": {"type": "string",
              "description": "Optional unit the value is in: '%' for a percentage, 'dB' or 'Hz' "
                             "for the plugin's own scale. Naming a unit switches to raw."}},
    ["fx", "param", "value"],
)


def handle_set(args: dict[str, Any], **_kw) -> str:
    """Write one parameter, and answer with the value the plugin actually reports."""
    from . import rig

    try:
        done = rig.set_param(
            args.get("fx"), args.get("param"), float(args.get("value")),
            track=int(args.get("track") or 1),
            mode=str(args.get("mode") or "auto"),
            unit=args.get("unit"),
        )
    except port.NoRig:
        # A rig that hosts its own chain has no REAPER plug-in to poke, but it does have
        # knobs, and they answer through the same port the rest of this module uses.
        setter = getattr(RIG, "set_param", None)
        if setter is None:
            return f"NOT SET: {_host()} is not running, call amphead_setup first"
        try:
            wrote = setter(
                track=int(args.get("track") or 1), fx=int(args.get("fx") or 0),
                param=args.get("param"), value=float(args.get("value")),
                unit=args.get("unit"), mode=str(args.get("mode") or "auto"),
            )
        except (AttributeError, TypeError):
            return f"NOT SET: {_host()} has no knobs to write outside REAPER"
        except port.RigError as exc:
            return f"NOT SET: {exc}"
        return _fmt([
            ("set", wrote.get("landed")), ("fx", wrote.get("fx_name")),
            ("param", wrote.get("param_name")),
            ("wrote", f"{float(args.get('value')):g} ({wrote.get('mode')})"),
            ("reads_back", wrote.get("fmt")), ("norm", round(float(wrote.get("norm") or 0), 4)),
            ("clamped", False), ("retried", False),
        ])
    except rig.WriteRefused as exc:
        # Before ReaperError: a refusal is a rig failure, not "the host would not answer".
        return f"REFUSED: {exc}"
    except port.RigError as exc:
        return f"NOT SET: {exc}"
    except LookupError as exc:
        return f"NOT SET: {exc}"
    except (TypeError, ValueError) as exc:
        return f"NOT SET: {exc}"

    return _fmt([
        ("set", True), ("fx", done.fx_name), ("param", done.param_name),
        ("wrote", f"{done.written:g} ({done.mode})"), ("reads_back", done.fmt),
        ("norm", done.norm), ("clamped", done.clamped), ("retried", done.retried),
    ])


BUILD_SCHEMA = _schema(
    "amphead_build",
    "Build the practice rig in its own tab on disk, then prove what landed: a named track, the amp "
    "on it, the level, optionally an input and arming, in one batch. FX that came with the project "
    "are reported and left alone. Arming is opt-in, because going live is the player's decision, not "
    "a side effect of building.",
    {"amp": {"type": "string",
             "description": "Plugin name to load, as REAPER knows it (default 'NAM Universal')."},
     "input": {"type": ["string", "integer"],
               "description": "Recording input, counted the way the interface's own front panel "
                              "counts: 1 is the first channel. 1..N for a mono channel, 'stereo' or "
                              "'1-2' for the first stereo pair, 'none' for no input. Omit to leave "
                              "the input decision alone."},
     "track_name": {"type": "string", "description": "Name for the new track (default Gtr)."},
     "start_db": {"type": "number", "description": "Starting track level in dB (default -6)."},
     "arm": {"type": "boolean",
             "description": "Arm and monitor the track. Off by default: the Live switch is the "
                            "player's. When on, the master is parked at -60 dB first."},
     "force": {"type": "boolean", "description": "Overwrite an existing rig.rpp."}},
    [],
)


def handle_build(args: dict[str, Any], **_kw) -> str:
    """Build the rig, and answer with the facts that prove it, not with a promise."""
    from . import rig

    try:
        built = rig.build_rig(
            amp=str(args.get("amp") or rig.DEFAULT_AMP),
            input=args.get("input"),
            track_name=str(args.get("track_name") or rig.DEFAULT_TRACK),
            start_db=float(args["start_db"]) if args.get("start_db") is not None else rig.START_DB,
            arm=bool(args.get("arm")),
            force=bool(args.get("force")),
        )
    except port.NoRig:
        return "NOT BUILT: REAPER is not running, call amphead_setup first"
    except rig.RigRefused as exc:
        return f"NOT BUILT: {exc}"
    except port.RigError as exc:
        return f"NOT BUILT: {exc}"
    except (TypeError, ValueError) as exc:
        return f"NOT BUILT: {exc}"

    facts = [
        ("built", True), ("rig", built.path), ("track", f"{built.track} {built.track_name}"),
        ("amp", f"{built.amp} at fx {built.amp_index}"), ("chain", len(built.chain)),
        ("db", built.db), ("armed", built.armed), ("monitor", built.monitor),
        ("input", RIG.describe_input(built.input) if built.input is not None else "none (as asked)"),
        ("seconds", built.seconds),
    ]
    if built.pre_fx:
        facts.append(("already there", ", ".join(built.pre_fx) + " (kept, not deleted)"))
    facts.append(("next", "the player flips Live" if not built.armed
                  else "armed; master parked at -60 dB until the player raises it"))
    return _fmt(facts)


PRESET_SCHEMA = _schema(
    "amphead_preset",
    "Apply a rig preset (clean, crunch, lead) as one move: every knob in one undo group, each one "
    "resolved by name against the live chain and read back afterwards, and a diff of what actually "
    "changed in the plugin's own units. Plugins that are not on this rig are named, not skipped "
    "silently, and a knob that refuses the write is reported as refused rather than counted as done.",
    {"name": {"type": "string",
              "description": "Preset name (clean, crunch, lead) or a path to a preset .json. "
                             "Omit to list what ships with the plugin."},
     "track": {"type": "integer", "description": "Track index, 1 based (default 1).",
               "minimum": 1}},
    [],
)


def handle_preset(args: dict[str, Any], **_kw) -> str:
    """Apply a preset, and answer with the knobs that really moved."""
    from . import presets, rig

    name = args.get("name")
    if not name:
        rows = presets.catalog()
        if not rows:
            return f"NO PRESETS: {presets.PRESETS_DIR} holds none"
        return " | ".join(f"{row['name']}: {row['targets']}" for row in rows)

    try:
        applied = presets.apply(str(name), track=int(args.get("track") or 1))
    except port.NoRig:
        return "NOT APPLIED: REAPER is not running, call amphead_setup first"
    except LookupError as exc:
        return f"NOT APPLIED: {exc}"
    except ValueError as exc:
        return f"NOT APPLIED: {exc}"
    except rig.RigRefused as exc:
        return f"NOT APPLIED: {exc}"
    except port.RigError as exc:
        return f"NOT APPLIED: {exc}"

    lines = presets.diff_lines(applied)
    head = applied.sentence()
    if applied.refused:
        head += " | refused: " + ", ".join(f"{c.param} on {c.fx}" for c in applied.refused)
    return head + "\n" + "\n".join(lines)


AB_SCHEMA = _schema(
    "amphead_ab",
    "A/B the rig honestly. snapshot writes down the whole chain and every knob in raw, normalized "
    "and formatted form (read-only: no undo point). compare prints exactly what differs between two "
    "snapshots in the plugin's own units, and says 'identical' when nothing moved. Take a snapshot, "
    "apply a preset or a tweak, snapshot again, and compare; undo and compare again to prove undo "
    "put everything back.",
    {"action": {"type": "string", "enum": ["snapshot", "compare", "list"],
                "description": "snapshot (default), compare, or list what is on disk."},
     "label": {"type": "string",
               "description": "Snapshot name. For compare, the earlier snapshot; default 'a'."},
     "other": {"type": "string",
               "description": "For compare: the later snapshot name (default 'a' vs 'b')."},
     "track": {"type": "integer", "description": "Track index, 1 based (default 1).",
               "minimum": 1}},
    [],
)


def handle_ab(args: dict[str, Any], **_kw) -> str:
    """Take a snapshot, or show what really differs between two."""
    from . import snapshots

    action = str(args.get("action") or "snapshot").strip().lower()
    track = int(args.get("track") or 1)
    label = str(args.get("label") or "").strip()

    if action in {"list", "ls"}:
        rows = snapshots.catalog()
        if not rows:
            return f"NO SNAPSHOTS: {snapshots.snapshots_dir()} is empty"
        return "\n".join(
            f"{row['label']} | {row['at']} | plugins={row['plugins']} | knobs={row['knobs']} | "
            f"db={row['db']:.1f}" for row in rows)

    if action == "compare":
        left = label or "a"
        right = str(args.get("other") or "b").strip()
        try:
            return snapshots.summary(left, right)
        except LookupError as exc:
            return f"NOT COMPARED: {exc}"

    try:
        snap = snapshots.capture(label or "a", track=track)
    except port.NoRig:
        return "NOT SNAPSHOT: REAPER is not running, call amphead_setup first"
    except port.RigError as exc:
        return f"NOT SNAPSHOT: {exc}"
    return snap.sentence() + f"\nnext: amphead_ab action=compare label={snap.label} other=<later>"


def run_call(cmd: str, **kwargs) -> dict:
    """Small indirection so tests can stub the wire without a live host."""
    return RIG.debug_call(cmd, **kwargs)


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
    ("amphead_build", BUILD_SCHEMA, handle_build, "🏗️"),
    ("amphead_preset", PRESET_SCHEMA, handle_preset, "🎸"),
    ("amphead_ab", AB_SCHEMA, handle_ab, "🔀"),
)

# ---- the shape the host sees -------------------------------------------------------------
# The plugin guide asks a handler to return a JSON string; the panes, the tests and the sentences a
# player reads are all built on the handler returning a line of prose. Both are true at once: the
# handlers above keep answering the way they always did, and the function the host registers wraps
# that answer in JSON. Nothing a person reads changes, and the agent gets named fields instead of a
# pipe-delimited line it would have to parse.
FAILURE_WORDS = ("REFUSED", "NOT", "CANNOT", "ERROR", "NO ")


def _ok(summary: str) -> bool:
    """Whether a sentence reports success, judged on its opening clause.

    A handler returns prose, so the only signal available is the words it leads with. The opening
    clause (everything before the first colon) is where every handler puts its verdict, and looking
    only there keeps a caveat later in the sentence ("... nothing is clipping, nothing is NOT
    parked") from flipping the answer. `REAPER NOT RUNNING: installed at ...` is a failed ping even
    though it does not begin with a failure word, which is why NOT is read inside the clause too.
    """
    clause = summary.strip().split(":")[0].upper()
    if clause.startswith(FAILURE_WORDS):
        return False
    return " NOT " not in f" {clause} "


def json_answer(name: str, summary: str) -> str:
    """One JSON string: the tool, the sentence, whether it failed, and the numbers it carried."""
    payload: dict[str, Any] = {
        "tool": name,
        "ok": _ok(summary),
        "summary": summary,
    }
    fields: dict[str, str] = {}
    for part in summary.split("|"):
        key, sep, value = part.partition("=")
        key = key.strip()
        if sep and key and len(key) <= 40 and "\n" not in key:
            fields[key] = value.strip()
    if fields:
        payload["fields"] = fields
    return json.dumps(payload, ensure_ascii=False)


def call_shape(name: str, handler):
    """The handler the host registers: the same work, answered as one JSON string."""

    def call(args: dict[str, Any]) -> str:
        return json_answer(name, handler(args))

    call.__name__ = f"{name}_json"
    call.__doc__ = handler.__doc__
    return call


def json_tools() -> list[tuple[str, dict, Any, str]]:
    """TOOLS, with each handler wrapped. The raw handlers stay as they are for the panes."""
    return [(name, schema, call_shape(name, handler), emoji) for name, schema, handler, emoji in TOOLS]
