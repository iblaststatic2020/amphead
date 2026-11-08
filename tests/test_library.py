"""The amp and cabinet menus, and the name of the tone they add up to.

These are the two questions the pane cannot answer on its own: what files exist, and what to
call the noise. Both are answered from the file names, so both are tested against names a real
capture pack actually ships with.
"""

import os
import pathlib
import tempfile
import unittest

from amphead import library


class ToneNameTest(unittest.TestCase):
    def test_a_capture_rig_name_becomes_a_tone_name(self):
        # The vendor prefix and the capture rig's parameter tail are file-name noise; the amp,
        # the channel, the mic and the cabinet's size are what a player says.
        got = library.tone_name(
            "C:/x/Phillipe P Bugera6262-Lead-M77-ESR0,020.nam",
            "C:/y/AURORA_212_GOV.wav",
        )
        self.assertEqual(got, "Bugera 6262 Lead M77 / AURORA 212 GOV")

    def test_nothing_loaded_says_so(self):
        self.assertEqual(library.tone_name("", ""), "nothing loaded")

    def test_one_half_alone_is_not_a_slash(self):
        self.assertEqual(library.tone_name("C:/x/AC30.nam", ""), "AC30")


class ScanTest(unittest.TestCase):
    def setUp(self):
        self.room = pathlib.Path(tempfile.mkdtemp())

    def touch(self, *parts: str) -> pathlib.Path:
        path = self.room.joinpath(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
        return path

    def test_files_are_named_by_the_folder_that_tells_them_apart(self):
        self.touch("NAM", "Bugera 6262", "Lead.nam")
        self.touch("NAM", "Bugera 6262", "Clean.nam")
        rows = library.scan([self.room / "NAM"], (".nam",))
        self.assertEqual([r["name"] for r in rows], ["Clean", "Lead"])
        self.assertEqual({r["folder"] for r in rows}, {"Bugera 6262"})

    def test_the_scan_respects_its_own_kind_of_file(self):
        self.touch("NAM", "Lead.nam")
        self.touch("NAM", "Cab.wav")
        self.assertEqual([r["name"] for r in library.scan([self.room / "NAM"], (".nam",))],
                         ["Lead"])
        self.assertEqual([r["name"] for r in library.scan([self.room / "NAM"], (".wav",))],
                         ["Cab"])

    def test_the_loaded_files_folder_is_scanned_even_if_it_is_unusual(self):
        """A capture played once stays reachable, wherever the player keeps it."""
        odd = self.touch("elsewhere", "Mystery Amp.nam")
        with _env({"AMPHEAD_CAPTURE_DIR": str(self.room / "empty")}):
            got = library.listing(capture=str(odd))
        self.assertIn("Mystery Amp", [row["name"] for row in got["captures"]])

    def test_a_missing_folder_is_not_an_error(self):
        """A folder that is not there is skipped, not raised: the usual ones still answer."""
        with _env({"AMPHEAD_CAPTURE_DIR": str(self.room / "gone")}):
            got = library.listing()
        self.assertNotIn(str(self.room / "gone"), got["capture_dirs"])
        self.assertTrue(got["capture_dirs"])


class _env:
    def __init__(self, values: dict):
        self.values = values
        self.old = {}

    def __enter__(self):
        for key, value in self.values.items():
            self.old[key] = os.environ.get(key)
            os.environ[key] = value
        return self

    def __exit__(self, *exc):
        for key, value in self.old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        return False


if __name__ == "__main__":
    unittest.main()
