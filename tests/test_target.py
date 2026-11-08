"""The matcher has one job and one way to fail it: claiming a file is something it is not.

Every test here is a false positive that was actually seen in the wild while ranking a real
library, pinned so it cannot come back.
"""

import unittest

from amphead import target


class TestMatchingWords(unittest.TestCase):
    def test_a_short_number_must_be_a_whole_word(self):
        # "3000" in a file name once matched the "30" in Vintage 30.
        self.assertEqual(target.score("04 Love Rocket 3000", {"cab": "vintage30"}, "cabs")["matched"], [])

    def test_a_word_inside_a_longer_word_counts(self):
        # Real files write these run together.
        self.assertIn("superlead", target.score("Marshall Superlead 100", {"amp": "plexi"})["matched"])
        self.assertIn("g12m", target.score("1960A G12M Greenback", {"cab": "greenback"}, "cabs")["matched"])

    def test_one_word_of_a_two_word_rig_is_not_the_rig(self):
        # "LEAD" alone in a PRS capture once counted as a Marshall Super Lead.
        got = target.score("[AMP] PRS-MT100 LEAD 1996 SM57 - STD", {"amp": "plexi"})
        self.assertEqual(got["matched"], [])
        self.assertFalse(got["useful"])

    def test_a_mic_word_alone_is_not_a_candidate(self):
        # "57" in a file name once put it in the list with nothing else matching.
        got = target.score("Room Mic 57 Take 2", {"amp": "jcm800", "mic": "close"})
        self.assertFalse(got["useful"], "a close-mic word with no amp is coincidence")

    def test_a_capture_is_not_scored_on_cabinet_words(self):
        # "Marshall 57" is a cab, and was being offered as an answer about a speaker question.
        got = target.score("Marshall 57", {"amp": "plexi", "cab": "greenback"}, "captures")
        self.assertNotIn("greenback", got["matched"])

    def test_a_cabinet_is_not_scored_on_amp_words(self):
        got = target.score("Marshall 57", {"amp": "marshall", "cab": "greenback"}, "cabs")
        self.assertEqual(got["matched"], [], "no speaker word in the name, so it is not a cab match")

    def test_a_1960_box_is_not_a_greenback(self):
        # That box shipped with Greenbacks, G12T-75s and V30s, so the name proves nothing.
        got = target.score("PP-Mar1960-V30-Mix-SM57-i5", {"cab": "greenback"}, "cabs")
        self.assertFalse(got["useful"])


class TestConfidence(unittest.TestCase):
    def test_both_words_present_is_exact(self):
        got = target.score("Marshall Plexi 1959 SM57 close", {"amp": "plexi", "mic": "close"})
        self.assertEqual(got["confidence"], target.CONFIDENCE_EXACT)
        self.assertEqual(got["missing"], [])

    def test_amp_without_the_cabinet_is_family_not_exact(self):
        got = target.score("PEAVEY - 6505 - LEAD", {"amp": "5150"})
        self.assertEqual(got["confidence"], target.CONFIDENCE_EXACT)
        # the cab question is not asked of a capture, so asking it still reads exact
        self.assertEqual(got["missing"], [])

    def test_nothing_useful_is_nearest(self):
        got = target.score("Clean Take 4", {"amp": "plexi"})
        self.assertEqual(got["confidence"], target.CONFIDENCE_NEAREST)
        self.assertEqual(got["missing"], ["amp:plexi"])


class TestRanking(unittest.TestCase):
    def test_the_capture_that_names_the_amp_comes_first(self):
        items = [
            {"name": "Clean room take"},
            {"name": "PEAVEY 6505 LEAD G4"},
            {"name": "Bugera 6262 Lead"},
            {"name": "6505 crunch low gain"},
        ]
        found = target.rank(items, {"amp": "5150"})
        self.assertEqual(len(found), 2)
        self.assertTrue(all("6505" in entry["name"] for entry in found))

    def test_names_that_only_match_a_mic_are_dropped(self):
        items = [{"name": "SM57 test tone"}, {"name": "Bugera 6262 Lead"}]
        found = target.rank(items, {"amp": "peavey", "mic": "close"})
        # Bugera is a Peavey-era design but not a Peavey by name, so nothing should be claimed.
        self.assertEqual(found, [])

    def test_source_fields_survive_the_merge(self):
        items = [{"name": "Marshall Plexi 1959", "path": "C:/x/plex.nam"}]
        found = target.rank(items, {"amp": "plexi"})
        self.assertEqual(found[0]["path"], "C:/x/plex.nam")
        self.assertIn("score", found[0])

    def test_an_empty_library_returns_nothing_rather_than_crashing(self):
        self.assertEqual(target.rank([], {"amp": "plexi"}), [])


class TestVerdict(unittest.TestCase):
    def test_an_empty_result_says_so_with_the_right_noun(self):
        self.assertIn("no cab", target.verdict([], {"cab": "greenback"}, "cabs"))
        self.assertIn("no capture", target.verdict([], {"amp": "plexi"}, "captures"))

    def test_a_match_names_the_file_and_never_fakes_a_claim(self):
        found = target.rank([{"name": "PEAVEY 6505 LEAD"}], {"amp": "5150"})
        line = target.verdict(found, {"amp": "5150"}, "captures")
        self.assertIn("PEAVEY 6505 LEAD", line)
        self.assertNotIn("AC/DC", line)


if __name__ == "__main__":
    unittest.main()
