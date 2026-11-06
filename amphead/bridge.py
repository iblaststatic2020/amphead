"""Bridge client, protocol v2.

Wire format (one line in ``request.txt``)::

    <id>|<CMD>|<key>=<value>|<key>=<value>...

Values are percent-encoded, so ``|``, ``=``, ``,``, ``;`` and ``:`` cannot appear raw.
Responses land in ``out/resp_<id>.json``.

The failure taxonomy is the point of this module. "REAPER is not running", "REAPER is
showing a dialog", "the installed bridge is an old build" and "the command failed" need
different words in front of a user, and all four are easy to collapse into one useless
timeout.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote, unquote

PROTO = 2
DEFAULT_TIMEOUT = 2.0
LOCK_TIMEOUT = 10.0      # how long to wait for the slot
HEARTBEAT_MAX_AGE = 5.0


class ReaperError(Exception):
    """Base class for every bridge failure."""


class NoReaper(ReaperError):
    """No bridge answering and no fresh heartbeat: REAPER is closed or the script is not loaded."""


class BridgeBlocked(ReaperError):
    """Heartbeat is fresh but no answer arrived: REAPER's main thread is owned by a dialog."""


class OldBridge(ReaperError):
    """The installed bridge speaks a different protocol than this client."""


class BridgeTimeout(ReaperError):
    """The request was handed over and the bridge is alive, but no answer arrived in time."""


class BadResponse(ReaperError):
    """The response file is missing, empty, truncated, not JSON, or answers a different id."""


class CommandFailed(ReaperError):
    """The bridge answered ``ok: false``."""

    def __init__(self, payload: dict, message: str | None = None):
        self.payload = payload if isinstance(payload, dict) else {"error": str(payload)}
        super().__init__(message or self.payload.get("error") or "command failed")


def bridge_root(root: str | os.PathLike | None = None) -> Path:
    if root is not None:
        return Path(root)
    env = os.environ.get("HERMES_REAPER_BRIDGE")
    if env:
        return Path(env)
    appdata = os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming")
    return Path(appdata) / "REAPER" / "Scripts" / "hermes_bridge"


def _enc(value: Any) -> str:
    return quote(str(value), safe="")


def _alive_age(base: Path) -> float | None:
    try:
        return time.time() - (base / "alive").stat().st_mtime
    except OSError:
        return None


def _try_lock(fd: int) -> bool:
    """Ask the kernel for an exclusive lock on the open file, without waiting."""
    try:
        if os.name == "nt":
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


class _Lock:
    """Exclusive lock on the single request slot.

    ``request.txt`` is one global file. Two sessions calling at once would otherwise
    overwrite each other's requests and both see a timeout.

    This is a kernel file lock, not a "create the file, delete it when done" flag. The
    create/delete protocol is a check-then-act race: the release-side delete can land after
    the next holder has already created the file, so two callers share the slot and one of
    them waits out a timeout for an answer written for the other. The kernel also releases
    the lock when a process dies, so a crash cannot leave a lock that nobody owns.
    The file stays on disk empty and unlocked between calls, which is why uninstall can
    still remove it.
    """

    def __init__(self, path: Path, timeout: float = LOCK_TIMEOUT, stale: float | None = None):
        self.path = path
        self.timeout = timeout
        self.stale = stale      # accepted for old callers; a kernel lock needs no staleness rule
        self.fd: int | None = None

    def touch(self) -> None:
        """Refresh the lock file's mtime. No longer load-bearing (the kernel holds the
        lock), kept so a holder's heartbeat stays visible in the directory listing."""
        try:
            os.utime(self.path, None)
        except OSError:
            pass

    def __enter__(self) -> "_Lock":
        deadline = time.monotonic() + self.timeout
        while True:
            fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
            if _try_lock(fd):
                self.fd = fd
                return self
            os.close(fd)
            if time.monotonic() > deadline:
                raise BridgeTimeout(f"could not acquire bridge lock {self.path}")
            time.sleep(0.01)

    def __exit__(self, *exc: object) -> None:
        if self.fd is None:
            return
        try:
            os.close(self.fd)           # closing the handle releases the kernel lock
        except OSError:
            pass
        self.fd = None


def _process_running() -> bool:
    """Is a reaper.exe alive? Imported lazily: launch imports this module."""
    try:
        from . import launch
        return bool(launch.running())
    except Exception:
        return False


def liveness(root: str | os.PathLike | None = None) -> tuple[str, str]:
    """One verdict function for the client, the tools and the pane.

    Returns ``(verdict, sentence)`` where verdict is ``live``, ``not-running``, ``not-loaded``
    or ``blocked``. A heartbeat proves the bridge script is being serviced; the process proves
    REAPER exists at all. Neither alone can tell "quit" from "showing a dialog".
    """
    base = bridge_root(root)
    age = _alive_age(base)
    # The process decides first: a heartbeat can outlive reaper.exe by a few seconds, and a
    # heartbeat can also be written by a script whose host is being killed.
    if not _process_running():
        if age is None:
            return "not-running", "REAPER is not running"
        return "not-running", (f"reaper.exe is gone; the last heartbeat was {age:.0f}s ago")
    if age is None:
        return "not-loaded", f"REAPER is running but the bridge script is not loaded: {base}"
    if age > HEARTBEAT_MAX_AGE:
        return "blocked", (f"bridge heartbeat is {age:.0f}s stale while reaper.exe is alive: "
                           f"a dialog probably owns the main thread")
    return "live", "bridge is answering"


def _parse_response(text: str, rid: str) -> dict:
    """Decode one response and refuse anything that cannot be this call's answer.

    The checks are ordered from cheap to specific so each failure says what actually went wrong:
    truncated JSON does not get reported as "not JSON", and a foreign id does not get parsed
    into a plausible-looking dict.
    """
    if not text.strip():
        raise BadResponse("empty response")
    if not text.rstrip().endswith("}"):
        raise BadResponse(f"truncated response: {text[-80:]!r}")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise BadResponse(f"not JSON: {text[:120]!r}") from exc
    if not isinstance(payload, dict):
        raise BadResponse(f"not an object: {text[:120]!r}")
    answer_id = payload.get("id")
    if answer_id is not None and str(answer_id) != rid:
        raise BadResponse(f"response id {answer_id!r} does not match request {rid!r}")
    return payload


def call(
    cmd: str,
    *args: object,
    timeout: float = DEFAULT_TIMEOUT,
    root: str | os.PathLike | None = None,
    _sleep=time.sleep,
    **kwargs: Any,
) -> dict:
    """Send one command and return the decoded answer.

    Positional ``args`` are kept for the old call style; keyword arguments become named
    fields, which is what the v2 protocol wants.
    """
    if not cmd or not cmd.strip():
        raise ValueError("empty command")
    base = bridge_root(root)
    out = base / "out"
    req = base / "request.txt"
    if not base.is_dir():
        raise NoReaper(f"bridge directory missing: {base}")
    out.mkdir(parents=True, exist_ok=True)

    rid = uuid.uuid4().hex[:12]
    parts = [rid, cmd.upper()]
    for value in args:
        parts.append(f"arg{len(parts) - 1}={_enc(value)}")
    for key, value in kwargs.items():
        if value is None:
            continue
        parts.append(f"{_enc(key)}={_enc(value)}")
    line = "|".join(parts)
    resp = out / f"resp_{rid}.json"

    with _Lock(base / "request.lock", timeout=LOCK_TIMEOUT) as lock:
        tmp = base / f".req_{rid}.tmp"
        tmp.write_text(line, encoding="utf-8")
        try:
            os.replace(tmp, req)
        except OSError as exc:              # REAPER holding the file open on Windows
            tmp.unlink(missing_ok=True)
            raise NoReaper(f"cannot hand a request to REAPER: {exc}") from exc

        deadline = time.monotonic() + timeout
        next_touch = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            if resp.exists():
                break
            if time.monotonic() >= next_touch:
                lock.touch()            # do not let our own lock age out under a slow command
                next_touch = time.monotonic() + 1.0
            _sleep(0.01)
        else:
            verdict, sentence = liveness(base)
            if verdict in {"blocked", "live"}:
                raise BridgeBlocked(f"{sentence}; no answer within {timeout:.1f}s")
            raise NoReaper(sentence)

    try:
        text = resp.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise BadResponse(f"response unreadable: {exc}") from exc
    finally:
        resp.unlink(missing_ok=True)

    payload = _parse_response(text, rid)

    proto = payload.get("proto")
    if proto is not None and proto != PROTO:
        raise OldBridge(f"bridge speaks protocol {proto}, this client needs {PROTO}: {base}")
    if payload.get("ok") is not True:
        raise CommandFailed(payload)
    return payload


def call_ok(cmd: str, *args: object, **kwargs) -> dict:
    """Same as :func:`call`; kept because everything above this layer uses this name."""
    return call(cmd, *args, **kwargs)


def ping(timeout: float = DEFAULT_TIMEOUT, **kwargs) -> dict:
    payload = call("PING", timeout=timeout, **kwargs)
    proto = payload.get("proto")
    if proto != PROTO:
        raise OldBridge(f"bridge speaks protocol {proto!r}, this client needs {PROTO}")
    return payload


def status(**kwargs) -> dict:
    return call("STATUS", **kwargs)


def tabs_list(**kwargs) -> list:
    """TABS ships its tab array as a JSON string; decode before anyone iterates it."""
    return _nested(tabs(**kwargs), "tabs")


def tabs(**kwargs) -> dict:
    return call("TABS", **kwargs)


def dev_reopen(**kwargs) -> dict:
    """Reopen a wedged/closed audio device (ASIO stays locked after a killed REAPER)."""
    kwargs.setdefault("timeout", 15.0)
    return call("DEV_REOPEN", **kwargs)


def devinfo(**kwargs) -> dict:
    return call("DEVINFO", **kwargs)


def meter(track: int = 1, **kwargs) -> dict:
    return call("METER", track=track, **kwargs)


def track_info(track: int = 1, **kwargs) -> dict:
    return call("TRACK_INFO", track=track, **kwargs)


def inputs(**kwargs) -> dict:
    return call("INPUTS", **kwargs)


def _nested(payload: dict, field: str) -> list:
    """``FX_LIST``/``FX_PARAMS`` ship arrays as JSON strings: decode, never guess the shape.

    A caller that iterates the raw string gets characters and reports "param not found" for
    a parameter that exists, which is exactly the kind of bug that wastes an afternoon.
    """
    raw = payload.get(field)
    if isinstance(raw, list):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return []
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BadResponse(f"{field} is not JSON: {raw[:120]!r}") from exc
    if not isinstance(value, list):
        raise BadResponse(f"{field} is not an array: {raw[:120]!r}")
    return value


def track_names(**kwargs) -> list[str]:
    """Just the names, in track order. Used to know where a new track will land."""
    return _nested(call("TRACKS", **kwargs), "names")


def fx_list(track: int = 1, **kwargs) -> list:
    """The chain as a list of ``{i, name, enabled}`` in real order."""
    return _nested(call("FX_LIST", track=track, **kwargs), "fx")


def fx_params(track: int = 1, fx: int = 0, keep: int | None = None, **kwargs) -> list:
    """Every parameter as ``{i, name, fmt}``."""
    return _nested(call("FX_PARAMS", track=track, fx=fx, keep=keep, **kwargs), "params")


def param_index(name: str, track: int = 1, fx: int = 0, **kwargs) -> int:
    """Resolve a parameter by name, refusing ambiguity instead of taking the first match.

    One ladder only: the ranking lives in :mod:`amphead.resolver`, so a name that resolves here
    resolves the same way everywhere. Imported inside the function because the resolver reads the
    bridge for its default chain and parameters.
    """
    from . import resolver

    outcome = resolver.resolve_param(name, track=track, fx=fx, **kwargs)
    if isinstance(outcome, resolver.Resolved):
        return outcome.index
    raise LookupError(outcome.sentence())


def fx_bypass(track: int = 1, fx: int = 0, on: bool = True, **kwargs) -> dict:
    """Bypass (``on=True``) or restore one FX slot, then trust only the read-back."""
    payload = call("FX_BYPASS", track=track, fx=fx, on="1" if on else "0", **kwargs)
    landed = bool(payload.get("bypassed"))
    if landed != bool(on):
        raise CommandFailed(payload, f"fx {fx} reads bypassed={landed} after asking for {on}")
    return payload


ARM_SAFE_MASTER_DB = -60.0


def master(**kwargs) -> dict:
    """Master volume as ``{vol, db}``; pass ``db=`` or ``vol=`` to set it."""
    return call("MASTER_VOL", **kwargs)


def fx_focus(track: int = 1, fx: int = 0, **kwargs) -> dict:
    """Bring one plugin to the front of REAPER's chain window. The caller owns the visibility rule."""
    return call("FX_FOCUS", track=track, fx=fx, **kwargs)


def disarm(track: int = 1, **kwargs) -> dict:
    """Take the track out of record-arm and input monitoring, no questions asked."""
    payload = call("DISARM", track=track, **kwargs)
    if payload.get("armed") or payload.get("monitor"):
        raise CommandFailed(payload, "track is still armed after DISARM")
    return payload


def arm(track: int = 1, monitor: bool = True, allow_loud: bool = False, **kwargs) -> dict:
    """Arm a track, and prove the master actually came down first.

    Arming is the moment the amp faces the speakers, so the refusal path matters more than
    the success path: if the master cannot be verified at or below the floor, no arming.
    """
    payload = call("ARM", track=track, monitor="1" if monitor else "0",
                   allow_loud="1" if allow_loud else "0", confirm="1", **kwargs)
    if not payload.get("armed"):
        raise CommandFailed(payload, "track did not arm")
    after = float(payload.get("master_db_after", 0.0))
    if not allow_loud and after > ARM_SAFE_MASTER_DB + 0.5:
        raise CommandFailed(payload, f"armed at {after:.1f} dB, which is not the safe floor")
    return payload


INPUT_NONE = 0
INPUT_PAIR_BASE = 1024


def input_encoding(spec: str | int, inputs: int = 2) -> int:
    """Turn "1", "in 2", "stereo", "none" or a raw number into REAPER's I_RECINPUT value."""
    if isinstance(spec, int):
        return spec
    text = str(spec).strip().lower()
    if text in {"", "none", "off", "no input"}:
        return INPUT_NONE
    if text in {"stereo", "stereo 1", "1-2", "in 1-2"}:
        return INPUT_PAIR_BASE
    digits = "".join(ch for ch in text if ch.isdigit())
    if not digits:
        raise ValueError(f"cannot read an input from {spec!r}")
    value = int(digits)
    if value > inputs and value < INPUT_PAIR_BASE:
        raise ValueError(f"input {value} does not exist on a {inputs}-input device")
    return value


def set_input(track: int = 1, spec: str | int = 1, **kwargs) -> dict:
    """Select a recording input and prove REAPER stored it."""
    dev = kwargs.pop("inputs", None)
    if dev is None:
        try:
            dev = int(call("DEVINFO").get("inputs", 2))
        except (NoReaper, BridgeBlocked, BadResponse):
            dev = 2
    want = input_encoding(spec, dev)
    payload = call("SET_INPUT", track=track, input=want, **kwargs)
    got = int(float(payload.get("recinput", -1)))
    if not payload.get("applied") or got != want:
        raise CommandFailed(payload, f"input {want} did not stick: REAPER reads back {got}")
    return payload


def open_rig(path: str, **kwargs) -> dict:
    """Create the rig tab by opening a project file, which is the only silent way to give
    an unnamed tab a real identity."""
    kwargs.setdefault("timeout", 15.0)
    return call("PROJ_OPEN", path=path, **kwargs)


def undo(**kwargs) -> dict:
    """Undo the last group: one BATCH, one bare write, one preset."""
    return call("UNDO", **kwargs)


# TX_BEGIN / TX_END are gone on purpose. REAPER drops an undo block that is opened in one
# script invocation and closed in the next, so those commands looked open, did nothing, and
# left the writes sitting on top of whatever undo point came before them. Use batch().


def _inner(value: Any) -> str:
    """Inner encoding keeps the op string splittable on ``;``, ``,`` and ``:``."""
    return quote(str(value), safe="")


def batch(
    ops: Iterable[tuple[str, dict]],
    label: str = "Hermes: rig",
    timeout: float = 10.0,
    **kwargs,
) -> dict:
    """Run several commands as one transaction in one round trip.

    Stops at the first failure on the REAPER side and reports which operation failed, so a
    half-built rig is never reported as done.
    """
    chunks = []
    for cmd, op_args in ops:
        pairs = [f"{_inner(k)}={_inner(v)}" for k, v in op_args.items() if v is not None]
        chunks.append(cmd.upper() + (":" + ",".join(pairs) if pairs else ""))
    return call("BATCH", timeout=timeout, ops=";".join(chunks), label=label, **kwargs)


def guard(expect: str | None = None, created: bool = False) -> dict:
    """Guard fields for a mutating command.

    ``expect`` is the project path the caller believes is active; ``created`` marks a
    project this same batch made, which is the only case where an unnamed project may be
    written to.
    """
    out: dict[str, Any] = {}
    if expect:
        out["expect"] = expect
    if created:
        out["new"] = 1
    return out


def active_project() -> str | None:
    """Path of the project the user is looking at.

    Mutating commands refuse to run without it, so every write carries this as `expect`: a write
    cannot land in a tab nobody meant to touch just because that tab happened to be active.
    """
    for tab in tabs_list():
        if tab.get("active") and tab.get("path"):
            return str(tab["path"])
    return None


def wait_ready(timeout: float = 15.0, **kwargs) -> dict:
    """Poll until the bridge answers, for launches and restarts."""
    deadline = time.monotonic() + timeout
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            return ping(**kwargs)
        except ReaperError as exc:
            last = exc
            time.sleep(0.15)
    raise NoReaper(f"bridge did not come up within {timeout:.0f}s: {last}")


def prune_responses(older_than: float = 3600.0, root=None) -> int:
    """Remove stale response files; meter polls otherwise accumulate forever."""
    out = bridge_root(root) / "out"
    if not out.is_dir():
        return 0
    cutoff = time.time() - older_than
    removed = 0
    for path in out.glob("resp_*.json"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            continue
    return removed


def decode(value: str) -> str:
    return unquote(value)
