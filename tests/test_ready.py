"""The verdict is the contract: one word, one sentence, and the problems behind it."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from amphead import port, tools, verdict
from amphead.fake_reaper import FakeReaper


def live(bridge_state="live", out_ms=11.6, closed=False):
    return {"bridge": bridge_state, "reason": "no answer",
            "device": {"buffer": "128", "rate": "48000", "latency_out_ms": out_ms,
                       "closed": closed}}


def track(armed=True, monitor=True, recinput=0):
    return {"armed": armed, "monitor": monitor, "recinput": recinput}


def read(peak=0.05, clip=False):
    return {"l": peak, "r": peak, "clip": clip}


CHAIN = [{"name": "VST3: NAM Universal (WaveMind)", "enabled": True}]


class TestJudge(unittest.TestCase):
    def test_no_reaper_install_is_its_own_word(self):
        self.assertEqual(verdict.judge({"bridge": "missing"}, {}, {}, None)["verdict"],
                         "REAPER NOT RUNNING")

    def test_down_bridge_reads_busy_not_dead(self):
        self.assertEqual(verdict.judge({"bridge": "down"}, {}, {}, None)["verdict"],
                         "REAPER BUSY")

    def test_closed_device_is_named_before_arm_state(self):
        out = verdict.judge(live(closed=True), track(armed=False), read(0.0), None)
        self.assertEqual(out["verdict"], "DEVICE CLOSED")
        self.assertIn("audio device", out["reason"])

    def test_unarmed_track_is_standby(self):
        out = verdict.judge(live(), track(armed=False), read(0.0), CHAIN)
        self.assertEqual(out["verdict"], "STANDBY")
        self.assertEqual(out["problems"], ["the track is not armed"])

    def test_armed_with_no_input_says_no_input(self):
        out = verdict.judge(live(), track(recinput=-1), read(0.0), CHAIN)
        self.assertEqual(out["verdict"], "NO INPUT")
        self.assertIn("recinput", out["problems"][0])

    def test_clipping_wins_over_signal(self):
        out = verdict.judge(live(), track(), read(0.9, clip=True), CHAIN)
        self.assertEqual(out["verdict"], "CLIPPING")

    def test_empty_chain_says_you_hear_the_dry_di(self):
        out = verdict.judge(live(), track(), read(0.2), [])
        self.assertEqual(out["verdict"], "NO CHAIN")
        self.assertIn("raw DI", out["reason"])

    def test_everything_bypassed_is_no_chain_and_points_at_panic(self):
        out = verdict.judge(live(), track(), read(0.2),
                            [{"name": "VST3: NAM Universal (WaveMind)", "enabled": False}])
        self.assertEqual(out["verdict"], "NO CHAIN")
        self.assertIn("PANIC", out["reason"])

    def test_armed_monitoring_and_quiet_is_no_signal(self):
        out = verdict.judge(live(), track(), read(0.0), CHAIN)
        self.assertEqual(out["verdict"], "NO SIGNAL")
        self.assertIn("never rose", out["problems"][-1])

    def test_signal_arriving_is_ready_and_names_the_latency(self):
        out = verdict.judge(live(), track(), read(0.2), CHAIN)
        self.assertEqual(out["verdict"], "READY")
        self.assertIn("11.6 ms out", out["reason"])
        self.assertEqual(out["problems"], [])

    def test_a_bypassed_plugin_and_slow_latency_are_problems_not_silence(self):
        out = verdict.judge(live(out_ms=62.0), track(), read(0.2),
                            [{"name": "Amp", "enabled": True}, {"name": "Delay", "enabled": False}])
        self.assertEqual(out["verdict"], "READY")
        joined = " | ".join(out["problems"])
        self.assertIn("still bypassed: Delay", joined)
        self.assertIn("62.0 ms is above 40.0 ms", joined)

    def test_an_unread_chain_is_not_reported_as_empty(self):
        self.assertEqual(verdict.judge(live(), track(), read(0.2), None)["verdict"], "READY")

    def test_the_pane_and_the_tool_answer_from_one_judge(self):
        from dashboard import plugin_api

        for state, info, peak, clip, chain in (
            (live(), track(), 0.2, False, CHAIN),
            (live(), track(armed=False), 0.0, False, CHAIN),
            (live(closed=True), track(), 0.0, False, None),
            ({"bridge": "down"}, {}, 0.0, False, None),
        ):
            pane_word, _ = plugin_api._verdict(state, info, peak, clip)
            tool_word = verdict.judge(state, info, read(peak, clip), chain)["verdict"]
            self.assertEqual(pane_word, tool_word)


class TestReadyTool(unittest.TestCase):
    def test_live_rig_reports_ready_with_the_numbers_behind_it(self):
        with mock.patch.object(tools.RIG, "state", return_value=live()), \
             mock.patch.object(tools.RIG, "track_info", return_value=track()), \
             mock.patch.object(tools.RIG, "fx_list", return_value=CHAIN), \
             mock.patch.object(tools.RIG, "master", return_value={"db": -18.0}), \
             mock.patch.object(tools.RIG, "meter", return_value=read(0.2)) as meter:
            out = tools.handle_ready({"settle_ms": 0})
        self.assertIn("verdict=READY", out)
        self.assertIn("input=input 1", out)
        self.assertIn("live_fx=1", out)
        self.assertIn("peak_db=-14.0", out)
        self.assertEqual(meter.call_count, 1)

    def test_it_listens_again_before_calling_a_quiet_rig_silent(self):
        samples = [read(0.0), read(0.0), read(0.3)]
        with mock.patch.object(tools.RIG, "state", return_value=live()), \
             mock.patch.object(tools.RIG, "track_info", return_value=track()), \
             mock.patch.object(tools.RIG, "fx_list", return_value=CHAIN), \
             mock.patch.object(tools.RIG, "master", return_value={"db": -18.0}), \
             mock.patch.object(tools.RIG, "meter", side_effect=samples) as meter:
            out = tools.handle_ready({"settle_ms": 500})
        self.assertIn("verdict=READY", out)
        self.assertEqual(meter.call_count, 3)

    def test_an_armed_rig_with_the_master_parked_is_not_called_ready(self):
        with mock.patch.object(tools.RIG, "state", return_value=live()), \
             mock.patch.object(tools.RIG, "track_info", return_value=track()), \
             mock.patch.object(tools.RIG, "fx_list", return_value=CHAIN), \
             mock.patch.object(tools.RIG, "master", return_value={"db": -60.0}), \
             mock.patch.object(tools.RIG, "meter", return_value=read(0.2)):
            out = tools.handle_ready({"settle_ms": 0})
        self.assertIn("verdict=STANDBY", out)
        self.assertIn("master is parked", out)

    def test_a_quiet_rig_with_no_reaper_device_is_not_called_ready(self):
        with mock.patch.object(tools.RIG, "state", return_value=live(closed=True)), \
             mock.patch.object(tools.RIG, "track_info", return_value=track()), \
             mock.patch.object(tools.RIG, "fx_list", return_value=CHAIN), \
             mock.patch.object(tools.RIG, "meter", return_value=read(0.0)):
            out = tools.handle_ready({"settle_ms": 0})
        self.assertIn("verdict=DEVICE CLOSED", out)

    def test_a_bridge_that_stops_answering_mid_call_is_not_a_panic(self):
        with mock.patch.object(tools.RIG, "state", return_value=live()), \
             mock.patch.object(tools.RIG, "liveness",
                               return_value=("not-running", "REAPER is not running")), \
             mock.patch.object(tools.RIG, "track_info", side_effect=port.NoRig):
            out = tools.handle_ready({"settle_ms": 0})
        self.assertIn("verdict=REAPER NOT RUNNING", out)

    def test_a_bridge_that_vanished_mid_call_is_bridge_gone(self):
        # REAPER is still up, so the honest word is that the script went away, not that REAPER did.
        with mock.patch.object(tools.RIG, "state", return_value=live()), \
             mock.patch.object(tools.RIG, "liveness",
                               return_value=("not-loaded", "REAPER is running but the bridge is not loaded")), \
             mock.patch.object(tools.RIG, "track_info", side_effect=port.NoRig):
            out = tools.handle_ready({"settle_ms": 0})
        self.assertIn("verdict=BRIDGE GONE", out)

    def test_a_read_that_fails_on_an_empty_project_is_not_a_busy_reaper(self):
        # The bridge is answering, but the tab in front of REAPER has no track 1: not the rig.
        empty = live()
        empty["rig_tab"] = False
        with mock.patch.object(tools.RIG, "state", return_value=empty), \
             mock.patch.object(tools.RIG, "track_info",
                               side_effect=port.RigError("no track 1")):
            out = tools.handle_ready({"settle_ms": 0})
        self.assertIn("verdict=RIG TAB CLOSED", out)

    def test_ready_is_registered_and_read_only(self):
        names = [name for name, _schema, _fn, _emoji in tools.TOOLS]
        self.assertIn("amphead_ready", names)
        schema = dict((name, schema) for name, schema, _f, _e in tools.TOOLS)["amphead_ready"]
        self.assertEqual(schema["parameters"]["properties"]["settle_ms"]["maximum"], 3000)


class TestErrorWords(unittest.TestCase):
    """Each error state names the broken thing, because the symptom is shared by several of them."""

    def test_a_bridge_that_is_not_loaded_is_bridge_gone(self):
        out = verdict.judge({"bridge": "gone", "reason": "REAPER is up but the bridge script is not loaded"},
                            track(), read(0.2), CHAIN)
        self.assertEqual(out["verdict"], "BRIDGE GONE")
        self.assertIn("not loaded", out["reason"])

    def test_two_copies_of_reaper_are_named_and_counted(self):
        state = live()
        state["instances"] = 2
        out = verdict.judge(state, track(), read(0.2), CHAIN)
        self.assertEqual(out["verdict"], "TWO REAPER")
        self.assertIn("2 copies", out["reason"])
        self.assertIn("2 reaper.exe", out["problems"][0])

    def test_another_project_in_front_of_reaper_is_a_closed_rig_tab(self):
        state = live()
        state["rig_tab"] = False
        out = verdict.judge(state, track(), read(0.2), CHAIN)
        self.assertEqual(out["verdict"], "RIG TAB CLOSED")
        self.assertIn("not the amp rig", out["reason"])

    def test_two_reaper_beats_the_rig_tab_because_nothing_can_be_trusted(self):
        state = live()
        state["rig_tab"] = False
        state["instances"] = 3
        self.assertEqual(verdict.judge(state, track(), read(0.2), CHAIN)["verdict"], "TWO REAPER")

    def test_a_matching_rig_tab_and_a_single_instance_are_ignored(self):
        state = live()
        state["rig_tab"] = True
        state["instances"] = 1
        self.assertEqual(verdict.judge(state, track(), read(0.2), CHAIN)["verdict"], "READY")

    def test_a_device_with_no_inputs_is_unplugged_not_quiet(self):
        state = live()
        state["device"]["inputs"] = 0
        out = verdict.judge(state, track(), read(0.0), CHAIN)
        self.assertEqual(out["verdict"], "DEVICE UNPLUGGED")
        self.assertIn("0 inputs", out["problems"][0])

    def test_an_unchecked_input_count_is_not_read_as_unplugged(self):
        # DEVLIST and the pane do not always ask; unknown must not become an accusation.
        self.assertEqual(verdict.judge(live(), track(), read(0.0), CHAIN)["verdict"], "NO SIGNAL")

    def test_a_muted_track_is_named_before_no_signal(self):
        muted = track()
        muted["mute"] = True
        out = verdict.judge(live(), muted, read(0.0), CHAIN)
        self.assertEqual(out["verdict"], "MUTED")
        self.assertIn("muted", out["reason"])

    def test_a_reopened_device_is_not_reported_as_no_signal(self):
        state = live()
        state["device"]["reopened"] = True
        out = verdict.judge(state, track(), read(0.0), CHAIN)
        self.assertEqual(out["verdict"], "REOPENED DEVICE")
        self.assertIn("reopened it", out["reason"])

    def test_a_reopened_device_that_is_playing_says_playable(self):
        state = live()
        state["device"]["reopened"] = True
        out = verdict.judge(state, track(), read(0.2), CHAIN)
        self.assertEqual(out["verdict"], "REOPENED DEVICE")
        self.assertIn("playable", out["reason"])

    def test_a_closed_device_outranks_a_reopened_note(self):
        state = live(closed=True)
        state["device"]["reopened"] = True
        self.assertEqual(verdict.judge(state, track(), read(0.0), CHAIN)["verdict"],
                         "DEVICE CLOSED")


class TestReadyOverTheWire(unittest.TestCase):
    """The plan asks for fake REAPER scenarios, not only stubs: these go through the real wire."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "hermes_bridge"
        self.root.mkdir(parents=True)
        self.fake = FakeReaper(self.root).start()
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(self.fake.stop)
        self.addCleanup(os.environ.pop, "HERMES_REAPER_BRIDGE", None)
        os.environ["HERMES_REAPER_BRIDGE"] = str(self.root)
        self.state = live()
        patcher = mock.patch.object(tools.RIG, "state", return_value=self.state)
        patcher.start()
        self.addCleanup(patcher.stop)

    def arm(self, recinput=0):
        track = self.fake.tracks[0]
        track["armed"] = True
        track["monitor"] = True
        track["recinput"] = recinput

    def test_silent_input_over_the_wire_is_no_signal(self):
        self.arm()
        out = tools.handle_ready({"settle_ms": 100})
        self.assertIn("verdict=NO SIGNAL", out)

    def test_signal_over_the_wire_is_ready(self):
        self.arm()
        self.fake.signal = 0.2
        out = tools.handle_ready({"settle_ms": 100})
        self.assertIn("verdict=READY", out)
        self.assertIn("live_fx=2", out)

    def test_unarmed_track_over_the_wire_is_standby(self):
        self.fake.signal = 0.2
        self.assertIn("verdict=STANDBY", tools.handle_ready({"settle_ms": 0}))

    def test_no_input_over_the_wire_says_no_input(self):
        self.arm(recinput=-1)
        self.assertIn("verdict=NO INPUT", tools.handle_ready({"settle_ms": 0}))

    def test_a_bypassed_fx_is_named_as_a_problem(self):
        self.arm()
        self.fake.signal = 0.2
        self.fake.tracks[0]["fx"][1]["enabled"] = False
        out = tools.handle_ready({"settle_ms": 100})
        self.assertIn("verdict=READY", out)
        self.assertIn("still bypassed: VST3: RigSculpt (WaveMind)", out)

    def test_a_bridge_that_stops_answering_is_reported_as_busy(self):
        # The process still looks alive, so the honest word is BUSY, not "not running".
        self.fake.stall()
        self.assertIn("verdict=REAPER BUSY", tools.handle_ready({"settle_ms": 0}))


    def test_a_bridge_that_is_not_loaded_is_reported_as_bridge_gone(self):
        # The pane must not read a missing bridge script as REAPER NOT RUNNING.
        with mock.patch.object(tools.RIG, "state", return_value={
                "bridge": "gone", "reason": "REAPER is up but the bridge script is not loaded"}):
            self.assertIn("verdict=BRIDGE GONE", tools.handle_ready({"settle_ms": 0}))


if __name__ == "__main__":
    unittest.main()
