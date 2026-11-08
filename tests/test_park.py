"""The amp parks itself when nobody plays, and says so instead of going quiet for no reason.

The timer is pure, so most of this is arithmetic with a clock handed in. One test runs the real
render path with silence, because the wiring is where a timer like this actually goes wrong.
"""

import unittest

try:
    from amphead import engine2, verdict
    from amphead.park import IDLE_SECONDS, QUIET_DB, AutoPark
except ImportError:  # pragma: no cover
    raise unittest.SkipTest("amphead not importable")


class Clock:
    def __init__(self, at=1000.0):
        self.now = float(at)

    def __call__(self):
        return self.now

    def tick(self, seconds):
        self.now += float(seconds)


class TestTheTimer(unittest.TestCase):
    def test_a_pick_resets_the_count(self):
        clock = Clock()
        park = AutoPark(idle_seconds=60, clock=clock)
        clock.tick(50)
        self.assertFalse(park.hear(0.5))
        self.assertAlmostEqual(park.quiet_for, 0.0, places=3)
        clock.tick(50)
        self.assertFalse(park.hear(0.0001), "one quiet block after a pick must not trip it")

    def test_it_trips_once_after_the_stated_silence(self):
        clock = Clock()
        park = AutoPark(idle_seconds=60, clock=clock)
        for _ in range(5):
            clock.tick(10)
            self.assertFalse(park.hear(0.0))
        clock.tick(11)
        self.assertTrue(park.hear(0.0), "silence past the limit did not trip")
        self.assertFalse(park.hear(0.0), "it tripped twice for one silence")
        clock.tick(1)
        self.assertFalse(park.hear(0.0), "it kept tripping after the first time")

    def test_a_player_who_plays_now_and_then_stays_live(self):
        clock = Clock()
        park = AutoPark(idle_seconds=60, clock=clock)
        for _ in range(10):
            clock.tick(59)
            self.assertFalse(park.hear(0.4), "a pick every 59 s should keep the amp live")
            clock.tick(1)

    def test_reset_starts_the_count_over(self):
        clock = Clock()
        park = AutoPark(idle_seconds=60, clock=clock)
        clock.tick(120)
        park.reset()
        clock.tick(30)
        self.assertFalse(park.hear(0.0))

    def test_room_noise_is_quiet(self):
        """-50 dB is the floor: a fan and a hand on the strings must not count as playing."""
        clock = Clock()
        park = AutoPark(idle_seconds=1, clock=clock)
        clock.tick(2)
        self.assertTrue(park.hear(10 ** (QUIET_DB / 20.0) * 0.5))
        park.reset()
        clock.tick(2)
        self.assertFalse(park.hear(10 ** ((QUIET_DB + 12) / 20.0)))

    def test_the_numbers_are_not_small_accidents(self):
        self.assertEqual(IDLE_SECONDS, 15 * 60, "the idle window changed without a reason")
        self.assertEqual(QUIET_DB, -50.0)


class TestTheEngineActsOnIt(unittest.TestCase):
    def setUp(self):
        try:
            import numpy  # noqa: F401
        except ImportError:  # pragma: no cover
            raise unittest.SkipTest("numpy is required for the render path")
        self.engine = engine2.Engine(samplerate=44100, blocksize=64)
        self.engine.update(armed=True, master_db=-6.0)
        self.engine._park = AutoPark(idle_seconds=0.0)

    def test_a_long_silence_parks_the_master_and_leaves_the_chain_armed(self):
        import numpy as np

        y = self.engine._render(np.zeros((64, 2), dtype=np.float32), 64)
        self.assertTrue(self.engine.parked_by_idle)
        self.assertTrue(self.engine.chain.armed, "parking must not disarm the amp")
        self.assertEqual(self.engine.chain.master_db, engine2.SAFE_MASTER_DB)
        self.assertEqual(float(np.max(np.abs(y))), 0.0)

    def test_raising_the_master_clears_it(self):
        import numpy as np

        self.engine._render(np.zeros((64, 2), dtype=np.float32), 64)
        self.engine.set_gain("master_db", -6.0)
        self.assertFalse(self.engine.parked_by_idle)
        self.assertEqual(self.engine.chain.master_db, -6.0)

    def test_a_disarmed_amp_never_counts(self):
        import numpy as np

        self.engine.update(armed=False)
        self.engine._render(np.zeros((64, 2), dtype=np.float32), 64)
        self.assertFalse(self.engine.parked_by_idle)

    def test_the_verdict_names_it_rather_than_calling_it_stage_by(self):
        said = verdict.judge({"open": True, "armed": True, "master_db": -60.0,
                              "parked_by_idle": True,
                              "device": {"closed": False, "inputs": 2, "out_ms": 10.0}},
                             {"l": 0.0, "r": 0.0}, [{"name": "capture", "enabled": True}])
        self.assertEqual(said["verdict"], verdict.PARKED)
        self.assertIn("raise the master", said["reason"].lower())


if __name__ == "__main__":
    unittest.main()
