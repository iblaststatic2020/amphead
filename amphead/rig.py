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
from dataclasses import dataclass, asdict
from pathlib import Path
import time
from typing import Any

from . import bridge, resolver

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
