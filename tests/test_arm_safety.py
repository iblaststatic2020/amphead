"""The arm safety floor: a rig must never turn the speakers into a microphone.

Every test here is about the refusal path, because the failure that matters is arming a
loud amp into a live mic, not arming at all.
"""

import unittest

from amphead import bridge
from amphead.fake_reaper import ARM_SAFE_MASTER_DB

from test_bridge import BridgeCase


class TestArmSafety(BridgeCase):
    rig = "/tmp/hermes-rig/arm.rpp"

    def setUp(self):
        super().setUp()
        bridge.open_rig(self.rig, root=self.root, new="1")
        self.track = int(self.call("TRACK_ADD", name="Gtr L", expect=self.rig)["index"])

    def test_arming_pulls_the_master_down_first(self):
        self.call("MASTER_VOL", db=0.0, expect=self.rig)
        payload = bridge.arm(self.track, root=self.root, expect=self.rig)
        self.assertTrue(payload["armed"])
        self.assertTrue(payload["lowered"])
        self.assertLessEqual(payload["master_db_after"], ARM_SAFE_MASTER_DB + 0.5)
        self.assertAlmostEqual(self.call("MASTER_VOL")["db"], ARM_SAFE_MASTER_DB, places=1)

    def test_arming_a_quiet_master_does_not_touch_it(self):
        self.call("MASTER_VOL", db=-72.0, expect=self.rig)
        payload = bridge.arm(self.track, root=self.root, expect=self.rig)
        self.assertFalse(payload["lowered"])
        self.assertAlmostEqual(payload["master_db_after"], -72.0, places=1)

    def test_building_a_rig_leaves_the_track_dead(self):
        self.call("SET_INPUT", track=self.track, input="1", expect=self.rig)
        info = self.call("TRACK_INFO", track=self.track)
        self.assertFalse(info["armed"])
        self.assertFalse(info["monitor"])
        born = self.call("TRACK_ADD", name="Gtr R", expect=self.rig)
        self.assertFalse(born["armed"])
        self.assertFalse(born["monitor"])

    def test_agent_cannot_arm_without_the_user(self):
        with self.assertRaises(bridge.CommandFailed) as ctx:
            self.call("ARM", track=self.track, expect=self.rig)
        self.assertIn("confirm=1", ctx.exception.payload["error"])
        self.assertFalse(self.call("TRACK_INFO", track=self.track)["armed"])

    def test_disarm_takes_it_back_down(self):
        bridge.arm(self.track, root=self.root, expect=self.rig)
        payload = bridge.disarm(self.track, root=self.root, expect=self.rig)
        self.assertFalse(payload["armed"])
        self.assertFalse(payload["monitor"])

    def test_stuck_master_refuses_to_arm(self):
        self.call("MASTER_VOL", db=0.0, expect=self.rig)
        with self.assertRaises(bridge.CommandFailed) as ctx:
            self.call("ARM", track=self.track, confirm="1", expect=self.rig, stuck_master="1")
        self.assertIn("refusing to arm", ctx.exception.payload["error"])
        info = self.call("TRACK_INFO", track=self.track)
        self.assertFalse(info["armed"])

    def test_allow_loud_is_explicit(self):
        self.call("MASTER_VOL", db=0.0, expect=self.rig)
        payload = bridge.arm(self.track, allow_loud=True, root=self.root, expect=self.rig)
        self.assertFalse(payload["lowered"])
        self.assertAlmostEqual(payload["master_db_after"], 0.0, places=1)

    def test_client_rejects_an_arm_that_did_not_land_quiet(self):
        # A bridge that says "armed" while reporting a hot master is lying; the client
        # checks the number itself rather than trusting the word.
        from unittest import mock

        hot = {"armed": True, "lowered": False, "master_db_before": 0.0,
               "master_db_after": 0.0, "master_safety": "lowered"}
        with mock.patch.object(bridge, "call", return_value=hot):
            with self.assertRaises(bridge.CommandFailed) as ctx:
                bridge.arm(self.track)
        self.assertEqual(ctx.exception.payload["master_db_after"], 0.0)


if __name__ == "__main__":
    unittest.main()
