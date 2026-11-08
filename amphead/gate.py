"""A noise gate in front of the amp.

A guitar on a stand with the capture at +15 dB is not silence, it is hiss, hum and the
fridge; the gate is what turns that into silence. This one is deliberately three knobs
short of a real gate pedal: one threshold, because that is the only control a player
reaches for between takes.

Two details matter more than the DSP:

* Off means off. At the bottom of the knob the block is returned untouched, so a gate that
  is not wanted cannot colour the amp or cost a millisecond.
* It opens fast and closes slowly, and the closing is ramped across blocks. A gate that
  snaps shut on a decaying chord is the click people blame on the audio driver.
"""

from __future__ import annotations

from .limits import GATE

#: The same range the engine and the pane use, so the knob cannot ask for a threshold the
#: gate would refuse.
FLOOR_DB, CEIL_DB = GATE
# Opening has to be immediate or the pick attack is eaten; closing is slack so a ringing
# chord or a held note does not chatter.
HOLD_MS = 40.0
CLOSE_MS = 90.0


def _gain(db: float) -> float:
    return 0.0 if db <= -100.0 else 10.0 ** (db / 20.0)


class Gate:
    """Envelope gate on the dry input, one threshold, sample-smooth gain ramp."""

    def __init__(self, threshold_db: float = FLOOR_DB, rate: int = 44100) -> None:
        self.rate = int(rate)
        self.threshold_db = float(threshold_db)
        self._open = 0.0          # gain at the start of the current block
        self._hold = 0            # blocks left to keep the gate open after the last loud one
        self._hold_blocks = max(1, int(round(HOLD_MS * self.rate / 1000.0 / 256.0)))

    @property
    def threshold(self) -> float:
        return _gain(self.threshold_db)

    def set_threshold(self, db: float) -> float:
        self.threshold_db = max(FLOOR_DB, min(CEIL_DB, float(db)))
        return self.threshold_db

    @property
    def off(self) -> bool:
        return self.threshold_db <= FLOOR_DB + 0.5

    def process(self, block):
        import numpy as np

        x = np.asarray(block, dtype=np.float32).reshape(-1)
        if self.off or x.size == 0:
            self._open = 1.0
            return x

        level = float(np.max(np.abs(x)))
        # 6 dB of hysteresis: a note decaying across the threshold must not flutter it.
        loud = level >= self.threshold if self._open < 0.5 else level >= self.threshold * 0.5
        if loud:
            self._hold = self._hold_blocks
            target = 1.0
        elif self._hold > 0:
            self._hold -= 1
            target = 1.0
        else:
            target = 0.0

        block_ms = 1000.0 * x.size / float(self.rate)
        # Open at once: a pick attack is shorter than a block, and a gate that fades up eats
        # it. Close a slice at a time so a decaying note fades rather than clicks.
        if target >= self._open:
            gain_now = 1.0
        else:
            gain_now = max(target, self._open - block_ms / max(CLOSE_MS, block_ms))
        start = self._open
        self._open = float(min(1.0, max(0.0, gain_now)))
        ramp = np.linspace(start, self._open, x.size, dtype=np.float32)
        return x * ramp
