"""Panic tests: the switch that has to work while the player is still holding the guitar.

Every case stubs the wire at the bridge layer, so nothing here needs REAPER, an audio device, or
a live rig. The point of each test is the wording: panic must report what it verified, and it must
refuse to say "silenced" when a plugin ignored the bypass.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from amphead import bridge, tools  # noqa: E402


class FakeHost:
    """A tiny REAPER: master level, a chain that can ignore a bypass, and an arm flag."""

    def __init__(self, chain=None, master_db=0.0, armed=True, monitor=True, deaf_fx=(),
                 stubborn_master=False):
        self.stubborn_master = stubborn_master
        self.master_db = master_db
        self.armed = armed
        self.monitor = monitor
        self.chain = [dict(fx, enabled=True) for fx in (chain or [{"i": 0, "name": "NAM Universal"}])]
        self.deaf_fx = set(deaf_fx)
        self.calls = []

    def master(self, db=None, **kw):
        if self.stubborn_master:
            return {"vol": 1.0, "db": 0.0}
        if db is not None:
            self.calls.append(("master", float(db)))
            self.master_db = float(db)
        return {"vol": 1.0, "db": self.master_db}

    def fx_list(self, track=1, **kw):
        return [dict(fx) for fx in self.chain]

    def fx_bypass(self, track=1, fx=0, on=True, **kw):
        self.calls.append(("bypass", fx, on))
        if fx not in self.deaf_fx:
            for item in self.chain:
                if item["i"] == fx:
                    item["enabled"] = not on
        landed = not self.chain[fx]["enabled"]
        if landed != bool(on):
            raise bridge.CommandFailed({"bypassed": landed}, f"fx {fx} ignored bypass")
        return {"bypassed": landed}

    def disarm(self, track=1, **kw):
        self.armed = False
        self.monitor = False
        return {"armed": False, "monitor": False}

    def track_info(self, track=1, **kw):
        return {"index": track, "armed": self.armed, "monitor": self.monitor, "mute": False}


class PanicCase(unittest.TestCase):
    def run_panic(self, host, args=None):
        patches = [
            mock.patch.object(tools, "active_path", return_value="C:/rig/amphead.rpp"),
            mock.patch.object(tools.bridge, "master", host.master),
            mock.patch.object(tools.bridge, "fx_list", host.fx_list),
            mock.patch.object(tools.bridge, "fx_bypass", host.fx_bypass),
            mock.patch.object(tools.bridge, "disarm", host.disarm),
            mock.patch.object(tools.bridge, "track_info", host.track_info),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return tools.handle_panic(args or {})


class TestPanic(PanicCase):
    def test_silences_master_disarms_and_bypasses_every_plugin(self):
        host = FakeHost(chain=[{"i": 0, "name": "NAM Universal"}, {"i": 1, "name": "Shoeless"}],
                        master_db=-18.0)
        out = self.run_panic(host)
        self.assertIn("panicked=True", out)
        self.assertIn("master_db=-60.0", out)
        self.assertIn("bypassed=2", out)
        self.assertIn("armed=False", out)
        self.assertIn("monitor=False", out)
        self.assertTrue(all(not fx["enabled"] for fx in host.chain))

    def test_master_goes_down_before_anything_asks_a_plugin_for_permission(self):
        """A plugin that argues must not be able to hold the speakers open while it argues."""
        host = FakeHost()
        self.run_panic(host)
        self.assertEqual(host.calls[0], ("master", -60.0))
        self.assertEqual(host.master_db, -60.0)

    def test_refuses_to_claim_silence_when_a_plugin_ignores_the_bypass(self):
        host = FakeHost(chain=[{"i": 0, "name": "Stubborn"}], deaf_fx={0})
        out = self.run_panic(host)
        self.assertIn("panicked=False", out)
        self.assertIn("Stubborn", out)
        self.assertIn("refused the bypass", out)

    def test_reports_a_master_that_will_not_come_down(self):
        host = FakeHost(master_db=0.0, stubborn_master=True)
        out = self.run_panic(host)
        self.assertIn("panicked=False", out)
        self.assertIn("master is at 0.0 dB", out)

    def test_meter_still_works_so_the_input_stays_visible(self):
        out = self.run_panic(FakeHost())
        self.assertIn("still live", out)

    def test_restore_unbypasses_and_lifts_the_master_without_arming(self):
        host = FakeHost(master_db=-60.0, armed=False, monitor=False)
        for fx in host.chain:
            fx["enabled"] = False
        out = self.run_panic(host, {"restore": True})
        self.assertIn("panicked=False", out)
        self.assertIn("plugins_on=1", out)
        self.assertIn("master_db=-18.0", out)
        self.assertIn("armed=False", out)
        self.assertIn("never arms", out)
        self.assertTrue(host.chain[0]["enabled"])

    def test_reports_reaper_not_running_instead_of_pretending(self):
        with mock.patch.object(tools, "active_path", side_effect=bridge.NoReaper("stale")):
            out = tools.handle_panic({})
        self.assertIn("NOT PANICKED", out)
        self.assertIn("REAPER is not running", out)

    def test_reports_no_project_open(self):
        with mock.patch.object(tools, "active_path", return_value=None):
            out = tools.handle_panic({})
        self.assertIn("NOT PANICKED", out)
        self.assertIn("no project", out)


class TestBypassHelper(unittest.TestCase):
    """``bridge.fx_bypass`` is the one place that trusts REAPER's answer over its own request."""

    def test_accepts_a_read_back_that_matches(self):
        with mock.patch.object(bridge, "call", return_value={"bypassed": True}):
            self.assertTrue(bridge.fx_bypass(fx=2, on=True)["bypassed"])

    def test_raises_when_the_plugin_did_not_move(self):
        with mock.patch.object(bridge, "call", return_value={"bypassed": False}):
            with self.assertRaises(bridge.CommandFailed):
                bridge.fx_bypass(fx=2, on=True)

    def test_unbypass_asks_for_the_opposite_flag(self):
        seen = {}

        def fake(cmd, **kwargs):
            seen.update(kwargs)
            return {"bypassed": False}

        with mock.patch.object(bridge, "call", fake):
            bridge.fx_bypass(fx=1, on=False)
        self.assertEqual(seen.get("on"), "0")


if __name__ == "__main__":
    unittest.main()
