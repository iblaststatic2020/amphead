"""The gate: silence when the guitar is quiet, the guitar when it is not."""

from __future__ import annotations

import unittest

try:
    import numpy as np
except Exception as exc:  # the plugin interpreter has no numpy; the gate runs in the engine's
    raise unittest.SkipTest(f"the gate needs numpy: {exc}")

from amphead.gate import CEIL_DB, CLOSE_MS, FLOOR_DB, Gate


def block(peak: float, n: int = 256) -> np.ndarray:
    return np.full(n, peak, dtype=np.float32)


def run(gate: Gate, peak: float, blocks: int) -> np.ndarray:
    out = block(peak)
    for _ in range(blocks):
        out = gate.process(out)
    return out


class TheGateIsOffUntilAsked(unittest.TestCase):
    def test_the_bottom_of_the_knob_returns_the_signal_untouched(self):
        x = np.linspace(-0.2, 0.2, 256, dtype=np.float32)
        self.assertTrue(np.array_equal(Gate().process(x), x))

    def test_off_means_no_ramp_even_on_silence(self):
        gate = Gate()
        quiet = block(0.00001)
        self.assertTrue(np.array_equal(gate.process(quiet), quiet))

    def test_the_floor_is_the_default(self):
        self.assertEqual(Gate().threshold_db, FLOOR_DB)
        self.assertTrue(Gate().off)


class TheGateClosesOnHiss(unittest.TestCase):
    def test_hiss_below_the_threshold_is_closed(self):
        gate = Gate(threshold_db=-40.0)
        out = run(gate, 0.001, 40)
        self.assertEqual(gate._open, 0.0)
        self.assertEqual(float(np.max(np.abs(out))), 0.0)

    def test_a_quiet_block_from_cold_does_not_open_the_gate(self):
        gate = Gate(threshold_db=-40.0)
        out = gate.process(block(0.001))
        self.assertEqual(gate._open, 0.0)
        self.assertEqual(float(np.max(np.abs(out))), 0.0)

    def test_closed_hiss_does_not_pump_back_open(self):
        """The gate's own output must not be the input: a closed gate on live hiss that
        keeps re-opening every release is an 11 Hz tremolo, not silence."""
        gate = Gate(threshold_db=-40.0)
        run(gate, 0.5, 1)                      # a note opens it
        levels = []
        for _ in range(300):
            out = gate.process(block(0.001))   # fresh quiet input every block
            levels.append(float(np.max(np.abs(out))))
        self.assertEqual(gate._open, 0.0)
        self.assertEqual(levels[-1], 0.0)
        # And it stayed shut, not just happened to be shut at the end.
        self.assertEqual(max(levels[-100:]), 0.0)

    def test_a_loud_block_opens_it_and_passes_the_guitar(self):
        gate = Gate(threshold_db=-40.0)
        out = run(gate, 0.5, 1)
        self.assertAlmostEqual(float(np.max(np.abs(out))), 0.5, places=3)

    def test_it_closes_slowly_rather_than_clicking(self):
        gate = Gate(threshold_db=-40.0)
        run(gate, 0.5, 1)                 # open
        run(gate, 0.001, 12)              # past the hold, the fade has started
        run(gate, 0.001, 1)
        # One sample is one slice of the release, not a step to zero: a gate that jumps
        # straight down turns a decaying chord into a click.
        self.assertGreater(gate._open, 0.5)
        self.assertLess(gate._open, 0.95)

    def test_it_does_eventually_close(self):
        gate = Gate(threshold_db=-40.0)
        run(gate, 0.5, 1)
        out = run(gate, 0.001, 200)
        self.assertLess(float(np.max(np.abs(out))), 0.001 * 0.02)

    def test_a_held_note_does_not_chatter(self):
        gate = Gate(threshold_db=-40.0)
        run(gate, 0.5, 4)
        # Sits just under the threshold: hysteresis plus hold must keep it open.
        out = run(gate, 0.009, 8)
        self.assertGreater(float(np.max(np.abs(out))), 0.009 * 0.5)


class TheThresholdIsClamped(unittest.TestCase):
    def test_below_the_floor_is_the_floor(self):
        self.assertEqual(Gate().set_threshold(-500.0), FLOOR_DB)

    def test_above_the_ceiling_is_the_ceiling(self):
        self.assertEqual(Gate().set_threshold(12.0), CEIL_DB)

    def test_a_number_sets_the_number(self):
        gate = Gate()
        gate.set_threshold(-45.0)
        self.assertEqual(gate.threshold_db, -45.0)
        self.assertFalse(gate.off)

    def test_a_block_is_the_size_it_was(self):
        gate = Gate(threshold_db=-40.0)
        out = gate.process(np.zeros(128, dtype=np.float32))
        self.assertEqual(out.shape, (128,))

    def test_the_release_is_slower_than_the_hold(self):
        self.assertGreater(CLOSE_MS, 0.0)


class TheEngineHasOne(unittest.TestCase):
    def test_the_engine_starts_with_the_gate_off(self):
        try:
            from amphead.engine2 import Engine
        except Exception as exc:  # pragma: no cover - numpy missing
            self.skipTest(f"engine unavailable: {exc}")
        e = Engine()
        self.assertEqual(e.chain.gate_db, FLOOR_DB)
        self.assertTrue(e.chain.gate.off)

    def test_setting_the_gate_lands_in_the_chain(self):
        try:
            from amphead.engine2 import Engine
        except Exception as exc:  # pragma: no cover
            self.skipTest(f"engine unavailable: {exc}")
        e = Engine()
        e.set_gate(-38.0)
        self.assertEqual(e.chain.gate_db, -38.0)
        self.assertEqual(e.chain.gate.threshold_db, -38.0)

    def test_the_gate_runs_before_the_capture(self):
        from pathlib import Path

        src = Path(__file__).resolve().parent.parent / "amphead" / "engine2.py"
        body = src.read_text(encoding="utf-8")
        gate_at = body.index("chain.gate.process(")
        gain_at = body.index("db_to_gain(chain.input_gain_db)")
        self.assertLess(gate_at, gain_at)


class TheProbePicksABlockSize(unittest.TestCase):
    def test_it_times_every_candidate(self):
        try:
            from amphead.engine2 import Engine
            from amphead import tune
            chain = Engine().chain
        except Exception as exc:  # pragma: no cover
            self.skipTest(f"engine unavailable: {exc}")
        rows = tune.measure(chain, 44100, candidates=(128, 256), warmup=2, measured=6)
        self.assertEqual([r["blocksize"] for r in rows], [128, 256])
        for row in rows:
            self.assertGreater(row["render_ms"], 0.0)
            self.assertAlmostEqual(row["block_ms"], round(1000.0 * row["blocksize"] / 44100, 2))

    def test_the_pick_is_the_smallest_block_that_fits_above_the_floor(self):
        try:
            from amphead import tune
        except Exception as exc:  # pragma: no cover
            self.skipTest(str(exc))
        rows = [
            {"blocksize": 64, "block_ms": 1.5, "render_ms": 3.0, "fits": False},
            {"blocksize": 128, "block_ms": 2.9, "render_ms": 1.0, "fits": True},
            {"blocksize": 256, "block_ms": 5.8, "render_ms": 1.0, "fits": True},
        ]
        # 128 fits, but a soak cannot see the next app that interrupts: the answer a player is
        # left with is the floor, and asking for 128 by name is how you get it.
        self.assertEqual(tune.choose(rows), 256)
        self.assertEqual(tune.choose(rows, floor=0), 128)

    def test_a_chain_that_fits_nowhere_still_answers(self):
        try:
            from amphead import tune
        except Exception as exc:  # pragma: no cover
            self.skipTest(str(exc))
        rows = [{"blocksize": 64, "block_ms": 1.5, "render_ms": 9.0, "fits": False}]
        self.assertIsNone(tune.choose(rows))
        self.assertIn("cannot fill a block", tune.verdict(rows, None))

    def test_a_refused_size_puts_the_chain_back_where_it_found_it(self):
        from types import SimpleNamespace
        from amphead import tune

        class Fake:
            samplerate = 48000
            blocksize = 512
            mode = None

            def __init__(self):
                self.chain = SimpleNamespace(armed=True, master_db=-6.0)
                self.stopped = 0
                self.running = False

            def update(self, armed=None, master_db=None):
                self.chain = SimpleNamespace(
                    armed=self.chain.armed if armed is None else armed,
                    master_db=self.chain.master_db if master_db is None else master_db)
                return self.chain

            def stop(self):
                self.stopped += 1
                self.running = False

            def start(self):
                raise RuntimeError("invalid device")

        engine = Fake()
        with self.assertRaises(RuntimeError):
            tune.soak(engine, 256, seconds=1.0)
        self.assertTrue(engine.chain.armed, "a failed soak left the amp armed and silent")
        self.assertEqual(engine.chain.master_db, -6.0)
        self.assertGreaterEqual(engine.stopped, 1)

    def test_one_refused_block_size_does_not_end_the_calibration(self):
        from types import SimpleNamespace
        from unittest import mock
        from amphead import tune
        engine = SimpleNamespace(samplerate=48000)
        good = {"blocksize": 512, "block_ms": 10.67, "seconds": 2, "blocks": 40,
                "underflows": 0, "overflows": 0, "late": 0, "work_ms": 2.0,
                "peak_ms": 3.0, "peak_realtime": 0.28, "mode": "wdm-ks-exclusive", "ok": True}
        with mock.patch("amphead.tune.soak", side_effect=[RuntimeError("invalid device"), good]):
            rows = tune.soak_all(engine, candidates=(256, 512), seconds=1.0)
        self.assertEqual([r["blocksize"] for r in rows], [256, 512])
        self.assertIn("invalid device", rows[0]["error"])
        self.assertEqual(tune.choose_soak(rows), 512)
        self.assertEqual(tune.soak_verdict(rows, 512), "512 frames, 10.7 ms a block, 2 s with nothing dropped")

    def test_a_heavy_chain_is_never_left_below_the_floor(self):
        from amphead import tune
        # The small size technically fits, the large one does not. Calibration must still
        # leave the rig at the large size: 64 frames is an explicit request, not a fallback.
        rows = [{"blocksize": 64, "block_ms": 1.5, "render_ms": 0.5, "fits": True},
                {"blocksize": 1024, "block_ms": 23.2, "render_ms": 30.0, "fits": False}]
        self.assertEqual(tune.choose(rows), 1024)
        live = [{"blocksize": 64, "ok": True, "blocks": 40, "underflows": 0, "overflows": 0,
                 "late": 0, "block_ms": 1.5, "seconds": 3},
                {"blocksize": 512, "ok": False, "blocks": 40, "underflows": 12, "overflows": 0,
                 "late": 0, "block_ms": 11.6, "seconds": 3}]
        self.assertEqual(tune.choose_soak(live), 512)


if __name__ == "__main__":
    unittest.main()


class TheTuneSoaksQuietly(unittest.TestCase):
    """A soak at full volume in someone's room is not a test, it is a noise complaint."""

    def _engine(self, drops):
        class Fake:
            samplerate = 44100
            blocksize = 256
            running = False
            mode = "fake"
            latency_ms = 10.0

            class chain:
                armed = False
                master_db = -12.0

            def __init__(self):
                self.asked = []
                self.starts = 0

            def update(self, **kw):
                self.asked.append(kw)
                if "armed" in kw:
                    Fake.chain.armed = kw["armed"]
                if "master_db" in kw:
                    Fake.chain.master_db = kw["master_db"]

            def start(self):
                self.starts += 1
                Fake.running = True

            def stop(self):
                Fake.running = False

            def counters(self):
                return {}

        fake = Fake()
        fake.counters = {"blocks": 100, "underflows": drops, "overflows": 0, "late": 0}
        return fake

    def test_the_soak_arms_at_the_floor_and_puts_the_master_back(self):
        from amphead import tune
        engine = self._engine(drops=0)
        row = tune.soak(engine, 128, seconds=0.01)
        # Armed so the chain runs, floored so the speaker hears nothing.
        self.assertIn({"armed": True, "master_db": -60.0}, engine.asked)
        self.assertEqual(engine.asked[-1], {"armed": False, "master_db": -12.0})
        self.assertTrue(row["ok"])
        self.assertEqual(row["blocksize"], 128)

    def test_a_dropped_block_fails_the_size(self):
        from amphead import tune
        row = tune.soak(self._engine(drops=3), 128, seconds=0.01)
        self.assertFalse(row["ok"])
        self.assertIn("dropped", tune.soak_verdict([row], 128))

    def test_measure_primes_the_cabinet_for_each_candidate(self):
        """A hardware-free measurement still has to rebuild the partition bank when the
        block size changes, or a 128 frame candidate is timed while padded to 512."""
        from amphead import tune
        from amphead.engine2 import Chain

        class SpyCab:
            def __init__(self):
                self.primes = []

            def prime(self, frames):
                self.primes.append(frames)

            def process(self, block):
                return block

        cab = SpyCab()
        tune.measure(Chain(cab=cab), 44100, candidates=(128, 256), warmup=0, measured=1)
        self.assertEqual(cab.primes, [128, 256])

    def test_a_block_that_used_most_of_its_deadline_fails_even_with_no_drops(self):
        """The missed-block counter is not the whole story: a callback using 90% of its
        deadline has not dropped a block yet, but the next interruption will. Calibration
        must reject it before the player hears the stutter."""
        from amphead import tune
        engine = self._engine(drops=0)
        engine.counters = {"blocks": 100, "underflows": 0, "overflows": 0, "late": 0,
                           "work_ms": 7.1, "peak_ms": 7.1}
        row = tune.soak(engine, 256, seconds=0.01)
        self.assertFalse(row["ok"])
        self.assertGreater(row["peak_realtime"], tune.PEAK_HEADROOM)
        self.assertIn("worst block", tune.soak_verdict([row], 256))

    def test_the_pick_is_the_smallest_size_that_held_above_the_floor(self):
        from amphead import tune
        rows = [{"blocksize": 64, "ok": False, "blocks": 10, "underflows": 2, "overflows": 0,
                 "late": 0, "block_ms": 1.5, "seconds": 3},
                {"blocksize": 128, "ok": True, "blocks": 10, "underflows": 0, "overflows": 0,
                 "late": 0, "block_ms": 2.9, "seconds": 3},
                {"blocksize": 256, "ok": True, "blocks": 5, "underflows": 0, "overflows": 0,
                 "late": 0, "block_ms": 5.8, "seconds": 3}]
        self.assertEqual(tune.choose_soak(rows), 256)
        self.assertEqual(tune.choose_soak(rows, floor=0), 128)
        words = tune.soak_verdict(rows, 256)
        self.assertIn("256 frames", words)
        self.assertIn("128 frames also ran clean", words)
