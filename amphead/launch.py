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

from . import bridge

WINDOWS_CANDIDATES = [
    r"C:\Program Files\REAPER (x64)\reaper.exe",
    r"C:\Program Files\REAPER\reaper.exe",
    r"C:\Program Files (x86)\REAPER\reaper.exe",
]

# Windows show-state constants, used only for the hidden-desktop option.
HIDDEN_LAUNCHER = Path(__file__).resolve().parents[1] / "scripts" / "reaper_hidden.ps1"


class NoReaperInstall(RuntimeError):
    """REAPER is not installed where we can find it."""


def find_reaper() -> Path:
    """Locate reaper.exe: env override, then the usual install paths, then PATH."""
    override = os.environ.get("REAPER_EXE")
    if override and Path(override).is_file():
        return Path(override)
    for candidate in WINDOWS_CANDIDATES:
        if Path(candidate).is_file():
            return Path(candidate)
    found = shutil.which("reaper") or shutil.which("reaper.exe")
    if found:
        return Path(found)
    raise NoReaperInstall(
        "reaper.exe not found; set REAPER_EXE to its full path"
    )


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


def start(minimized: bool = True, hidden_desktop: bool = False) -> int | None:
    """Launch REAPER. Default minimized: no window, no focus steal, taskbar icon only."""
    exe = find_reaper()
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


def ensure_audio(timeout: float = 20.0) -> dict:
    """Open the audio device if it is closed. ASIO stays locked after a killed REAPER."""
    info = bridge.devinfo(timeout=3.0)
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
    out = _powershell(_MINIMIZE_PS.format(max_wait), timeout=max_wait + 20)
    return out.strip().endswith("minimized")


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


def state() -> dict:
    """One call for the pane: bridge, project, safety, device, latency.

    Never raises for a missing host. A stale heartbeat is the normal aftermath of quitting
    REAPER, so "down" with a reason is a state, not an exception: the pane has to be able to
    draw "REAPER NOT RUNNING" instead of crashing on the way to it.
    """
    try:
        info = bridge.devinfo(timeout=3.0)
        status = bridge.status(timeout=3.0)
    except bridge.ReaperError as exc:
        alive = _running()
        return {
            "bridge": "down" if alive else "not-running",
            # The client guesses "a dialog probably has it"; only say that when the process exists.
            "reason": str(exc) if alive else "REAPER is not running; the bridge heartbeat is stale",
            "project": None,
            "path": "",
            "tracks": 0,
            "device": {},
        }
    return {
        "bridge": "live",
        "project": status.get("project") or "(unnamed)",
        "path": status.get("path") or "",
        "tracks": status.get("tracks", 0),
        "device": {
            "driver": info.get("ident_in") or "",
            "mode": info.get("mode") or "",
            "closed": bool(info.get("device_closed")),
            "buffer": info.get("bsize") or "",
            "rate": info.get("srate") or "",
            "bits": info.get("bps") or "",
            "inputs": int(info.get("inputs") or 0),
            "latency_total_ms": info.get("device_latency_ms"),
            "latency_out_ms": info.get("output_latency_ms"),
        },
    }


def _running() -> bool:
    """Is a reaper.exe alive right now? Used by stop() and by ensure_running()."""
    if sys.platform != "win32":
        return False
    try:
        out = _powershell("if (Get-Process -Name reaper -ErrorAction SilentlyContinue) { 'yes' } else { 'no' }")
    except NoReaperInstall:
        return False
    return "yes" in out


def running() -> bool:
    """Public form of the process check: is a reaper.exe alive right now?"""
    return _running()


def stop(pid: int | None = None, wait: float = 10.0) -> bool:
    """Stop REAPER and confirm it is gone.

    "Already not running" is a success, not an error: the first thing any restart path does is
    call this, and raising there would turn a clean state into a failed boot.
    """
    if sys.platform != "win32":
        raise NoReaperInstall("stopping REAPER is Windows-only for now")
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
