"""The local engine is not REAPER, and the words the pane shows should say so.

"BRIDGE GONE" is true of a DAW that closed its Lua bridge. The local engine has no bridge to
be gone: this plugin starts it, so if it is not running the honest word is off, and the reason
should name the control that turns it on rather than a file that stopped answering.
"""

import unittest

from amphead import verdict


class EngineWordsTest(unittest.TestCase):
    def judge(self, state):
        return verdict.judge(state, {}, {"l": 0.0, "r": 0.0, "clip": False}, [])

    def test_a_stopped_engine_says_off_not_bridge_gone(self):
        got = self.judge({"bridge": "gone", "engine": "amphead2", "rig_tab": True})
        self.assertEqual(got["verdict"], verdict.ENGINE_OFF)
        self.assertIn("Setup", got["reason"])

    def test_reaper_keeps_its_own_word(self):
        got = self.judge({"bridge": "gone"})
        self.assertEqual(got["verdict"], verdict.BRIDGE_GONE)

    def test_a_running_engine_is_judged_by_the_ordinary_rules(self):
        got = self.judge({"bridge": "live", "engine": "amphead2", "rig_tab": True,
                          "tracks": 1, "device": {"closed": False}})
        self.assertNotEqual(got["verdict"], verdict.ENGINE_OFF)


if __name__ == "__main__":
    unittest.main()
