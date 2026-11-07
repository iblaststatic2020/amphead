"""Start REAPER for AmpHead without putting its UI in the user's face.

The plugin is the interface. REAPER is a headless-ish audio host behind it: we launch it
minimized (taskbar icon is fine, a full window and a second app's UI is not), wait for the
bridge, and reopen the audio device if the driver came up wedged.

Nothing here closes the user's projects or picks a project for them: REAPER opens on
whatever it opens on, and the rig is a tab AmpHead creates and names itself.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import appdecl, bridge, port

WINDOWS_CANDIDATES = [
    r"C:\Program Files\REAPER (x64)\reaper.exe",
    r"C:\Program Files\REAPER\reaper.exe",
    r"C:\Program Files (x86)\REAPER\reaper.exe",
]

# Windows show-state constants, used only for the hidden-desktop option.
HIDDEN_LAUNCHER = Path(__file__).resolve().parents[1] / "scripts" / "reaper_hidden.ps1"


class NoReaperInstall(port.NoRig):
    """REAPER is not installed where we can find it."""


def find_reaper() -> Path:
    """Locate reaper.exe: env override, then the application declaration, then our own list.

    The declaration is the host's evaluator doing the looking (install roots, then the uninstall
    registry), which is how a copy in a non-standard folder is found at all. The hand-written
    candidates stay as the fallback for a checkout with no hermes_platform, and PATH catches a
    portable install that someone put on it.
    """
    override = os.environ.get("REAPER_EXE")
    if override and Path(override).is_file():
        return Path(override)
    declared = appdecl.located()
    if declared and Path(declared).is_file():
        return Path(declared)
    for candidate in WINDOWS_CANDIDATES:
        if Path(candidate).is_file():
            return Path(candidate)
    found = shutil.which("reaper") or shutil.which("reaper.exe")
    if found:
        return Path(found)
    # Too old is its own sentence: "not installed" would send a player to the installer.
    raise NoReaperInstall(appdecl.unavailable_reason() or "reaper.exe not found; set REAPER_EXE to its full path")


def bridge_alive(timeout: float = 0.6) -> bool:
    try:
        bridge.ping(timeout=timeout)
        return True
    except bridge.ReaperError:
        return False


def _powershell(script: str, timeout: float = 30.0) -> str:
    if sys.platform != "win32":
        raise NoReaperInstall("starting REAPER minimized is Windows-only for now")
    result = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise NoReaperInstall(f"could not start REAPER: {result.stderr.strip()[:200]}")
    return result.stdout


def _posix_pids() -> list[int]:
    """Live REAPER process ids on macOS or Linux. Empty when none, or when pgrep is absent.

    ``pgrep -x`` matches the whole process name, and ``-i`` covers the macOS binary being
    ``REAPER`` while the Linux one is ``reaper``. Exit code 1 is "no match", not a failure.
    """
    for argv in (["pgrep", "-x", "-i", "reaper"], ["pgrep", "-x", "REAPER"], ["pgrep", "-x", "reaper"]):
        try:
            result = subprocess.run(argv, capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            continue
        if result.returncode == 0:
            return [int(x) for x in result.stdout.split() if x.isdigit()]
        if result.returncode == 1:
            return []
    return []


def _start_posix(exe: Path) -> int | None:
    """Launch REAPER on macOS or Linux.

    macOS resolves to an ``.app`` bundle, which is started with ``open``; Linux is a bare
    executable started directly. Neither platform is asked to hide its window: on macOS that
    needs accessibility permission, so the honest behaviour is "the window appears".
    """
    if sys.platform == "darwin":
        result = subprocess.run(["open", "-na", str(exe)], capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            raise NoReaperInstall(f"could not start REAPER: {result.stderr.strip()[:200]}")
        pids = _posix_pids()
        return pids[0] if pids else None
    try:
        proc = subprocess.Popen(
            [str(exe)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        raise NoReaperInstall(f"could not start REAPER: {exc}") from exc
    return proc.pid


def start(minimized: bool = True, hidden_desktop: bool = False) -> int | None:
    """Launch REAPER. Default minimized: no window, no focus steal, taskbar icon only.

    Minimizing is Windows-only; elsewhere the window appears and ``minimize_window()`` says so.
    """
    exe = find_reaper()
    if sys.platform != "win32":
        return _start_posix(exe)
    if hidden_desktop:
        if not HIDDEN_LAUNCHER.is_file():
            raise NoReaperInstall(f"missing launcher script: {HIDDEN_LAUNCHER}")
        out = _powershell(
            f"& '{HIDDEN_LAUNCHER}' -Exe '{exe}'", timeout=60.0
        )
    else:
        style = "Minimized" if minimized else "Normal"
        out = _powershell(
            f"$p = Start-Process -FilePath '{exe}' -WindowStyle {style} -PassThru; $p.Id",
            timeout=30.0,
        )
    try:
        return int(out.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None


def ensure_audio(timeout: float = 20.0, info: dict | None = None) -> dict:
    """Open the audio device if it is closed. ASIO stays locked after a killed REAPER.

    ``info`` lets a caller that has already read DEVINFO hand it over instead of paying for a
    second round trip on every poll.
    """
    info = bridge.devinfo(timeout=3.0) if info is None else info
    if not info.get("device_closed"):
        return info
    bridge.dev_reopen(timeout=timeout)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        info = bridge.devinfo(timeout=3.0)
        if not info.get("device_closed"):
            return info
        time.sleep(0.25)
    return info


_MINIMIZE_PS = r"""
Add-Type -Name W -Namespace U -MemberDefinition '
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int n);
  [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);'
$deadline = (Get-Date).AddSeconds({0})
while ((Get-Date) -lt $deadline) {{
  $p = Get-Process reaper -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($p -and $p.MainWindowHandle -ne 0) {{
    if (-not [U.W]::IsIconic($p.MainWindowHandle)) {{
      [U.W]::ShowWindow($p.MainWindowHandle, 6) | Out-Null
      Start-Sleep -Milliseconds 250
    }}
    if ([U.W]::IsIconic($p.MainWindowHandle)) {{ 'minimized' ; exit 0 }}
  }}
  Start-Sleep -Milliseconds 200
}}
'still-visible'
"""


def minimize_window(max_wait: float = 20.0) -> bool:
    """Shrink REAPER to the taskbar.

    REAPER restores its saved window placement on startup, so ``-WindowStyle Minimized``
    alone does not stick: it re-shows the window a moment after launch. This waits for the
    window to exist and minimizes it, which does stick.
    """
    if sys.platform != "win32":
        return False                # no window steering off Windows; the window simply shows
    out = _powershell(_MINIMIZE_PS.format(max_wait), timeout=max_wait + 20)
    return out.strip().endswith("minimized")


_VISIBLE_PS = r"""
Add-Type -Name W2 -Namespace U2 -MemberDefinition '
  [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);'
$p = Get-Process reaper -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
if (-not $p) { 'hidden' ; exit 0 }
if ([U2.W2]::IsWindowVisible($p.MainWindowHandle) -and -not [U2.W2]::IsIconic($p.MainWindowHandle)) { 'visible' } else { 'hidden' }
"""


_UI_CACHE: tuple[float, bool] | None = None


def ui_visible() -> bool:
    """True only when REAPER has a window on screen: a minimized or closed window cannot be focused.

    The pane uses this to decide whether a chain-row click may reach FX_FOCUS at all, because
    focusing a plugin in a hidden chain window would be a click with no visible effect.
    """
    global _UI_CACHE
    if sys.platform != "win32":
        return False
    now = time.monotonic()
    if _UI_CACHE and now - _UI_CACHE[0] < 1.5:
        return _UI_CACHE[1]
    try:
        answer = _powershell(_VISIBLE_PS, timeout=15.0).strip().endswith("visible")
    except NoReaperInstall:
        answer = False
    _UI_CACHE = (now, answer)
    return answer


def ensure_running(
    minimized: bool = True,
    hidden_desktop: bool = False,
    boot_timeout: float = 45.0,
) -> dict:
    """Make sure REAPER answers, with no UI in the user's way.

    Returns ``{"started", "pid", "waited", "ui", "device"}``. ``ui`` is ``minimized``,
    ``hidden``, ``visible`` (could not be minimized) or ``already-running``.
    Raises ``NoReaper`` if REAPER never answers, which is the one case the caller must
    show a human sentence for.
    """
    if bridge_alive():
        info = bridge.devinfo(timeout=3.0)
        ui = "already-running"
        if minimized and sys.platform == "win32":
            ui = "minimized" if minimize_window(max_wait=3.0) else "already-running"
        return {"started": False, "pid": None, "waited": 0.0, "ui": ui,
                "device": info if not info.get("device_closed") else ensure_audio()}

    started_at = time.monotonic()
    pid = start(minimized=False, hidden_desktop=hidden_desktop)
    bridge.wait_ready(timeout=boot_timeout)
    ui = "hidden" if hidden_desktop else "visible"
    if minimized and not hidden_desktop:
        ui = "minimized" if minimize_window() else "visible"
    return {
        "started": True,
        "pid": pid,
        "waited": round(time.monotonic() - started_at, 2),
        "ui": ui,
        "device": ensure_audio(),
    }


_REOPEN_AT = float("-inf")  # when we last tried to repair a closed device (never, at boot)
REOPEN_COOLDOWN = 15.0      # a poller must not spawn a repair attempt every two seconds


def _device_info() -> tuple[dict, bool]:
    """DEVINFO, and whether this call had to reopen a device that came up closed.

    A killed REAPER leaves ASIO wedged, so the next start can come up with no device at all. That
    is a repair, not a diagnosis: the player did not unplug anything, so the pane should not read
    like they did.
    """
    global _REOPEN_AT
    info = bridge.devinfo(timeout=3.0)
    if not info.get("device_closed"):
        return info, False
    if time.monotonic() - _REOPEN_AT < REOPEN_COOLDOWN:
        return info, False
    _REOPEN_AT = time.monotonic()
    repaired = ensure_audio(timeout=5.0, info=info)
    return repaired, not repaired.get("device_closed")


def _same_file(left: str, right: Path | None) -> bool:
    """Compare two ways of naming a project: REAPER hands back forward slashes and its own case."""
    if not left or right is None:
        return False
    norm = lambda text: str(text).replace("\\", "/").rstrip("/").lower()  # noqa: E731
    return norm(left) == norm(right)


def state() -> dict:
    """One call for the pane: bridge, project, safety, device, latency.

    Never raises for a missing host. A stale heartbeat is the normal aftermath of quitting
    REAPER, so "down" with a reason is a state, not an exception: the pane has to be able to
    draw "REAPER NOT RUNNING" instead of crashing on the way to it.
    """
    rig = _rig_path()
    try:
        info, reopened = _device_info()
        status = bridge.status(timeout=3.0)
    except bridge.ReaperError as exc:
        alive = _running()
        if not alive:
            return {
                "bridge": "not-running",
                "reason": "REAPER is not running; the bridge heartbeat is stale",
                "project": None, "path": "", "tracks": 0, "device": {},
                "instances": 0, "rig_tab": None,
            }
        # REAPER is up but nothing answers: either a dialog owns the main thread, or the bridge
        # script is not there at all (deleted, or a resource folder that got rebuilt).
        kind, sentence = bridge.liveness()
        if kind == "not-loaded":
            return {
                "bridge": "gone",
                "reason": f"{sentence}. Run setup to put it back.",
                "project": None, "path": "", "tracks": 0, "device": {},
                "instances": instances(), "rig_tab": None,
            }
        return {
            "bridge": "down",
            # The client guesses "a dialog probably has it"; only say that when the process exists.
            "reason": str(exc),
            "project": None, "path": "", "tracks": 0, "device": {},
            "instances": instances(), "rig_tab": None,
        }
    path = status.get("path") or ""
    return {
        "bridge": "live",
        "project": status.get("project") or "(unnamed)",
        "path": path,
        "tracks": status.get("tracks", 0),
        # The controls in the pane and the tools both write to track 1 of whatever project is in
        # front of REAPER, so the rig has to be the project in front of REAPER. An empty or
        # unnamed project (the tab a second `reaper.exe -new` leaves in front) is never the rig.
        "rig_tab": _same_file(path, rig) if rig is not None else None,
        "instances": instances(),
        "device": {
            "driver": info.get("ident_in") or "",
            "mode": info.get("mode") or "",
            "closed": bool(info.get("device_closed")),
            "reopened": reopened,
            "buffer": info.get("bsize") or "",
            "rate": info.get("srate") or "",
            "bits": info.get("bps") or "",
            "inputs": int(info.get("inputs") or 0),
            "latency_total_ms": info.get("device_latency_ms"),
            "latency_out_ms": info.get("output_latency_ms"),
        },
    }


def _running() -> bool:
    """Is a REAPER process alive right now? Used by stop() and by ensure_running()."""
    if sys.platform != "win32":
        return bool(_posix_pids())
    try:
        out = _powershell("if (Get-Process -Name reaper -ErrorAction SilentlyContinue) { 'yes' } else { 'no' }")
    except NoReaperInstall:
        return False
    return "yes" in out


# Counting processes costs a PowerShell launch, and the pane asks every couple of seconds. Two
# instances is a state a player stays in for minutes, so a few seconds of stale count can only
# ever delay noticing, never invent a problem.
_INSTANCES: tuple[float, int] = (0.0, 0)


def instances(ttl: float = 5.0) -> int:
    """How many REAPER processes are alive. 0 means none."""
    global _INSTANCES
    stamp, count = _INSTANCES
    if time.monotonic() - stamp < ttl:
        return count
    if sys.platform != "win32":
        count = len(_posix_pids())
        _INSTANCES = (time.monotonic(), count)
        return count
    try:
        out = _powershell(
            "@(Get-Process -Name reaper -ErrorAction SilentlyContinue).Count")
    except NoReaperInstall:
        return 0
    try:
        count = int(str(out).strip() or 0)
    except ValueError:
        count = 0
    _INSTANCES = (time.monotonic(), count)
    return count


def _rig_path() -> Path | None:
    """The rig the plugin owns. Late import: tools imports this module, not the other way round."""
    from .tools import rig_path

    try:
        return rig_path()
    except Exception:
        return None


def running() -> bool:
    """Public form of the process check: is a reaper.exe alive right now?"""
    return _running()


def stop(pid: int | None = None, wait: float = 10.0) -> bool:
    """Stop REAPER and confirm it is gone.

    "Already not running" is a success, not an error: the first thing any restart path does is
    call this, and raising there would turn a clean state into a failed boot.
    """
    if sys.platform != "win32":
        # SIGTERM first, then SIGKILL: REAPER flushes its config on a normal signal, and a
        # killed instance is the case that leaves the audio device wedged.
        import signal

        targets = [int(pid)] if pid else _posix_pids()
        for target in targets:
            try:
                os.kill(target, signal.SIGTERM)
            except OSError:
                pass
        deadline = time.time() + wait
        while time.time() < deadline:
            if not _running():
                return True
            time.sleep(0.25)
        for target in targets:
            try:
                os.kill(target, signal.SIGKILL)
            except OSError:
                pass
        time.sleep(0.5)
        return not _running()
    if pid:
        script = (
            f"if (Get-Process -Id {int(pid)} -ErrorAction SilentlyContinue) "
            f"{{ Stop-Process -Id {int(pid)} -Force -ErrorAction SilentlyContinue }}; 'done'"
        )
    else:
        script = (
            "if (Get-Process -Name reaper -ErrorAction SilentlyContinue) "
            "{ Stop-Process -Name reaper -Force -ErrorAction SilentlyContinue }; 'done'"
        )
    try:
        _powershell(script)
    except NoReaperInstall:
        pass                        # powershell itself refused; the poll below decides
    deadline = time.time() + wait
    while time.time() < deadline:
        if not _running():
            return True
        time.sleep(0.25)
    return False
