"""Polyfill of the minimal RPP file the rig tab is opened from."""

from pathlib import Path

TEMPLATE = """<REAPER_PROJECT 0.1 "7.77/x64" {stamp}
  TEMPO 120 4 4
  SAMPLERATE 0 0 0
  LOOP 0
  <RENDER_CFG
    ZXZhdyA0NDEwMDAwMDAwMDAw
  >
>
"""


def write_template(path: str | Path, tempo: float = 120.0, stamp: int = 0) -> Path:
    """Write a minimal, valid project. REAPER only needs the header, a tempo and the tag."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(TEMPLATE.format(stamp=stamp or 1750000000), encoding="utf-8", newline="\n")
    return target
