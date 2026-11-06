"""The amp face backend, tested without FastAPI or REAPER.

The route functions are plain coroutines, so the file is imported with a stub `fastapi` module
and the decorated functions are called directly. Nothing here needs a gateway or a guitar.
"""

import asyncio
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

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


class TestState(unittest.TestCase):
    def test_state_payload_has_the_five_pane_rows(self):
        with mock.patch.object(API.launch, "state", return_value=_live()), \
             mock.patch.object(API, "_track", return_value={"armed": True, "monitor": True}), \
             mock.patch.object(API.bridge, "meter",
                               return_value={"l": 0.3, "r": 0.3, "clip": False}), \
             mock.patch.object(API.bridge, "master", return_value={"db": -20.0}), \
             mock.patch.object(API, "_chain", return_value=[{"name": "VST3: NAM Universal", "enabled": True}]), \
             mock.patch.object(API, "_knobs", return_value=[{"name": "Bass", "value": "4.4"}]):
            payload = asyncio.run(STATE())
        self.assertEqual(payload["verdict"], "READY")
        for key in ("chain", "knobs", "presets", "device", "master_db", "project"):
            self.assertIn(key, payload)
        self.assertTrue(payload["presets"])

    def test_no_reaper_install_is_a_sentence_not_a_crash(self):
        with mock.patch.object(API.launch, "state", side_effect=API.launch.NoReaperInstall("gone")):
            payload = asyncio.run(STATE())
        self.assertEqual(payload["verdict"], "NO REAPER INSTALL")
        self.assertIn("not installed", payload["reason"])

    def test_meter_is_cheap_and_survives_a_dead_bridge(self):
        with mock.patch.object(API.bridge, "meter", side_effect=API.bridge.NoReaper("down")):
            payload = asyncio.run(METER())
        self.assertEqual(payload, {"l": 0.0, "r": 0.0, "clip": False, "live": False})


class TestLiveRoute(unittest.TestCase):
    """The pane's Live button must not repoint the track behind the player's back."""

    def test_live_button_leaves_the_input_alone_by_default(self):
        with mock.patch("amphead.tools.handle_live", return_value="live=True") as handler:
            payload = asyncio.run(LIVE({"on": True, "master_db": -20}))
        self.assertEqual(handler.call_args.args[0]["input"], "0")
        self.assertEqual(handler.call_args.args[0]["master_db"], -20)
        self.assertTrue(payload["on"])

    def test_live_button_still_passes_an_explicit_input_through(self):
        with mock.patch("amphead.tools.handle_live", return_value="live=True") as handler:
            asyncio.run(LIVE({"on": True, "input": "1+2"}))
        self.assertEqual(handler.call_args.args[0]["input"], "1+2")


class TestPanicRoute(unittest.TestCase):
    """The pane's red button has to reach the tool, and the tool's sentence has to come back."""

    def test_panic_passes_the_call_through_and_keeps_the_words(self):
        said = "panicked=True | master_db=-60.0 | bypassed=1"
        with mock.patch("amphead.tools.handle_panic", return_value=said) as handler:
            payload = asyncio.run(PANIC({"restore": False}))
        handler.assert_called_once_with({"restore": False, "master_db": -60})
        self.assertEqual(payload["output"], said)
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["restore"])

    def test_restore_is_forwarded_with_the_level(self):
        with mock.patch("amphead.tools.handle_panic", return_value="panicked=False") as handler:
            payload = asyncio.run(PANIC({"restore": True, "master_db": -24}))
        handler.assert_called_once_with({"restore": True, "master_db": -24})
        self.assertTrue(payload["restore"])

    def test_a_rig_that_was_never_up_is_not_reported_as_ok(self):
        with mock.patch("amphead.tools.handle_panic",
                        return_value="NOT PANICKED: REAPER is not running, so nothing can reach the speakers anyway"):
            payload = asyncio.run(PANIC({}))
        self.assertFalse(payload["ok"])


class TestPretty(unittest.TestCase):
    def test_host_decimals_are_trimmed(self):
        self.assertEqual(API._pretty("0.0000000"), "0")
        self.assertEqual(API._pretty("4.4000001"), "4.4")
        self.assertEqual(API._pretty("-80.0000000"), "-80")

    def test_words_pass_through(self):
        self.assertEqual(API._pretty("On"), "On")


if __name__ == "__main__":
    unittest.main()
