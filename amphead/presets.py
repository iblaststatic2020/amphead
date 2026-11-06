"""Presets as intent, not as a list of numbers frozen to one plugin version.

A preset file says "the drive, about three quarters up" and names the plugin and the knob the way a
player would. Indices are never stored: every apply resolves the plugin and the parameter against the
live chain, so a preset still works after the chain is reordered, and a preset whose plugin is not on
the rig is reported rather than silently skipped.

The whole preset goes in as one BATCH, which is one undo point: a player who dislikes the sound hits
undo once and is back where they were, not five times. Each write is still checked by reading it back,
and a knob that refuses is named in the result instead of being counted as done.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import bridge, rig

PRESETS_DIR = Path(__file__).with_name("presets")


@dataclass
class Change:
    """One knob, before and after, in the plugin's own words."""

    fx: str
    param: str
    before: str
    after: str
    norm_before: float
    norm_after: float
    ok: bool = True
    note: str = ""

    def line(self) -> str:
        arrow = "->" if self.ok else "=(no change)"
        text = f"{self.fx} | {self.param}: {self.before} {arrow} {self.after}"
        return f"{text} ({self.note})" if self.note else text


@dataclass
class Applied:
    preset: str
    changes: list[Change] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    seconds: float = 0.0

    @property
    def refused(self) -> list[Change]:
        return [c for c in self.changes if not c.ok]

    @property
    def landed(self) -> list[Change]:
        return [c for c in self.changes if c.ok]

    @property
    def moved(self) -> list[Change]:
        """Knobs whose value really changed, as opposed to ones already where the preset wanted."""
        return [c for c in self.landed if abs(c.norm_after - c.norm_before) > 1e-4]

    @property
    def already(self) -> list[Change]:
        return [c for c in self.landed if c not in self.moved]

    def sentence(self) -> str:
        return (f"preset={self.preset} | changed={len(self.moved)}"
                f"{f' | already there={len(self.already)}' if self.already else ''}"
                f"{f' | refused={len(self.refused)}' if self.refused else ''}"
                f"{f' | not on this rig={len(self.missing)}' if self.missing else ''}"
                f" | {self.seconds:.2f}s | one undo point")


def path_for(name: str) -> Path:
    """A preset name means ``presets/<name>.json``; anything with a suffix is a path."""
    candidate = Path(name)
    if candidate.suffix.lower() == ".json" or candidate.is_absolute() or "/" in name or "\\" in name:
        return candidate
    return PRESETS_DIR / f"{name}.json"


def load(name: str) -> dict[str, Any]:
    target = path_for(name)
    if not target.is_file():
        raise LookupError(f"no preset called {name!r}: {target} does not exist")
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"preset {target} could not be read: {exc}") from exc
    if not isinstance(data.get("targets"), dict) or not data["targets"]:
        raise ValueError(f"preset {target} has no targets")
    data.setdefault("name", target.stem)
    return data


def catalog() -> list[dict[str, Any]]:
    """Every preset that ships with the plugin, with what it touches."""
    rows = []
    if not PRESETS_DIR.is_dir():
        return rows
    for target in sorted(PRESETS_DIR.glob("*.json")):
        try:
            data = load(str(target))
        except (LookupError, ValueError):
            continue
        knobs = ", ".join(f"{fx}: {len(params)} knob(s)" for fx, params in data["targets"].items())
        rows.append({"name": data["name"], "about": data.get("about", ""), "targets": knobs})
    return rows


_LONG_FLOAT = re.compile(r"-?\d+\.\d{3,}")


def tidy(fmt: str) -> str:
    """Trim float noise off a reported value: a plugin saying -4.7999992 means -4.8.

    Only the shown string changes; the normalized value in the record keeps REAPER's precision.
    """
    def shorten(match: re.Match) -> str:
        text = f"{float(match.group()):.2f}".rstrip("0").rstrip(".")
        return text or "0"

    return _LONG_FLOAT.sub(shorten, fmt or "")


def _want(spec: Any) -> tuple[float, str | None]:
    """A preset value is either a normalized number or ``{"value": -3, "unit": "dB"}``."""
    if isinstance(spec, dict):
        return float(spec.get("value")), spec.get("unit")
    return float(spec), None


def apply(name: str, *, track: int = 1, expect: str | None = None,
          log_path: Path | str | None = None, **kwargs: Any) -> Applied:
    """Apply a preset in one undo point, then prove which knobs moved."""
    started = time.time()
    preset = load(name)
    label = str(preset.get("name") or name)
    if expect is None:
        expect = bridge.active_project()

    ops: list[tuple[str, dict]] = []
    rows: list[dict[str, Any]] = []
    missing: list[str] = []

    if preset.get("track_db") is not None:
        ops.append(("TRACK_VOL", {"track": track, "db": f"{float(preset['track_db']):g}"}))

    for fx_query, params in preset["targets"].items():
        try:
            fx_index, fx_name, _chain = rig._resolve_fx(fx_query, track)
        except LookupError as exc:
            missing.append(f"{fx_query}: {exc}")
            continue
        for param_query, spec in params.items():
            try:
                param_index, param_name = rig._resolve_param(param_query, fx_index, track)
            except LookupError as exc:
                missing.append(f"{fx_query} | {param_query}: {exc}")
                continue
            info = rig.read_param(fx_index, param_index, track=track, **kwargs)
            value, unit = _want(spec)
            wanted, amount, mode, clamped = rig._plan(value, info, "auto", unit)
            already = abs(float(info.get("norm", 0.0)) - wanted) <= rig.TOLERANCE
            if not already:
                # A knob that is already where the preset wants it is left alone: writing it would
                # add nothing to the sound and one more line of noise to the diff.
                ops.append(("FX_SETV", {"track": track, "fx": fx_index, "param": param_index,
                                        "value": f"{amount:.6f}", "mode": mode}))
            rows.append({
                "already": already,
                "fx": fx_index, "fx_name": fx_name,
                "param": param_index, "param_name": param_name,
                "wanted": wanted, "amount": amount, "mode": mode, "clamped": clamped,
                "before_fmt": str(info.get("fmt", "")),
                "before_norm": float(info.get("norm", 0.0)),
                "low": float(info.get("min", 0.0)), "high": float(info.get("max", 1.0)),
            })

    if not ops:
        return Applied(preset=label, missing=missing, seconds=round(time.time() - started, 2),
                       changes=[
                           Change(fx=row["fx_name"], param=row["param_name"],
                                  before=tidy(row["before_fmt"]), after=tidy(row["before_fmt"]),
                                  norm_before=round(row["before_norm"], 4),
                                  norm_after=round(row["before_norm"], 4), note="already there")
                           for row in rows])

    try:
        bridge.batch(ops, label=f"Hermes: preset {label}", **bridge.guard(expect))
    except bridge.CommandFailed as exc:
        # Nothing half-applied is reported as applied: the batch stopped, and the tab is the state.
        raise rig.RigRefused(exc.payload, f"preset {label} stopped early: {exc}") from exc

    # Read every knob back before deciding anything: the batch is one undo point, and a refusal is
    # a fact about the plugin, not a reason to stop reporting the rest.
    after_rows: dict[int, dict[str, Any]] = {}
    for row in rows:
        after_rows[id(row)] = rig.read_param(row["fx"], row["param"], track=track, **kwargs)

    def settled(row: dict[str, Any], info: dict[str, Any]) -> bool:
        return (abs(float(info.get("norm", 0.0)) - row["wanted"]) <= rig.TOLERANCE
                and not rig.is_garbage(info.get("fmt", "")))

    stubborn = [row for row in rows if not settled(row, after_rows[id(row)])]
    retry_note: dict[int, str] = {}
    if stubborn:
        # One corrective batch in the other write mode, so a plugin that only honours one of the
        # two paths costs one more undo point rather than one per knob.
        retry_ops = []
        for row in stubborn:
            other = "raw" if row["mode"] == "norm" else "norm"
            span = row["high"] - row["low"]
            amount = row["low"] + row["wanted"] * span if other == "raw" else row["wanted"]
            retry_ops.append(("FX_SETV", {"track": track, "fx": row["fx"], "param": row["param"],
                                          "value": f"{amount:.6f}", "mode": other}))
        try:
            bridge.batch(retry_ops, label=f"Hermes: preset {label} (retry)",
                         **bridge.guard(expect))
        except bridge.CommandFailed as exc:
            for row in stubborn:
                retry_note[id(row)] = f"retry failed: {exc}"
        else:
            for row in stubborn:
                again = rig.read_param(row["fx"], row["param"], track=track, **kwargs)
                after_rows[id(row)] = again
                if settled(row, again):
                    retry_note[id(row)] = "landed on the retry"
                else:
                    retry_note[id(row)] = "the retry did not move it"

    changes: list[Change] = []
    for row in rows:
        after = after_rows[id(row)]
        norm_after = float(after.get("norm", 0.0))
        fmt = str(after.get("fmt", ""))
        ok = settled(row, after)
        note = "" if ok and not retry_note.get(id(row)) else retry_note.get(id(row), "")
        if not ok and not note:
            note = ("the plugin reports a value that is not a number"
                    if rig.is_garbage(fmt) else "the value did not move")
        change = Change(
            fx=row["fx_name"], param=row["param_name"],
            before=tidy(row["before_fmt"]), after=tidy(fmt),
            norm_before=round(row["before_norm"], 4), norm_after=round(norm_after, 4),
            ok=ok, note=note or ("already there" if row.get("already") else ""),
        )
        changes.append(change)
        rig._append_log(log_path, {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "op": "preset", "preset": label,
            "track": track, "fx": row["fx_name"], "param": row["param_name"],
            "requested_norm": round(row["wanted"], 6), "norm": round(norm_after, 6),
            "fmt": fmt, "verdict": "ok" if ok else "refused", "reason": note,
        })

    result = Applied(preset=label, changes=changes, missing=missing,
                     seconds=round(time.time() - started, 2))
    rig._append_log(log_path, {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "op": "preset_done",
                               "preset": label, "changed": len(result.landed),
                               "refused": len(result.refused), "missing": missing})
    return result


def diff_lines(applied: Applied) -> list[str]:
    """What actually changed, in the plugin's own units, ready to print."""
    lines = [change.line() for change in applied.changes]
    for name in applied.missing:
        lines.append(f"not on this rig: {name}")
    return lines
