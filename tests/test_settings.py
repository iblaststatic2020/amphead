"""The settings file: defaults, precedence, and never losing the file to a bad write."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from amphead import settings


class TmpSettings(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.file = Path(self.tmp.name) / "nested" / "settings.json"
        env = mock.patch.dict(os.environ, {settings.SETTINGS_ENV: str(self.file)})
        env.start()
        self.addCleanup(env.stop)
        for key in ("AMPHEAD_CAPTURE_DIR", "AMPHEAD_CAB_DIR", "AMPHEAD_ENGINE_PYTHON"):
            os.environ.pop(key, None)


class TestPath(TmpSettings):
    def test_the_environment_can_move_the_file(self):
        self.assertEqual(settings.path(), self.file)

    def test_without_an_override_each_platform_keeps_it_where_that_platform_keeps_prefs(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(settings.SETTINGS_ENV, None)
            with mock.patch.object(sys, "platform", "win32"), \
                    mock.patch.dict(os.environ, {"APPDATA": r"C:\Users\someone\AppData\Roaming"}):
                got = settings.path()
            self.assertTrue(str(got).endswith(os.path.join("amphead", "settings.json")))
            self.assertIn("AppData", str(got))
            with mock.patch.object(sys, "platform", "darwin"):
                self.assertIn("Application Support", str(settings.path()))
            with mock.patch.object(sys, "platform", "linux"), \
                    mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": "/home/x/.config"}):
                self.assertEqual(settings.path(), Path("/home/x/.config/amphead/settings.json"))


class TestLoad(TmpSettings):
    def test_a_missing_file_is_the_defaults_not_an_error(self):
        got = settings.load()
        self.assertEqual(got["capture_dirs"], [])
        self.assertEqual(got["audio"]["blocksize"], 256)
        self.assertEqual(got["audio"]["input_channel"], 1)

    def test_a_truncated_file_does_not_stop_the_rig(self):
        self.file.parent.mkdir(parents=True)
        self.file.write_text('{"audio": {"blocksize": 256', encoding="utf-8")
        self.assertEqual(settings.load()["audio"]["blocksize"], 256)

    def test_a_json_array_is_not_settings(self):
        self.file.parent.mkdir(parents=True)
        self.file.write_text("[]", encoding="utf-8")
        self.assertEqual(settings.load()["capture_dirs"], [])

    def test_a_partial_audio_block_keeps_the_other_defaults(self):
        self.file.parent.mkdir(parents=True)
        self.file.write_text(json.dumps({"audio": {"input_device": 37}}), encoding="utf-8")
        audio = settings.audio()
        self.assertEqual(audio["input_device"], 37)
        self.assertIsNone(audio["output_device"])
        self.assertEqual(audio["samplerate"], 44100)


class TestSave(TmpSettings):
    def test_save_creates_the_folders_and_merges_rather_than_replaces(self):
        settings.save({"capture_dirs": [r"C:\caps"]})
        settings.save({"audio": {"latency_ms": 8}})
        on_disk = json.loads(self.file.read_text(encoding="utf-8"))
        self.assertEqual(on_disk["capture_dirs"], [r"C:\caps"])
        self.assertEqual(on_disk["audio"]["latency_ms"], 8)
        self.assertEqual(on_disk["audio"]["blocksize"], 256, "a nested patch must not wipe siblings")

    def test_no_temp_file_is_left_beside_it(self):
        settings.save({"cab_dirs": ["/irs"]})
        leftovers = [p.name for p in self.file.parent.iterdir() if p.name != self.file.name]
        self.assertEqual(leftovers, [])

    def test_saving_something_that_is_not_an_object_is_refused(self):
        with self.assertRaises(TypeError):
            settings.save(["nope"])


class TestFolders(TmpSettings):
    @unittest.skipUnless(sys.platform == "win32", "Windows-style drive letters only exist on Windows")
    def test_the_file_is_enough(self):
        settings.save({"capture_dirs": [r"C:\a", r"D:\b"]})
        self.assertEqual([p.name for p in settings.folders("capture_dirs")], ["a", "b"])

    def test_the_environment_variable_still_wins_and_comes_first(self):
        settings.save({"capture_dirs": ["/from/file"]})
        os.environ["AMPHEAD_CAPTURE_DIR"] = os.pathsep.join(["/from/env", "/also/env"])
        self.assertEqual([p.name for p in settings.folders("capture_dirs")],
                         ["env", "env", "file"])

    def test_the_junk_an_empty_environment_variable_leaves_is_skipped(self):
        os.environ["AMPHEAD_CAB_DIR"] = ""
        self.assertEqual(settings.folders("cab_dirs"), [])


class TestAudio(TmpSettings):
    def test_strings_from_a_hand_edited_file_are_coerced(self):
        settings.save({"audio": {"input_channel": "2", "blocksize": "256",
                                 "latency_ms": "7.5", "input_device": "37"}})
        audio = settings.audio()
        self.assertEqual((audio["input_channel"], audio["blocksize"]), (2, 256))
        self.assertEqual(audio["latency_ms"], 7.5)
        self.assertEqual(audio["input_device"], 37)

    def test_nonsense_falls_back_instead_of_crashing_the_engine(self):
        settings.save({"audio": {"blocksize": "big", "input_device": "default"}})
        audio = settings.audio()
        self.assertEqual(audio["blocksize"], 256)
        self.assertIsNone(audio["input_device"], "a named device is not an index")


class TestDescribe(TmpSettings):
    def test_it_says_where_each_answer_came_from(self):
        settings.save({"capture_dirs": ["/caps"]})
        os.environ["AMPHEAD_CAB_DIR"] = "/irs"
        rows = {r["key"]: r for r in settings.describe()["settings"]}
        self.assertEqual(rows["capture_dirs"]["from"], "settings")
        self.assertEqual(rows["cab_dirs"]["from"], "environment")
        self.assertEqual(rows["audio"]["from"], "settings")
        self.assertEqual(rows["engine_python"]["from"], "built in")
