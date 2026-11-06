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
               "description": "Track input: \"1\" for mono input 1, \"1+2\" for a stereo pair "
                              "(default 1)."},
     "track": {"type": "integer", "description": "Track index, 1 based (default 1).", "minimum": 1}},
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
    except bridge.ReaperError as exc:
        return f"SETUP FAILED: {exc}"
    except launch.NoReaperInstall as exc:
        return f"NO REAPER INSTALL: {exc}"
    return _fmt([
        ("state", "STANDBY"), ("ui", running.get("ui")), ("rig", path.as_posix()),
        ("tabs", f"{tabs_before}->{tabs_after}"),
        ("master_db", lowered.get("db")), ("armed", False),
        ("next", "the player flips Live; nothing here arms the track"),
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
    spec = str(args.get("input") or "1")

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
        bridge.set_input(track=track, spec=spec, expect=expect)
        armed = bridge.arm(track=track, monitor=True, allow_loud=True, expect=expect)
        level = bridge.master()
    except bridge.NoReaper:
        return "NOT LIVE: REAPER is not running, call amphead_setup first"
    except bridge.ReaperError as exc:
        return f"NOT LIVE: {exc}"

    return _fmt([("live", True), ("track", track), ("armed", armed.get("armed")),
                 ("monitor", armed.get("monitor")), ("master_db", level.get("db")),
                 ("input", spec), ("clips_at", device.get("output_latency_ms"))])


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
)
