"""Protocol v2 tests. No REAPER involved: every test runs against ``FakeReaper``.

These are the failures that matter: closed REAPER, blocked REAPER, an old installed
bridge, a wrong project, a batch that stops halfway, a write whose read-back lies.
"""

from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from amphead import bridge
from amphead.fake_reaper import FakeReaper


class BridgeCase(unittest.TestCase):
    mode = "ok"

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "hermes_bridge"
        self.root.mkdir(parents=True)
        self.fake = FakeReaper(self.root, mode=self.mode).start()
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(self.fake.stop)

    def call(self, cmd: str, **kwargs):
        return bridge.call(cmd, root=self.root, **kwargs)


class TestLiveness(BridgeCase):
    def test_ping_reports_proto(self):
        payload = self.call("PING")
        self.assertEqual(payload["proto"], 2)
        self.assertEqual(payload["version"], "7.77/x64")
        self.assertIn("BATCH", payload["cmds"])

    def test_missing_directory_is_noreaper(self):
        with self.assertRaises(bridge.NoReaper):
            bridge.call("PING", root=Path(self._tmp.name) / "nope")


class TestClosed(BridgeCase):
    mode = "closed"

    def test_closed_reaper_is_noreaper(self):
        with self.assertRaises(bridge.NoReaper):
            self.call("PING", timeout=0.3)


class TestClosedIsFast(BridgeCase):
    """A host that is not there must not cost a full timeout.

    The pane polls /state every four seconds. When that poll sat out the whole timeout with
    REAPER closed, the gateway's event loop was blocked a third of every cycle, health checks
    took seconds, and every list in the desktop app timed out.
    """

    mode = "closed"

    def test_a_call_with_no_bridge_fails_without_waiting_the_timeout(self):
        (self.root / "alive").unlink(missing_ok=True)
        with mock.patch.object(bridge, "_process_running", return_value=False):
            started = time.monotonic()
            with self.assertRaises(bridge.NoReaper):
                bridge.call("PING", root=self.root, timeout=3.0)
            spent = time.monotonic() - started
        self.assertLess(spent, 1.0, f"a dead bridge took {spent:.2f}s to say so")

    def test_a_stale_heartbeat_with_no_process_also_fails_fast(self):
        (self.root / "alive").write_text("1", encoding="utf-8")
        old = time.time() - 600
        os.utime(self.root / "alive", (old, old))
        with mock.patch.object(bridge, "_process_running", return_value=False):
            started = time.monotonic()
            with self.assertRaises(bridge.NoReaper):
                bridge.call("PING", root=self.root, timeout=3.0)
            spent = time.monotonic() - started
        self.assertLess(spent, 1.0, f"a stale heartbeat took {spent:.2f}s to say so")


class TestProcessCheckIsCached(unittest.TestCase):
    """Answering "is reaper.exe alive" costs a process listing, and the pane asks constantly."""

    def test_repeated_asks_within_the_window_read_the_process_once(self):
        from amphead import launch

        bridge._PROC_SEEN = (0.0, False)
        with mock.patch.object(launch, "running", return_value=False) as probe:
            self.assertFalse(bridge._process_running())
            self.assertFalse(bridge._process_running())
            self.assertFalse(bridge._process_running())
        self.assertEqual(probe.call_count, 1)
        bridge._PROC_SEEN = (0.0, False)

    def test_the_answer_is_rechecked_after_the_window(self):
        from amphead import launch

        bridge._PROC_SEEN = (0.0, False)
        with mock.patch.object(launch, "running", return_value=True):
            bridge._process_running()
            bridge._PROC_SEEN = (time.monotonic() - bridge._PROC_TTL - 1, False)
            self.assertTrue(bridge._process_running())
        bridge._PROC_SEEN = (0.0, False)


class TestBlocked(BridgeCase):
    mode = "silent"

    def test_heartbeat_without_answer_is_blocked(self):
        # One tick writes the heartbeat and swallows the request: that is a modal dialog.
        # The process host is mocked alive because this box has no reaper.exe in the test run.
        time.sleep(0.05)
        with mock.patch.object(bridge, "_process_running", return_value=True):
            with self.assertRaises(bridge.BridgeBlocked):
                self.call("PING", timeout=0.4)


class TestGarbage(BridgeCase):
    mode = "garbage"

    def test_non_json_is_bad_response(self):
        with self.assertRaises(bridge.BadResponse):
            self.call("PING")


class TestOldBridge(BridgeCase):
    mode = "old"

    def test_proto_one_is_refused(self):
        with self.assertRaises(bridge.OldBridge):
            self.call("PING")


class TestCommands(BridgeCase):
    def test_status_reports_project(self):
        payload = self.call("STATUS")
        self.assertEqual(payload["project"], "rig.rpp")
        self.assertEqual(payload["tracks"], 1)

    def test_unknown_command_fails_loudly(self):
        with self.assertRaises(bridge.CommandFailed) as ctx:
            self.call("NOPE")
        self.assertIn("unknown command", str(ctx.exception))

    def test_devinfo_reports_device_latency(self):
        payload = self.call("DEVINFO")
        self.assertEqual(payload["ident_in"], "AudioBox ASIO Driver")
        self.assertAlmostEqual(payload["device_latency_ms"], 578.0, places=1)
        self.assertEqual(payload["bsize"], "512")

    def test_meter_returns_track_fields(self):
        payload = self.call("METER", track=1)
        self.assertEqual(payload["name"], "Gtr")
        self.assertFalse(payload["armed"])

    def test_track_info_lists_chain(self):
        payload = self.call("TRACK_INFO", track=1)
        self.assertEqual(payload["fx_count"], 2)
        self.assertIn("Shoeless", payload["chain"][0]["name"])

    def test_closed_device_is_reported_and_reopened(self):
        self.fake.device_closed = True
        self.assertTrue(self.call("DEVINFO")["device_closed"])
        self.assertFalse(self.call("DEV_REOPEN")["mode"] == "")
        info = self.call("DEVINFO")
        self.assertFalse(info["device_closed"])
        self.assertEqual(info["ident_in"], "AudioBox ASIO Driver")

    def test_output_latency_is_converted_from_samples(self):
        payload = self.call("DEVINFO")
        srate = float(payload["srate"])
        expected = payload["output_latency_samples"] * 1000 / srate
        self.assertAlmostEqual(payload["output_latency_ms"], expected, places=2)


class TestPercentEncoding(BridgeCase):
    def test_values_survive_pipes_commas_and_spaces(self):
        weird = "Gtr | Lead, DI; 2"
        self.call("TRACK_ADD", name=weird, expect=self.fake.project_path)
        names = self.call("TRACK_INFO", track=2)["name"]
        self.assertEqual(names, weird)


class TestGuard(BridgeCase):
    def test_write_to_wrong_project_is_refused(self):
        with self.assertRaises(bridge.CommandFailed) as ctx:
            self.call("TRACK_ADD", name="X", expect="C:/songs/The_riff.rpp")
        self.assertIn("wrong project", ctx.exception.payload["error"])
        self.assertIn("rig.rpp", ctx.exception.payload["active"])

    def test_write_without_guard_is_refused(self):
        with self.assertRaises(bridge.CommandFailed) as ctx:
            self.call("TRACK_ADD", name="X")
        self.assertEqual(ctx.exception.payload["error"], "guard required")

    def test_matching_guard_is_allowed(self):
        payload = self.call("TRACK_ADD", name="Gtr 2", expect=self.fake.project_path)
        self.assertEqual(payload["index"], 2)

    def test_created_flag_allows_unnamed_project(self):
        self.fake.project_path = ""
        payload = self.call("TRACK_ADD", name="Gtr", new=1)
        self.assertEqual(payload["index"], 2)

    def test_unnamed_project_needs_the_created_flag(self):
        self.fake.project_path = ""
        with self.assertRaises(bridge.CommandFailed) as ctx:
            self.call("TRACK_ADD", name="Gtr")
        self.assertIn("unnamed project", ctx.exception.payload["error"])

    def test_guard_is_case_and_slash_insensitive(self):
        payload = self.call(
            "TRACK_ADD", name="Gtr 2", expect=str(self.fake.project_path).lower().replace("/", "\\")
        )
        self.assertEqual(payload["index"], 2)


class TestRigTab(BridgeCase):
    """The rig tab has to own a path before any write is allowed near it."""

    rig = "/tmp/hermes-rig/rig.rpp"

    def test_open_rig_gives_the_tab_a_path(self):
        payload = bridge.open_rig(self.rig, root=self.root, new="1")
        self.assertEqual(payload["path"], self.rig)
        self.assertTrue(payload["marked"])

    def test_open_missing_file_is_an_error_with_the_path(self):
        with self.assertRaises(bridge.CommandFailed) as ctx:
            self.call("PROJ_OPEN", path=self.rig, missing="1")
        self.assertEqual(ctx.exception.payload["path"], self.rig)

    def test_save_that_leaves_the_tab_unnamed_is_refused(self):
        with self.assertRaises(bridge.CommandFailed) as ctx:
            self.call("PROJ_SAVE_AS", path=self.rig, silent_copy="1", expect=self.rig, new="1")
        self.assertIn("did not give the project this path", ctx.exception.payload["error"])

    def test_a_write_for_another_project_is_refused_on_the_rig(self):
        bridge.open_rig(self.rig, root=self.root, new="1")
        before = self.call("STATUS")["tracks"]
        with self.assertRaises(bridge.CommandFailed) as ctx:
            self.call("TRACK_ADD", name="Gtr L", expect="/tmp/hermes-rig/other.rpp")
        self.assertIn("wrong project", ctx.exception.payload["error"])
        self.assertIn("other.rpp", ctx.exception.payload["expected"])
        self.assertEqual(self.call("STATUS")["tracks"], before)


class TestTransactions(BridgeCase):
    """One undo point comes from one call, because REAPER cannot hold a block open across
    script runs. The bridge says so instead of pretending."""

    def test_tx_begin_is_refused_with_a_reason(self):
        with self.assertRaises(bridge.CommandFailed) as ctx:
            self.call("TX_BEGIN", label="rig")
        self.assertIn("one BATCH", ctx.exception.payload["error"])

    def test_tx_end_without_begin_is_refused(self):
        with self.assertRaises(bridge.CommandFailed) as ctx:
            self.call("TX_END")
        self.assertIn("one BATCH", ctx.exception.payload["error"])

    def test_undo_works_outside_a_batch(self):
        self.assertTrue(self.call("UNDO")["undone"])


class TestBatch(BridgeCase):
    def test_batch_runs_in_order_and_closes_transaction(self):
        payload = bridge.batch(
            [
                ("TRACK_VOL", {"track": 1, "db": -6, "expect": self.fake.project_path}),
                ("ARM", {"track": 1, "confirm": "1", "expect": self.fake.project_path}),
            ],
            root=self.root,
        )
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["count"], 2)
        self.assertFalse(self.fake.tx_open)
        self.assertTrue(self.fake.tracks[0]["armed"])

    def test_batch_stops_at_first_failure(self):
        with self.assertRaises(bridge.CommandFailed) as ctx:
            bridge.batch(
                [
                    ("TRACK_ADD", {"name": "A", "expect": "C:/songs/The_riff.rpp"}),
                    ("ARM", {"track": 1, "confirm": "1", "expect": self.fake.project_path}),
                ],
                root=self.root,
            )
        payload = ctx.exception.payload
        self.assertEqual(payload["failed_at"], 1)
        self.assertEqual(payload["count"], 1)
        self.assertIn("wrong project", payload["results"][0]["resp"]["error"])
        self.assertFalse(self.fake.tracks[0]["armed"])
        self.assertFalse(self.fake.tx_open)


class TestParameterTraps(BridgeCase):
    def test_inf_readback_is_visible_to_the_caller(self):
        payload = self.call(
            "FX_SETV", track=1, fx=0, param=0, value=0.35, mode="norm",
            expect=self.fake.project_path,
        )
        self.assertEqual(payload["fmt"], "-inf")

    def test_silent_noop_write_does_not_move_the_value(self):
        before = self.call("FX_INFO", track=1, fx=0, param=3)["norm"]
        payload = self.call(
            "FX_SETV", track=1, fx=0, param=3, value=0.9, mode="norm",
            expect=self.fake.project_path,
        )
        self.assertAlmostEqual(payload["norm"], before, places=6)

    def test_normal_write_moves_the_value(self):
        payload = self.call(
            "FX_SETV", track=1, fx=0, param=1, value=0.8, mode="norm",
            expect=self.fake.project_path,
        )
        self.assertAlmostEqual(payload["norm"], 0.8, places=4)


class TestConcurrency(BridgeCase):
    def test_two_threads_both_get_answers(self):
        results: list[dict] = []
        errors: list[Exception] = []

        def worker(track: int) -> None:
            try:
                results.append(bridge.call("METER", track=track, root=self.root))
            except Exception as exc:  # noqa: BLE001 - the failure is the test
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(1,)) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 4)


class TestResponseFiles(BridgeCase):
    def test_prune_removes_old_files(self):
        stale = self.root / "out" / "resp_dead.json"
        stale.write_text("{}", encoding="utf-8")
        old = time.time() - 7200
        import os

        os.utime(stale, (old, old))
        removed = bridge.prune_responses(older_than=3600, root=self.root)
        self.assertEqual(removed, 1)
        self.assertFalse(stale.exists())


if __name__ == "__main__":
    unittest.main()
