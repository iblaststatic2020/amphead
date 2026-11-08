"""The numbers every layer has to agree on.

One control is clamped in four places: the engine that applies it, the route the pane
posts to, the tool chat calls, and the JS that draws the knob. Four copies of "-24, 24"
is four chances for a knob to promise a range the engine will not accept. So the ranges
live here, once, and everything else asks.

Nothing in this module imports anything: it is the bottom of the stack.
"""

from __future__ import annotations

#: Master ramp. The floor is silence on purpose; the ceiling is the engine's, not a DAW's.
MASTER = (-60.0, 12.0)
#: Noise gate threshold. The bottom is "off", the ceiling is loud enough to be a mistake.
GATE = (-80.0, -20.0)
#: Either side of the amp: how hard the capture is driven, how the cabinet is balanced.
TRIM = (-24.0, 24.0)
#: Bass, mid, treble, the way one knob on a real head moves.
TONE = (-12.0, 12.0)

#: At or below this the gate is off, not merely low.
GATE_OFF_DB = -79.5

#: Silence is the master sitting on its floor. Somewhere below the gate's own floor, so a
#: player can tell "parked" from "quiet" without reading a number.
SILENT_DB = MASTER[0]


def clamp(bounds: tuple[float, float], value: float) -> float:
    """Put a number inside a range without ever trusting the caller."""
    low, high = bounds
    return max(low, min(high, float(value)))


def master(db: float) -> float:
    return clamp(MASTER, db)


def gate(db: float) -> float:
    return clamp(GATE, db)


def trim(db: float) -> float:
    return clamp(TRIM, db)


def tone(db: float) -> float:
    return clamp(TONE, db)


def gate_off(db: float | None) -> bool:
    """Whether a threshold means "no gate" rather than "a very low gate"."""
    return db is None or float(db) <= GATE_OFF_DB
