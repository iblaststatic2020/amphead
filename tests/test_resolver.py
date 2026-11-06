"""Naming a plugin or a knob must never be a coin flip.

The ladder is pinned here: exact, exact with the format prefix stripped, every word present, then
the query as a subsequence. A tie inside the best tier is refused and the candidates are named,
because the refusal sentence is the only thing standing between the caller and the wrong control.
"""

import os
import tempfile
import unittest

from amphead import bridge, resolver
from amphead.fake_reaper import FakeReaper

SHOELESS = "VST3: Shoeless Drive Pedal (WaveMind)"
RIGSCULPT = "VST3: RigSculpt (WaveMind)"

CHAIN = [{"i": 0, "name": SHOELESS, "enabled": True},
         {"i": 1, "name": RIGSCULPT, "enabled": True}]

PARAMS = [
    {"i": 0, "name": "Gain", "fmt": "5.0"},
    {"i": 1, "name": "Drive", "fmt": "2.5"},
    {"i": 2, "name": "Drive Boost", "fmt": "1.0"},
    {"i": 3, "name": "Tone", "fmt": "5.0"},
    {"i": 4, "name": "Output Gain", "fmt": "0.0 dB"},
]


class TestResolveFx(unittest.TestCase):
    def test_plain_word_finds_the_plugin(self):
        out = resolver.resolve_fx("shoeless", chain=CHAIN)
        self.assertIsInstance(out, resolver.Resolved)
        self.assertEqual(out.index, 0)
        self.assertEqual(out.name, SHOELESS)

    def test_full_name_is_exact(self):
        out = resolver.resolve_fx(SHOELESS, chain=CHAIN)
        self.assertEqual((out.index, out.tier), (0, resolver.EXACT))

    def test_name_without_the_format_prefix_is_exact(self):
        out = resolver.resolve_fx("Shoeless Drive Pedal (WaveMind)", chain=CHAIN)
        self.assertEqual((out.index, out.tier), (0, resolver.EXACT))

    def test_pro_q_finds_pro_q_4(self):
        chain = [{"i": 0, "name": "VST3: Pro-Q 4 (FabFilter)"},
                 {"i": 1, "name": "VST3: Pro-C 2 (FabFilter)"}]
        out = resolver.resolve_fx("pro-q", chain=chain)
        self.assertIsInstance(out, resolver.Resolved)
        self.assertEqual(out.name, "VST3: Pro-Q 4 (FabFilter)")

    def test_squashed_letters_still_land(self):
        out = resolver.resolve_fx("rigsclpt", chain=CHAIN)
        self.assertEqual(out.index, 1)

    def test_two_plugins_with_the_same_word_are_refused(self):
        chain = [{"i": 0, "name": "VST3: NAM Universal (WaveMind)"},
                 {"i": 1, "name": "VST3: NAM Modern (WaveMind)"}]
        out = resolver.resolve_fx("univers", chain=chain)
        self.assertEqual(out.index, 0)  # unique word in one name
        out = resolver.resolve_fx("nam", chain=chain)
        self.assertIsInstance(out, resolver.Ambiguous)
        self.assertEqual(len(out.candidates), 2)
        self.assertIn("NAM Universal", out.sentence())
        self.assertIn("NAM Modern", out.sentence())

    def test_transposed_letters_still_find_the_one_plugin(self):
        # "sheoless" is one transposition off and there is exactly one candidate: resolve it.
        out = resolver.resolve_fx("sheoless", chain=CHAIN)
        self.assertIsInstance(out, resolver.Resolved)
        self.assertEqual((out.index, out.tier), (0, resolver.FUZZY))

    def test_missing_plugin_with_nothing_close_does_not_invent_advice(self):
        out = resolver.resolve_fx("plexi", chain=CHAIN)
        self.assertEqual(out.suggestions, ())

    def test_one_shared_word_is_not_advice(self):
        chain = [{"i": 0, "name": "VST3: NAM Universal (WaveMind)"}]
        out = resolver.resolve_fx("nam modern", chain=chain)
        self.assertEqual(out.suggestions, ())

    def test_empty_chain_is_not_an_exception(self):
        self.assertIsInstance(resolver.resolve_fx("shoeless", chain=[]), resolver.NotFound)

    def test_duplicate_names_in_a_chain_are_refused(self):
        chain = [{"i": 0, "name": SHOELESS}, {"i": 1, "name": SHOELESS}]
        out = resolver.resolve_fx(SHOELESS, chain=chain)
        self.assertIsInstance(out, resolver.Ambiguous)
        self.assertEqual(len(out.candidates), 2)


class TestResolveParam(unittest.TestCase):
    def test_exact_name_wins_over_a_longer_one(self):
        # "Output Gain" also contains the word gain, but the player typed a control they can see.
        out = resolver.resolve_param("gain", params=PARAMS)
        self.assertEqual((out.index, out.name), (0, "Gain"))

    def test_qualified_name_picks_the_other_control(self):
        out = resolver.resolve_param("output gain", params=PARAMS)
        self.assertEqual((out.index, out.name), (4, "Output Gain"))

    def test_partial_word_lists_every_candidate(self):
        out = resolver.resolve_param("driv", params=PARAMS)
        self.assertIsInstance(out, resolver.Ambiguous)
        self.assertEqual([i for i, _n in out.candidates], [1, 2])
        self.assertIn("matches 2 parameters", out.sentence())

    def test_vendor_misspelling_still_resolves(self):
        # The real NAM Universal ships "Pedal Ouput Gain"; the player types it correctly.
        params = [{"i": 3, "name": "Output Gain"}, {"i": 7, "name": "Pedal Ouput Gain"},
                  {"i": 6, "name": "Pedal Input Gain"}]
        out = resolver.resolve_param("pedal output gain", params=params)
        self.assertIsInstance(out, resolver.Resolved)
        self.assertEqual((out.index, out.tier), (7, resolver.FUZZY))

    def test_misspelling_does_not_swallow_a_different_control(self):
        params = [{"i": 3, "name": "Output Gain"}, {"i": 7, "name": "Pedal Ouput Gain"},
                  {"i": 6, "name": "Pedal Input Gain"}]
        self.assertEqual(resolver.resolve_param("pedal input gain", params=params).index, 6)

    def test_two_gains_are_ambiguous(self):
        params = [{"i": 0, "name": "Gain"}, {"i": 9, "name": "Gain"}]
        out = resolver.resolve_param("gain", params=params)
        self.assertIsInstance(out, resolver.Ambiguous)
        self.assertEqual(len(out.candidates), 2)
        self.assertIn("#9 Gain", out.sentence())

    def test_case_and_padding_do_not_matter(self):
        out = resolver.resolve_param("  TONE ", params=PARAMS)
        self.assertEqual(out.index, 3)

    def test_missing_param_offers_a_typo_fix(self):
        out = resolver.resolve_param("trve", params=PARAMS)
        self.assertIsInstance(out, resolver.NotFound)
        self.assertIn("Drive", out.suggestions[0])
        self.assertIn("Closest names", out.sentence())

    def test_no_close_name_suggests_nothing(self):
        # Offering "Wet, Oomph, Delta" as closest names is noise, not help.
        out = resolver.resolve_param("presence", params=PARAMS)
        self.assertEqual(out.suggestions, ())
        self.assertNotIn("Closest", out.sentence())


class TestRecheck(unittest.TestCase):
    def test_shifted_index_is_re_resolved_by_name(self):
        found = resolver.resolve_fx("shoeless", chain=CHAIN)
        shifted = [{"i": 0, "name": RIGSCULPT}, {"i": 1, "name": SHOELESS}]
        again = resolver.recheck(shifted, found)
        self.assertEqual((again.index, again.name), (1, SHOELESS))

    def test_unchanged_chain_returns_the_same_answer(self):
        found = resolver.resolve_fx("shoeless", chain=CHAIN)
        self.assertEqual(resolver.recheck(CHAIN, found), found)

    def test_removed_plugin_is_not_found_not_guessed(self):
        found = resolver.resolve_fx("shoeless", chain=CHAIN)
        gone = [{"i": 0, "name": RIGSCULPT}]
        self.assertIsInstance(resolver.recheck(gone, found), resolver.NotFound)

    def test_index_still_holding_the_name_is_trusted_even_with_a_second_copy(self):
        # A duplicate further down the chain does not make index 0 wrong; it is still Shoeless.
        found = resolver.resolve_fx("shoeless", chain=CHAIN)
        twice = [{"i": 0, "name": SHOELESS}, {"i": 1, "name": SHOELESS}]
        self.assertEqual(resolver.recheck(twice, found).index, 0)

    def test_a_shift_that_lands_on_two_copies_stops_the_write(self):
        # The old index now holds something else and two copies carry the name: no safe answer.
        found = resolver.resolve_fx("shoeless", chain=CHAIN)
        moved = [{"i": 0, "name": RIGSCULPT},
                 {"i": 1, "name": SHOELESS},
                 {"i": 2, "name": SHOELESS}]
        self.assertIsInstance(resolver.recheck(moved, found), resolver.Ambiguous)


class TestOverTheWire(unittest.TestCase):
    """The same ladder, but read from the bridge instead of a hand-written chain."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.fake = FakeReaper(self.root).start()
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(self.fake.stop)
        self.addCleanup(os.environ.pop, "HERMES_REAPER_BRIDGE", None)
        os.environ["HERMES_REAPER_BRIDGE"] = str(self.root)

    def test_resolve_fx_reads_the_chain(self):
        out = resolver.resolve_fx("shoeless")
        self.assertEqual((out.index, out.name), (0, SHOELESS))
        self.assertEqual(resolver.resolve_fx("rigsculpt").index, 1)

    def test_resolve_param_reads_the_parameter_list(self):
        self.assertEqual(resolver.resolve_param("drive", track=1, fx=0).index, 1)
        self.assertEqual(resolver.resolve_param("output gain", track=1, fx=0).index, 4)

    def test_recheck_against_a_fresh_read(self):
        found = resolver.resolve_fx("shoeless")
        again = resolver.recheck(bridge.fx_list(track=1), found)
        self.assertIsInstance(again, resolver.Resolved)
        self.assertEqual(again.index, 0)


if __name__ == "__main__":
    unittest.main()
