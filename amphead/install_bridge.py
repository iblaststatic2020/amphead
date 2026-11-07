"""Install and uninstall the REAPER-side bridge.

The bridge only works if two files exist inside REAPER's resource path, and until now
both were copied there by hand, which means a stranger cannot set this plugin up.

Two files, two different jobs:

* ``Scripts/hermes_bridge/bridge.lua`` is ours alone. It is copied verbatim, so an
  upgrade is a byte-for-byte replacement and ``status()`` can prove it matches the
  repo copy.
* ``Scripts/__startup.lua`` belongs to the user. REAPER runs it at every launch, so it
  is the only autoload hook there is. Ours goes in as a marked block: a user script
  that was already there is backed up once and appended to, never overwritten, and a
  re-install rewrites the block instead of stacking a second copy.

The block uses ``local`` names on purpose. The hand-installed version used globals
(``path``, ``ok``, ``err``), which quietly collides with any user startup script that
picked the same names.

Uninstall reverses both: the block comes out, the backup goes back only if removing our
block left the file with nothing in it, and our files are deleted while anything we did
not put there is left alone and reported.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

BLOCK_BEGIN = "-- >>> hermes amphead bridge >>>"
BLOCK_END = "-- <<< hermes amphead bridge <<<"

# Written between the markers. Everything here is ours; uninstall removes it wholesale.
BLOCK_BODY = """\
-- Managed by the amphead plugin (reaper/bridge.lua). Edits inside this block are
-- overwritten on the next install; put your own scripts outside the markers.
local _amphead_sep = package.config:sub(1, 1)
local _amphead_path = reaper.GetResourcePath() .. _amphead_sep .. "Scripts" .. _amphead_sep
  .. "hermes_bridge" .. _amphead_sep .. "bridge.lua"
local _amphead_ok, _amphead_err = pcall(dofile, _amphead_path)
if not _amphead_ok then
  reaper.ShowConsoleMsg("Hermes bridge failed: " .. tostring(_amphead_err) .. "\\n")
end
"""

OUR_FILES = ("bridge.lua", "request.txt", "request.lock", "alive", "lock.txt")


from . import port


class InstallError(port.RigError):
    """A setup step that cannot proceed, with the sentence a user needs to read."""


@dataclass
class InstallResult:
    resource: Path
    bridge_path: Path
    bridge_bytes: int
    startup_path: Path
    startup_action: str          # created | updated | appended | adopted | unchanged | no block
    backup: Path | None = None
    warnings: list[str] = field(default_factory=list)

    def sentence(self) -> str:
        lines = [
            f"bridge {'installed' if self.startup_action != 'unchanged' else 'already current'}: "
            f"{self.bridge_path} ({self.bridge_bytes} bytes)",
            f"startup {self.startup_path.name}: {self.startup_action}"
            + (f", backup at {self.backup.name}" if self.backup else ""),
        ]
        lines.extend(f"warning: {w}" for w in self.warnings)
        return "\n".join(lines)


@dataclass
class UninstallResult:
    resource: Path
    removed: list[Path] = field(default_factory=list)
    kept: list[Path] = field(default_factory=list)
    startup_action: str = "no block"     # no block | block removed | file deleted | backup restored
    restored: Path | None = None
    warnings: list[str] = field(default_factory=list)

    def sentence(self) -> str:
        lines = [
            f"startup {self.startup_action}"
            + (f" from {self.restored.name}" if self.restored else ""),
            f"removed {len(self.removed)} file(s)" + (f", kept {len(self.kept)} not ours" if self.kept else ""),
        ]
        lines.extend(f"kept: {p.name}" for p in self.kept)
        lines.extend(f"warning: {w}" for w in self.warnings)
        return "\n".join(lines)


def source_bridge() -> Path:
    """The repo copy that gets installed."""
    return Path(__file__).resolve().parent.parent / "reaper" / "bridge.lua"


def resource_path(explicit: str | os.PathLike | None = None) -> Path:
    """REAPER's resource path.

    ``REAPER_RESOURCE_PATH`` wins because a portable install keeps it elsewhere, then
    the Windows default. The Lua side asks REAPER itself (``GetResourcePath()``), so a
    portable install that disagrees with this function is exactly the case the warning
    in ``install()`` exists for.
    """
    from .paths import resource_path as _resource_path

    return _resource_path(explicit)


def bridge_dir(resource: Path | None = None) -> Path:
    return (resource or resource_path()) / "Scripts" / "hermes_bridge"


def startup_script(resource: Path | None = None) -> Path:
    return (resource or resource_path()) / "Scripts" / "__startup.lua"


def block_text() -> str:
    return f"{BLOCK_BEGIN}\n{BLOCK_BODY}{BLOCK_END}\n"


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _spans(text: str) -> list[tuple[int, int]]:
    """Every marked block in the file, in order. A truncated block (begin with no end)
    runs to the end of the file so a half-written install still gets cleaned up."""
    spans: list[tuple[int, int]] = []
    cursor = 0
    while True:
        start = text.find(BLOCK_BEGIN, cursor)
        if start < 0:
            return spans
        end = text.find(BLOCK_END, start)
        if end < 0:
            spans.append((start, len(text)))
            return spans
        end += len(BLOCK_END)
        spans.append((start, end))
        cursor = end


def has_block(path: Path | None = None) -> bool:
    return bool(_spans(_read(path or startup_script())))


def strip_blocks(text: str) -> str:
    out = []
    cursor = 0
    for start, end in _spans(text):
        out.append(text[cursor:start])
        cursor = end
    out.append(text[cursor:])
    return "".join(out)


def _loads_bridge(text: str) -> bool:
    """Does this startup script already dofile our bridge outside the managed block?"""
    body = strip_blocks(text)
    return "hermes_bridge" in body and "bridge.lua" in body


def _meaningful(text: str) -> bool:
    """True if anything but blank lines and the block remains. Comments count as content:
    a user who left a note in __startup.lua should not lose the file."""
    return any(line.strip() for line in text.splitlines())


def _same(a: str | Path, b: str | Path) -> bool:
    """Two script bodies equal, ignoring line endings and trailing blank lines."""
    def norm(value: str | Path) -> list[str]:
        text = value if isinstance(value, str) else _read(value)
        return [line.rstrip() for line in text.strip().splitlines()]

    return norm(a) == norm(b)


def _remove(path: Path) -> str | None:
    """Delete one of our files. Returns the sentence to show when it will not go."""
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        return f"could not delete {path.name}: {exc.strerror or exc}"
    return None


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _backup_path(path: Path) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    candidate = path.with_name(f"{path.name}.bak-{stamp}")
    n = 2
    while candidate.exists():
        candidate = path.with_name(f"{path.name}.bak-{stamp}-{n}")
        n += 1
    return candidate


def install(resource: Path | str | None = None) -> InstallResult:
    """Copy the bridge in and make REAPER load it. Safe to run repeatedly."""
    res = resource_path(resource)
    if not res.exists():
        raise InstallError(
            f"REAPER's resource folder is not there: {res}. Start REAPER once so it "
            "creates the folder, or point REAPER_RESOURCE_PATH at a portable install."
        )

    src = source_bridge()
    if not src.exists():
        raise InstallError(f"the repo copy of the bridge is missing: {src}")

    data = src.read_bytes()
    target = bridge_dir(res) / "bridge.lua"
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".lua.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, target)

    startup = startup_script(res)
    block = block_text()
    warnings: list[str] = []
    backup: Path | None = None

    existing = _read(startup) if startup.exists() else None
    if existing is None:
        _write_atomic(startup, block)
        action = "created"
    elif _spans(existing):
        spans = _spans(existing)
        head = existing[: spans[0][0]]
        tail = strip_blocks(existing[spans[-1][1] :])
        if tail.startswith("\n"):
            tail = tail[1:]          # the block's own terminator, not the user's blank line
        if not tail.strip():
            tail = ""
        rebuilt = head + block + tail
        action = "unchanged" if rebuilt == existing else "updated"
        if action == "updated":
            _write_atomic(startup, rebuilt)
    else:
        if _loads_bridge(existing):
            # The user's own startup script already dofiles our bridge. Appending a block
            # would load it twice, and the second copy would fight the first for the
            # request slot, so the installer adopts what is already there and leaves the
            # file alone. Uninstall still removes our files; the user's line stays theirs.
            action = "adopted"
        else:
            backup = _backup_path(startup)
            shutil.copy2(startup, backup)
            _write_atomic(startup, existing + ("\n\n" if existing.strip() else "") + block)
            action = "appended"

    env_root = os.environ.get("HERMES_REAPER_BRIDGE")
    if env_root:
        env_path = Path(env_root)
        if env_path.resolve() != (target.parent).resolve():
            warnings.append(
                f"HERMES_REAPER_BRIDGE points at {env_path} but REAPER autoloads "
                f"{target.parent}; the client would talk to a different folder than the bridge reads"
            )

    if existing is not None and _spans(existing) and _loads_bridge(existing):
        warnings.append(
            "the startup script loads a bridge outside the managed block as well, so REAPER "
            "would load it twice: delete those lines (they are in the backup too) and leave "
            "only the block"
        )

    return InstallResult(
        resource=res,
        bridge_path=target,
        bridge_bytes=len(data),
        startup_path=startup,
        startup_action=action,
        backup=backup,
        warnings=warnings,
    )


def uninstall(resource: Path | str | None = None) -> UninstallResult:
    """Take the block out of the user's startup script and delete our files."""
    res = resource_path(resource)
    result = UninstallResult(resource=res)
    startup = startup_script(res)

    if startup.exists():
        text = _read(startup)
        if _spans(text):
            remainder = strip_blocks(text)
            backups = sorted(startup.parent.glob(f"{startup.name}.bak-*"))
            if _meaningful(remainder) and backups and _same(remainder, backups[-1]):
                # We appended to a user script and they have not touched their part since.
                # Restoring the backup is then a byte-for-byte no-op that also proves the
                # restore path works, so do it and say so.
                shutil.copy2(backups[-1], startup)
                result.startup_action = "backup restored"
                result.restored = backups[-1]
            elif _meaningful(remainder):
                # They edited their own part after we appended: their edits win over the
                # backup, which stays on disk as a copy.
                _write_atomic(startup, remainder.lstrip("\n"))
                result.startup_action = "block removed"
            elif backups:
                newest = backups[-1]
                shutil.copy2(newest, startup)
                result.startup_action = "backup restored"
                result.restored = newest
            else:
                startup.unlink()
                result.startup_action = "file deleted"

    bdir = bridge_dir(res)
    if bdir.exists():
        for entry in sorted(bdir.iterdir()):
            if entry.name == "out" and entry.is_dir():
                for inner in sorted(entry.iterdir()):
                    if inner.is_file() and (inner.name.startswith("resp_") or inner.suffix == ".tmp"):
                        problem = _remove(inner)
                        (result.warnings if problem else result.removed).append(problem or inner)
                        if problem:
                            result.kept.append(inner)
                    else:
                        result.kept.append(inner)
                try:
                    entry.rmdir()
                except OSError:
                    result.kept.append(entry)
            elif entry.name in OUR_FILES or entry.name.endswith((".tmp", ".lock")):
                problem = _remove(entry)
                if problem:
                    # Windows keeps a handle on the lock file for as long as anything has
                    # it open, so a leftover process on a slow exit can pin it. Say which
                    # file and keep going instead of dying halfway through an uninstall.
                    result.warnings.append(problem)
                    result.kept.append(entry)
                else:
                    result.removed.append(entry)
            else:
                result.kept.append(entry)
        try:
            bdir.rmdir()
            result.removed.append(bdir)
        except OSError:
            pass

    return result


def status(resource: Path | str | None = None) -> dict:
    """What is actually on disk, compared to what the repo would install."""
    res = resource_path(resource)
    target = bridge_dir(res) / "bridge.lua"
    src = source_bridge()
    installed = target.exists()
    same = installed and src.exists() and target.read_bytes() == src.read_bytes()
    startup = startup_script(res)
    env_root = os.environ.get("HERMES_REAPER_BRIDGE")
    return {
        "resource": str(res),
        "resource_exists": res.exists(),
        "bridge_path": str(target),
        "bridge_installed": installed,
        "bridge_matches_repo": bool(same),
        "startup_path": str(startup),
        "startup_exists": startup.exists(),
        "startup_block": has_block(startup),
        "client_root": env_root or str(target.parent),
        "client_root_matches": (Path(env_root).resolve() == target.parent.resolve()) if env_root else True,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="amphead.install_bridge", description=__doc__.splitlines()[0])
    ap.add_argument("action", choices=["install", "uninstall", "status"], nargs="?", default=None)
    ap.add_argument("--resource", default=None, help="REAPER resource directory (default: the REAPER folder inside APPDATA)")
    ap.add_argument("--uninstall", action="store_true", help="same as the uninstall action")
    args = ap.parse_args(argv)

    action = "uninstall" if args.uninstall else (args.action or "status")

    try:
        if action == "install":
            print(install(args.resource).sentence())
        elif action == "uninstall":
            print(uninstall(args.resource).sentence())
        else:
            info = status(args.resource)
            for key, value in info.items():
                print(f"{key}: {value}")
    except InstallError as exc:
        print(f"cannot continue: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
