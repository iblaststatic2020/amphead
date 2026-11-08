"""Asking the agent for a sound has to end in a file name or an honest no.

The failure this file guards against is the flattering one: picking the nearest capture that is not
the rig and letting the face imply it is.
"""

import unittest
from unittest import mock

from amphead import target, tools

CAPTURES = [
    {"name": "Marshall Plexi 1959 SM57 close", "path": "C:/nam/plexi.nam"},
    {"name": "PEAVEY 6505 LEAD G4", "path": "C:/nam/6505.nam"},
    {"name": "Clean room take", "path": "C:/nam/clean.nam"},
]
CABS = [
    {"name": "1960A G12M Greenback SM57", "path": "C:/irs/greenback.wav"},
    {"name": "1960A V30 SM57", "path": "C:/irs/v30.wav"},
]


class FakeRig:
    def __init__(self):
        self.running = False
        self.capture_path = "C:/nam/default.nam"
        self.cab_path = "C:/irs/default.wav"
        self.loaded = []
        self.gains = []
        self.params = []

    def state(self):
        return {"capture_path": self.capture_path, "cab_path": self.cab_path,
                "capture": "Plexi 1959", "cab": "Greenback"}

    def open_capture(self, path):
        self.loaded.append(("capture", path))
        return {"capture": "Plexi 1959"}

    def open_cab(self, path):
        self.loaded.append(("cab", path))
        return {"cab": "Greenback"}

    def set_input_gain(self, db):
        self.gains.append(("in", db))

    def set_output_gain(self, db):
        self.gains.append(("out", db))

    def set_param(self, track=1, fx=1, param=None, value=0.0, mode="auto"):
        self.params.append((param, value))


def listing(capture="", cab=""):
    return {"captures": [dict(row) for row in CAPTURES], "cabs": [dict(row) for row in CABS]}


class AimCase(unittest.TestCase):
    def setUp(self):
        self.rig = FakeRig()
        self.saved = {}
        self._rig = tools.RIG
        tools.RIG = self.rig
        patches = [
            mock.patch("amphead.library.listing", listing),
            mock.patch("amphead.lastchain.save", lambda **kw: self.saved.update(kw)),
        ]
        for active in patches:
            active.start()
            self.addCleanup(active.stop)
        self.addCleanup(lambda: setattr(tools, "RIG", self._rig))

    def answer(self, **args):
        return tools.handle_aim(args)


class TestFinding(AimCase):
    def test_no_words_is_refused_rather_than_guessed(self):
        self.assertIn("NEED WORDS", self.answer(artist="AC/DC"))

    def test_a_rig_the_library_holds_reads_exact(self):
        got = self.answer(artist="AC/DC", amp="plexi", cab="greenback", mic="close")
        self.assertIn("AIMED AT AC/DC", got)
        self.assertIn("confidence=exact", got)
        self.assertIn("would_load_capture=Marshall Plexi 1959 SM57 close", got)
        self.assertIn("would_load_cab=1960A G12M Greenback SM57", got)

    def test_the_v30_box_is_never_offered_as_the_greenback(self):
        got = self.answer(cab="greenback")
        self.assertIn("1960A G12M Greenback SM57", got)
        self.assertNotIn("would_load_cab=1960A V30 SM57", got)

    def test_half_a_rig_reads_family_not_exact(self):
        # A V30 cabinet genuinely answers "vintage30"; a Greenback does not answer "creamback".
        got = self.answer(amp="plexi", cab="creamback")
        self.assertIn("confidence=family", got)
        self.assertIn("missing=cab:creamback", got)

    def test_an_empty_answer_says_no_match_and_names_what_is_missing(self):
        got = self.answer(artist="Metallica", amp="rectifier", cab="creamback")
        self.assertIn("NO MATCH for Metallica", got)
        self.assertIn("rectifier", got)

    def test_the_aim_is_remembered_for_the_face(self):
        self.answer(artist="AC/DC", amp="plexi", cab="greenback")
        self.assertIn("aimed at AC/DC", self.saved["aim"])
        self.assertEqual(self.saved["aim_confidence"], "exact")


class TestApplying(AimCase):
    def test_apply_loads_the_pair_and_the_trims(self):
        self.rig.running = True
        got = self.answer(action="apply", artist="AC/DC", amp="plexi", cab="greenback",
                          input_gain_db=4, output_gain_db=-3, bass=6, treble=7)
        self.assertIn("loaded_capture=Plexi 1959", got)
        self.assertEqual(self.rig.loaded, [("capture", "C:/nam/plexi.nam"),
                                          ("cab", "C:/irs/greenback.wav")])
        self.assertEqual(self.rig.gains, [("in", 4.0), ("out", -3.0)])
        self.assertEqual(self.rig.params, [("bass", 6.0), ("treble", 7.0)])

    def test_apply_with_the_engine_down_says_so_and_loads_nothing(self):
        got = self.answer(action="apply", amp="plexi", cab="greenback")
        self.assertIn("the engine is not running", got)
        self.assertEqual(self.rig.loaded, [])


class TestMeasuring(AimCase):
    def test_measure_needs_both_files(self):
        self.assertIn("NEED TWO FILES", self.answer(action="measure", reference="a.wav"))
        self.assertIn("MISSING FILE",
                      self.answer(action="measure", reference="a.wav", take="b.wav"))

    def test_measure_reports_the_analysers_words(self):
        with mock.patch("amphead.tools._measure", lambda a, b: "distance 3.41 dB RMS"):
            got = self.answer(action="measure", reference=__file__, take=__file__)
        self.assertIn("distance 3.41 dB RMS", got)

    def test_measure_without_an_interpreter_is_a_sentence_not_a_traceback(self):
        def boom(a, b):
            raise tools._NoAnalyser("no engine interpreter")
        with mock.patch("amphead.tools._measure", boom):
            got = self.answer(action="measure", reference=__file__, take=__file__)
        self.assertIn("CANNOT MEASURE: no engine interpreter", got)


class TestConfidenceRule(unittest.TestCase):
    def test_the_weaker_half_decides(self):
        exact = [{"confidence": target.CONFIDENCE_EXACT}]
        family = [{"confidence": target.CONFIDENCE_FAMILY}]
        self.assertEqual(tools._confidence(exact, family, {"amp": "plexi", "cab": "greenback"}),
                         target.CONFIDENCE_FAMILY)
        self.assertEqual(tools._confidence(exact, exact, {"amp": "plexi", "cab": "greenback"}),
                         target.CONFIDENCE_EXACT)
        self.assertEqual(tools._confidence([], [], {"amp": "plexi"}),
                         target.CONFIDENCE_NEAREST)


if __name__ == "__main__":
    unittest.main()
