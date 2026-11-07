"""Launcher tests. Nothing here starts or kills REAPER: launching is exercised live."""

import os
import sys
import tempfile
import time
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
             mock.patch.object(launch.appdecl, "located", return_value=None), \
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


    def test_state_names_a_missing_bridge_script_instead_of_a_dead_reaper(self):
        with mock.patch.object(bridge, "devinfo", side_effect=bridge.NoReaper("no answer")), \
             mock.patch.object(launch, "_running", return_value=True), \
             mock.patch.object(bridge, "liveness",
                               return_value=("not-loaded", "REAPER is running but the bridge is not")), \
             mock.patch.object(launch, "instances", return_value=1):
            state = launch.state()
        self.assertEqual(state["bridge"], "gone")
        self.assertIn("Run setup", state["reason"])

    def test_state_says_not_running_only_when_the_process_is_gone(self):
        with mock.patch.object(bridge, "devinfo", side_effect=bridge.NoReaper("no answer")), \
             mock.patch.object(launch, "_running", return_value=False):
            state = launch.state()
        self.assertEqual(state["bridge"], "not-running")
        self.assertEqual(state["instances"], 0)

    def test_state_says_busy_when_reaper_is_up_and_nothing_answers(self):
        with mock.patch.object(bridge, "devinfo", side_effect=bridge.NoReaper("a dialog has it")), \
             mock.patch.object(launch, "_running", return_value=True), \
             mock.patch.object(bridge, "liveness", return_value=("blocked", "a dialog has the thread")), \
             mock.patch.object(launch, "instances", return_value=1):
            state = launch.state()
        self.assertEqual(state["bridge"], "down")
        self.assertIn("dialog", state["reason"])

    def test_state_counts_reaper_copies_and_checks_the_rig_is_in_front(self):
        rig = Path(tempfile.mkdtemp()) / "amphead.rpp"
        with mock.patch.object(bridge, "devinfo", return_value={"device_closed": False}), \
             mock.patch.object(bridge, "status", return_value={
                 "project": "somebody-elses-song", "path": "C:/Music/other song.rpp", "tracks": 12}), \
             mock.patch.object(launch, "_rig_path", return_value=rig), \
             mock.patch.object(launch, "instances", return_value=2):
            state = launch.state()
        self.assertEqual(state["instances"], 2)
        self.assertFalse(state["rig_tab"])

    def test_the_rig_in_front_of_reaper_is_recognised_through_path_case_and_slashes(self):
        rig = Path(tempfile.mkdtemp()) / "AmpHead.rpp"
        with mock.patch.object(bridge, "devinfo", return_value={"device_closed": False}), \
             mock.patch.object(bridge, "status", return_value={
                 "project": "amphead", "path": str(rig).replace("\\", "/").upper(), "tracks": 1}), \
             mock.patch.object(launch, "_rig_path", return_value=rig), \
             mock.patch.object(launch, "instances", return_value=1):
            self.assertTrue(launch.state()["rig_tab"])

    def test_an_unnamed_project_in_front_of_reaper_is_not_the_rig(self):
        # `reaper.exe -new` leaves an empty project in front of the same process; nothing in it is
        # the rig, so the pane must not treat "no path" as "could not look".
        rig = Path(tempfile.mkdtemp()) / "amphead.rpp"
        with mock.patch.object(bridge, "devinfo", return_value={"device_closed": False}), \
             mock.patch.object(bridge, "status", return_value={"project": "", "path": "", "tracks": 0}), \
             mock.patch.object(launch, "_rig_path", return_value=rig), \
             mock.patch.object(launch, "instances", return_value=1):
            self.assertFalse(launch.state()["rig_tab"])

    def test_a_closed_device_is_reopened_once_and_reported_as_reopened(self):
        closed = {"ok": True, "device_closed": True}
        open_ = {"ok": True, "device_closed": False, "mode": "ASIO", "output_latency_ms": 11.6}
        with mock.patch.object(bridge, "devinfo", side_effect=[closed, open_, closed]), \
             mock.patch.object(bridge, "dev_reopen") as reopened, \
             mock.patch.object(bridge, "status", return_value={"project": "amphead", "tracks": 1}), \
             mock.patch.object(launch, "instances", return_value=1), \
             mock.patch.object(launch, "_rig_path", return_value=None):
            first = launch.state()
            # A poller must not spawn a repair every two seconds: the cooldown holds it off.
            launch._REOPEN_AT = time.monotonic()
            second = launch.state()
        self.assertTrue(first["device"]["reopened"])
        self.assertFalse(second["device"]["reopened"])
        reopened.assert_called_once()


if __name__ == "__main__":
    unittest.main()
