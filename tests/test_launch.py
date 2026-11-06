"""Launcher tests. Nothing here starts or kills REAPER: launching is exercised live."""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from amphead import bridge, launch


class FindReaperTests(unittest.TestCase):
    def test_env_override_wins_when_file_exists(self):
        with mock.patch.dict(os.environ, {"REAPER_EXE": sys.executable}):
            self.assertEqual(launch.find_reaper(), Path(sys.executable))

    def test_bad_env_override_falls_through(self):
        with mock.patch.dict(os.environ, {"REAPER_EXE": r"C:\nope\reaper.exe"}):
            found = launch.find_reaper()
        self.assertTrue(found.is_file())

    def test_missing_install_raises_with_instruction(self):
        with mock.patch.dict(os.environ, {"REAPER_EXE": ""}), \
             mock.patch.object(launch, "WINDOWS_CANDIDATES", []), \
             mock.patch.object(launch.shutil, "which", return_value=None):
            with self.assertRaises(launch.NoReaperInstall) as caught:
                launch.find_reaper()
        self.assertIn("REAPER_EXE", str(caught.exception))


class LivenessTests(unittest.TestCase):
    def test_bridge_alive_is_false_not_raising_when_reaper_is_down(self):
        with mock.patch.object(bridge, "ping", side_effect=bridge.NoReaper("down")):
            self.assertFalse(launch.bridge_alive())

    def test_bridge_alive_true_on_ping(self):
        with mock.patch.object(bridge, "ping", return_value={"ok": True}):
            self.assertTrue(launch.bridge_alive())

    def test_ensure_audio_leaves_a_working_device_alone(self):
        info = {"ok": True, "device_closed": False, "mode": "ASIO"}
        with mock.patch.object(bridge, "devinfo", return_value=info), \
             mock.patch.object(bridge, "dev_reopen") as reopened:
            self.assertEqual(launch.ensure_audio(), info)
        reopened.assert_not_called()

    def test_ensure_audio_reopens_a_closed_device_then_reports_it(self):
        closed = {"ok": True, "device_closed": True}
        open_ = {"ok": True, "device_closed": False, "mode": "ASIO"}
        with mock.patch.object(bridge, "devinfo", side_effect=[closed, open_]), \
             mock.patch.object(bridge, "dev_reopen") as reopened:
            self.assertEqual(launch.ensure_audio(), open_)
        reopened.assert_called_once()

    def test_state_speaks_in_units_a_person_reads(self):
        with mock.patch.object(bridge, "devinfo", return_value={
            "ident_in": "ASIO AudioBox ASIO Driver", "mode": "ASIO",
            "device_closed": False, "bsize": "512", "srate": "44100", "bps": "32",
            "inputs": 2, "device_latency_ms": 578.0, "output_latency_ms": 11.61,
        }), mock.patch.object(bridge, "status", return_value={
            "project": "", "path": "", "tracks": 0,
        }):
            state = launch.state()
        self.assertEqual(state["project"], "(unnamed)")
        self.assertEqual(state["device"]["buffer"], "512")
        self.assertEqual(state["device"]["latency_total_ms"], 578.0)


if __name__ == "__main__":
    unittest.main()
