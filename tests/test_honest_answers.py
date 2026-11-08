"""An answer that says it failed has to be readable as a failure by the agent, not just by a person.

Every handler answers in one line of prose and the JSON wrapper decides `ok` from the words. Two
shapes slipped through: a second line carrying the bad news, and a cheerful first field next to
`problems=` / `panicked=False`. Both told the host the call worked. This file pins the reading.
"""

import json
import unittest

from amphead import answers


class TheWordsAreReadAllTheWayDown(unittest.TestCase):
    def test_a_second_line_of_bad_news_is_not_a_success(self):
        said = "capture=Deluxe | apply=True\nnot loaded: the engine is not running"
        self.assertFalse(answers.ok(said))

    def test_a_plain_success_is_still_a_success(self):
        self.assertTrue(answers.ok("live=True | armed=True | master_db=-6.0"))

    def test_a_failure_word_later_in_a_line_is_not_a_failure(self):
        # "ARMED: ... gate is not set" is a report, and the old rule existed to keep it one.
        self.assertTrue(answers.ok("ARMED: the amp is up, the gate is not set"))


class TheFieldsOutrankTheCheerfulOpening(unittest.TestCase):
    def test_panicked_false_is_not_ok(self):
        said = answers.json_answer(
            "amphead_panic",
            "panicked=False | master_db=0.0 | problems=master is at 0.0 dB")
        self.assertFalse(json.loads(said)["ok"])

    def test_a_panic_with_no_problems_is_ok(self):
        said = answers.json_answer(
            "amphead_panic", "panicked=True | master_db=-60.0 | problems=none")
        self.assertTrue(json.loads(said)["ok"])

    def test_problems_reported_by_ready_are_not_ok(self):
        said = answers.json_answer(
            "amphead_ready", "verdict=ENGINE_OFF | reason=the engine is not running "
                             "| problems=engine off")
        self.assertFalse(json.loads(said)["ok"])


if __name__ == "__main__":
    unittest.main()
