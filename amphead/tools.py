"""AmpHead tools: the four entry points an agent actually calls.

Nothing here touches REAPER at import time: the bridge is contacted inside handlers only, so
``hermes plugins validate`` can import this plugin with the host closed.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from . import bridge, launch
from .rigfile import write_template

TOOLSET = "amphead"

# Below this the master is quiet enough that arming cannot howl. Same floor the bridge enforces.
SAFE_MASTER_DB = bridge.ARM_SAFE_MASTER_DB


def rig_path() -> Path:
    env = os.environ.get("AMPHEAD_RIG")
    if env:
        return Path(env)
    base = os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
    return Path(base) / "hermes" / "amphead" / "rig" / "amphead.rpp"


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
    "Turn a plain-language tone request (\"an amp that sounds like blink182\", \"jazz clean\", "
    "\"drop C metal\") into a saved NAM preset, report which capture and IR it uses and how the "
    "knobs sit, and load it into the rig's NAM Universal. Returns the archetype, the .nam capture "
    "path, the preset file and the knob values read back from the plugin after the write.",
    {"query": {"type": "string",
               "description": "What the player asked for, in their own words."},
     "apply": {"type": "boolean",
               "description": "Write the preset into the rig now (default true). False is a dry "
                              "run that only reports the choice."}},
    required=["query"],
)


LIVE_SCHEMA = _schema(
    "amphead_live",
    "Open or close the guitar's path to the speakers. on=true sets the master to the requested "
    "level, points the track at the input, arms it and turns input monitoring on; on=false disarms "
    "and parks the master at -60 dB. Going live is audible, so on=true requires confirm=true.",
    {"on": {"type": "boolean", "description": "true goes live, false returns to standby."},
     "confirm": {"type": "boolean",
                 "description": "Required for on=true. Confirms the player wants the amp audible."},
     "master_db": {"type": "number",
                   "description": "Master level in dB for live playing (default -18, floor -60, "
                                  "ceiling 0).",
                   "minimum": -60, "maximum": 0},
     "input": {"type": "string",
               "description": "Track input: \"0\" (default) leaves whatever input REAPER already has, "
                              "\"1\" for mono input 1, \"1+2\" for a stereo pair, \"none\" to clear it."},
     "track": {"type": "integer", "description": "Track index, 1 based (default 1).", "minimum": 1}},
)

PANIC_SCHEMA = _schema(
    "amphead_panic",
    "Silence the rig in one call: park the master at -60 dB, disarm the track and bypass every "
    "plugin in the chain, then read all three back and report what is actually true. The meter "
    "keeps working, so the input is still visible while nothing reaches the speakers. restore=true "
    "puts the chain back and lifts the master to the given level, without arming the track.",
    {"restore": {"type": "boolean",
                 "description": "Undo the panic: un-bypass the chain and lift the master "
                                "(default false, which silences the rig)."},
     "master_db": {"type": "number",
                   "description": "Master level used when restoring (default -18).",
                   "minimum": -60, "maximum": 0},
     "track": {"type": "integer", "description": "Track index, 1 based (default 1).",
               "minimum": 1}},
)

STANDBY_DB = -60.0


def check_reaper_available() -> bool:
    """True when REAPER is installed. Deliberately does not require it to be running: setup's
    whole job is to start it, so requiring a live host here would hide the tool that starts it."""
    try:
        launch.find_reaper()
        return True
    except launch.NoReaperInstall:
        return False


def _whole(value: Any) -> Any:
    """REAPER reports I_RECINPUT as a float, and input=1.0 reads like a bug next to input=1."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return value
    return int(number) if number.is_integer() else value


def _fmt(fields: list[tuple[str, Any]]) -> str:
    return " | ".join(f"{k}={v}" for k, v in fields)


def handle_ping(args: dict[str, Any], **_kw) -> str:
    try:
        info = bridge.ping()
    except bridge.NoReaper:
        return ("REAPER NOT RUNNING: installed at " + str(launch.find_reaper())
                + "; call amphead_setup to start it")
    except bridge.OldBridge as exc:
        return f"OLD BRIDGE: {exc}"
    except bridge.BridgeBlocked as exc:
        # A heartbeat can outlive the process by a few seconds. Ask the OS before blaming a dialog.
        if not launch.running():
            return ("REAPER NOT RUNNING: installed at " + str(launch.find_reaper())
                    + "; call amphead_setup to start it")
        return f"REAPER BLOCKED: {exc}"
    except bridge.ReaperError as exc:
        if not launch.running():
            return "REAPER NOT RUNNING: call amphead_setup to start it"
        return f"BRIDGE ERROR: {exc}"
    return _fmt([("ok", info.get("ok")), ("proto", info.get("proto")),
                 ("reaper", info.get("version")), ("bridge", info.get("bridge"))])


def handle_status(args: dict[str, Any], **_kw) -> str:
    try:
        state = launch.state()
    except launch.NoReaperInstall:
        return "NO REAPER INSTALL: neither REAPER_EXE nor a standard install path resolved"
    if state.get("bridge") != "live":
        label = "REAPER BLOCKED" if state.get("bridge") == "down" else "REAPER NOT RUNNING"
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
        fields.append(("master_db", bridge.master().get("db")))
        if int(state.get("tracks") or 0) >= 1:
            info = bridge.track_info(1)
            fields += [("armed", info.get("armed")), ("monitor", info.get("monitor"))]
        else:
            fields.append(("rig", "empty"))
    except bridge.ReaperError:
        pass
    return _fmt(fields)


def handle_setup(args: dict[str, Any], **_kw) -> str:
    path = rig_path()
    try:
        launch.find_reaper()
    except launch.NoReaperInstall as exc:
        return f"NO REAPER INSTALL: {exc}"
    tempo = float(args.get("tempo") or 120.0)
    hide = bool(args.get("hide"))
    try:
        running = launch.ensure_running(hidden_desktop=hide)
        if not path.is_file():
            path.parent.mkdir(parents=True, exist_ok=True)
            write_template(path, tempo=tempo)
        tabs_before = len(bridge.tabs_list())
        opened = bridge.open_rig(path.as_posix(), new="1")
        rig = opened.get("path") or path.as_posix()
        # The guard is on for file-backed tabs, our own rig included: a write must name the tab it
        # intends to touch, so a stale handle can never land on somebody's song.
        lowered = bridge.call("MASTER_VOL", db=SAFE_MASTER_DB, expect=rig)
        tabs_after = len(bridge.tabs_list())
        # Report the track as it actually is, not as the plan says it should be: the template arms
        # and monitors so the chain is testable, and the parked master is what keeps that silent.
        info = bridge.track_info(1)
    except bridge.ReaperError as exc:
        return f"SETUP FAILED: {exc}"
    except launch.NoReaperInstall as exc:
        return f"NO REAPER INSTALL: {exc}"
    return _fmt([
        ("state", "STANDBY"), ("ui", running.get("ui")), ("rig", path.as_posix()),
        ("tabs", f"{tabs_before}->{tabs_after}"),
        ("master_db", lowered.get("db")),
        ("armed", f"{info.get('armed')} (silent: master parked at {lowered.get('db')} dB)"),
        ("monitor", info.get("monitor")),
        ("next", "the player flips Live; setup never raises the master"),
    ])


def handle_teardown(args: dict[str, Any], **_kw) -> str:
    stop_host = bool(args.get("stop_host"))
    path = rig_path()
    closed = "no rig tab (REAPER not running)"
    try:
        if launch.bridge_alive():
            payload = bridge.call("PROJ_TAB_CLOSE", expect=path.as_posix(), timeout=20.0)
            closed = f"mode={payload.get('mode')} tracks_removed={payload.get('tracks_removed')}"
    except bridge.ReaperError as exc:
        closed = f"refused: {exc}"
    if stop_host:
        gone = launch.stop()
        return _fmt([("rig", closed), ("host", "stopped" if gone else "still running")])
    return _fmt([("rig", closed), ("host", "leave running")])


def handle_tone(args: dict[str, Any], **_kw) -> str:
    from . import tones

    query = str(args.get("query") or "").strip()
    if not query:
        return "NO QUERY: say what sound you want, for example \"an amp that sounds like blink182\""
    apply = args.get("apply")
    apply = True if apply is None else bool(apply)

    arch, ranked = tones.best(query)
    if arch is None:
        options = "; ".join(a.key for _, a in ranked[:4])
        return f"NO MATCH for {query!r} | closest={options}"

    root = tones.root_of(arch)
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
    except bridge.NoReaper:
        return "\n".join(lines) + "\nnot applied: REAPER is not running, call amphead_setup first"
    except tones.NoRig as exc:
        return "\n".join(lines) + f"\nnot applied: {exc}"
    except bridge.ReaperError as exc:
        return "\n".join(lines) + f"\nnot applied: {exc}"

    lines.append(_fmt([("loaded", Path(applied.project).name), ("track", applied.track),
                       ("capture_now", applied.amp),
                       ("matches", applied.amp == applied.expected_amp)]))
    lines.append("knobs: " + ", ".join(f"{k}={v}" for k, v in applied.knobs.items()))
    return "\n".join(lines)


def active_path() -> str | None:
    """The project the user is looking at. Every write carries it as `expect`."""
    for tab in bridge.tabs_list():
        if tab.get("active") and tab.get("path"):
            return str(tab["path"])
    return None


def handle_live(args: dict[str, Any], **_kw) -> str:
    on = args.get("on")
    on = True if on is None else bool(on)
    track = int(args.get("track") or 1)
    try:
        expect = active_path()
    except bridge.NoReaper:
        return "NOT LIVE: REAPER is not running, call amphead_setup first"
    if expect is None:
        return "NOT LIVE: no project is open, call amphead_setup first"

    if not on:
        try:
            payload = bridge.disarm(track=track, expect=expect)
            level = bridge.master(db=STANDBY_DB, expect=expect)
        except bridge.NoReaper:
            return "NOT LIVE: REAPER is not running, call amphead_setup first"
        except bridge.ReaperError as exc:
            return f"NOT LIVE: {exc}"
        return _fmt([("live", False), ("armed", payload.get("armed")),
                     ("monitor", payload.get("monitor")), ("master_db", level.get("db"))])

    if not args.get("confirm"):
        return ("REFUSED: going live makes the amp audible. Call again with confirm=true, "
                "and pick a master_db you can live with (default -18).")

    db = float(args.get("master_db", -18.0))
    db = max(STANDBY_DB, min(0.0, db))
    # Input 0 means "leave the track's input alone". Pointing a player's track at input 1 behind
    # their back is the one write that breaks a rig silently, so it only happens when asked.
    spec = str(args.get("input") if args.get("input") is not None else "0").strip().lower()
    retarget = spec not in {"", "0", "keep", "as-is", "unchanged"}

    # REAPER can park the device closed while it sits idle, and a closed device has zero inputs,
    # so the input write would fail for a reason that has nothing to do with the player's rig.
    try:
        device = launch.ensure_audio()
    except bridge.ReaperError as exc:
        return f"NOT LIVE: could not open the audio device: {exc}"
    if int(device.get("inputs") or 0) < 1:
        return ("NOT LIVE: REAPER's audio device is closed, so the track has no input to point at. "
                "Open it once in Options > Preferences > Audio > Device, then call this again.")

    try:
        bridge.master(db=db, expect=expect)
        if retarget:
            bridge.set_input(track=track, spec=spec, expect=expect)
        armed = bridge.arm(track=track, monitor=True, allow_loud=True, expect=expect)
        level = bridge.master()
        now = bridge.track_info(track=track, expect=expect)
    except bridge.NoReaper:
        return "NOT LIVE: REAPER is not running, call amphead_setup first"
    except bridge.ReaperError as exc:
        return f"NOT LIVE: {exc}"

    return _fmt([("live", True), ("track", track), ("armed", armed.get("armed")),
                 ("monitor", armed.get("monitor")), ("master_db", level.get("db")),
                 ("input", _whole(now.get("recinput", spec if retarget else "unchanged"))),
                 ("input_changed", retarget),
                 ("clips_at", device.get("output_latency_ms"))])


def handle_panic(args: dict[str, Any], **_kw) -> str:
    """The dead man's switch: silence first, then prove it with read-backs, never assume it."""
    track = int(args.get("track") or 1)
    restore = bool(args.get("restore"))
    try:
        expect = active_path()
    except bridge.NoReaper:
        return "NOT PANICKED: REAPER is not running, so nothing can reach the speakers anyway"
    if expect is None:
        return "NOT PANICKED: no project is open, so there is no rig to silence"

    try:
        if restore:
            db = max(STANDBY_DB, min(0.0, float(args.get("master_db", -18.0))))
            bridge.master(db=db, expect=expect)
        else:
            # Master first: it is the one control that cannot fail because a plugin refused it.
            bridge.master(db=STANDBY_DB, expect=expect)

        # One stubborn plugin must not stop the others from closing: keep going and report the
        # stragglers by name, because "panicked, except the amp" is the sentence that saves ears.
        chain = bridge.fx_list(track=track, expect=expect)
        refused: list[str] = []
        for fx in chain:
            try:
                bridge.fx_bypass(track=track, fx=int(fx["i"]), on=not restore, expect=expect)
            except bridge.ReaperError as exc:
                refused.append(f"{fx.get('name')} ({exc})")

        if restore:
            armed = bridge.track_info(track=track, expect=expect)
        else:
            bridge.disarm(track=track, expect=expect)
            armed = bridge.track_info(track=track, expect=expect)

        level = bridge.master()
        after = bridge.fx_list(track=track, expect=expect)
    except bridge.NoReaper:
        return "NOT PANICKED: REAPER stopped answering mid-call"
    except bridge.ReaperError as exc:
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


def run_call(cmd: str, **kwargs) -> dict:
    """Small indirection so tests can stub the wire without a live host."""
    return bridge.call(cmd, **kwargs)


TOOLS = (
    ("amphead_ping", PING_SCHEMA, handle_ping, "🎸"),
    ("amphead_status", STATUS_SCHEMA, handle_status, "📊"),
    ("amphead_setup", SETUP_SCHEMA, handle_setup, "🎛️"),
    ("amphead_teardown", TEARDOWN_SCHEMA, handle_teardown, "⏏️"),
    ("amphead_tone", TONE_SCHEMA, handle_tone, "🎚️"),
    ("amphead_live", LIVE_SCHEMA, handle_live, "🔊"),
    ("amphead_panic", PANIC_SCHEMA, handle_panic, "⏹️"),
)
