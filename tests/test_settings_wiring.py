"""Settings actually reach the library, the engine and the agent's tool."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from amphead import engine2, engine_main, library, settings, tools


class Rig(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.file = self.root / "settings.json"
        env = mock.patch.dict(os.environ, {settings.SETTINGS_ENV: str(self.file)})
        env.start()
        self.addCleanup(env.stop)
        for key in ("AMPHEAD_CAPTURE_DIR", "AMPHEAD_CAB_DIR", "AMPHEAD_ENGINE_PYTHON"):
            os.environ.pop(key, None)

    def touch(self, *parts):
        path = self.root.joinpath(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
        return path


class TestTheLibraryFollowsTheSettings(Rig):
    def test_a_capture_folder_a_player_chose_is_scanned(self):
        self.touch("MyTones", "Combo", "jcm800.nam")
        settings.save({"capture_dirs": [str(self.root / "MyTones")]})
        listing = library.listing()
        self.assertIn("jcm800", [c["name"] for c in listing["captures"]])
        self.assertEqual(listing["capture_dirs"][0], str(self.root / "MyTones"))

    def test_a_cab_folder_a_player_chose_is_scanned(self):
        self.touch("Cabs", "v30.wav")
        settings.save({"cab_dirs": [str(self.root / "Cabs")]})
        self.assertIn("v30", [c["name"] for c in library.listing()["cabs"]])

    def test_the_usual_folders_are_still_there_when_nothing_is_set(self):
        listing = library.listing()
        self.assertTrue(listing["capture_dirs"], "a fresh install must still offer a library")


class TestFindingWhatIsAlreadyOnTheDisk(Rig):
    def test_it_finds_captures_and_cabinets_in_the_synced_folders(self):
        self.touch("Documents", "Guitar", "NAM", "amp.nam")
        self.touch("Documents", "Guitar", "IRs", "4x12.wav")
        with mock.patch.object(Path, "home", return_value=self.root):
            found = library.find()
        paths = {Path(r["path"]).name: r for r in found["candidates"]}
        self.assertEqual(paths["NAM"]["kind"], "captures")
        self.assertEqual(paths["IRs"]["kind"], "cabs")
        self.assertEqual(paths["NAM"]["captures"], 1)

    def test_it_does_not_wander_off_the_end_of_the_world(self):
        deep = ["Documents"] + ["deep"] * 12 + ["late.nam"]
        self.touch(*deep)
        with mock.patch.object(Path, "home", return_value=self.root):
            found = library.find()
        self.assertEqual(found["candidates"], [], "past the depth limit is somebody else's tree")

    def test_a_render_folder_does_not_outrank_an_ir_library_by_count_alone(self):
        self.touch(*["Documents", "REAPER Media", "Media"] + [f"take{i}.wav" for i in range(20)])
        self.touch("Documents", "Guitar", "IRs", "v30.wav")
        with mock.patch.object(Path, "home", return_value=self.root):
            names = [Path(r["path"]).name for r in library.find()["candidates"]]
        self.assertEqual(names[0], "IRs", "the name says library; the render folder just has files")

    def test_hidden_folders_are_left_alone(self):
        self.touch("Documents", ".cache", "junk.nam")
        with mock.patch.object(Path, "home", return_value=self.root):
            self.assertEqual(library.find()["candidates"], [])


class TestTheEngineOpensWhatTheSettingsSay(Rig):
    def test_the_device_pair_and_the_guitar_channel_come_from_the_file(self):
        settings.save({"audio": {"input_device": 37, "output_device": 38,
                                 "input_channel": 2, "samplerate": 48000,
                                 "blocksize": 256, "latency_ms": 8}})
        engine = engine_main.engine_from_settings()
        self.assertEqual((engine.input_device, engine.output_device), (37, 38))
        self.assertEqual(engine.input_channel, 1, "the file counts from 1, PortAudio from 0")
        self.assertEqual((engine.samplerate, engine.blocksize), (48000, 256))
        self.assertEqual(engine2.TARGET_LATENCY_MS, 8.0)

    def test_an_explicit_argument_still_beats_the_file(self):
        settings.save({"audio": {"samplerate": 48000, "blocksize": 256}})
        engine = engine_main.engine_from_settings(samplerate=44100, blocksize=512)
        self.assertEqual((engine.samplerate, engine.blocksize), (44100, 512))

    def test_a_fresh_install_gets_the_measured_defaults(self):
        engine = engine_main.engine_from_settings()
        self.assertEqual((engine.samplerate, engine.blocksize), (44100, 512))
        self.assertIsNone(engine.input_device)


class TestTheAgentCanSetIt(Rig):
    def test_show_reads_like_a_sentence_about_this_machine(self):
        settings.save({"capture_dirs": [str(self.root / "caps")]})
        (self.root / "caps").mkdir()
        said = tools.handle_settings({"action": "show"})
        self.assertIn("SETTINGS at", said)
        self.assertIn("exists", said)
        self.assertIn("audio=", said)

    def test_a_missing_folder_is_named_missing_rather_than_hidden(self):
        settings.save({"cab_dirs": [str(self.root / "gone")]})
        said = tools.handle_settings({"action": "show"})
        self.assertIn("MISSING", said)

    def test_set_writes_the_file_and_keeps_the_rest(self):
        settings.save({"audio": {"blocksize": 256}})
        out = tools.handle_settings({"action": "set", "capture_dirs": [str(self.root / "caps")]})
        self.assertTrue(out.startswith("SAVED"))
        on_disk = json.loads(self.file.read_text(encoding="utf-8"))
        self.assertEqual(on_disk["capture_dirs"], [str(self.root / "caps")])
        self.assertEqual(on_disk["audio"]["blocksize"], 256)

    def test_set_accepts_a_path_list_as_a_string_because_people_paste_one(self):
        joined = os.pathsep.join([str(self.root / "a"), str(self.root / "b")])
        tools.handle_settings({"action": "set", "cab_dirs": joined})
        self.assertEqual(json.loads(self.file.read_text(encoding="utf-8"))["cab_dirs"],
                         [str(self.root / "a"), str(self.root / "b")])

    def test_set_with_nothing_to_set_says_so(self):
        self.assertIn("NOTHING TO SET", tools.handle_settings({"action": "set"}))

    def test_find_offers_the_folders_it_found(self):
        self.touch("Documents", "NAM", "a.nam")
        with mock.patch.object(Path, "home", return_value=self.root):
            out = tools.handle_settings({"action": "find"})
        self.assertIn("FOUND 1 folders", out)
        self.assertIn("captures=", out)

    def test_find_is_honest_when_there_is_nothing(self):
        with mock.patch.object(Path, "home", return_value=self.root):
            out = tools.handle_settings({"action": "find"})
        self.assertIn("NO LIBRARY FOUND", out)
