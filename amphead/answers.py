"""How an answer is shaped, as opposed to what happened.

Every handler answers in one line of prose, because that line is what a player reads in a pane
and what a test asserts on. The host wants one JSON string. Both are true at once, so the
shaping lives here: the handlers decide what happened and say it plainly, and this module turns
that sentence into the thing the host registers, with named fields instead of a pipe line the
agent would have to parse.

Nothing here knows what an amp is, so it can be read in one sitting.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any


def fmt(fields: list[tuple[str, Any]]) -> str:
    """One line, `key=value | key=value`, the shape every handler answers in."""
    return " | ".join(f"{k}={v}" for k, v in fields)


def db_level(amplitude: float) -> str:
    """A player reads dB, not a raw amplitude like 0.014."""
    if amplitude <= 0:
        return "-inf"
    return f"{20 * math.log10(amplitude):.1f}"


#: The opening words that mean the sentence is a refusal rather than a report. Judged on the
#: first clause only, because a later clause may legitimately contain "not" ("ARMED: ... gate
#: is not set").
FAILURE_WORDS = ("REFUSED", "NOT", "CANNOT", "ERROR", "NO ")


def ok(summary: str) -> bool:
    """Whether a sentence reports success, judged on the opening clause of every line.

    Every line, because a handler that answers in two lines says the bad news second: "capture=X
    | apply=True" followed by "not loaded: the engine is not running" is a failure, and reading
    only the first line called it a success.
    """
    for line in summary.splitlines() or [summary]:
        clause = line.strip().split(":")[0].upper()
        if clause.startswith(FAILURE_WORDS):
            return False
        # Parentheses describe, they do not verdict. "STANDBY (device not open yet)" is standby,
        # while "ENGINE NOT RUNNING" is a failure; the difference is inside the brackets.
        if " NOT " in " " + re.sub(r"\([^)]*\)", " ", clause) + " ":
            return False
    return True


def json_answer(name: str, summary: str) -> str:
    """One JSON string: the tool, the sentence, whether it failed, and the numbers it carried."""
    payload: dict[str, Any] = {"tool": name, "ok": ok(summary), "summary": summary}
    fields: dict[str, str] = {}
    for part in summary.split("|"):
        key, sep, value = part.partition("=")
        key = key.strip()
        if sep and key and len(key) <= 40 and "\n" not in key:
            fields[key] = value.strip()
    if fields:
        payload["fields"] = fields
        # The words can be cheerful while the numbers say otherwise: "panicked=False | ...
        # | problems=master is at 0.0 dB" opens like a report. Trust the fields over the prose.
        problems = fields.get("problems", "none").strip().lower()
        if payload["ok"] and problems not in ("", "none"):
            payload["ok"] = False
        if payload["ok"] and fields.get("panicked", "").strip().lower() == "false":
            payload["ok"] = False
    return json.dumps(payload, ensure_ascii=False)


def call_shape(name: str, handler):
    """The handler the host registers: the same work, answered as one JSON string."""

    def call(args: dict[str, Any]) -> str:
        return json_answer(name, handler(args))

    call.__name__ = f"{name}_json"
    call.__doc__ = handler.__doc__
    return call
