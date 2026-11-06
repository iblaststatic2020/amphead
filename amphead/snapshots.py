"""Snapshots: what the rig sounded like, written down so it can be compared and argued with.

A snapshot is the whole rig at one moment: the chain in real order, every knob on every plugin in
raw, normalized and formatted form, and the track level. It is captured in two read-only calls per
plugin (one FX_LIST for the whole track, one FX_PARAMS sweep per plugin), so taking one costs nothing
and cannot add an undo point.

Comparison is the point. `compare(a, b)` prints what actually differs, in the plugin's own units,
and says so plainly when nothing moved. That is what makes an A/B honest: apply a preset, snapshot,
undo, snapshot again, and the tool proves whether undo really put everything back.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import bridge, rig

# A plugin can expose hundreds of parameters and the bridge caps a sweep at 64 by default. A
# snapshot wants every knob, so ask for more than any plugin here has.
KEEP_ALL = 4096

# Two normalized values this close are the same knob position; formatting noise lives below it.
SAME = 0.0005


def snapshots_dir() -> Path:
    return rig.data_dir() / "snapshots"


def _slug(text: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9._-]+", "-", str(text).strip()).strip("-")
    return clean or "snap"


@dataclass
class Knob:
    name: str
    norm: float
    raw: float
    fmt: str

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "norm": self.norm, "raw": self.raw, "fmt": self.fmt}


@dataclass
class Plugin:
    index: int
    name: str
    enabled: bool
    knobs: list[Knob] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"index": self.index, "name": self.name, "enabled": self.enabled,
                "knobs": [k.as_dict() for k in self.knobs]}

    def knob(self, name: str) -> Knob | None:
        for item in self.knobs:
            if item.name == name:
                return item
        return None


@dataclass
class Snapshot:
    label: str
    at: str
    project: str
    track: int
    db: float
    plugins: list[Plugin]
    path: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"label": self.label, "at": self.at, "project": self.project, "track": self.track,
                "db": self.db, "plugins": [p.as_dict() for p in self.plugins], "path": self.path}

    def sentence(self) -> str:
        knobs = sum(len(p.knobs) for p in self.plugins)
        return (f"snapshot={self.label} | plugins={len(self.plugins)} | knobs={knobs} | "
                f"db={self.db:.1f} | {self.path}")


def capture(label: str = "snap", track: int = 1, *, keep_file: bool = True,
            **kwargs: Any) -> Snapshot:
    """Read the whole rig and write it down. Read-only: no undo point, no writes."""
    chain = bridge.fx_list(track=track, **kwargs)
    plugins: list[Plugin] = []
    for item in chain:
        index = int(item.get("i", len(plugins)))
        name = str(item.get("name", ""))
        enabled = bool(item.get("enabled", True))
        params = bridge.fx_params(track=track, fx=index, keep=KEEP_ALL, **kwargs)
        knobs = [Knob(name=str(p.get("name", "")), norm=float(p.get("norm") or 0.0),
                      raw=float(p.get("raw") or 0.0), fmt=str(p.get("fmt", "")))
                 for p in params]
        plugins.append(Plugin(index=index, name=name, enabled=enabled, knobs=knobs))

    level = bridge.track_info(track=track, **kwargs)
    snap = Snapshot(
        label=_slug(label),
        at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        project=str(bridge.active_project(**kwargs) or ""),
        track=track,
        db=round(float(level.get("db", 0.0)), 2),
        plugins=plugins,
    )
    if keep_file:
        directory = snapshots_dir()
        directory.mkdir(parents=True, exist_ok=True)
        base = f"{time.strftime('%Y%m%d-%H%M%S')}-{snap.label}"
        path = directory / f"{base}.json"
        # Two snapshots in the same second must not overwrite each other: the whole point of taking
        # two is to compare them.
        serial = 2
        while path.exists():
            path = directory / f"{base}-{serial}.json"
            serial += 1
        path.write_text(json.dumps(snap.as_dict(), indent=1), encoding="utf-8")
        snap.path = str(path)
        # Keep the newest copy of each label on disk so `compare` can find it by name later.
        latest = directory / f"latest-{snap.label}.json"
        latest.write_text(json.dumps(snap.as_dict(), indent=1), encoding="utf-8")
    return snap


def _read(path: Path) -> Snapshot:
    raw = json.loads(path.read_text(encoding="utf-8"))
    plugins = [Plugin(index=int(p.get("index", 0)), name=str(p.get("name", "")),
                      enabled=bool(p.get("enabled", True)),
                      knobs=[Knob(name=str(k.get("name", "")), norm=float(k.get("norm") or 0.0),
                                  raw=float(k.get("raw") or 0.0), fmt=str(k.get("fmt", "")))
                             for k in p.get("knobs", [])])
               for p in raw.get("plugins", [])]
    return Snapshot(label=str(raw.get("label", path.stem)), at=str(raw.get("at", "")),
                    project=str(raw.get("project", "")), track=int(raw.get("track", 1)),
                    db=float(raw.get("db", 0.0)), plugins=plugins, path=str(path))


def find(label: str) -> Path:
    """A label, a path, or `latest`; refuses by name instead of guessing when several match."""
    candidate = Path(label)
    if candidate.is_file():
        return candidate
    directory = snapshots_dir()
    exact = directory / f"latest-{_slug(label)}.json"
    if exact.is_file():
        return exact
    matches = sorted(directory.glob(f"*-{_slug(label)}.json"))
    if matches:
        return matches[-1]
    raise LookupError(f"no snapshot called {label!r} in {directory}")


def load(label: str) -> Snapshot:
    return _read(find(label))


def catalog() -> list[dict[str, Any]]:
    rows = []
    for path in sorted(snapshots_dir().glob("*.json")):
        if path.name.startswith("latest-"):
            continue
        snap = _read(path)
        rows.append({"label": snap.label, "at": snap.at, "plugins": len(snap.plugins),
                     "knobs": sum(len(p.knobs) for p in snap.plugins), "db": snap.db,
                     "path": str(path)})
    return rows


def compare(a: Snapshot | str, b: Snapshot | str) -> list[str]:
    """Every difference between two snapshots, in the plugin's own words."""
    left = load(a) if isinstance(a, str) else a
    right = load(b) if isinstance(b, str) else b
    lines: list[str] = []

    if abs(left.db - right.db) > 0.05:
        lines.append(f"track | level: {left.db:.1f} -> {right.db:.1f} dB")

    left_by_name = {p.name: p for p in left.plugins}
    right_by_name = {p.name: p for p in right.plugins}
    for name in left_by_name:
        if name not in right_by_name:
            lines.append(f"chain | removed: {name}")
    for name in right_by_name:
        if name not in left_by_name:
            lines.append(f"chain | added: {name}")

    for name, before in left_by_name.items():
        after = right_by_name.get(name)
        if after is None:
            continue
        if before.enabled != after.enabled:
            state = "on" if after.enabled else "bypassed"
            lines.append(f"{name} | plugin: {'on' if before.enabled else 'bypassed'} -> {state}")
        # Knobs are matched by index, not by name. NAM Universal exposes two parameters both
        # called "Bypass", and matching by name compares the wrong pair and invents a difference.
        # Duplicate names are shown with their index so the line points at one real knob.
        before_labels = _labels(before)
        after_labels = _labels(after)
        after_by_index = {k_index: k for k_index, k in enumerate(after.knobs)}
        for k_index, knob in enumerate(before.knobs):
            other = after_by_index.get(k_index)
            label = before_labels[k_index]
            if other is None:
                lines.append(f"{name} | {label}: removed")
                continue
            if abs(knob.norm - other.norm) > SAME or knob.fmt != other.fmt:
                lines.append(f"{name} | {label}: {knob.fmt} -> {other.fmt}")
        for k_index, knob in enumerate(after.knobs):
            if k_index >= len(before.knobs):
                lines.append(f"{name} | {after_labels[k_index]}: added ({knob.fmt})")
    return lines


def _labels(plugin: Plugin) -> list[str]:
    """Knob names, with the duplicated ones qualified by index so no line is ambiguous."""
    counts: dict[str, int] = {}
    for knob in plugin.knobs:
        counts[knob.name] = counts.get(knob.name, 0) + 1
    return [knob.name if counts[knob.name] == 1 else f"{knob.name} [#{index}]"
            for index, knob in enumerate(plugin.knobs)]


def summary(a: Snapshot | str, b: Snapshot | str) -> str:
    left = load(a) if isinstance(a, str) else a
    right = load(b) if isinstance(b, str) else b
    lines = compare(left, right)
    head = f"ab | {left.label} -> {right.label} | differences={len(lines)}"
    if not lines:
        return head + " | identical: nothing moved"
    return "\n".join([head] + lines)
