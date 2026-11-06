"""Parameter lookup is where a careless plugin silently edits the wrong control.

These tests pin the rules: exact name wins, ambiguity is refused with candidates, a unique
substring is allowed, and a payload that is not the shape we expect is an error, not an
empty list that reads as "the parameter does not exist".
"""

import json
import unittest
from unittest import mock

from amphead import bridge

PARAMS = [
    {"i": 0, "name": "Drive", "fmt": "7.0"},
    {"i": 3, "name": "Drive Tone", "fmt": "5.0"},
    {"i": 7, "name": "Level", "fmt": "-3.0"},
    {"i": 9, "name": "Drive", "fmt": "2.0"},
]


class TestParamIndex(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(bridge, "fx_params", return_value=PARAMS)
        self.addCleanup(patcher.stop)
        self.params = patcher.start()

    def test_unique_exact_name(self):
        self.assertEqual(bridge.param_index("Level", fx=0), 7)

    def test_exact_match_beats_partial(self):
        # "Drive Tone" contains "drive", but the exact hits must win and then be refused
        # for being two, never quietly resolved to the substring match at index 3.
        with self.assertRaises(LookupError) as ctx:
            bridge.param_index("Drive", fx=0)
        message = str(ctx.exception)
        self.assertIn("2 parameters", message)
        self.assertIn("#0", message)
        self.assertIn("#9", message)

    def test_unique_substring_is_allowed(self):
        self.assertEqual(bridge.param_index("Tone", fx=0), 3)

    def test_case_and_space_insensitive(self):
        self.assertEqual(bridge.param_index("  level ", fx=0), 7)

    def test_missing_name_lists_nothing_but_says_so(self):
        with self.assertRaises(LookupError) as ctx:
            bridge.param_index("Presence", fx=0)
        self.assertIn("no parameter matches", str(ctx.exception))

    def test_ambiguous_substring_lists_candidates(self):
        with self.assertRaises(LookupError) as ctx:
            bridge.param_index("driv", fx=0)
        message = str(ctx.exception)
        # Two params are literally named Drive and a third is Drive Tone: all three are candidates.
        self.assertIn("matches 3 parameters", message)
        self.assertIn("#0 Drive", message)
        self.assertIn("Drive Tone", message)


class TestInputEncoding(unittest.TestCase):
    def test_none(self):
        self.assertEqual(bridge.input_encoding("none", 2), 0)

    def test_mono_channel(self):
        self.assertEqual(bridge.input_encoding("in 2", 2), 2)

    def test_stereo_pair(self):
        self.assertEqual(bridge.input_encoding("1-2", 2), bridge.INPUT_PAIR_BASE)

    def test_channel_beyond_the_device_is_refused(self):
        with self.assertRaises(ValueError):
            bridge.input_encoding("input 5", 2)

    def test_words_without_a_number_are_refused(self):
        with self.assertRaises(ValueError):
            bridge.input_encoding("guitar", 2)


class TestNestedDecoding(unittest.TestCase):
    def test_accepts_a_real_list(self):
        self.assertEqual(bridge._nested({"fx": [{"i": 0}]}, "fx"), [{"i": 0}])

    def test_decodes_a_json_string(self):
        payload = {"fx": json.dumps([{"i": 2, "name": "ReaEQ"}])}
        self.assertEqual(bridge._nested(payload, "fx")[0]["name"], "ReaEQ")

    def test_missing_field_is_empty(self):
        self.assertEqual(bridge._nested({}, "fx"), [])

    def test_garbage_is_an_error_not_an_empty_list(self):
        with self.assertRaises(bridge.BadResponse):
            bridge._nested({"fx": "{not json"}, "fx")

    def test_object_instead_of_array_is_an_error(self):
        with self.assertRaises(bridge.BadResponse):
            bridge._nested({"fx": '{"i": 0}'}, "fx")


if __name__ == "__main__":
    unittest.main()
