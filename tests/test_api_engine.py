"""The pane's backend, exercised with a stand-in engine.

These routes are the only thing the face talks to, so they get tested here without a sound card:
click a knob and the fake rig records the exact write, and every reply has to be a sentence a
player can read plus a page full of fields the face already knows how to paint.
"""

import unittest

try:
    from dashboard import plugin_api
except Exception as exc:  # fastapi is only installed in the plugin interpreter
    raise unittest.SkipTest(f"the dashboard needs fastapi: {exc}")


class FakeRig:
    def __init__(self):
        self.calls = []
        self.db = -6.0
        self.in_db = 0.0
        self.out_db = 0.0
        self.input_channel = 1
        self.running = False
        self.open = True
        self.armed = False
        self.closed = False
        self.capture = "Bugera 6262 Lead"
        self.capture_path = "C:/nam/bugera.nam"
        self.cab = "AURORA 212 GOV"
        self.cab_path = "C:/irs/aurora.wav"
        self.clip = False
        self.peak = 0.2
        self.master_db = -6.0
        self.bpm = 120.0
        self.metronome = False
        self.metronome_db = -18.0

    # ---- what the engine knows -------------------------------------------------------------
    def state(self):
        return {"open": self.open, "running": self.running, "armed": self.armed,
                "master_db": self.db, "engine": "amphead2", "capture": self.capture,
                "capture_path": self.capture_path, "cab": self.cab,
                "cab_path": self.cab_path, "input_channel": self.input_channel,
                "input_gain_db": self.in_db, "output_gain_db": self.out_db,
                "bpm": self.bpm, "metronome": self.metronome,
                "metronome_db": self.metronome_db,
                "tone": {"bass": 5.0, "mid": 5.0, "treble": 5.0},
                "device": {"closed": self.closed, "inputs": 2, "mode": "wdm-ks",
                           "buffer": "512", "rate": "44100", "latency_out_ms": 5.1},
                "latency_ms": 10.2, "samplerate": 44100, "blocksize": 512,
                "rate_mismatch": getattr(self, "rate_mismatch", None),
                "callback_errors": getattr(self, "callback_errors", 0),
                "late": getattr(self, "late", 0),
                "underflows": getattr(self, "underflows", 0),
                "overflows": getattr(self, "overflows", 0),
                "stages": {"capture": True, "tone": True, "cab": True}}

    def meter(self, track=1):
        return {"l": self.peak, "r": self.peak, "clip": self.clip}

    def master(self):
        return {"db": self.master_db}

    def inputs(self):
        return {"count": 2, "channels": [{"i": 0, "name": "IN 1"}, {"i": 1, "name": "IN 2"}]}

    def fx_list(self, track=1):
        return [{"i": 0, "name": "capture", "enabled": True},
                {"i": 1, "name": "tone", "enabled": True}]

    def library(self):
        return {"captures": [{"name": "Bugera 6262 Lead", "path": "C:/nam/bugera.nam"}],
                "cabs": [{"name": "AURORA 212 GOV", "path": "C:/irs/aurora.wav"}],
                "capture": self.capture_path, "cab": self.cab_path}

    # ---- what the engine does --------------------------------------------------------------
    def set_master(self, db):
        self.calls.append(("master", db))
        self.master_db = db
        self.db = db
        return {"db": db}

    def set_input_gain(self, db):
        self.calls.append(("in", db))
        self.in_db = db
        return {"db": db}

    def set_output_gain(self, db):
        self.calls.append(("out", db))
        self.out_db = db
        return {"db": db}

    def set_tempo(self, bpm=None, metronome=None, metronome_db=None):
        if bpm is not None:
            self.bpm = float(bpm)
        if metronome is not None:
            self.metronome = bool(metronome)
        if metronome_db is not None:
            self.metronome_db = float(metronome_db)
        self.calls.append(("tempo", self.bpm, self.metronome))
        return {"bpm": self.bpm, "metronome": self.metronome,
                "metronome_db": self.metronome_db}

    def set_gate(self, db=-80.0):
        self.calls.append(("gate", db))
        self.gate_db = db
        return {"db": db}

    def apply_rig(self, rig):
        self.calls.append(("rig", dict(rig)))
        known = {row["name"].lower() for row in self.library()["captures"]}
        self.capture = rig.get("capture") or self.capture
        self.cab = rig.get("cab") or self.cab
        kept = []
        if rig.get("capture") and rig["capture"].lower() not in known:
            kept.append(f"capture {rig['capture']!r} is not in the library")
        return {"loaded": {}, "kept": kept, "pending": False}

    def tune(self):
        self.calls.append(("tune",))
        return {"verdict": "256 frames, 5.8 ms a block, 3.1 ms of work, with room to spare",
                "blocksize": 256, "changed": False, "round_trip_ms": 15.8}

    def start_tune(self):
        self.calls.append(("start_tune",))
        return {"running": True, "phase": "starting", "seconds": 0.0, "already": False}

    def set_param(self, track=1, fx=1, param=None, value=None, mode="auto"):
        self.calls.append(("param", param, value))
        got = {("bass", "mid", "treble")[i]: name for i, name in
               enumerate(("Bass", "Mid", "Treble"))}.get(str(param).lower(), str(param))
        return {"param_name": got, "landed": value, "fmt": f"{float(value):+.1f} dB"}

    def set_input(self, track=1, spec=1):
        self.calls.append(("input", spec))
        self.input_channel = int(spec)
        return {"value": int(spec)}

    def open_capture(self, path):
        self.calls.append(("capture", path))
        self.capture_path = path
        self.capture = path.split("/")[-1]
        return {"capture": self.capture, "capture_path": path}

    def open_cab(self, path):
        self.calls.append(("cab", path))
        self.cab_path = path
        self.cab = path.split("/")[-1]
        return {"cab": self.cab, "cab_path": path}

    def arm(self, track=1, monitor=True, allow_loud=False):
        self.armed = True
        return {"armed": True, "master_db": self.db}

    def disarm(self, track=1):
        self.armed = False
        return {"armed": False}

    def fx_bypass(self, track=1, fx=1, on=False):
        return {"fx": fx, "bypassed": on}

    def set_track_level(self, track=1, db=0.0):
        return self.set_master(db)

    def stop(self):
        self.running = False
        self.open = False
        return True

    def ensure(self):
        self.calls.append(("ensure",))
        self.open = self.running = True
        return {"open": True, "master_db": self.master_db}

    def start(self, **kw):
        self.running = True
        self.open = True
        return {"open": True}


class ApiTest(unittest.TestCase):
    def setUp(self):
        self.rig = FakeRig()
        self._saved = plugin_api.RIG
        plugin_api.RIG = self.rig

    def tearDown(self):
        plugin_api.RIG = self._saved

    def state(self):
        return plugin_api.state()


class StateTest(ApiTest):
    def test_the_face_gets_one_page_it_can_paint(self):
        got = self.state()
        for key in ("verdict", "reason", "running", "armed", "master_db", "device", "input",
                    "chain", "knobs", "capture", "cab", "tone", "tone_name",
                    "input_gain_db", "output_gain_db", "latency_ms"):
            self.assertIn(key, got)

    def test_the_page_names_the_two_knobs_either_side_of_the_amp(self):
        got = self.state()
        self.assertEqual(got["input_gain_db"], 0.0)
        self.assertEqual(got["output_gain_db"], 0.0)

    def test_the_tone_name_is_read_off_the_files_not_asked_for(self):
        got = self.state()
        self.assertEqual(got["tone_name"], "bugera / aurora")

    def test_a_closed_engine_still_answers_with_words(self):
        self.rig.open = False
        got = self.state()
        self.assertEqual(got["verdict"], plugin_api.verdict.ENGINE_OFF)
        self.assertEqual(got["state"], plugin_api.verdict.ENGINE_OFF)
        self.assertEqual(got["next"], "press SET UP")
        self.assertFalse(got["running"])
        self.assertEqual(got["chain"], [])

    def test_the_page_says_the_activity_and_the_health_not_just_a_word(self):
        got = self.state()
        self.assertEqual(got["activity"], "idle")
        self.assertEqual(got["health"], "ok")
        self.assertEqual(got["next"], "press GO LIVE")

    def test_a_rate_mismatch_reaches_the_face_as_a_notice(self):
        self.rig.armed = True
        self.rig.rate_mismatch = {"capture_hz": 48000, "stream_hz": 44100}
        got = self.state()
        self.assertEqual(got["health"], "warn")
        self.assertIn("48 kHz", got["notice"])
        self.assertEqual(got["verdict"], plugin_api.verdict.READY)


class SwitchTest(ApiTest):
    def test_setup_opens_the_engine_through_the_tool_handler(self):
        out = plugin_api.setup_rig({})
        self.assertIn("output", out)

    def test_teardown_says_already_closed_rather_than_pretending(self):
        self.rig.running = False
        out = plugin_api.teardown({})
        self.assertTrue(out["ok"])
        self.assertIn("already closed", out["output"])

    def test_teardown_closes_a_running_engine(self):
        self.rig.running = True
        out = plugin_api.teardown({})
        self.assertTrue(out["ok"])
        self.assertFalse(self.rig.open)

    def test_teardown_reaps_an_engine_left_from_an_earlier_session(self):
        self.rig.running = False
        self.rig.reap = lambda: [4242]
        out = plugin_api.teardown({})
        self.assertTrue(out["ok"])
        self.assertIn("stopped 1 engine process", out["output"])


class TrimTest(ApiTest):
    def test_input_gain_is_written_and_echoed(self):
        out = plugin_api.input_gain({"db": 6.0})
        self.assertTrue(out["ok"])
        self.assertIn(("in", 6.0), self.rig.calls)
        self.assertEqual(out["db"], 6.0)

    def test_output_gain_is_written_and_echoed(self):
        out = plugin_api.output_gain({"db": -4.5})
        self.assertTrue(out["ok"])
        self.assertIn(("out", -4.5), self.rig.calls)

    def test_the_trims_stop_at_the_engine_ceiling(self):
        self.assertEqual(plugin_api.input_gain({"db": 99})["db"], 24.0)
        self.assertEqual(plugin_api.output_gain({"db": -99})["db"], -24.0)

    def test_the_gate_is_written_and_echoed(self):
        out = plugin_api.gate({"db": -42.0})
        self.assertTrue(out["ok"])
        self.assertIn(("gate", -42.0), self.rig.calls)
        self.assertEqual(out["db"], -42.0)

    def test_the_gate_floor_is_off_and_says_so(self):
        out = plugin_api.gate({"db": -999})
        self.assertEqual(out["db"], -80.0)
        self.assertIn("off", out["output"])

    def test_the_gate_stops_at_its_ceiling(self):
        self.assertEqual(plugin_api.gate({"db": 12})["db"], -20.0)

    def test_a_word_instead_of_a_gate_number_is_refused(self):
        out = plugin_api.gate({"db": "hiss"})
        self.assertFalse(out["ok"])
        self.assertIn("NO LEVEL", out["output"])

    def test_tempo_is_written_and_echoed(self):
        out = plugin_api.tempo({"bpm": 192})
        self.assertTrue(out["ok"])
        self.assertIn(("tempo", 192.0, False), self.rig.calls)
        self.assertEqual(out["bpm"], 192.0)

    def test_tempo_stops_at_the_ends_of_the_range(self):
        self.assertEqual(plugin_api.tempo({"bpm": 999})["bpm"], 300.0)
        self.assertEqual(plugin_api.tempo({"bpm": 1})["bpm"], 40.0)

    def test_the_click_is_switched_without_moving_the_tempo(self):
        out = plugin_api.tempo({"metronome": True})
        self.assertEqual(out["bpm"], 120.0)
        self.assertTrue(out["metronome"])
        self.assertIn("click on", out["output"])

    def test_a_word_instead_of_a_tempo_is_refused(self):
        out = plugin_api.tempo({"bpm": "fast"})
        self.assertFalse(out["ok"])
        self.assertIn("NO TEMPO", out["output"])

    def test_tune_starts_at_once_and_names_its_phase(self):
        out = plugin_api.tune({})
        self.assertTrue(out["ok"])
        self.assertIn("TUNING", out["output"])
        self.assertTrue(out["tuning"]["running"])
        self.assertIn(("start_tune",), self.rig.calls)

    def test_a_master_write_is_clamped_between_the_floor_and_the_ceiling(self):
        self.assertEqual(plugin_api.master_level({"db": 99})["db"], 12.0)
        self.assertEqual(plugin_api.master_level({"db": -999})["db"], -60.0)

    def test_a_word_instead_of_a_number_is_refused_plainly(self):
        out = plugin_api.master_level({"db": "loud"})
        self.assertFalse(out["ok"])
        self.assertIn("NO LEVEL", out["output"])


class KnobTest(ApiTest):
    def test_a_knob_lands_on_the_param_name_the_engine_has(self):
        out = plugin_api.knob({"param": 0, "value": 7.0})
        self.assertTrue(out["ok"])
        self.assertIn(("param", "bass", 7.0), self.rig.calls)

    def test_a_fourth_knob_is_refused_because_there_are_three(self):
        out = plugin_api.knob({"param": 3, "value": 1.0})
        self.assertFalse(out["ok"])
        self.assertIn("NO PARAM", out["output"])

    def test_norm_mode_maps_the_midpoint_to_flat(self):
        plugin_api.knob({"param": 2, "value": 0.5, "mode": "norm"})
        self.assertIn(("param", "treble", 0.0), self.rig.calls)

    def test_a_negative_norm_lands_as_a_cut_not_a_boost(self):
        # 0.25 is a quarter turn left of flat: -6 dB, not +2.5 dB. The old route multiplied
        # by ten and clamped at zero, so every bass cut arrived as a small bass boost.
        plugin_api.knob({"param": 0, "value": 0.25, "mode": "norm"})
        self.assertIn(("param", "bass", -6.0), self.rig.calls)
        plugin_api.knob({"param": 0, "value": 1.0, "mode": "norm"})
        self.assertIn(("param", "bass", 12.0), self.rig.calls)


class PickTest(ApiTest):
    def test_a_capture_is_loaded_by_path(self):
        out = plugin_api.choose_capture({"path": "C:/nam/other.nam"})
        self.assertTrue(out["ok"])
        self.assertIn(("capture", "C:/nam/other.nam"), self.rig.calls)

    def test_a_missing_path_is_a_sentence_not_a_traceback(self):
        self.assertFalse(plugin_api.choose_capture({})["ok"])
        self.assertFalse(plugin_api.choose_cab({})["ok"])


class InputTest(ApiTest):
    def test_the_switch_counts_sockets_from_one_and_names_them(self):
        out = plugin_api.input_switch({"channel": 2})
        self.assertTrue(out["ok"])
        self.assertEqual(out["value"], 2)
        self.assertEqual(out["name"], "IN 2")
        self.assertIn(("input", 2), self.rig.calls)


if __name__ == '__main__':
    unittest.main()


class RigTest(ApiTest):
    """Saved sounds through the pane: the same file the tool writes, read back as buttons."""

    def setUp(self):
        super().setUp()
        import os, tempfile
        from pathlib import Path as _Path

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self._env = os.environ.get("AMPHEAD_SETTINGS")
        os.environ["AMPHEAD_SETTINGS"] = str(_Path(self.tmp.name) / "settings.json")
        self.addCleanup(self._restore)

    def _restore(self):
        import os

        if self._env is None:
            os.environ.pop("AMPHEAD_SETTINGS", None)
        else:
            os.environ["AMPHEAD_SETTINGS"] = self._env

    def test_saving_the_sound_that_is_loaded_makes_a_button(self):
        out = plugin_api.rig_action({"action": "save", "name": "lead"})
        self.assertTrue(out["ok"], out)
        self.assertTrue(out["output"].startswith("SAVED"), out["output"])
        listed = plugin_api.rigs_list()
        self.assertEqual([row["name"] for row in listed["rigs"]], ["lead"])

    def test_a_saved_rig_can_be_loaded_by_name(self):
        plugin_api.rig_action({"action": "save", "name": "lead"})
        out = plugin_api.rig_action({"action": "load", "name": "lead"})
        self.assertTrue(out["ok"], out)
        self.assertTrue(out["output"].startswith("LOADED"), out["output"])
        self.assertIn("rig", [call[0] for call in self.rig.calls])

    def test_a_pasted_line_loads_and_names_what_it_could_not_find(self):
        line = "amphead:friend | capture=Not Mine;cab=AURORA 212 GOV;bass=4;gate=-60"
        out = plugin_api.rig_action({"action": "load", "text": line})
        self.assertTrue(out["ok"], out)
        self.assertIn("kept the old capture", out["output"])

    def test_forgetting_one_that_is_not_there_says_so(self):
        out = plugin_api.rig_action({"action": "forget", "name": "ghost"})
        self.assertFalse(out["ok"])
        self.assertTrue(out["output"].startswith("NOT FOUND"), out["output"])

    def test_pasting_something_that_is_not_a_rig_is_refused(self):
        out = plugin_api.rig_action({"action": "load", "text": "hello"})
        self.assertFalse(out["ok"])
        self.assertIn("amphead:", out["output"])
