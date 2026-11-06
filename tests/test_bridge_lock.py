"""The request-slot lock.

``request.txt`` is one global file, so the lock is the only thing that stops two sessions
from overwriting each other's requests and both waiting out a timeout. The bug this file
guards is subtle: a lock made of "create the file, delete it when done" can be handed to
two callers at once, because one caller's release-side delete can land after the next
caller has already created its own file. Then both write the request slot and one of them
never gets an answer. The lock is now a kernel lock, so exclusion is the kernel's job.
"""

from __future__ import annotations

import os
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from amphead import bridge


class TestLock(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "request.lock"

    def test_the_lock_is_exclusive(self) -> None:
        with bridge._Lock(self.path, timeout=0.2):
            with self.assertRaises(bridge.BridgeTimeout):
                with bridge._Lock(self.path, timeout=0.05):
                    pass

    def test_the_lock_is_released_on_exit(self) -> None:
        with bridge._Lock(self.path, timeout=0.2):
            pass
        # The file stays (it is the thing the kernel locks) but the lock itself is free.
        with bridge._Lock(self.path, timeout=0.2):
            pass

    def test_a_lock_file_left_by_a_crashed_client_does_not_block(self) -> None:
        self.path.write_text("999999:deadbeef", encoding="utf-8")
        with bridge._Lock(self.path, timeout=0.5):
            pass

    def test_threads_never_share_the_lock(self) -> None:
        """The regression: several callers at once, exactly one inside at a time."""
        state = {"inside": 0}
        overlaps: list[int] = []
        order: list[int] = []
        guard = threading.Lock()

        def worker(n: int) -> None:
            with bridge._Lock(self.path, timeout=10.0):
                with guard:
                    state["inside"] += 1
                    if state["inside"] > 1:
                        overlaps.append(state["inside"])
                    order.append(n)
                time.sleep(0.01)
                with guard:
                    state["inside"] -= 1

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(overlaps, [], "two callers held the request slot at once")
        self.assertEqual(sorted(order), list(range(8)), "a caller never got its turn")

    def test_a_waiter_times_out_rather_than_hanging(self) -> None:
        held = bridge._Lock(self.path, timeout=0.2)
        held.__enter__()
        try:
            started = time.monotonic()
            with self.assertRaises(bridge.BridgeTimeout):
                with bridge._Lock(self.path, timeout=0.15):
                    pass
            self.assertLess(time.monotonic() - started, 2.0)
        finally:
            held.__exit__(None, None, None)


if __name__ == "__main__":
    unittest.main()
