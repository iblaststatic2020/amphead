"""Looking for a library is a press, never a habit.

An empty picker used to answer with an environment variable, and the agent could spend a turn
walking the disk to fill it. Both of those decide something on the player's behalf: the walk reads
their folders, and on a metered model the turn costs tokens they did not offer. So the walk got a
button and these tests pin the two halves of that promise: the route does the walk when it is
called, the settings writer remembers the folder it was given, and no other path in the plugin can
reach the walk at all.

The source pin is deliberate. A behaviour test can only prove that today's call graph is clean;
this one fails the moment somebody adds a second caller, wherever they add it.
"""

import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

try:
    from dashboard import plugin_api
except Exception as exc:  # fastapi is only installed in the plugin interpreter
    raise unittest.SkipTest(f"the dashboard needs fastapi: {exc}")

ROOT = Path(__file__).resolve().parent.parent
API = (ROOT / "dashboard" / "plugin_api.py").read_text(encoding="utf-8")
FACE = (ROOT / "desktop" / "plugin.js").read_text(encoding="utf-8")


class ConsentedWalk(unittest.TestCase):
    def setUp(self):
        self.room = tempfile.TemporaryDirectory()
        self.addCleanup(self.room.cleanup)
        self.base = Path(self.room.name)
        # A pretend home, because the real one holds tens of thousands of files and the walk stops
        # after forty thousand visits: on this machine that is before it reaches a scratch folder.
        self.home = self.base / "home"
        self.lib = self.home / "Documents" / "Guitar Amp Sims" / "NAM" / "Bugera 6262"
        self.lib.mkdir(parents=True)
        for i in range(3):
            (self.lib / f"amp {i}.nam").write_bytes(b"n")
        (self.lib / "take.wav").write_bytes(b"w")
        self.settings = self.base / "settings.json"
        self._env = {key: os.environ.get(key) for key in ("AMPHEAD_CAPTURE_DIR", "AMPHEAD_SETTINGS")}
        self.addCleanup(self._restore)
        os.environ["AMPHEAD_CAPTURE_DIR"] = str(self.lib)
        os.environ["AMPHEAD_SETTINGS"] = str(self.settings)
        patcher = mock.patch("pathlib.Path.home", classmethod(lambda cls: self.home))
        patcher.start()
        self.addCleanup(patcher.stop)

    def _restore(self):
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_the_walk_reports_the_folder_and_what_is_in_it(self):
        from amphead import library

        found = library.find()
        paths = [row["path"] for row in found["candidates"]]
        self.assertIn(str(self.lib), paths)
        row = found["candidates"][paths.index(str(self.lib))]
        self.assertEqual(3, row["captures"])
        self.assertEqual(1, row["cabs"])

    def test_the_route_answers_with_the_page_the_face_paints(self):
        found = plugin_api.find_library()
        for key in ("candidates", "scanned"):
            self.assertIn(key, found)

    def test_the_walk_says_where_it_looked(self):
        found = plugin_api.find_library()
        self.assertTrue(any(str(self.base) in seen for seen in found["scanned"]),
                        found["scanned"])

    def test_the_folder_the_player_picks_is_written_to_the_settings_file(self):
        out = plugin_api.adopt_folder({"path": str(self.lib), "kind": "capture"})
        self.assertTrue(out["ok"], out)
        import json

        said = json.loads(self.settings.read_text(encoding="utf-8"))
        self.assertEqual([str(self.lib)], said["capture_dirs"])

    def test_picking_the_same_folder_twice_does_not_double_it(self):
        plugin_api.adopt_folder({"path": str(self.lib), "kind": "capture"})
        plugin_api.adopt_folder({"path": str(self.lib), "kind": "capture"})
        import json

        said = json.loads(self.settings.read_text(encoding="utf-8"))
        self.assertEqual([str(self.lib)], said["capture_dirs"])

    def test_cabinets_go_to_the_cabinet_key(self):
        out = plugin_api.adopt_folder({"path": str(self.lib), "kind": "cab"})
        self.assertTrue(out["ok"], out)
        import json

        said = json.loads(self.settings.read_text(encoding="utf-8"))
        self.assertEqual([str(self.lib)], said["cab_dirs"])
        self.assertEqual([], said.get("capture_dirs") or [])

    def test_a_folder_that_is_gone_is_refused_in_a_sentence(self):
        out = plugin_api.adopt_folder({"path": str(self.base / "gone"), "kind": "capture"})
        self.assertFalse(out["ok"], out)
        self.assertIn("not a folder", out["output"])
        self.assertFalse(self.settings.exists())


class NobodyScansWithoutAsking(unittest.TestCase):
    """One caller, in the pressed route, and no query that fires on its own."""

    def test_the_walk_has_exactly_one_caller(self):
        callers = [line.strip() for line in API.splitlines() if "amp_library.find(" in line]
        self.assertEqual(1, len(callers), callers)

    def test_the_one_caller_is_the_pressed_route(self):
        handler = API.split("@router.get(\"/library/find\")", 1)[1].split("@router.", 1)[0]
        self.assertIn("amp_library.find()", handler)

    def test_the_pane_only_asks_when_the_button_is_pressed(self):
        # The route may appear once, inside the function the button calls; a useQuery on it would
        # mean the pane scans every time the plate is opened.
        hits = re.findall(r"/library/find", FACE)
        self.assertEqual(1, len(hits), hits)
        self.assertIn("async function searchFolders", FACE)
        self.assertNotIn("queryFn: () => rest('/library/find')", FACE)

    def test_the_button_sits_where_the_list_would_have_been_empty(self):
        self.assertIn("search my folders for amps", FACE)
        self.assertIn("search my folders for cabs", FACE)
        # The old sentence sent a player to an environment variable. Nobody holding a guitar wants
        # that, and it read as the plugin's problem rather than a folder that was not there.
        self.assertNotIn("set AMPHEAD_CAPTURE_DIR or AMPHEAD_CAB_DIR", FACE)

    def test_the_empty_plate_names_the_folders_it_already_read(self):
        self.assertIn("nothing in ${roots.map(leafOf).join(', ')}", FACE)


if __name__ == "__main__":
    unittest.main()
