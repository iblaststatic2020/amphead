"""The amp face backend, tested without FastAPI or REAPER.

The route functions are plain functions, not coroutines: FastAPI runs a sync endpoint in its
threadpool, so a slow or missing REAPER costs a worker thread instead of freezing the gateway.
The file is imported with a stub `fastapi` module so the decorated functions are called
directly. Nothing here needs a gateway or a guitar.
"""

import inspect
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

from amphead import rig

ROOT = Path(__file__).resolve().parent.parent


class _Router:
    def __init__(self):
        self.routes = {}
        self.dependencies = []

    def _reg(self, method):
        def deco(path):
            def wrap(fn):
                self.routes[(method, path)] = fn
                return fn
            return wrap
        return deco

    def get(self, path):
        return self._reg("GET")(path)

    def post(self, path):
        return self._reg("POST")(path)


def _load():
    router = _Router()
    fake = types.ModuleType("fastapi")
    fake.APIRouter = lambda *a, **k: router
    fake.Depends = lambda dep=None, **k: dep
    sys.modules.setdefault("fastapi", fake)
    sys.path.insert(0, str(ROOT / "dashboard"))
    import plugin_api
    return router, plugin_api


ROUTER, API = _load()
STATE = ROUTER.routes[("GET", "/state")]
METER = ROUTER.routes[("GET", "/meter")]
LIVE = ROUTER.routes[("POST", "/live")]
PANIC = ROUTER.routes[("POST", "/panic")]
TWEAK = ROUTER.routes[("POST", "/tweak")]
PRESET = ROUTER.routes[("POST", "/preset")]
FOCUS = ROUTER.routes[("POST", "/focus")]
INPUT = ROUTER.routes[("POST", "/input")]
MASTER = ROUTER.routes[("POST", "/master")]

# The project a write names as `expect`: every mutating bridge command carries one, so a write
# cannot land in a tab nobody meant to touch.
RIG_PATH = "C:/Users/Chris/Music/rig.rpp"

# The demo switch is saved state, so a box that once showed the canned rig would otherwise run
# every route here against the demo rig and quietly prove nothing about the seam. Tests ask for
# demo explicitly when they want it; the rest of the file starts from the real rig.
_demo_off = None


def setUpModule():
    global _demo_off
    _demo_off = mock.patch.object(API.demo, "enabled", return_value=False)
    _demo_off.start()


def tearDownModule():
    _demo_off.stop()



def _live(**over):
    base = {"bridge": "live", "project": "amphead.rpp", "tracks": 1,
            "device": {"buffer": "512", "rate": "44100", "latency_out_ms": 11.6, "closed": False}}
    base.update(over)
    return base


class TestVerdict(unittest.TestCase):
    def test_ready_when_armed_and_signal_arrives(self):
        word, reason = API._verdict(_live(), {"armed": True, "monitor": True}, 0.2, False)
        self.assertEqual(word, "READY")
        self.assertIn("11.6", reason)

    def test_standby_when_not_armed(self):
        word, _ = API._verdict(_live(), {"armed": False}, 0.2, False)
        self.assertEqual(word, "STANDBY")

    def test_no_signal_when_armed_but_silent(self):
        word, _ = API._verdict(_live(), {"armed": True}, 0.0, False)
        self.assertEqual(word, "NO SIGNAL")

    def test_clipping_beats_ready(self):
        word, _ = API._verdict(_live(), {"armed": True}, 1.0, True)
        self.assertEqual(word, "CLIPPING")

    def test_closed_device_is_named(self):
        word, reason = API._verdict(_live(device={"closed": True}), {"armed": True}, 0.0, False)
        self.assertEqual(word, "DEVICE CLOSED")
        self.assertIn("LIVE", reason)

    def test_reaper_absent(self):
        self.assertEqual(API._verdict({"bridge": "down"}, {}, 0.0, False)[0], "REAPER BUSY")
        self.assertEqual(API._verdict({"bridge": "missing"}, {}, 0.0, False)[0], "REAPER NOT RUNNING")

    def test_a_missing_bridge_script_reaches_the_pane_as_bridge_gone(self):
        word, reason = API._verdict({"bridge": "gone", "reason": "the bridge script is not loaded"},
                                    {}, 0.0, False)
        self.assertEqual(word, "BRIDGE GONE")
        self.assertIn("not loaded", reason)

    def test_two_reaper_reaches_the_pane_as_two_reaper(self):
        state = _live()
        state["instances"] = 2
        self.assertEqual(API._verdict(state, {"armed": True}, 0.2, False)[0], "TWO REAPER")

    def test_someone_elses_project_reaches_the_pane_as_rig_tab_closed(self):
        state = _live()
        state["rig_tab"] = False
        self.assertEqual(API._verdict(state, {"armed": True}, 0.2, False)[0], "RIG TAB CLOSED")

    def test_a_muted_track_reaches_the_pane_as_muted(self):
        word, reason = API._verdict(_live(), {"armed": True, "monitor": True, "mute": True}, 0.0, False)
        self.assertEqual(word, "MUTED")
        self.assertIn("unmute", reason.lower())

    def test_a_device_with_no_inputs_reaches_the_pane_as_unplugged(self):
        state = _live(device={"closed": False, "inputs": 0, "latency_out_ms": 11.6})
        self.assertEqual(API._verdict(state, {"armed": True}, 0.0, False)[0], "DEVICE UNPLUGGED")

    def test_a_device_that_had_to_be_reopened_reaches_the_pane_as_reopened(self):
        state = _live(device={"closed": False, "reopened": True, "latency_out_ms": 11.6})
        self.assertEqual(API._verdict(state, {"armed": True, "monitor": True}, 0.0, False)[0],
                         "REOPENED DEVICE")


class TestState(unittest.TestCase):
    def test_state_payload_has_the_five_pane_rows(self):
        with mock.patch.object(API.RIG, "state", return_value=_live()), \
             mock.patch.object(API, "_track", return_value={"armed": True, "monitor": True}), \
             mock.patch.object(API.RIG, "meter",
                               return_value={"l": 0.3, "r": 0.3, "clip": False}), \
             mock.patch.object(API.RIG, "master", return_value={"db": -20.0}), \
             mock.patch.object(API, "_chain", return_value=[{"name": "VST3: NAM Universal", "enabled": True}]), \
             mock.patch.object(API, "_knobs", return_value=[{"name": "Bass", "value": "4.4"}]):
            payload = STATE()
        self.assertEqual(payload["verdict"], "READY")
        for key in ("chain", "knobs", "presets", "device", "master_db", "project"):
            self.assertIn(key, payload)
        self.assertTrue(payload["presets"])

    def test_no_reaper_install_is_a_sentence_not_a_crash(self):
        with mock.patch.object(API.RIG, "state", side_effect=API.port.NoRig("gone")):
            payload = STATE()
        self.assertEqual(payload["verdict"], "NO REAPER INSTALL")
        self.assertIn("not installed", payload["reason"])

    def test_meter_is_cheap_and_survives_a_dead_bridge(self):
        with mock.patch.object(API.RIG, "meter", side_effect=API.port.NoRig("down")):
            payload = METER()
        self.assertEqual(payload, {"l": 0.0, "r": 0.0, "clip": False, "live": False})


class TestLiveRoute(unittest.TestCase):
    """The pane's Live button must not repoint the track behind the player's back."""

    def test_live_button_leaves_the_input_alone_by_default(self):
        with mock.patch("amphead.tools.handle_live", return_value="live=True") as handler:
            payload = LIVE({"on": True, "master_db": -20})
        self.assertEqual(handler.call_args.args[0]["input"], "0")
        self.assertEqual(handler.call_args.args[0]["master_db"], -20)
        self.assertTrue(payload["on"])

    def test_live_button_still_passes_an_explicit_input_through(self):
        with mock.patch("amphead.tools.handle_live", return_value="live=True") as handler:
            LIVE({"on": True, "input": "1+2"})
        self.assertEqual(handler.call_args.args[0]["input"], "1+2")


class TestMasterRoute(unittest.TestCase):
    """The player's volume ramp: silent unless they move it, and bounded when they do."""

    def test_live_without_a_level_asks_for_silence(self):
        # The button no longer sends a level. If the route filled in a comfortable default the rig
        # would come up loud through the player's speakers, which is the thing being prevented.
        with mock.patch("amphead.tools.handle_live", return_value="live=True") as handler:
            LIVE({"on": True})
        self.assertEqual(handler.call_args.args[0]["master_db"], -60)

    # The route now hands the write to the rig the request named, so these build the rig they
    # expect to be used instead of patching the module-level one.
    def fake_rig(self, seen):
        rig = mock.Mock()
        rig.set_master.side_effect = lambda db, expect=None: seen.append(db) or {"db": db}
        rig.master.side_effect = lambda: {"db": seen[-1]}
        return rig

    def test_master_clamps_above_unity(self):
        seen = []
        with mock.patch.object(API.port, "choose", lambda name=None: self.fake_rig(seen)), \
             mock.patch("amphead.tools.active_path", return_value="C:/x/rig.rpp"):
            payload = MASTER({"db": 12})
        self.assertEqual(seen, [0.0])
        self.assertTrue(payload["ok"])

    def test_master_clamps_below_the_floor(self):
        seen = []
        with mock.patch.object(API.port, "choose", lambda name=None: self.fake_rig(seen)), \
             mock.patch("amphead.tools.active_path", return_value="C:/x/rig.rpp"):
            payload = MASTER({"db": -200})
        self.assertEqual(seen, [-60.0])
        self.assertEqual(payload["db"], -60.0)

    def test_master_without_a_number_answers_in_words(self):
        payload = MASTER({"db": "louder"})
        self.assertFalse(payload["ok"])
        self.assertIn("NO LEVEL", payload["output"])

    def test_master_refuses_a_number_that_is_not_one(self):
        # NaN survives a naive clamp and would sit in the master as a level nothing can read.
        payload = MASTER({"db": float("nan")})
        self.assertFalse(payload["ok"])
        self.assertIn("NO LEVEL", payload["output"])


class TestPanicRoute(unittest.TestCase):
    """The pane's red button has to reach the tool, and the tool's sentence has to come back."""

    def test_panic_passes_the_call_through_and_keeps_the_words(self):
        said = "panicked=True | master_db=-60.0 | bypassed=1"
        with mock.patch("amphead.tools.handle_panic", return_value=said) as handler:
            payload = PANIC({"restore": False})
        handler.assert_called_once_with({"restore": False, "master_db": -60})
        self.assertEqual(payload["output"], said)
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["restore"])

    def test_restore_is_forwarded_with_the_level(self):
        with mock.patch("amphead.tools.handle_panic", return_value="panicked=False") as handler:
            payload = PANIC({"restore": True, "master_db": -24})
        handler.assert_called_once_with({"restore": True, "master_db": -24})
        self.assertTrue(payload["restore"])

    def test_a_rig_that_was_never_up_is_not_reported_as_ok(self):
        with mock.patch("amphead.tools.handle_panic",
                        return_value="NOT PANICKED: REAPER is not running, so nothing can reach the speakers anyway"):
            payload = PANIC({})
        self.assertFalse(payload["ok"])


class TestFocusRoute(unittest.TestCase):
    """A click that focuses a plugin in a hidden chain window would be a click with no effect."""

    def test_a_hidden_reaper_is_told_in_words_and_nothing_is_focused(self):
        with mock.patch.object(API.RIG, "ui_visible", return_value=False), \
             mock.patch.object(API.RIG, "fx_focus") as focus:
            payload = FOCUS({"fx": 0})
        focus.assert_not_called()
        self.assertFalse(payload["ok"])
        self.assertIn("not showing", payload["output"])

    def test_a_visible_reaper_gets_the_focus_call(self):
        with mock.patch.object(API.RIG, "ui_visible", return_value=True), \
             mock.patch.object(API.RIG, "fx_focus",
                               return_value={"fx": "VST3: NAM Universal"}) as focus:
            payload = FOCUS({"fx": 0})
        focus.assert_called_once_with(1, 0)
        self.assertTrue(payload["ok"])
        self.assertIn("NAM Universal", payload["output"])

    def test_a_refused_focus_is_not_ok(self):
        with mock.patch.object(API.RIG, "ui_visible", return_value=True), \
             mock.patch.object(API.RIG, "fx_focus", side_effect=API.port.RigError("no fx 7")):
            self.assertFalse(FOCUS({"fx": 7})["ok"])


class TestUiVisible(unittest.TestCase):
    def test_window_answers_are_read_from_powershell(self):
        from amphead import launch
        launch._UI_CACHE = None
        with mock.patch.object(launch, "_powershell", return_value="visible"):
            self.assertTrue(launch.ui_visible())
        launch._UI_CACHE = None
        with mock.patch.object(launch, "_powershell", return_value="hidden"):
            self.assertFalse(launch.ui_visible())
        launch._UI_CACHE = None


class TestPretty(unittest.TestCase):
    def test_host_decimals_are_trimmed(self):
        self.assertEqual(API._pretty("0.0000000"), "0")
        self.assertEqual(API._pretty("4.4000001"), "4.4")
        self.assertEqual(API._pretty("-80.0000000"), "-80")

    def test_words_pass_through(self):
        self.assertEqual(API._pretty("On"), "On")


class TestParkedMaster(unittest.TestCase):
    """Armed and fed but parked is silent, so the face must not call it READY."""

    def test_a_parked_master_is_not_ready(self):
        word, reason = API._verdict(_live(), {"armed": True}, 0.3, False, -60.0)
        self.assertEqual(word, "STANDBY")
        self.assertIn("parked", reason)

    def test_no_master_reading_keeps_the_old_answer(self):
        self.assertEqual(API._verdict(_live(), {"armed": True}, 0.3, False)[0], "READY")

    def test_state_reads_the_master_before_judging(self):
        with mock.patch.object(API.RIG, "state", return_value=_live()), \
             mock.patch.object(API, "_track", return_value={"armed": True, "monitor": True}), \
             mock.patch.object(API.RIG, "meter",
                               return_value={"l": 0.3, "r": 0.3, "clip": False}), \
             mock.patch.object(API.RIG, "master", return_value={"db": -60.0}), \
             mock.patch.object(API, "_chain", return_value=[{"name": "NAM", "enabled": True}]), \
             mock.patch.object(API, "_knobs", return_value=[]):
            payload = STATE()
        self.assertEqual(payload["verdict"], "STANDBY")
        self.assertEqual(payload["master_db"], -60.0)


class TestKnobs(unittest.TestCase):
    def test_knobs_carry_index_and_normalized_value(self):
        params = [
            {"i": 2, "name": "Input Gain", "fmt": "-12.6787491", "norm": 0.490573, "raw": 0.49},
            {"i": 5, "name": "Amplifier Bass", "fmt": "4.4000001", "norm": 0.44, "raw": 4.4},
            {"i": 9, "name": "Pedal Bypass", "fmt": "Off", "norm": 0.0, "raw": 0.0},
        ]
        with mock.patch.object(API.RIG, "fx_params", return_value=params):
            knobs = API._knobs(1)
        # Every control the plugin has, amp first, in the plugin's own order within each section:
        # the pane is meant to be the VST's own face, not a shortlist this file invented.
        self.assertEqual([k["name"] for k in knobs], ["Amplifier Bass", "Pedal Bypass", "Input Gain"])
        self.assertEqual([k["index"] for k in knobs], [5, 9, 2])
        self.assertEqual([k["section"] for k in knobs], ["amp", "pedal", "other"])
        self.assertEqual(knobs[0]["norm"], 0.44)
        self.assertEqual(knobs[0]["value"], "4.4")

    def test_every_parameter_is_shown_even_a_word_valued_one(self):
        """A switch reads "Off", and hiding it would leave the face disagreeing with the VST."""
        params = [{"i": 1, "name": "Amplifier Voicing", "fmt": "Vintage", "norm": 0.33, "raw": 2.0},
                  {"i": 2, "name": "Pedal Bypass", "fmt": "Off", "norm": 0.0, "raw": 0.0}]
        with mock.patch.object(API.RIG, "fx_params", return_value=params):
            knobs = API._knobs(1)
        self.assertEqual([k["value"] for k in knobs], ["Vintage", "Off"])

    def test_an_on_off_parameter_is_marked_as_a_two_position_knob(self):
        """On/Off is decided by the host's own word, so a knob that says Off snaps between two
        ends instead of sitting at 0.47 with no meaning."""
        params = [
            {"i": 1, "name": "Pedal Bypass", "fmt": "Off", "norm": 0.0, "raw": 0.0},
            {"i": 2, "name": "Amplifier Bass", "fmt": "4.4", "norm": 0.44, "raw": 4.4},
            {"i": 3, "name": "HD Mode", "fmt": "On", "norm": 1.0, "raw": 1.0},
        ]
        with mock.patch.object(API.RIG, "fx_params", return_value=params):
            knobs = {k["name"]: k for k in API._knobs(1)}
        self.assertTrue(knobs["Pedal Bypass"]["bool"])
        self.assertTrue(knobs["HD Mode"]["bool"])
        self.assertFalse(knobs["Amplifier Bass"]["bool"])

    def test_a_nameless_parameter_is_skipped_rather_than_shown_blank(self):
        params = [{"i": 1, "name": "", "fmt": "0", "norm": 0.0}, {"i": 2, "name": "Input Gain", "fmt": "0", "norm": 0.0}]
        with mock.patch.object(API.RIG, "fx_params", return_value=params):
            self.assertEqual([k["name"] for k in API._knobs(1)], ["Input Gain"])

    def test_a_failed_read_is_an_empty_list_not_a_crash(self):
        with mock.patch.object(API.RIG, "fx_params", side_effect=API.port.RigError("no fx")):
            self.assertEqual(API._knobs(1), [])


class TestInputSocket(unittest.TestCase):
    """The socket, and the two numbers that must never be confused: track and input."""

    def _channels(self, count=2, names=("IN 1", "IN 2")):
        return {"count": count, "channels": [{"i": i, "name": name} for i, name in enumerate(names)]}

    def test_socket_one_is_the_first_channel_not_the_track_number(self):
        """REAPER stores the first channel as 0, and a track's own number plays no part in it."""
        with mock.patch.object(API.RIG, "inputs", return_value=self._channels()):
            mine = API._input({"recinput": 0.0}, {"bridge": "live"})
        self.assertEqual((mine["number"], mine["name"], mine["value"]), (1, "IN 1", "input 1"))
        self.assertEqual(mine["count"], 2)
        self.assertEqual(mine["norm"], 0.0)

    def test_the_second_socket_is_the_second_stop_of_the_knob(self):
        with mock.patch.object(API.RIG, "inputs", return_value=self._channels()):
            mine = API._input({"recinput": 1.0}, {"bridge": "live"})
        self.assertEqual((mine["number"], mine["name"]), (2, "IN 2"))
        self.assertEqual(mine["norm"], 1.0)

    def test_a_track_pointed_past_the_sockets_is_reported_not_clamped(self):
        """Three tracks and two sockets must not invent a third socket."""
        with mock.patch.object(API.RIG, "inputs", return_value=self._channels()):
            mine = API._input({"recinput": 2.0}, {"bridge": "live"})
        self.assertEqual(mine["number"], 3)
        self.assertEqual(mine["name"], "input 3")
        self.assertEqual([row["name"] for row in mine["channels"]], ["IN 1", "IN 2"])

    def test_a_socket_turn_writes_and_reports_the_interface_name(self):
        with mock.patch.object(API.RIG, "project", return_value=RIG_PATH), \
             mock.patch.object(API.RIG, "inputs", return_value=self._channels()), \
             mock.patch.object(API.RIG, "set_input",
                               return_value={"recinput": 1.0, "applied": True}) as wrote, \
             mock.patch.object(API.RIG, "track_info", return_value={"recinput": 1.0}):
            answer = INPUT({"input": 2})
        # The write names the project the player is looking at, or the bridge's guard refuses it.
        wrote.assert_called_once_with(track=1, spec=2, inputs=2, expect=RIG_PATH)
        self.assertTrue(answer["ok"])
        self.assertEqual((answer["number"], answer["name"], answer["track"]), (2, "IN 2", 1))
        self.assertIn("IN 2", answer["output"])

    def test_a_turn_with_no_sockets_says_the_device_is_closed(self):
        with mock.patch.object(API.RIG, "project", return_value=RIG_PATH), \
             mock.patch.object(API.RIG, "inputs", return_value={"count": 0, "channels": []}), \
             mock.patch.object(API.RIG, "ensure_audio",
                               return_value={"count": 0, "inputs": 0}) as opened, \
             mock.patch.object(API.RIG, "set_input") as wrote:
            answer = INPUT({"input": 1})
        self.assertFalse(answer["ok"])
        self.assertIn("closed", answer["output"])
        opened.assert_called_once()
        wrote.assert_not_called()

    def test_a_sleeping_device_is_opened_before_the_socket_is_blamed(self):
        """REAPER parks the device closed when idle; that is not the interface losing its sockets."""
        # First read: the device is asleep and has no sockets. Second read, after it is opened:
        # the interface answers with its own channel names again.
        asleep = {"count": 0, "channels": []}
        awake = self._channels()
        with mock.patch.object(API.RIG, "project", return_value=RIG_PATH), \
             mock.patch.object(API.RIG, "inputs", side_effect=[asleep, awake]), \
             mock.patch.object(API.RIG, "ensure_audio",
                               return_value={"count": 2, "inputs": 2}) as opened, \
             mock.patch.object(API.RIG, "set_input",
                               return_value={"recinput": 0.0, "applied": True}) as wrote, \
             mock.patch.object(API.RIG, "track_info", return_value={"recinput": 0.0}):
            answer = INPUT({"input": 1})
        opened.assert_called_once()
        wrote.assert_called_once_with(track=1, spec=1, inputs=2, expect=RIG_PATH)
        self.assertTrue(answer["ok"], answer["output"])
        self.assertEqual(answer["name"], "IN 1")

    def test_a_turn_with_no_project_open_is_refused(self):
        with mock.patch.object(API.RIG, "project", return_value=None), \
             mock.patch.object(API.RIG, "inputs", return_value=self._channels()), \
             mock.patch.object(API.RIG, "set_input") as wrote:
            answer = INPUT({"input": 1})
        self.assertFalse(answer["ok"])
        self.assertIn("no project", answer["output"])
        wrote.assert_not_called()

    def test_a_turn_with_no_socket_named_is_refused(self):
        self.assertFalse(INPUT({})["ok"])
        self.assertFalse(INPUT({"input": "  "})["ok"])

    def test_the_state_carries_the_socket_so_the_pane_can_turn_it(self):
        with mock.patch.object(API.RIG, "inputs", return_value=self._channels()):
            mine = API._input({"recinput": 0.0}, {"bridge": "live"})
        self.assertEqual(set(mine), {"value", "name", "number", "count", "channels", "stereo",
                                     "norm", "track"})


class TestKnobRoute(unittest.TestCase):
    """The drag path: one write, the plugin's own reading, and nothing else behind it."""

    def test_a_turn_answers_in_the_plugins_units(self):
        done = mock.Mock(norm=0.3137, fmt="6.25", written=0.3137, clamped=False,
                         retried=False, param_name="Amplifier Bass")
        done.sentence.return_value = "Amplifier Bass = 6.25"
        with mock.patch("amphead.rig.set_param", return_value=done) as write:
            payload = API.knob({"param": 5, "value": 0.31, "mode": "norm"})
        write.assert_called_once()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["value"], "6.25")
        self.assertAlmostEqual(payload["norm"], 0.3137)
        self.assertEqual(payload["param"], "Amplifier Bass")

    def test_a_turn_never_lists_the_chain_or_the_meter(self):
        """A drag asks ten times a second, so the route must not pay for the face's state."""
        with mock.patch.object(API.RIG, "fx_list", side_effect=AssertionError("chain read")), \
             mock.patch.object(API.RIG, "meter", side_effect=AssertionError("meter read")), \
             mock.patch.object(API.RIG, "track_info", side_effect=AssertionError("track read")):
            done = mock.Mock(norm=0.5, fmt="0.5", written=0.5, clamped=False, retried=False,
                             param_name="Amplifier Mid")
            done.sentence.return_value = "ok"
            with mock.patch("amphead.rig.set_param", return_value=done):
                payload = API.knob({"param": 3, "value": 0.5, "mode": "norm"})
        self.assertTrue(payload["ok"], payload)

    def test_a_clamped_turn_says_so(self):
        done = mock.Mock(norm=1.0, fmt="10", written=1.0, clamped=True, retried=False,
                         param_name="Amplifier Treble")
        done.sentence.return_value = "Amplifier Treble = 10 | clamped from 1.2"
        with mock.patch("amphead.rig.set_param", return_value=done):
            payload = API.knob({"param": 4, "value": 1.2, "mode": "norm"})
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["clamped"])
        self.assertAlmostEqual(payload["norm"], 1.0)

    def test_a_turn_with_no_reaper_answers_in_words(self):
        with mock.patch("amphead.rig.set_param", side_effect=API.port.NoRig()):
            payload = API.knob({"param": 3, "value": 0.5})
        self.assertFalse(payload["ok"])
        self.assertIn("REAPER is not running", payload["output"])

    def test_a_refusal_is_not_a_crash(self):
        with mock.patch("amphead.rig.set_param", side_effect=rig.WriteRefused("no proof")):
            payload = API.knob({"param": 3, "value": 0.5})
        self.assertFalse(payload["ok"])
        self.assertIn("REFUSED", payload["output"])

    def test_a_turn_with_no_knob_named_is_refused_before_the_host(self):
        with mock.patch("amphead.rig.set_param") as write:
            payload = API.knob({"value": 0.3})
        write.assert_not_called()
        self.assertIn("NO PARAM", payload["output"])


class TestTweakRoute(unittest.TestCase):
    def test_a_slider_write_goes_through_the_chat_handler(self):
        said = "Amplifier Bass [#5] -> 0.25 | reads_back=0.25 | one undo point"
        with mock.patch("amphead.tools.handle_set", return_value=said) as handler:
            payload = TWEAK({"param": 5, "value": 0.25, "mode": "norm"})
        args = handler.call_args.args[0]
        self.assertEqual((args["param"], args["value"], args["mode"]), (5, 0.25, "norm"))
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["output"], said)

    def test_a_write_with_no_knob_named_is_refused_before_the_host(self):
        with mock.patch("amphead.tools.handle_set") as handler:
            payload = TWEAK({"value": 0.3})
        handler.assert_not_called()
        self.assertFalse(payload["ok"])
        self.assertIn("NO PARAM", payload["output"])

    def test_a_refused_write_is_reported_as_not_ok(self):
        with mock.patch("amphead.tools.handle_set", return_value="REFUSED: that knob does not exist"):
            self.assertFalse(TWEAK({"param": 5, "value": 0.3})["ok"])


class TestPresetRoute(unittest.TestCase):
    def test_preset_applies_through_the_tone_handler(self):
        said = "preset=crunch | changed=9 | already there=1 | 1.11s | one undo point"
        with mock.patch("amphead.tools.handle_tone", return_value=said) as handler:
            payload = PRESET({"query": "make it crunchier"})
        self.assertTrue(payload["ok"])
        self.assertEqual(handler.call_args.args[0]["query"], "make it crunchier")
        self.assertTrue(handler.call_args.args[0]["apply"])

    def test_a_preset_that_matches_nothing_is_not_ok(self):
        with mock.patch("amphead.tools.handle_tone",
                        return_value="NO MATCH: no sound in the library answers to ..."):
            self.assertFalse(PRESET({"query": "wobble"})["ok"])

    def test_an_empty_preset_request_never_reaches_the_host(self):
        with mock.patch("amphead.tools.handle_tone") as handler:
            payload = PRESET({"query": "  "})
        handler.assert_not_called()
        self.assertFalse(payload["ok"])


class TestTheSecondFaceWritesToItsOwnRig(unittest.TestCase):
    """A click in Amp 2 has to reach the engine, not the REAPER rig that happens to be at import.

    Reads already resolved per request; the write routes go through the tool handlers, which
    hold one rig for the process. Before this, every Amp 2 button was answered by REAPER and
    came back "REAPER is not running" while the local engine sat open in the next pane.
    """

    def setUp(self):
        from amphead import tools

        self.tools = tools
        self.held = tools.RIG

    def tearDown(self):
        self.tools.RIG = self.held
        API._ENGINE.set("")

    def test_live_under_engine_amphead2_hands_the_handler_the_engine_rig(self):
        engine = object()
        seen = {}

        def fake_live(args, **_kw):
            seen["rig"] = self.tools.RIG
            return "live=True"

        with mock.patch.object(API.port, "choose", lambda name=None: engine), \
             mock.patch.object(self.tools, "handle_live", fake_live):
            API._ENGINE.set("amphead2")
            LIVE({"on": True, "master_db": -12})
        self.assertIs(seen["rig"], engine)
        self.assertIs(self.tools.RIG, self.held)

    def test_panic_under_engine_amphead2_hands_the_handler_the_engine_rig(self):
        engine = object()
        seen = {}

        def fake_panic(args, **_kw):
            seen["rig"] = self.tools.RIG
            return "panicked=True"

        with mock.patch.object(API.port, "choose", lambda name=None: engine), \
             mock.patch.object(self.tools, "handle_panic", fake_panic):
            API._ENGINE.set("amphead2")
            PANIC({})
        self.assertIs(seen["rig"], engine)

    def test_the_turn_routes_do_the_same(self):
        engine = object()
        seen = {}

        def fake_set(args, **_kw):
            seen["rig"] = self.tools.RIG
            return "bass = 3.0"

        with mock.patch.object(API.port, "choose", lambda name=None: engine), \
             mock.patch.object(self.tools, "handle_set", fake_set):
            API._ENGINE.set("amphead2")
            TWEAK({"param": "Bass", "value": 3})
        self.assertIs(seen["rig"], engine)

    def test_the_master_ramp_reaches_the_engine(self):
        """The ramp is the step that makes sound, so it is the worst place to answer for REAPER."""
        engine = mock.Mock()
        engine.set_master.return_value = {"db": -14.0}
        engine.master.return_value = {"db": -14.0}

        with mock.patch.object(API.port, "choose", lambda name=None: engine), \
             mock.patch("amphead.tools.active_path", return_value="AmpHead 2"):
            API._ENGINE.set("amphead2")
            out = MASTER({"db": -14})
        self.assertTrue(out["ok"])
        self.assertEqual(engine.set_master.call_args.kwargs["db"], -14.0)

    def test_setup_is_a_route_of_its_own(self):
        """Amp 2 has no REAPER to open, so the pane needs a door that starts the engine."""
        self.assertIn(("POST", "/setup"), ROUTER.routes)
        engine = object()
        seen = {}

        def fake_setup(args, **_kw):
            seen["rig"] = self.tools.RIG
            return "state=STANDBY"

        with mock.patch.object(API.port, "choose", lambda name=None: engine), \
             mock.patch.object(self.tools, "handle_setup", fake_setup):
            API._ENGINE.set("amphead2")
            out = ROUTER.routes[("POST", "/setup")]({})
        self.assertIs(seen["rig"], engine)
        self.assertTrue(out["ok"])

    def test_a_failed_setup_is_reported_as_not_ok(self):
        with mock.patch.object(self.tools, "handle_setup", lambda args, **kw: "NOT STARTED: no audio device"):
            out = ROUTER.routes[("POST", "/setup")]({})
        self.assertFalse(out["ok"])


class TestRoutesStayOffTheEventLoop(unittest.TestCase):
    """A route declared `async def` blocks the whole gateway while the bridge waits.

    With REAPER closed every call here burned its full timeout inside the event loop that the
    desktop app shares, so health checks took seconds and every list in the app timed out. The
    fix is that these stay plain functions: FastAPI then runs them in a threadpool.
    """

    def test_no_route_is_a_coroutine(self):
        for method, path in (("GET", "/state"), ("GET", "/meter"), ("POST", "/live"),
                             ("POST", "/panic"), ("POST", "/tweak"), ("POST", "/focus"),
                             ("POST", "/preset")):
            route = ROUTER.routes[(method, path)]
            self.assertFalse(inspect.iscoroutinefunction(route),
                             f"{method} {path} is async: it will block the gateway's event loop")


if __name__ == "__main__":
    unittest.main()
