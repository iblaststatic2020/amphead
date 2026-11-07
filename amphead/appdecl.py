"""REAPER as an application declaration, and what this host says about it.

Hermes has a vocabulary for "this plugin fronts a desktop application": name the app, say where
it lives per OS, say what version you need, and one evaluator answers present / missing / too
old / wrong OS. AmpHead used to answer that question itself, in five hand-written paths and a
sentence per tool. This module means REAPER is described once, in the host's own shape.

Two things worth knowing before changing it:

AmpHead is a native plugin, not an MCP server, so the core's automatic gate (the MCP check_fn)
never fires for our tools: registering the declaration does not by itself hide anything. We read
`availability()` ourselves and turn its state into the sentence a player gets. Registering is
still right: it names REAPER for the host, and it makes a skill that says `requires_apps: [reaper]`
resolve correctly.

`hermes_platform` ships with Hermes, but the plugin must still import without it (a test run, a
checkout without the host). Every entry point here catches ImportError and degrades to "unknown",
which reads as "we could not check", never as "REAPER is not installed".
"""

from __future__ import annotations

import sys
from functools import lru_cache
from typing import Any

#: The name the declaration is registered under, and what a skill would say in `requires_apps:`.
NAME = "reaper"

#: REAPER 7.0 is the floor: earlier versions do not answer the chunk calls the rig needs.
MIN_VERSION = "7.0"

# `%ProgramFiles%` covers 64-bit and the (x86) folder on a 32-bit-only install; the uninstall
# registry entry is what finds a copy installed somewhere else. A portable install has neither,
# which is why REAPER_EXE is still honoured first in launch.find_reaper().
RAW_APP: dict[str, Any] = {
    "win32": {
        "presence": "executable",
        "location": [
            r"%ProgramFiles%/REAPER (x64)/reaper.exe",
            r"%ProgramFiles%/REAPER/reaper.exe",
            r"%ProgramFiles%/REAPER (x86)/reaper.exe",
            {"kind": "uninstall_registry", "display_name_prefix": "REAPER", "file": "reaper.exe"},
        ],
        # REAPER's exe carries a PE version resource; nothing else on Windows has one reliably.
        "version": {"kind": "pe_resource"},
    },
    # REAPER ships as a normal .app bundle, and its Info.plist carries the version. Same
    # product, same ReaScript bridge, same resource directory layout under ~/Library.
    "darwin": {
        "presence": "bundle",
        "location": [{"kind": "app_bundle", "name": "REAPER"}],
        "version": {"kind": "plist"},
    },
    # The Linux build is a bare executable. It has no version source we can read from a file
    # (the vocabulary allows that for linux alone), so presence is the whole check.
    "linux": {
        "presence": "executable",
        "location": [
            "/opt/REAPER/reaper",
            "~/REAPER/reaper",
            "/usr/local/REAPER/reaper",
        ],
        "version": {"kind": "none"},
    },
}

RAW_REQUIRES: dict[str, Any] = {"app": True, "min_version": MIN_VERSION}

WHERE = "amphead/plugin.yaml"


@lru_cache(maxsize=1)
def declaration():
    """The parsed declaration, or None when the host library is not importable."""
    try:
        from hermes_platform import declaration as vocab
    except ImportError:
        return None
    try:
        return vocab.parse_declaration(
            NAME, raw_app=RAW_APP, raw_requires=RAW_REQUIRES, where=WHERE
        )
    except Exception:  # a vocabulary change must not take tool registration down
        return None


def register() -> bool:
    """Register the declaration with the host. Idempotent; safe to call on every load.

    Returns True when a declaration is registered, False when the library is absent. Never
    raises: registration is a courtesy to the host, not a precondition for the plugin.
    """
    decl = declaration()
    if decl is None:
        return False
    try:
        from hermes_platform import declaration as vocab

        vocab.register(NAME, decl)
        return True
    except Exception:
        return False


def state() -> dict[str, Any]:
    """Ask the host's evaluator what it makes of REAPER, as a plain dict.

    `state` is one of the vocabulary's words (`available`, `missing_app`, `version_too_old`,
    `unsupported_os`, `no_requirements`) plus `unknown` for "the library is not here to ask".
    """
    decl = declaration()
    if decl is None:
        return {"state": "unknown", "version": None, "path": None, "min_version": MIN_VERSION}
    try:
        # `hermes_platform.resolver.availability` is the reader the developer-guide page names
        # but it is not in the published plugin_api.json, so `hermes plugins validate` reports
        # it as a private core import. It is imported here, inside the try, because the whole
        # point of the guard is that a release which moves it degrades to "unknown" instead of
        # taking the plugin down. The fallback below still finds REAPER by path.
        from hermes_platform.resolver import availability

        found = availability.availability(decl)
        return found.as_dict()
    except Exception as exc:  # pragma: no cover - only on a broken host install
        return {
            "state": "unknown",
            "version": None,
            "path": None,
            "min_version": MIN_VERSION,
            "detail": str(exc)[:200],
        }


def sentence(found: dict[str, Any] | None = None) -> str:
    """One sentence a person can act on, for each state the evaluator can report."""
    found = found or state()
    st = found.get("state")
    version = found.get("version")
    path = found.get("path")
    if st == "available":
        return f"REAPER {version or 'installed'} at {path}"
    if st == "missing_app":
        return ("REAPER is not installed on this machine; install it from reaper.fm, "
                "or set REAPER_EXE to a portable copy")
    if st == "version_too_old":
        return (f"REAPER {version or 'version unreadable'} is older than {MIN_VERSION}; "
                f"update it, then ask again")
    if st == "unsupported_os":
        return (f"AmpHead drives REAPER on Windows, macOS and Linux; this host reports "
                f"{sys.platform}, which is none of the three")
    if st == "unknown":
        return "REAPER_EXE or a standard install path did not resolve"
    return f"REAPER state={st}"


def located() -> str | None:
    """The executable path the host's evaluator found, or None.

    None means "no usable copy", which covers missing, too old and wrong OS: a caller that is
    about to spawn a process should treat all three the same way and read sentence() for why.
    """
    found = state()
    if found.get("state") == "available":
        return found.get("path") or None
    return None


def unavailable_reason() -> str | None:
    """The sentence to report when there is nothing to launch, else None."""
    found = state()
    if found.get("state") in ("available", "no_requirements", "unknown"):
        return None
    return sentence(found)
