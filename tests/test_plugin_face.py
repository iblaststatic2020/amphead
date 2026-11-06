"""The face and the verdicts have to agree.

A verdict word is only useful if the pane can colour it, and a face that quietly falls back to cream
for a broken rig is worse than one that says nothing at all. This walks the words verdict.py can
produce and checks each one has an entry in the pane's table, so adding a state cannot ship
half-done.
"""

import re
import re
import unittest
from pathlib import Path

from amphead import verdict as verdicts

PLUGIN = Path(__file__).resolve().parent.parent / "desktop" / "plugin.js"


def face_source() -> str:
    return PLUGIN.read_text(encoding="utf-8")


def verdict_table(source: str) -> str:
    """The VERDICT_COLOR object literal, from its name to the closing brace."""
    start = source.index("const VERDICT_COLOR = {")
    end = source.index("}", start)
    return source[start:end]


class TestFaceMatchesVerdicts(unittest.TestCase):
    def test_every_verdict_word_has_a_colour(self):
        table = verdict_table(face_source())
        words = [
            value
            for name, value in vars(verdicts).items()
            if name.isupper() and isinstance(value, str) and value == value.upper()
        ]
        self.assertGreater(len(words), 10, "verdict.py stopped exporting its words")
        # Keys may be quoted or bare, since words like READY are valid identifiers and others are not.
        missing = [word for word in words if not re.search(rf"^\s*['\"]?{re.escape(word)}['\"]?:", table, re.M)]
        self.assertEqual([], missing, f"the face has no colour for: {missing}")

    def test_the_face_stays_reachable(self):
        """Four doors, one face: the route, the sidebar row, the command, the pane, the chip."""
        source = face_source()
        for marker in ("ROUTES_AREA", "SIDEBAR_NAV_AREA", "PALETTE_AREA", "area: 'panes'", "'statusBar.right'"):
            self.assertIn(marker, source, f"the face lost its {marker}")

    def test_the_chip_speaks_words_when_there_is_no_rig(self):
        """-60 dB with REAPER closed reads as a quiet rig, not as no rig, so words carry it."""
        source = face_source()
        chip = source[source.index("id: 'amp-chip'"):]
        self.assertIn("const live = !!(meter.data && meter.data.live)", chip)
        self.assertIn("CHIP_WORD[verdict]", chip)
        self.assertRegex(chip, r"live \? `\$\{toDb\(peak\)\.toFixed\(0\)\} dB`",
                         "the dB number must sit behind the live check, never print unconditionally")

    def test_every_verdict_word_has_a_chip_word(self):
        source = face_source()
        start = source.index("const CHIP_WORD = {")
        table = source[start:source.index("}", start)]
        words = [
            value
            for name, value in vars(verdicts).items()
            if name.isupper() and isinstance(value, str) and value == value.upper()
        ]
        missing = [w for w in words if not re.search(rf"^\s*['\"]?{re.escape(w)}['\"]?:", table, re.M)]
        self.assertEqual([], missing, f"the chip has no word for: {missing}")

    def test_the_amp_and_host_knob_sets_stay_apart(self):
        """The amp's knobs and the host's knobs collide by name, so the split must stay explicit."""
        source = face_source()
        self.assertIn("const isAmpKnob = (name) => /^amplifier\\s/i.test(name || '')", source)
        self.assertIn("function groups(knobs)", source)
