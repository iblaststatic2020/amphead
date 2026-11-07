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
        with mock.patch.object(API.launch, "state", return_value=_live()), \
             mock.patch.object(API, "_track", return_value={"armed": True, "monitor": True}), \
             mock.patch.object(API.bridge, "meter",
                               return_value={"l": 0.3, "r": 0.3, "clip": False}), \
             mock.patch.object(API.bridge, "master", return_value={"db": -20.0}), \
             mock.patch.object(API, "_chain", return_value=[{"name": "VST3: NAM Universal", "enabled": True}]), \
             mock.patch.object(API, "_knobs", return_value=[{"name": "Bass", "value": "4.4"}]):
            payload = STATE()
        self.assertEqual(payload["verdict"], "READY")
        for key in ("chain", "knobs", "presets", "device", "master_db", "project"):
            self.assertIn(key, payload)
        self.assertTrue(payload["presets"])

    def test_no_reaper_install_is_a_sentence_not_a_crash(self):
        with mock.patch.object(API.launch, "state", side_effect=API.launch.NoReaperInstall("gone")):
            payload = STATE()
        self.assertEqual(payload["verdict"], "NO REAPER INSTALL")
        self.assertIn("not installed", payload["reason"])

    def test_meter_is_cheap_and_survives_a_dead_bridge(self):
        with mock.patch.object(API.bridge, "meter", side_effect=API.bridge.NoReaper("down")):
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
        with mock.patch.object(API.launch, "ui_visible", return_value=False), \
             mock.patch.object(API.bridge, "fx_focus") as focus:
            payload = FOCUS({"fx": 0})
        focus.assert_not_called()
        self.assertFalse(payload["ok"])
        self.assertIn("not showing", payload["output"])

    def test_a_visible_reaper_gets_the_focus_call(self):
        with mock.patch.object(API.launch, "ui_visible", return_value=True), \
             mock.patch.object(API.bridge, "fx_focus",
                               return_value={"fx": "VST3: NAM Universal"}) as focus:
            payload = FOCUS({"fx": 0})
        focus.assert_called_once_with(1, 0)
        self.assertTrue(payload["ok"])
        self.assertIn("NAM Universal", payload["output"])

    def test_a_refused_focus_is_not_ok(self):
        with mock.patch.object(API.launch, "ui_visible", return_value=True), \
             mock.patch.object(API.bridge, "fx_focus", side_effect=API.bridge.ReaperError("no fx 7")):
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
        with mock.patch.object(API.launch, "state", return_value=_live()), \
             mock.patch.object(API, "_track", return_value={"armed": True, "monitor": True}), \
             mock.patch.object(API.bridge, "meter",
                               return_value={"l": 0.3, "r": 0.3, "clip": False}), \
             mock.patch.object(API.bridge, "master", return_value={"db": -60.0}), \
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
        with mock.patch.object(API.bridge, "fx_params", return_value=params):
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
        with mock.patch.object(API.bridge, "fx_params", return_value=params):
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
        with mock.patch.object(API.bridge, "fx_params", return_value=params):
            knobs = {k["name"]: k for k in API._knobs(1)}
        self.assertTrue(knobs["Pedal Bypass"]["bool"])
        self.assertTrue(knobs["HD Mode"]["bool"])
        self.assertFalse(knobs["Amplifier Bass"]["bool"])

    def test_a_nameless_parameter_is_skipped_rather_than_shown_blank(self):
        params = [{"i": 1, "name": "", "fmt": "0", "norm": 0.0}, {"i": 2, "name": "Input Gain", "fmt": "0", "norm": 0.0}]
        with mock.patch.object(API.bridge, "fx_params", return_value=params):
            self.assertEqual([k["name"] for k in API._knobs(1)], ["Input Gain"])

    def test_a_failed_read_is_an_empty_list_not_a_crash(self):
        with mock.patch.object(API.bridge, "fx_params", side_effect=API.bridge.ReaperError("no fx")):
            self.assertEqual(API._knobs(1), [])


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
        with mock.patch.object(API.bridge, "fx_list", side_effect=AssertionError("chain read")), \
             mock.patch.object(API.bridge, "meter", side_effect=AssertionError("meter read")), \
             mock.patch.object(API.bridge, "track_info", side_effect=AssertionError("track read")):
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
        with mock.patch("amphead.rig.set_param", side_effect=API.bridge.NoReaper()):
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
