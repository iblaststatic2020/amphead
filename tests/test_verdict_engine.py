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
        self.assertIn("SET UP", got["reason"])

    def test_reaper_keeps_its_own_word(self):
        got = self.judge({"bridge": "gone"})
        self.assertEqual(got["verdict"], verdict.BRIDGE_GONE)

    def test_an_open_engine_says_it_is_open_and_silent(self):
        # The face has one switch. Once the engine is up that switch reads GO LIVE, so the reason
        # has to carry the fact that Setup already happened, or a player hunts for a control the
        # face is not showing because it has nothing left to do.
        got = verdict.judge({"bridge": "live", "engine": "amphead2", "rig_tab": True,
                             "tracks": 1, "device": {"closed": False}},
                            {}, {"l": 0.0, "r": 0.0, "clip": False, "master_db": -60.0}, [])
        self.assertEqual(got["verdict"], verdict.STANDBY)
        self.assertIn("open", got["reason"])
        self.assertIn("GO LIVE", got["reason"])
        self.assertIn("-6 dB", got["reason"])

    def test_a_reaper_rig_still_says_arm_the_track(self):
        got = verdict.judge({"bridge": "live", "rig_tab": True, "tracks": 1,
                             "device": {"closed": False}},
                            {}, {"l": 0.0, "r": 0.0, "clip": False})
        self.assertEqual(got["reason"], "the track is not armed, flip LIVE to play")

    def test_a_running_engine_is_judged_by_the_ordinary_rules(self):
        got = self.judge({"bridge": "live", "engine": "amphead2", "rig_tab": True,
                          "tracks": 1, "device": {"closed": False}})
        self.assertNotEqual(got["verdict"], verdict.ENGINE_OFF)


if __name__ == "__main__":
    unittest.main()
