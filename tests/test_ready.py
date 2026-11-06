"""The verdict is the contract: one word, one sentence, and the problems behind it."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from amphead import tools, verdict
from amphead.fake_reaper import FakeReaper


def live(bridge_state="live", out_ms=11.6, closed=False):
    return {"bridge": bridge_state, "reason": "no answer",
            "device": {"buffer": "128", "rate": "48000", "latency_out_ms": out_ms,
                       "closed": closed}}


def track(armed=True, monitor=True, recinput=1):
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
        out = verdict.judge(live(), track(recinput=0), read(0.0), CHAIN)
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
        with mock.patch.object(tools.launch, "state", return_value=live()), \
             mock.patch.object(tools.bridge, "track_info", return_value=track()), \
             mock.patch.object(tools.bridge, "fx_list", return_value=CHAIN), \
             mock.patch.object(tools.bridge, "meter", return_value=read(0.2)) as meter:
            out = tools.handle_ready({"settle_ms": 0})
        self.assertIn("verdict=READY", out)
        self.assertIn("input=1", out)
        self.assertIn("live_fx=1", out)
        self.assertIn("peak_db=-14.0", out)
        self.assertEqual(meter.call_count, 1)

    def test_it_listens_again_before_calling_a_quiet_rig_silent(self):
        samples = [read(0.0), read(0.0), read(0.3)]
        with mock.patch.object(tools.launch, "state", return_value=live()), \
             mock.patch.object(tools.bridge, "track_info", return_value=track()), \
             mock.patch.object(tools.bridge, "fx_list", return_value=CHAIN), \
             mock.patch.object(tools.bridge, "meter", side_effect=samples) as meter:
            out = tools.handle_ready({"settle_ms": 500})
        self.assertIn("verdict=READY", out)
        self.assertEqual(meter.call_count, 3)

    def test_a_quiet_rig_with_no_reaper_device_is_not_called_ready(self):
        with mock.patch.object(tools.launch, "state", return_value=live(closed=True)), \
             mock.patch.object(tools.bridge, "track_info", return_value=track()), \
             mock.patch.object(tools.bridge, "fx_list", return_value=CHAIN), \
             mock.patch.object(tools.bridge, "meter", return_value=read(0.0)):
            out = tools.handle_ready({"settle_ms": 0})
        self.assertIn("verdict=DEVICE CLOSED", out)

    def test_a_bridge_that_stops_answering_mid_call_is_not_a_panic(self):
        with mock.patch.object(tools.launch, "state", return_value=live()), \
             mock.patch.object(tools.bridge, "track_info", side_effect=tools.bridge.NoReaper):
            out = tools.handle_ready({"settle_ms": 0})
        self.assertIn("verdict=REAPER NOT RUNNING", out)

    def test_ready_is_registered_and_read_only(self):
        names = [name for name, _schema, _fn, _emoji in tools.TOOLS]
        self.assertIn("amphead_ready", names)
        schema = dict((name, schema) for name, schema, _f, _e in tools.TOOLS)["amphead_ready"]
        self.assertEqual(schema["parameters"]["properties"]["settle_ms"]["maximum"], 3000)


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
        patcher = mock.patch.object(tools.launch, "state", return_value=self.state)
        patcher.start()
        self.addCleanup(patcher.stop)

    def arm(self, recinput=1):
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
        self.arm(recinput=0)
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
        self.fake.stop()
        self.assertIn("verdict=REAPER BUSY", tools.handle_ready({"settle_ms": 0}))


if __name__ == "__main__":
    unittest.main()
