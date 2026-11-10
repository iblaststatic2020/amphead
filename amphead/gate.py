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
#: Envelope follower times. They are in milliseconds, not blocks, so the gate sounds the
#: same whether the device opened at 128 frames or 1024.
ATTACK_MS = 0.2
ENV_RELEASE_MS = 5.0


def _coeff(ms: float, rate: int) -> float:
    """One-pole coefficient for a time constant in milliseconds."""
    return 1.0 - pow(2.718281828459045, -1000.0 / (max(ms, 0.001) * rate))



def _gain(db: float) -> float:
    return 0.0 if db <= -100.0 else 10.0 ** (db / 20.0)


class Gate:
    """Envelope gate on the dry input, one threshold, sample-smooth gain ramp."""

    def __init__(self, threshold_db: float = FLOOR_DB, rate: int = 44100) -> None:
        self.rate = int(rate)
        self.threshold_db = float(threshold_db)
        self._open = 0.0          # gain at the end of the current block
        self._env = 0.0           # peak envelope, independent of the block size
        self._latched = False     # gate state after hysteresis
        self._hold = 0            # samples left to keep the gate open after the last loud one
        self._hold_samples = max(1, int(round(HOLD_MS * self.rate / 1000.0)))
        self._attack = _coeff(ATTACK_MS, self.rate)
        self._env_release = _coeff(ENV_RELEASE_MS, self.rate)
        self._close_step = 1.0 / max(1.0, CLOSE_MS * self.rate / 1000.0)

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
            self._env = 0.0
            self._latched = False
            self._hold = 0
            return x

        # Peak envelope, one sample at a time: a per-block peak made the gate open and
        # close on the loudest sample in whatever block size the device happened to use,
        # which is audible as chatter when hiss sits near the threshold.
        env = self._env
        attack = self._attack
        release = self._env_release
        latched = self._latched
        hold = self._hold
        gain = self._open
        open_thr = self.threshold
        close_thr = open_thr * 0.5
        hold_samples = self._hold_samples
        close_step = self._close_step
        out = np.empty_like(x)
        for i in range(x.size):
            sample = float(x[i])
            level = abs(sample)
            env += (attack if level > env else release) * (level - env)

            if env >= open_thr:
                latched = True
                hold = hold_samples
            elif latched:
                if env < close_thr:
                    if hold > 0:
                        hold -= 1
                    else:
                        latched = False
                else:
                    hold = hold_samples

            # Open instantly (a pick is shorter than a block); close over CLOSE_MS with
            # a per-sample ramp so the release is not a block-sized step.
            if latched:
                gain = 1.0
            elif gain > 0.0:
                gain = max(0.0, gain - close_step)

            out[i] = sample * gain

        self._env = env
        self._latched = latched
        self._hold = hold
        self._open = float(gain)
        return out
