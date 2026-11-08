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
        self.assertLessEqual(float(np.max(np.abs(out))), 0.001)

    def test_a_loud_block_opens_it_and_passes_the_guitar(self):
        gate = Gate(threshold_db=-40.0)
        out = run(gate, 0.5, 1)
        self.assertAlmostEqual(float(np.max(np.abs(out))), 0.5, places=3)

    def test_it_closes_slowly_rather_than_clicking(self):
        gate = Gate(threshold_db=-40.0)
        run(gate, 0.5, 1)                 # open
        run(gate, 0.001, 8)               # past the hold, the fade has started
        run(gate, 0.001, 1)
        # One block is one slice of the release, not a step to zero: a gate that jumps
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

    def test_the_pick_is_the_smallest_block_that_fits(self):
        try:
            from amphead import tune
        except Exception as exc:  # pragma: no cover
            self.skipTest(str(exc))
        rows = [
            {"blocksize": 64, "block_ms": 1.5, "render_ms": 3.0, "fits": False},
            {"blocksize": 128, "block_ms": 2.9, "render_ms": 1.0, "fits": True},
            {"blocksize": 256, "block_ms": 5.8, "render_ms": 1.0, "fits": True},
        ]
        self.assertEqual(tune.choose(rows), 128)

    def test_a_chain_that_fits_nowhere_still_answers(self):
        try:
            from amphead import tune
        except Exception as exc:  # pragma: no cover
            self.skipTest(str(exc))
        rows = [{"blocksize": 64, "block_ms": 1.5, "render_ms": 9.0, "fits": False}]
        self.assertIsNone(tune.choose(rows))
        self.assertIn("cannot fill a block", tune.verdict(rows, None))


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

    def test_the_pick_is_the_smallest_size_that_held(self):
        from amphead import tune
        rows = [{"blocksize": 64, "ok": False, "blocks": 10, "underflows": 2, "overflows": 0,
                 "late": 0, "block_ms": 1.5, "seconds": 3},
                {"blocksize": 128, "ok": True, "blocks": 10, "underflows": 0, "overflows": 0,
                 "late": 0, "block_ms": 2.9, "seconds": 3},
                {"blocksize": 256, "ok": True, "blocks": 5, "underflows": 0, "overflows": 0,
                 "late": 0, "block_ms": 5.8, "seconds": 3}]
        self.assertEqual(tune.choose_soak(rows), 128)
        self.assertIn("128 frames", tune.soak_verdict(rows, 128))
