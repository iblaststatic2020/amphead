"""Look inside a NAM plugin's state blob without opening an audio device.

The question this answers: can a headless host choose the capture? Everything the plugin knows
lives in the VST3 component state, and `NO_REAPER.md` recorded it as `VC2! <len> <xml>`. This
script prints what is actually in there, element by element, so the answer is measured rather
than assumed.

Run with the engine venv (outside the repo, see scripts/engine_env.ps1):
    "$LOCALAPPDATA/amphead/engine-venv/Scripts/python" scripts/probe_vst_state.py
"""

from __future__ import annotations

import os
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

VST = Path(r"C:\Program Files\Common Files\VST3\NAM Universal.vst3")
CAPTURE_ROOTS = [Path(r"C:\Users\Chris\Documents\Guitar Amp Sims"), Path(r"C:\Users\Chris\Downloads")]

#: Names that would mean "this element points at an amp model or a cab".
INTERESTING = re.compile(r"model|capture|ir\b|file|path|preset|name", re.I)


def load():
    from pedalboard import load_plugin

    if not VST.exists():
        raise SystemExit(f"missing: {VST}")
    return load_plugin(str(VST))


def xml_span(data: bytes) -> tuple[int, int] | None:
    start = data.find(b"<?xml")
    if start < 0:
        start = data.find(b"<PARAMETERS")
        if start < 0:
            return None
    end = data.find(b"</PARAMETERS>", start)
    if end < 0:
        return None
    return start, end + len(b"</PARAMETERS>")


def report_xml(xml: bytes) -> list[str]:
    """Every element whose tag or attribute looks like it names a file."""
    root = ET.fromstring(xml)
    lines = []
    for el in root.iter():
        attrs = {k: v for k, v in el.attrib.items() if INTERESTING.search(k) or INTERESTING.search(v or "")}
        if INTERESTING.search(el.tag) or attrs:
            text = (el.text or "").strip()[:120]
            lines.append("<%s> %s text=%r" % (el.tag, attrs, text))
    return lines


def main() -> int:
    plugin = load()
    params = plugin.parameters
    print(f"parameters: {len(params)} of type {type(params).__name__}")
    print("first 12: " + ", ".join(list(params)[:12]))

    raw = bytes(plugin.raw_state or b"")
    print(f"raw_state: {len(raw)} bytes")
    print(f"head: {raw[:48]!r}")

    if b"VC2!" in raw:
        i = raw.find(b"VC2!")
        print(f"VC2! at {i}, 12 bytes after: {raw[i:i+12]!r}")
    span = xml_span(raw)
    if span:
        start, end = span
        print(f"xml: {end - start} bytes at {start}")
        lines = report_xml(raw[start:end])
        print(f"interesting elements: {len(lines)}")
        for line in lines[:40]:
            print("  " + line)
    else:
        print("no XML document in this blob; capture is somewhere else")
        # A JUCE IComponent blob usually hides a nested length-prefixed child state.
        for marker in (b"<", b"{\"", b"<?xml"):
            print(f"  count of {marker!r}: {raw.count(marker)}")

    names = []
    for root in CAPTURE_ROOTS:
        if root.exists():
            names += [p for p in root.rglob("*.nam")]
    print(f"\n.nam files reachable: {len(names)}")
    for p in names[:5]:
        print(f"  {p}")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    raise SystemExit(main())
