"""Step 1.2: the client's concurrency and honesty guarantees.

Three things are being bought here, and each one is a distinct lie the client could otherwise
tell: two sessions eating each other's request (lock), parsing somebody else's answer
(identity), and calling a dialog "REAPER is not running" (liveness).
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from amphead import bridge
from amphead.fake_reaper import FakeReaper


class ClientCase(unittest.TestCase):
    mode = "ok"
    latency = 0.0

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "hermes_bridge"
        self.root.mkdir(parents=True)
        self.fake = FakeReaper(self.root, mode=self.mode, latency=self.latency).start()
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(self.fake.stop)

    def call(self, cmd: str, **kwargs):
        kwargs.setdefault("root", self.root)
        return bridge.call(cmd, **kwargs)


class TestLock(ClientCase):
    latency = 0.05

    def test_two_threads_get_their_own_answers(self):
        """One request slot, two callers: both must be answered, neither may steal the other."""
        results: dict[str, object] = {}
        errors: list[BaseException] = []

        def worker(name: str) -> None:
            try:
                results[name] = bridge.call("PING", root=self.root, timeout=10.0)
            except BaseException as exc:            # noqa: BLE001 - the assertion reports it
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(f"t{i}",)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        self.assertEqual(errors, [], f"concurrent calls failed: {errors}")
        self.assertEqual(len(results), 4)
        for payload in results.values():
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["proto"], 2)
        self.assertEqual(len(self.fake.requests), 4, "every request reached the bridge exactly once")

    def test_a_stale_lock_from_a_dead_client_is_reclaimed(self):
        lock = self.root / "request.lock"
        lock.write_text("99999", encoding="utf-8")
        old = time.time() - 60
        os.utime(lock, (old, old))
        payload = self.call("PING")
        self.assertTrue(payload["ok"])
        self.assertFalse(lock.exists(), "reclaimed lock must be released after the call")

    def test_a_live_holder_is_not_evicted(self):
        """A slow command longer than the stale window must keep its slot by refreshing it."""
        with mock.patch.object(bridge, "LOCK_STALE", 0.3):
            payload = self.call("PING", timeout=10.0)
        self.assertTrue(payload["ok"])
        self.assertEqual(len(self.fake.requests), 1)

    def test_waiter_gives_up_instead_of_hanging_forever(self):
        lock = self.root / "request.lock"
        lock.write_text(str(os.getpid()), encoding="utf-8")   # fresh lock, held forever
        with mock.patch.object(bridge, "LOCK_TIMEOUT", 0.2):
            with self.assertRaises(bridge.BridgeTimeout):
                self.call("PING")


class TestResponseIdentity(unittest.TestCase):
    RID = "abc123abc123"

    def test_matching_id_is_accepted(self):
        payload = bridge._parse_response(json.dumps({"ok": True, "id": self.RID}), self.RID)
        self.assertTrue(payload["ok"])

    def test_foreign_id_is_refused(self):
        with self.assertRaises(bridge.BadResponse) as ctx:
            bridge._parse_response(json.dumps({"ok": True, "id": "someoneelse99"}), self.RID)
        self.assertIn("does not match", str(ctx.exception))

    def test_truncated_file_is_not_reported_as_bad_json(self):
        with self.assertRaises(bridge.BadResponse) as ctx:
            bridge._parse_response('{"ok":true,"id":"abc123abc123"', self.RID)
        self.assertIn("truncated", str(ctx.exception))

    def test_empty_and_html_bodies(self):
        with self.assertRaises(bridge.BadResponse):
            bridge._parse_response("   ", self.RID)
        with self.assertRaises(bridge.BadResponse) as ctx:
            bridge._parse_response("<html>nope</html>", self.RID)
        self.assertIn("truncated", str(ctx.exception))

    def test_response_without_id_is_still_usable(self):
        """Older bridge builds do not echo the id; the filename is then the only identity."""
        payload = bridge._parse_response(json.dumps({"ok": True}), self.RID)
        self.assertTrue(payload["ok"])


class TestLivenessVerdicts(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "hermes_bridge"
        self.root.mkdir(parents=True)
        self.addCleanup(self._tmp.cleanup)

    def test_no_heartbeat_and_no_process_is_not_running(self):
        with mock.patch.object(bridge, "_process_running", return_value=False):
            verdict, sentence = bridge.liveness(self.root)
        self.assertEqual(verdict, "not-running")
        self.assertIn("not running", sentence)

    def test_no_heartbeat_but_process_alive_is_not_loaded(self):
        with mock.patch.object(bridge, "_process_running", return_value=True):
            verdict, sentence = bridge.liveness(self.root)
        self.assertEqual(verdict, "not-loaded")
        self.assertIn("not loaded", sentence)
        self.assertIn(str(self.root), sentence)

    def test_stale_heartbeat_with_process_is_blocked(self):
        alive = self.root / "alive"
        alive.write_text("1", encoding="utf-8")
        old = time.time() - 60
        os.utime(alive, (old, old))
        with mock.patch.object(bridge, "_process_running", return_value=True):
            verdict, sentence = bridge.liveness(self.root)
        self.assertEqual(verdict, "blocked")
        self.assertIn("dialog", sentence)

    def test_stale_heartbeat_without_process_is_not_running(self):
        alive = self.root / "alive"
        alive.write_text("1", encoding="utf-8")
        old = time.time() - 60
        os.utime(alive, (old, old))
        with mock.patch.object(bridge, "_process_running", return_value=False):
            verdict, sentence = bridge.liveness(self.root)
        self.assertEqual(verdict, "not-running")
        self.assertIn("gone", sentence)

    def test_fresh_heartbeat_is_live(self):
        (self.root / "alive").write_text("1", encoding="utf-8")
        with mock.patch.object(bridge, "_process_running", return_value=True):
            verdict, _ = bridge.liveness(self.root)
        self.assertEqual(verdict, "live")


class TestBlockedVersusClosed(ClientCase):
    mode = "silent"

    def test_silent_bridge_with_a_heartbeat_is_blocked(self):
        with mock.patch.object(bridge, "_process_running", return_value=True):
            with self.assertRaises(bridge.BridgeBlocked) as ctx:
                self.call("PING", timeout=0.4)
        self.assertIn("no answer within", str(ctx.exception))

    def test_silent_bridge_without_a_process_is_not_running(self):
        (self.root / "alive").unlink(missing_ok=True)
        with mock.patch.object(bridge, "_process_running", return_value=False):
            with self.assertRaises(bridge.NoReaper):
                self.call("PING", timeout=0.4)


class TestClosedReaper(ClientCase):
    mode = "closed"

    def test_closed_bridge_names_the_directory(self):
        with mock.patch.object(bridge, "_process_running", return_value=False):
            with self.assertRaises(bridge.NoReaper):
                self.call("PING", timeout=0.3)


if __name__ == "__main__":
    unittest.main()
