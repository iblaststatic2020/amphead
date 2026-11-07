"""Writes that can prove they landed.

A plugin can accept a write and do nothing, or land on a value that formats as ``-inf``. The real
ReaEQ gain does exactly that at 0.35 normalized: the slider moves, the read-back is ``-inf``, and a
caller that only checks for an error report happily reports success. So every write here follows the
same order: read the parameter, pick a mode, clamp and say so, write as one undo group, read it back,
accept only if the number moved where it was asked and the formatted value is not garbage, retry once
in the other mode, and record both attempts in ``log.jsonl``. A write that cannot be proven is refused
with both read-backs shown, never reported as done.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, asdict
from pathlib import Path
import time
from typing import Any

from . import bridge, resolver, rigfile

TOLERANCE = 0.005

# Values a plugin prints when it is not really reporting a number.
BAD_FORMATS = {"-inf", "inf", "+inf", "nan", "?", "", "n/a", "--"}


@dataclass(frozen=True)
class Write:
    """What was asked, what was written, and what the plugin said afterwards."""

    fx: int
    fx_name: str
    param: int
    param_name: str
    requested: float
    written: float
    mode: str
    norm_wanted: float
    norm: float
    fmt: str
    clamped: bool = False
    retried: bool = False
    verdict: str = "ok"
    reason: str = ""

    def sentence(self) -> str:
        bits = [f"{self.param_name} = {self.fmt}"]
        if self.clamped:
            bits.append(f"clamped from {self.requested:g}")
        if self.retried:
            bits.append(f"landed on the {self.mode} retry")
        if self.verdict != "ok":
            bits.append(f"refused: {self.reason}")
        return " | ".join(bits)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class WriteRefused(bridge.CommandFailed):
    """The plugin took the write and would not prove it took the write."""


def default_log() -> Path:
    env = os.environ.get("AMPHEAD_LOG")
    if env:
        return Path(env)
    base = os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
    return Path(base) / "hermes" / "amphead" / "log.jsonl"


def _append_log(path: Path | str | None, record: dict[str, Any]) -> None:
    target = Path(path) if path else default_log()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        # A full disk must not turn a refused write into a crash; the verdict is still returned.
        pass


def read_param(fx: int, param: int, track: int = 1, **kwargs: Any) -> dict[str, Any]:
    """One parameter as REAPER sees it: name, min, max, norm, raw, formatted."""
    return bridge.call("FX_INFO", track=track, fx=fx, param=param, **kwargs)


def is_garbage(fmt: Any) -> bool:
    """True when the formatted value is not a number a player can trust."""
    text = str(fmt or "").strip().lower()
    return text in BAD_FORMATS or text.startswith("nan") or text.endswith("-inf")


def _resolve_fx(fx: int | str, track: int) -> tuple[int, str, list[dict]]:
    """Index and name for the FX, re-read from the live chain so an index is never trusted alone."""
    chain = bridge.fx_list(track=track)
    if isinstance(fx, int) or (isinstance(fx, str) and fx.strip().isdigit()):
        index = int(fx)
        found = [item for item in resolver.chain_items(chain) if item[0] == index]
        if not found:
            raise LookupError(f"no plugin at index {index} on track {track}")
        return index, found[0][1], chain
    outcome = resolver.resolve_fx(str(fx), chain=chain)
    if not isinstance(outcome, resolver.Resolved):
        raise LookupError(outcome.sentence())
    # The chain was just read, so confirm the resolved index still holds that name.
    again = resolver.recheck(chain, outcome)
    if not isinstance(again, resolver.Resolved):
        raise LookupError(again.sentence())
    return again.index, again.name, chain


def _resolve_param(param: int | str, fx: int, track: int) -> tuple[int, str]:
    params = bridge.fx_params(track=track, fx=fx)
    if isinstance(param, int) or (isinstance(param, str) and param.strip().isdigit()):
        index = int(param)
        found = [item for item in resolver.param_items(params) if item[0] == index]
        if not found:
            raise LookupError(f"no parameter at index {index} on fx {fx}")
        return index, found[0][1]
    outcome = resolver.resolve_param(str(param), params=params)
    if not isinstance(outcome, resolver.Resolved):
        raise LookupError(outcome.sentence())
    return outcome.index, outcome.name


def _plan(value: float, info: dict[str, Any], mode: str,
          unit: str | None) -> tuple[float, float, str, bool]:
    """Decide what to write: (wanted_norm, amount, mode, clamped)."""
    low = float(info.get("min", 0.0))
    high = float(info.get("max", 1.0))
    span = high - low

    percent = unit in {"%", "percent", "pct"}
    chosen = mode
    if chosen == "auto":
        # Raw units differ per plugin, so normalized is the default. A caller who names the
        # plugin's own unit (dB, Hz) means the value they can read off the plugin; a percentage
        # is how people say a normalized value, so that one stays normalized.
        chosen = "raw" if unit and not percent else "norm"

    if chosen == "norm":
        wanted = value / 100.0 if percent else value
        amount = wanted
        clamped = wanted < 0.0 or wanted > 1.0
        wanted = min(1.0, max(0.0, wanted))
        return wanted, wanted, "norm", clamped

    clamped = value < low or value > high
    amount = min(high, max(low, value))
    wanted = (amount - low) / span if span else 0.0
    return wanted, amount, "raw", clamped


def _write_once(amount: float, mode: str, fx: int, param: int, track: int,
                expect: str | None, label: str) -> dict[str, Any]:
    """One BATCH of one write, which is the only way to get exactly one undo point."""
    payload = bridge.batch(
        [("FX_SETV", {"track": track, "fx": fx, "param": param,
                      "value": f"{amount:.6f}", "mode": mode})],
        label=label,
        **bridge.guard(expect),
    )
    if not payload.get("ok"):
        failed = payload.get("failed_at")
        raise bridge.CommandFailed(payload, f"write failed at op {failed}")
    return payload


def set_param(fx: int | str, param: int | str, value: float, *, track: int = 1,
              mode: str = "auto", unit: str | None = None, expect: str | None = None,
              log_path: Path | str | None = None, **kwargs: Any) -> Write:
    """Write one parameter and accept it only if the read-back proves it landed."""
    if mode not in {"auto", "norm", "raw"}:
        raise ValueError(f"mode must be auto, norm or raw, not {mode!r}")

    if expect is None:
        # The bridge refuses an unguarded write, and rightly so: the guard is what keeps a
        # write from landing in whichever tab happens to be in front.
        expect = bridge.active_project()

    fx_index, fx_name, _chain = _resolve_fx(fx, track)
    param_index, param_name = _resolve_param(param, fx_index, track)
    info = read_param(fx_index, param_index, track=track, **kwargs)

    wanted_norm, amount, chosen, clamped = _plan(float(value), info, mode, unit)
    low = float(info.get("min", 0.0))
    span = float(info.get("max", 1.0)) - low
    # The retry is the same value expressed in the other mode's units, so a plugin that only
    # honours one of the two write paths still gets a fair second chance.
    other_mode = "raw" if chosen == "norm" else "norm"
    other_amount = (low + wanted_norm * span) if other_mode == "raw" else wanted_norm
    plan = [(chosen, amount), (other_mode, other_amount)]

    label = f"Hermes: {param_name}"
    attempts: list[dict[str, Any]] = []
    last: dict[str, Any] | None = None

    for attempt, (this_mode, this_amount) in enumerate(plan):
        last = _write_once(this_amount, this_mode, fx_index, param_index, track, expect, label)
        after = read_param(fx_index, param_index, track=track, **kwargs)
        norm_after = float(after.get("norm", 0.0))
        fmt = str(after.get("fmt", ""))
        good = abs(norm_after - wanted_norm) <= TOLERANCE and not is_garbage(fmt)

        attempts.append({
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "track": track,
            "fx": fx_index,
            "fx_name": fx_name,
            "param": param_index,
            "param_name": param_name,
            "requested": float(value),
            "written": this_amount,
            "mode": this_mode,
            "norm_wanted": round(wanted_norm, 6),
            "norm": round(norm_after, 6),
            "fmt": fmt,
            "verdict": "ok" if good else "rejected",
            "reason": "" if good else ("formatted value is not a number"
                                       if is_garbage(fmt) else "value did not move"),
        })
        _append_log(log_path, attempts[-1])

        result = Write(
            fx=fx_index, fx_name=fx_name, param=param_index, param_name=param_name,
            requested=float(value), written=this_amount, mode=this_mode,
            norm_wanted=round(wanted_norm, 6), norm=round(norm_after, 6), fmt=fmt,
            clamped=clamped, retried=attempt == 1, verdict="ok" if good else "refused",
            reason=attempts[-1]["reason"],
        )
        if good:
            return result

    shown = " then ".join(f"{a['mode']} {a['written']:.4f} read back {a['fmt'] or '-'} "
                          f"(norm {a['norm']:.3f})" for a in attempts)
    why = attempts[-1]["reason"] if attempts else "no attempt was made"
    raise WriteRefused(last or {},
                       f"{param_name} on {fx_name} refused the write: {shown}; {why}")

# --- Rig identity: a tab that has a path on disk, so every write can be guarded ------------

DEFAULT_AMP = "NAM Universal"
DEFAULT_TRACK = "Gtr"
DEFAULT_INPUT = 1
START_DB = -6.0


@dataclass
class Built:
    """A rig tab that exists, and the facts that prove what is on it."""

    path: str
    track: int
    track_name: str
    amp: str
    amp_index: int
    chain: list[str]
    pre_fx: list[str]
    input: int | None
    db: float
    armed: bool
    monitor: bool
    snapshot: str
    seconds: float
    created_file: bool

    def sentence(self) -> str:
        bits = [
            f"rig={self.path}",
            f"track={self.track} {self.track_name}",
            f"amp={self.amp} at fx {self.amp_index}",
            f"chain={len(self.chain)}",
            f"db={self.db:g}",
            f"armed={self.armed}",
            f"monitor={self.monitor}",
            f"in={self.input if self.input is not None else 'untouched'}",
            f"{self.seconds:.2f}s",
        ]
        return " | ".join(bits)


class RigRefused(bridge.CommandFailed):
    """The rig could not be built as asked, and nothing is left half-built."""


#: The name the host's storage convention knows this plugin by.
PLUGIN_NAME = "amphead"


def _host_data_dir() -> Path | None:
    """``<hermes home>/plugin-data/amphead``, when the host library is importable.

    ``plugin_data_dir()`` resolves the active profile on every call, which is the whole point: the
    hand-built path this replaces put two profiles on one machine into the same rig, log and
    snapshots. The import stays inside the function so the package still imports with the host
    closed (a test run, a checkout without Hermes), where the appdata fallback answers instead.
    """
    try:
        from plugins.plugin_storage import plugin_data_dir
    except ImportError:
        return None
    try:
        return plugin_data_dir(PLUGIN_NAME)
    except Exception:  # an older host without the seam, or a refused name: fall back, never raise
        return None


def _legacy_data_dir() -> Path:
    """The path this module used to build by hand, kept as the fallback and as the migration source."""
    base = os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
    return Path(base) / "hermes" / "amphead"


def _inherit_legacy_home(target: Path) -> None:
    """Copy a pre-profile home into the new one, once, and leave the old copy where it is.

    Someone who has been playing through this plugin has a rig tab, snapshots and a log in the old
    folder. Making them find it again, or silently starting with an empty home while their rig file
    sits one directory over, is the kind of thing that reads as data loss. So: copy if the new home
    is empty, keep the original (deleting a player's files to tidy up is worse than a stale folder),
    and never raise into a caller that only asked where to write.
    """
    legacy = _legacy_data_dir()
    try:
        if legacy == target or not legacy.is_dir():
            return
        if any(target.iterdir()):
            return
        shutil.copytree(legacy, target, dirs_exist_ok=True)
    except OSError:
        return


def data_dir() -> Path:
    """Where amphead keeps its state: rig tab, snapshots, log and demo flag, one folder.

    AMPHEAD_HOME wins outright: the tests point it at a scratch directory and expect nothing else
    to answer. Otherwise the host's per-plugin data directory, then the legacy appdata path.
    """
    env = os.environ.get("AMPHEAD_HOME")
    if env:
        return Path(env)
    target = _host_data_dir()
    if target is None:
        return _legacy_data_dir()
    _inherit_legacy_home(target)
    return target


#: The rig tab's file name. One name, because the status readout and the file that gets saved
#: have to be the same file: they were `amphead.rpp` in one module and `rig.rpp` in another, and
#: a restore that fell back to its own default reopened a leftover stub instead of the rig.
RIG_FILE = "amphead.rpp"


def rig_path() -> Path:
    """Where the rig tab lives. The only place that answers this question.

    ``tools.rig_path`` used to compute its own answer, so setup saved one file and snapshot
    restore opened another. Anything that needs this path asks here.
    """
    env = os.environ.get("AMPHEAD_RIG")
    if env:
        return Path(env)
    return data_dir() / "rig" / RIG_FILE


def _snapshot(tag: str, facts: dict[str, Any]) -> str:
    """Write what the tab looked like, so a later diff can say what changed."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = data_dir() / "snapshots" / f"{stamp}-{tag}.json"
    record = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "tag": tag, **facts}
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(record, indent=2), encoding="utf-8")
    except OSError:
        return ""
    return str(target)


def _pre_state(path: str) -> tuple[dict[str, Any], list[str], int]:
    """Read the tab before touching it: its tabs, its track names, and the FX already on track 1.

    Returns the track count too, because the track this build will create is the next one, and
    naming it up front is what lets the whole build be a single BATCH.
    """
    facts: dict[str, Any] = {"path": path, "tabs": [], "tracks": [], "pre_fx": []}
    try:
        facts["tabs"] = bridge.tabs_list()
    except (bridge.NoReaper, bridge.CommandFailed):
        pass
    names: list[str] = []
    try:
        names = bridge.track_names(expect=path)
    except (bridge.NoReaper, bridge.CommandFailed):
        names = []
    facts["tracks"] = names
    pre_fx: list[str] = []
    try:
        chain = bridge.fx_list(track=1, expect=path)
        pre_fx = [f.get("name", "") for f in chain]
        facts["pre_fx"] = pre_fx
    except (bridge.NoReaper, bridge.CommandFailed):
        pass
    return facts, pre_fx, len(names)


def build_rig(
    amp: str = DEFAULT_AMP,
    input: str | int | None = None,
    track_name: str = DEFAULT_TRACK,
    start_db: float = START_DB,
    path: str | Path | None = None,
    arm: bool = False,
    force: bool = False,
    timeout: float = 20.0,
) -> Built:
    """Build the practice rig in its own tab, and prove each step landed.

    Order matters. The tab is created from a project file on disk first, because that is the only
    silent way to give a tab a path, and a path is what every later write is guarded against.
    Then one BATCH does the building: track, amp, input, level, and optionally arming. Arming is
    opt-in because the moments a track goes live belong to the player. The input defaults to
    input 1, because a track with no input reads exactly like a dead cable in the verdict.

    FX that came with the project file are reported and left alone: a build never deletes
    something it did not add.
    """
    started = time.time()
    target = Path(path) if path else rig_path()
    created_file = False
    if target.exists() and not force:
        raise RigRefused({}, f"rig already exists at {target}; pass force=True to overwrite it")
    rigfile.write_template(target)
    created_file = True

    bridge.open_rig(str(target), new="0", timeout=timeout)
    # The tab now reports this path; from here on that path is the guard on every write.
    expect = str(target)

    pre_facts, pre_fx, tracks_before = _pre_state(expect)
    snap = _snapshot("pre-build", pre_facts)

    ops: list[tuple[str, dict]] = [("TRACK_ADD", {"name": track_name})]
    # The new track lands at the end, so its index is known before the batch runs.
    built_track = tracks_before + 1
    ops.append(("FX_ADD", {"track": built_track, "name": amp}))
    spec = None
    # A rig whose track has no input is indistinguishable from a broken cable, so the build picks
    # the first input (mono channel 1, which REAPER stores as 0) unless told otherwise;
    # input="none" builds a track with no input at all.
    wanted = DEFAULT_INPUT if input is None else input
    if str(wanted) not in ("0", "none", ""):
        try:
            dev = int(bridge.call("DEVINFO").get("inputs", 2))
        except (bridge.NoReaper, bridge.CommandFailed):
            dev = 2
        if dev >= 1:
            # A closed device (dev 0) has no channels to point at: the build is still a build, and
            # setup repairs the input on the next pass rather than failing here for a closed device.
            spec = bridge.input_encoding(wanted, dev)
            ops.append(("SET_INPUT", {"track": built_track, "input": spec}))
    ops.append(("TRACK_VOL", {"track": built_track, "db": f"{start_db:g}"}))
    if arm:
        ops.append(("ARM", {"track": built_track, "monitor": "1", "confirm": "1"}))

    try:
        payload = bridge.batch(ops, label="Hermes: build rig", timeout=timeout,
                               **bridge.guard(expect=expect))
    except bridge.CommandFailed as exc:
        # BATCH stops at the first failure and says which op it was; a half-built rig is never
        # reported as built, and the tab it started stays put for inspection.
        results = exc.payload.get("results") or []
        failed = exc.payload.get("failed_at")
        raise RigRefused(exc.payload, f"build stopped at op {failed} "
                                      f"({_op_name(results, failed)}): "
                                      f"{_op_error(results, failed)}") from exc
    results = payload.get("results") or []

    add = _resp(results, 1)
    reported = int(float(add.get("index", built_track) or built_track))
    if reported != built_track:
        raise RigRefused(payload, f"the track landed at {reported}, expected {built_track}")
    fx = _resp(results, 2)
    amp_index = int(float(fx["fx"])) if fx.get("fx") is not None else -1
    if amp_index < 0:
        raise RigRefused(payload, f"the amp went in but REAPER did not report its index: {fx}")

    chain = [f.get("name", "") for f in bridge.fx_list(track=built_track, expect=expect)]
    info = bridge.track_info(built_track, expect=expect)
    armed = bool(info.get("armed"))
    monitor = bool(info.get("monitor"))
    db = round(float(info.get("db", start_db) or start_db), 2)
    if arm and not (armed and monitor):
        raise RigRefused(info, f"track {built_track} was asked to arm and reads "
                               f"armed={armed} monitor={monitor}")
    held = info.get("recinput")
    recinput = int(float(held)) if held not in (None, "") else None
    if spec is None:
        # No input was asked for, so nothing about the input is this build's to report.
        recinput = None
    elif recinput != spec:
        raise RigRefused(info, f"input {spec} did not stick: REAPER reads back {recinput}")

    built = Built(
        path=expect,
        track=built_track,
        track_name=str(add.get("name") or track_name),
        amp=str(fx.get("name") or amp),
        amp_index=amp_index,
        chain=chain,
        pre_fx=pre_fx,
        input=recinput,
        db=db,
        armed=armed,
        monitor=monitor,
        snapshot=snap,
        seconds=round(time.time() - started, 2),
        created_file=created_file,
    )
    _append_log(None, {"op": "build_rig", **built.__dict__, "found_fx": pre_fx})
    return built


def _op_error(results: list[dict], index: Any) -> str:
    """The failing op's own words, plus the fields that say which name was asked for."""
    for row in results:
        if row.get("i") == index:
            resp = row.get("resp") or {}
            extra = ", ".join(f"{k}={v}" for k, v in resp.items()
                              if k not in {"ok", "error"} and v not in ("", None))
            why = resp.get("error") or "failed"
            return f"{why} ({extra})" if extra else why
    return "failed"


def _op_name(results: list[dict], index: Any) -> str:
    for row in results:
        if row.get("i") == index:
            return str(row.get("cmd"))
    return "?"


def _resp(results: list[dict], index: int) -> dict:
    for row in results:
        if row.get("i") == index:
            return row.get("resp") or {}
    return {}


def _same_path(a: str, b: str) -> bool:
    """Compare two project paths the way REAPER means them: case-insensitive, separators folded."""
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def teardown(path: str | Path | None = None, delete: bool = True, **kwargs: Any) -> dict:
    """Close the rig tab, and delete the file only when it is the rig's own file.

    The path check comes first, so a tab holding a song cannot be closed by passing a path that
    is not the rig's, and the file is only removed after REAPER has confirmed the tab closed.
    """
    target = Path(path) if path else rig_path()
    expect = str(target)
    active = bridge.active_project()
    if active is None:
        raise RigRefused({}, "no active project: REAPER may not be running")
    if _same_path(active, expect) is False:
        raise RigRefused({"active": active},
                         f"the active tab is {active}, not the rig ({expect}); refusing to close it")
    closed = bridge.call("PROJ_TAB_CLOSE", expect=expect, force="1", timeout=kwargs.get("timeout", 15.0))
    removed = False
    if delete and target.exists():
        try:
            target.unlink()
            removed = True
        except OSError:
            removed = False
    left = bridge.tabs_list()
    return {"closed": bool(closed.get("closed")), "path": expect, "file_removed": removed,
            "tabs": [t.get("path") or t.get("project") or "" for t in left]}
