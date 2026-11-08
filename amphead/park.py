"""The amp parks itself when the guitar has been quiet for a long time.

A head left live with the master up and nobody playing is a powered amp with the guitar on the
stand: no music, and the interface is still held. This is the timer for that, and it watches the
*input* rather than the output, because the output goes silent the moment the master is parked
while the guitar is still arriving.

Pure on purpose: it takes a peak and a clock reading, returns a decision, and holds no device.
"""

from __future__ import annotations

import math
import time

#: Room noise, a hand on the strings and a fan are not playing. -50 dB is well above the noise
#: floor of a quiet interface and well below anything a pick makes.
QUIET_DB = -50.0

#: Long enough that a break between songs never trips it, short enough that an amp forgotten
#: overnight does. Picking a number by ear is impossible, so this one is a stated choice.
IDLE_SECONDS = 15 * 60


def _db(peak: float) -> float:
    peak = float(peak or 0.0)
    if peak <= 0.0:
        return -math.inf
    return 20.0 * math.log10(peak)


class AutoPark:
    """Counts how long the input has been quiet and says once when it has had enough."""

    def __init__(self, idle_seconds: float = IDLE_SECONDS, quiet_db: float = QUIET_DB,
                 clock=None) -> None:
        self.idle_seconds = float(idle_seconds)
        self.quiet_db = float(quiet_db)
        self._clock = clock or time.monotonic
        self._last = self._clock()
        self._tripped = False

    def reset(self, now: float | None = None) -> None:
        """Start counting again: the player touched the amp, so the silence is over."""
        self._last = self._clock() if now is None else float(now)
        self._tripped = False

    def hear(self, peak_in: float, now: float | None = None) -> bool:
        """Feed one block's input peak. True exactly once per quiet stretch, when it trips.

        A peak above the quiet floor restarts the count, so a player who plays for one bar every
        fourteen minutes keeps the amp live.
        """
        moment = self._clock() if now is None else float(now)
        if _db(peak_in) > self.quiet_db:
            self._last = moment
            self._tripped = False
            return False
        if self._tripped:
            return False
        if (moment - self._last) < self.idle_seconds:
            return False
        self._tripped = True
        # The countdown restarts at the trip so a caller that ignores the return value (or a
        # caller that resets while parking) cannot be told twice for the same silence.
        self._last = moment
        return True

    @property
    def quiet_for(self) -> float:
        """Seconds since the input last rose above the quiet floor."""
        return max(0.0, self._clock() - self._last)

    @property
    def tripped(self) -> bool:
        return self._tripped
