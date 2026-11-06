"""Tool-layer tests: what the agent sees when the host is missing, blocked, or fine.

No REAPER, no PowerShell, no audio device: every wire call is stubbed, so these run on any box.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from amphead import bridge, launch, tools  # noqa: E402


class TestCheckFn(unittest.TestCase):
    def test_true_when_reaper_is_installed(self):
        with mock.patch.object(tools.launch, "find_reaper", return_value=Path("C:/reaper.exe")):
            self.assertTrue(tools.check_reaper_available())

    def test_false_when_no_install(self):
        with mock.patch.object(tools.launch, "find_reaper",
                               side_effect=launch.NoReaperInstall("nope")):
            self.assertFalse(tools.check_reaper_available())

    def test_check_does_not_require_a_running_host(self):
        """setup exists to start REAPER; hiding it until REAPER runs would be a dead end."""
        called = mock.Mock(side_effect=AssertionError("check_fn must not probe the bridge"))
        with mock.patch.object(tools.launch, "find_reaper", return_value=Path("C:/reaper.exe")), \
             mock.patch.object(tools.bridge, "ping", called):
            self.assertTrue(tools.check_reaper_available())
        called.assert_not_called()


class TestPing(unittest.TestCase):
    def test_not_running(self):
        with mock.patch.object(tools.bridge, "ping", side_effect=bridge.NoReaper("stale")), \
             mock.patch.object(tools.launch, "find_reaper", return_value=Path("C:/reaper.exe")):
            out = tools.handle_ping({})
        self.assertIn("REAPER NOT RUNNING", out)
        self.assertIn("amphead_setup", out)

    def test_ok(self):
        payload = {"ok": True, "proto": 2, "version": "7.77/x64", "bridge": "2.0.0"}
        with mock.patch.object(tools.bridge, "ping", return_value=payload):
            out = tools.handle_ping({})
        self.assertIn("ok=True", out)
        self.assertIn("proto=2", out)
        self.assertIn("reaper=7.77/x64", out)

    def test_blocked_blames_a_dialog_only_when_the_process_exists(self):
        exc = bridge.BridgeBlocked("REAPER is running but did not answer")
        with mock.patch.object(tools.bridge, "ping", side_effect=exc), \
             mock.patch.object(tools.launch, "running", return_value=True):
            self.assertIn("REAPER BLOCKED", tools.handle_ping({}))
        with mock.patch.object(tools.bridge, "ping", side_effect=exc), \
             mock.patch.object(tools.launch, "running", return_value=False), \
             mock.patch.object(tools.launch, "find_reaper", return_value=Path("C:/reaper.exe")):
            self.assertIn("REAPER NOT RUNNING", tools.handle_ping({}))

    def test_old_bridge_is_named(self):
        with mock.patch.object(tools.bridge, "ping", side_effect=bridge.OldBridge("proto 1")):
            out = tools.handle_ping({})
        self.assertIn("OLD BRIDGE", out)
        self.assertIn("proto 1", out)


class TestStatus(unittest.TestCase):
    DOWN = {"bridge": "not-running", "reason": "REAPER is not running", "project": None,
            "path": "", "tracks": 0, "device": {}}

    def test_down_state_explains_itself(self):
        with mock.patch.object(tools.launch, "state", return_value=dict(self.DOWN)):
            out = tools.handle_status({})
        self.assertIn("REAPER NOT RUNNING", out)
        self.assertIn("amphead_setup", out)

    def test_live_state_reports_audio_and_safety(self):
        state = {"bridge": "live", "project": "amphead.rpp", "path": "C:/rig/amphead.rpp",
                 "tracks": 1,
                 "device": {"buffer": "512", "rate": "44100", "latency_out_ms": 11.61,
                            "closed": False}}
        with mock.patch.object(tools.launch, "state", return_value=state), \
             mock.patch.object(tools.bridge, "master", return_value={"db": -60.0}), \
             mock.patch.object(tools.bridge, "track_info",
                               return_value={"armed": False, "monitor": False}):
            out = tools.handle_status({})
        self.assertIn("buffer=512", out)
        self.assertIn("rate=44100", out)
        self.assertIn("master_db=-60.0", out)
        self.assertIn("armed=False", out)

    def test_empty_rig_does_not_ask_for_track_one(self):
        state = {"bridge": "live", "project": "amphead.rpp", "path": "C:/rig/amphead.rpp",
                 "tracks": 0, "device": {}}
        asked = mock.Mock(side_effect=AssertionError("no tracks, no TRACK_INFO"))
        with mock.patch.object(tools.launch, "state", return_value=state), \
             mock.patch.object(tools.bridge, "master", return_value={"db": -60.0}), \
             mock.patch.object(tools.bridge, "track_info", asked):
            out = tools.handle_status({})
        asked.assert_not_called()
        self.assertIn("rig=empty", out)

    def test_no_install(self):
        with mock.patch.object(tools.launch, "state",
                               side_effect=launch.NoReaperInstall("gone")):
            self.assertIn("NO REAPER INSTALL", tools.handle_status({}))


class TestSetup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.rig = Path(self.tmp.name) / "amphead.rpp"
        self._env = mock.patch.dict(os.environ, {"AMPHEAD_RIG": str(self.rig)})
        self._env.start()
        # Setup repairs the install, which must never touch the real REAPER resource folder
        # from a test.
        self._inst = mock.patch.object(
            tools.install_bridge, "install",
            return_value=mock.Mock(bridge_path=Path("C:/res/Scripts/hermes_bridge/bridge.lua"),
                                   startup_action="adopted", warnings=[]))
        self._inst.start()
        self._alive = mock.patch.object(tools.launch, "bridge_alive", return_value=True)
        self._alive.start()

    def tearDown(self):
        self._alive.stop()
        self._inst.stop()
        self._env.stop()
        self.tmp.cleanup()

    def test_no_install_is_refused_before_any_work(self):
        with mock.patch.object(tools.launch, "find_reaper",
                               side_effect=launch.NoReaperInstall("gone")):
            self.assertIn("NO REAPER INSTALL", tools.handle_setup({}))

    def test_setup_writes_the_rig_tab_and_parks_the_master(self):
        seen: list[tuple[str, dict]] = []

        def record(cmd, **kwargs):
            seen.append((cmd, kwargs))
            if cmd == "MASTER_VOL":
                return {"db": -60.0}
            if cmd == "PROJ_OPEN":
                return {"path": self.rig.as_posix()}
            return {"ok": True}

        with mock.patch.object(tools.launch, "find_reaper", return_value=Path("C:/reaper.exe")), \
             mock.patch.object(tools.launch, "ensure_running", return_value={"ui": "minimized"}), \
             mock.patch.object(tools.bridge, "open_rig",
                               return_value={"path": self.rig.as_posix()}), \
             mock.patch.object(tools.bridge, "call", side_effect=record), \
             mock.patch.object(tools.bridge, "tabs_list",
                               side_effect=[["a"], ["a", "b"]]), \
             mock.patch.object(tools.bridge, "arm",
                               side_effect=AssertionError("setup must never arm")):
            out = tools.handle_setup({})

        self.assertTrue(self.rig.is_file(), "template must exist on disk for PROJ_OPEN")
        self.assertIn("state=STANDBY", out)
        self.assertIn("master_db=-60.0", out)
        self.assertIn("tabs=1->2", out)
        writes = dict(seen)
        self.assertEqual(writes["MASTER_VOL"]["db"], -60.0)
        self.assertEqual(writes["MASTER_VOL"]["expect"], self.rig.as_posix(),
                         "every write must name the tab it targets")

    def test_setup_repairs_the_install_before_it_touches_the_rig(self) -> None:
        """A pane on a rebuilt resource folder has to fix itself; telling the player to run
        a command line is how a plugin loses a musician."""
        order: list[str] = []
        self._inst.stop()
        self._inst = mock.patch.object(
            tools.install_bridge, "install",
            side_effect=lambda: (order.append("install"), mock.Mock(
                bridge_path=Path("C:/res/bridge.lua"), startup_action="created", warnings=[]))[1])
        self._inst.start()

        with mock.patch.object(tools.launch, "find_reaper", return_value=Path("C:/reaper.exe")), \
             mock.patch.object(tools.launch, "ensure_running",
                               side_effect=lambda **kw: order.append("launch") or {"ui": "minimized"}), \
             mock.patch.object(tools.bridge, "open_rig",
                               return_value={"path": self.rig.as_posix()}), \
             mock.patch.object(tools.bridge, "call", return_value={"db": -60.0}), \
             mock.patch.object(tools.bridge, "tabs_list", return_value=[]):
            tools.handle_setup({})

        self.assertEqual(order[:2], ["install", "launch"],
                         "the bridge has to exist before REAPER is asked to run it")

    def test_a_bridge_that_does_not_answer_is_reported_with_the_fix(self) -> None:
        self._alive.stop()
        self._alive = mock.patch.object(tools.launch, "bridge_alive", return_value=False)
        self._alive.start()

        with mock.patch.object(tools.launch, "find_reaper", return_value=Path("C:/reaper.exe")), \
             mock.patch.object(tools.launch, "ensure_running", return_value={"ui": "minimized"}), \
             mock.patch.object(tools.bridge, "call", side_effect=AssertionError(
                 "nothing else may be attempted when the bridge is deaf")):
            out = tools.handle_setup({})

        self.assertIn("BRIDGE NOT ANSWERING", out)
        self.assertIn("quit REAPER", out)
        self.assertNotIn("state=STANDBY", out, "a deaf bridge is not standby")

    def test_setup_reports_a_guard_refusal_instead_of_claiming_success(self):
        with mock.patch.object(tools.launch, "find_reaper", return_value=Path("C:/reaper.exe")), \
             mock.patch.object(tools.launch, "ensure_running", return_value={"ui": "minimized"}), \
             mock.patch.object(tools.bridge, "open_rig",
                               return_value={"path": self.rig.as_posix()}), \
             mock.patch.object(tools.bridge, "call",
                               side_effect=bridge.CommandFailed({"error": "guard required"})), \
             mock.patch.object(tools.bridge, "tabs_list", return_value=[]):
            out = tools.handle_setup({})
        self.assertIn("SETUP FAILED", out)
        self.assertIn("guard required", out)


class TestTeardown(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.rig = Path(self.tmp.name) / "amphead.rpp"
        self._env = mock.patch.dict(os.environ, {"AMPHEAD_RIG": str(self.rig)})
        self._env.start()

    def tearDown(self):
        self._env.stop()
        self.tmp.cleanup()

    def test_closes_only_the_rig_tab(self):
        seen = {}

        def fake_close(cmd, **kwargs):
            seen[cmd] = kwargs
            return {"mode": "cleared", "tracks_removed": 3}

        with mock.patch.object(tools.launch, "bridge_alive", return_value=True), \
             mock.patch.object(tools.bridge, "call", side_effect=fake_close):
            out = tools.handle_teardown({})
        self.assertEqual(seen["PROJ_TAB_CLOSE"]["expect"], self.rig.as_posix())
        self.assertIn("mode=cleared", out)
        self.assertIn("tracks_removed=3", out)
        self.assertIn("leave running", out)

    def test_refusal_is_reported_not_hidden(self):
        with mock.patch.object(tools.launch, "bridge_alive", return_value=True), \
             mock.patch.object(tools.bridge, "call",
                               side_effect=bridge.CommandFailed({"error": "wrong project"})):
            out = tools.handle_teardown({})
        self.assertIn("refused", out)
        self.assertIn("wrong project", out)

    def test_host_left_alone_without_host_closed_by_default(self):
        with mock.patch.object(tools.launch, "bridge_alive", return_value=False), \
             mock.patch.object(tools.launch, "stop",
                               side_effect=AssertionError("must not quit REAPER by default")):
            out = tools.handle_teardown({})
        self.assertIn("leave running", out)
        self.assertIn("REAPER not running", out)

    def test_stop_host_quits_the_host(self):
        with mock.patch.object(tools.launch, "bridge_alive", return_value=False), \
             mock.patch.object(tools.launch, "stop", return_value=True):
            out = tools.handle_teardown({"stop_host": True})
        self.assertIn("host=stopped", out)


class TestManifest(unittest.TestCase):
    def test_declared_tools_match_registrations(self):
        """The validator fails the whole plugin if provides_tools drifts from register()."""
        # No PyYAML in this interpreter; the manifest block we care about is a plain list.
        root = Path(__file__).resolve().parents[1]
        lines = (root / "plugin.yaml").read_text(encoding="utf-8").splitlines()
        start = lines.index("provides_tools:")
        declared = []
        for line in lines[start + 1:]:
            if not line.startswith("  - "):
                break
            declared.append(line[4:].strip())
        self.assertEqual(sorted(declared), sorted(n for n, *_ in tools.TOOLS))

    def test_schemas_are_wellformed(self):
        for name, schema, _handler, _emoji in tools.TOOLS:
            self.assertEqual(schema["name"], name)
            self.assertEqual(schema["parameters"]["type"], "object")
            self.assertFalse(schema["parameters"]["additionalProperties"])
            self.assertTrue(schema["description"])


class TestLive(unittest.TestCase):
    """Going live is the one tool that makes noise, so the refusal path gets tested hardest."""

    TABS = [{"i": 0, "project": "rig.rpp", "path": "C:/x/rig.rpp", "active": True}]

    def test_refuses_without_confirm(self):
        with mock.patch.object(tools.bridge, "tabs_list", return_value=self.TABS), \
             mock.patch.object(tools.bridge, "master") as master:
            out = tools.handle_live({"on": True, "master_db": -12})
        self.assertIn("REFUSED", out)
        master.assert_not_called()

    def test_off_disarms_and_parks_the_master(self):
        with mock.patch.object(tools.bridge, "tabs_list", return_value=self.TABS), \
             mock.patch.object(tools.bridge, "disarm",
                               return_value={"armed": False, "monitor": False}) as disarm, \
             mock.patch.object(tools.bridge, "master", return_value={"db": -60.0}) as master:
            out = tools.handle_live({"on": False})
        self.assertIn("live=False", out)
        self.assertIn("master_db=-60.0", out)
        disarm.assert_called_once()
        self.assertEqual(master.call_args.kwargs["db"], -60.0)

    def test_on_sets_level_and_arms_without_touching_the_input(self):
        with mock.patch.object(tools.bridge, "tabs_list", return_value=self.TABS), \
             mock.patch.object(tools.bridge, "master", return_value={"db": -20.0}), \
             mock.patch.object(tools.bridge, "set_input", return_value={"ok": True}) as set_input, \
             mock.patch.object(tools.bridge, "track_info",
                               return_value={"recinput": "3", "armed": True, "monitor": True}), \
             mock.patch.object(tools.bridge, "arm",
                               return_value={"armed": True, "monitor": True}) as arm, \
             mock.patch.object(tools.launch, "ensure_audio",
                               return_value={"inputs": 2, "output_latency_ms": 11.6}):
            out = tools.handle_live({"on": True, "confirm": True, "master_db": -20})
        self.assertIn("live=True", out)
        self.assertIn("armed=True", out)
        self.assertIn("master_db=-20.0", out)
        self.assertIn("clips_at=11.6", out)
        self.assertIn("input_changed=False", out)
        # The default keeps whatever input REAPER already had and reports it back.
        self.assertIn("input=3", out)
        set_input.assert_not_called()
        self.assertTrue(arm.call_args.kwargs["allow_loud"])

    def test_explicit_input_still_retargets_the_track(self):
        with mock.patch.object(tools.bridge, "tabs_list", return_value=self.TABS), \
             mock.patch.object(tools.bridge, "master", return_value={"db": -20.0}), \
             mock.patch.object(tools.bridge, "set_input", return_value={"ok": True}) as set_input, \
             mock.patch.object(tools.bridge, "track_info", return_value={"recinput": "1"}), \
             mock.patch.object(tools.bridge, "arm", return_value={"armed": True, "monitor": True}), \
             mock.patch.object(tools.launch, "ensure_audio", return_value={"inputs": 2}):
            out = tools.handle_live({"on": True, "confirm": True, "input": "1"})
        self.assertEqual(set_input.call_args.kwargs["spec"], "1")
        self.assertIn("input_changed=True", out)

    def test_every_write_carries_the_active_project_as_expect(self):
        with mock.patch.object(tools.bridge, "tabs_list", return_value=self.TABS), \
             mock.patch.object(tools.bridge, "master", return_value={"db": -20.0}) as master, \
             mock.patch.object(tools.bridge, "set_input", return_value={"ok": True}) as set_input, \
             mock.patch.object(tools.bridge, "track_info", return_value={"recinput": "2"}), \
             mock.patch.object(tools.bridge, "arm", return_value={"armed": True}) as arm, \
             mock.patch.object(tools.launch, "ensure_audio", return_value={"inputs": 2}):
            tools.handle_live({"on": True, "confirm": True, "input": "2"})
        for call in (set_input, arm):
            self.assertEqual(call.call_args.kwargs.get("expect"), "C:/x/rig.rpp")
        # master is written once and read once, so check the write, not the read
        self.assertEqual(master.call_args_list[0].kwargs.get("expect"), "C:/x/rig.rpp")

    def test_closed_device_is_named_instead_of_a_bare_value_error(self):
        with mock.patch.object(tools.bridge, "tabs_list", return_value=self.TABS), \
             mock.patch.object(tools.launch, "ensure_audio",
                               return_value={"inputs": 0, "device_closed": True}), \
             mock.patch.object(tools.bridge, "set_input") as set_input:
            out = tools.handle_live({"on": True, "confirm": True})
        self.assertIn("NOT LIVE", out)
        self.assertIn("audio device is closed", out)
        set_input.assert_not_called()

    def test_no_open_project(self):
        with mock.patch.object(tools.bridge, "tabs_list", return_value=[]):
            out = tools.handle_live({"on": True, "confirm": True})
        self.assertIn("NOT LIVE", out)
        self.assertIn("no project", out)


if __name__ == "__main__":
    unittest.main()
