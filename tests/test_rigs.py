"""Saved sounds: a rig is eight fields, a name, and one line you can paste.

Everything here runs without a device and without the engine, because the honest thing about a
rig is that it is a small dict in a JSON file. The one test that touches the rig uses a stub,
because what is being checked is that the sentence says what landed rather than what was asked.
"""

import os
import tempfile
import unittest
from pathlib import Path

try:
    from amphead import rigs, tools
except ImportError:  # pragma: no cover
    raise unittest.SkipTest("amphead not importable")

RIG = {
    "capture": "Bugera 6262 lead",
    "cab": "Vintage 30 4x12 close",
    "bass": 3.0,
    "mid": -1.5,
    "treble": 2.0,
    "input_gain_db": -6.0,
    "output_gain_db": -3.0,
    "gate_db": -58.0,
}


class TemporaryRigs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.where = Path(self.tmp.name) / "rigs.json"
        self.cabinet = rigs.Rigs(self.where)

    def test_a_rig_survives_a_round_trip_and_keeps_its_numbers(self):
        self.cabinet.save("lead", RIG)
        got = self.cabinet.get("lead")
        self.assertEqual(got["capture"], RIG["capture"])
        self.assertEqual(got["bass"], 3.0)
        self.assertEqual(got["gate_db"], -58.0)

    def test_names_are_matched_without_shouting(self):
        self.cabinet.save("Lead", RIG)
        self.assertIsNotNone(self.cabinet.get("lead"))
        again = self.cabinet.save("lead", RIG)
        self.assertTrue(again["replaced"], "a second save under the same name replaced nothing")
        self.assertEqual(len(self.cabinet.names()), 1)

    def test_forget_removes_one_and_says_when_it_did_not_exist(self):
        self.cabinet.save("lead", RIG)
        self.assertTrue(self.cabinet.forget("LEAD"))
        self.assertFalse(self.cabinet.forget("lead"))

    def test_an_empty_rig_is_refused(self):
        with self.assertRaises(ValueError):
            self.cabinet.save("nothing", {})
        with self.assertRaises(ValueError):
            self.cabinet.save("", RIG)

    def test_a_broken_file_is_not_a_crash(self):
        self.where.parent.mkdir(parents=True, exist_ok=True)
        self.where.write_text("{ not json", encoding="utf-8")
        self.assertEqual(self.cabinet.names(), [])
        self.assertIsNone(self.cabinet.get("lead"))

    def test_the_paths_a_rig_was_saved_with_ride_along(self):
        here = {"capture": "PR_6505", "capture_path": "C:/imports/PR_6505.nam",
                "cab": "V30", "cab_path": "C:/imports/V30.wav"}
        self.cabinet.save("lead", here)
        got = self.cabinet.get("lead")
        self.assertEqual(got["capture_path"], "C:/imports/PR_6505.nam")
        self.assertEqual(got["cab_path"], "C:/imports/V30.wav")

    def test_from_status_keeps_the_paths_beside_the_names(self):
        got = rigs.from_status({"capture": "PR_6505", "capture_path": "C:/imports/PR_6505.nam",
                                "cab": "V30", "cab_path": "C:/imports/V30.wav", "tone": {}})
        self.assertEqual(got["capture"], "PR_6505")
        self.assertEqual(got["capture_path"], "C:/imports/PR_6505.nam")
        self.assertEqual(got["cab_path"], "C:/imports/V30.wav")


class TheLineYouCanPaste(unittest.TestCase):
    def test_a_rig_encodes_readably_and_decodes_back_to_itself(self):
        line = rigs.encode(RIG)
        self.assertTrue(line.startswith("amphead:"))
        self.assertIn("capture=Bugera 6262 lead", line)
        self.assertNotIn("\n", line)
        back = rigs.decode(line)
        for key in ("bass", "mid", "treble", "input_gain_db", "output_gain_db", "gate_db"):
            self.assertEqual(float(back[key]), float(RIG[key]), key)
        self.assertEqual(back["capture"], RIG["capture"])

    def test_a_named_rig_carries_its_name(self):
        line = rigs.encode({"name": "lead", **RIG})
        self.assertTrue(line.startswith("amphead:lead |"))
        self.assertEqual(rigs.decode(line)["name"], "lead")

    def test_a_pasted_line_does_not_carry_paths_from_this_machine(self):
        line = rigs.encode({**RIG, "capture_path": "C:/imports/PR_6505.nam",
                            "cab_path": "C:/imports/V30.wav"})
        self.assertNotIn("capture_path", line)
        self.assertNotIn("C:/imports", line)
        self.assertNotIn("cab_path", rigs.decode(line))

    def test_short_keys_and_aliases_are_understood(self):
        got = rigs.decode("amphead: crunch | capture=Thing;cab=Cab;bass=2;in=-4;out=-2;gate=-60")
        self.assertEqual(got["bass"], "2")
        self.assertEqual(got["input_gain_db"], "-4")
        self.assertEqual(got["output_gain_db"], "-2")

    def test_anything_else_is_refused_in_words(self):
        for junk in ("", "lead", "preset: lead", "amphead:", "amphead:lead |"):
            with self.assertRaises(ValueError, msg=junk):
                rigs.decode(junk)

    def test_numbers_out_of_range_are_pulled_in_rather_than_stored(self):
        got = rigs.tidy({"capture": "x", "input_gain_db": 90, "gate_db": 0, "bass": -99})
        self.assertEqual(got["input_gain_db"], 24.0)
        self.assertEqual(got["gate_db"], -20.0)
        self.assertEqual(got["bass"], -12.0)

    def test_unknown_keys_never_reach_the_stored_rig(self):
        self.assertNotIn("master_db", rigs.tidy({"capture": "x", "master_db": 3}))


class ReadingARigOutLoud(unittest.TestCase):
    def test_a_full_rig_reads_as_one_sentence(self):
        said = rigs.summarise(RIG)
        self.assertIn("Bugera 6262 lead", said)
        self.assertIn("into Vintage 30 4x12 close", said)
        self.assertIn("bass +3", said)
        self.assertIn("gate -58", said)

    def test_a_gate_at_the_floor_says_off(self):
        self.assertIn("gate off", rigs.summarise({"capture": "x", "gate_db": -80.0}))

    def test_nothing_loaded_still_says_something(self):
        self.assertEqual(rigs.summarise({}), "nothing loaded")


class FindingANameOnThisMachine(unittest.TestCase):
    rows = [{"name": "Bugera", "path": "C:/packs/amp/Bugera.nam"},
            {"name": "Thing", "path": "C:/packs/cab/Thing.wav"}]

    def test_a_name_resolves_to_a_path(self):
        self.assertEqual(rigs.resolve("capture", "bugera", self.rows),
                         "C:/packs/amp/Bugera.nam")

    def test_a_missing_name_is_none_rather_than_an_error(self):
        self.assertIsNone(rigs.resolve("capture", "not here", self.rows))
        self.assertIsNone(rigs.resolve("capture", "", self.rows))


class TheToolAnswersLikeAPlayerReads(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.cabinet = rigs.Rigs(self.home / "rigs.json")
        self.old_env = os.environ.get("AMPHEAD_SETTINGS")
        os.environ["AMPHEAD_SETTINGS"] = str(self.home / "settings.json")
        self.addCleanup(self._restore_env)
        # The tool builds its own cabinet from the settings path, so the test points that path
        # at a temporary folder rather than the player's real rigs file.
        self.cabinet.save("lead", RIG)

    def _restore_env(self):
        if self.old_env is None:
            os.environ.pop("AMPHEAD_SETTINGS", None)
        else:
            os.environ["AMPHEAD_SETTINGS"] = self.old_env

    def test_list_names_what_is_saved(self):
        said = tools.handle_rig({"action": "list"})
        self.assertIn("saved=1", said)
        self.assertIn("lead", said)

    def test_saving_needs_a_sound_and_says_which(self):
        class Idle:
            label = "stub"

            def state(self):
                return {"capture": "", "cab": "", "tone": {}}

        real = tools.RIG
        tools.RIG = Idle()
        try:
            said = tools.handle_rig({"action": "save", "name": "quiet"})
        finally:
            tools.RIG = real
        self.assertTrue(said.startswith("NOT SAVED"), said)

    def test_loading_a_name_that_is_not_there_is_a_refusal_not_a_traceback(self):
        said = tools.handle_rig({"action": "load", "name": "nope"})
        self.assertTrue(said.startswith("NOT FOUND"), said)

    def test_delete_is_the_same_verb_as_forget(self):
        said = tools.handle_rig({"action": "delete", "name": "lead"})
        self.assertIn("deleted=lead", said)
        self.assertEqual([], self.cabinet.names())

    def test_pasting_junk_is_refused_in_words(self):
        said = tools.handle_rig({"action": "paste", "text": "lead please"})
        self.assertTrue(said.startswith("NOT LOADED"), said)
        self.assertIn("amphead:", said)


if __name__ == "__main__":
    unittest.main()
