"""Tone routing is pure text work, so it is testable with the host closed and no audio device."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from amphead import tones  # noqa: E402


class RoutingTest(unittest.TestCase):
    def check(self, query: str, key: str) -> None:
        arch, _ = tones.best(query)
        self.assertIsNotNone(arch, f"{query!r} matched nothing")
        self.assertEqual(arch.key, key, f"{query!r} went to {arch.key}")

    def test_artist_names_reach_their_archetype(self):
        self.check("an amp that sounds like blink182", "pop-punk")
        self.check("blink-182", "pop-punk")
        self.check("green day dookie", "pop-punk")
        self.check("qotsa fuzz", "stoner-fuzz")
        self.check("acdc crunch", "hard-rock")
        self.check("satriani lead", "lead")
        self.check("ampeg bass", "bass")

    def test_shapes_of_sentence_do_not_matter(self):
        for query in ("sounds like blink182", "give me blink 182", "I want a blink-182 tone"):
            self.check(query, "pop-punk")

    def test_another_blink_amp_reaches_the_hotter_flavour(self):
        self.check("another blink 182 tone", "pop-punk-hot")
        self.check("blink 182 but hotter and brighter", "pop-punk-hot")
        self.check("dude ranch blink", "pop-punk-hot")
        self.check("one more punk amp please", "pop-punk-hot")

    def test_filler_words_alone_match_nothing(self):
        arch, ranked = tones.best("give me a nice amp tone")
        self.assertIsNone(arch)
        self.assertTrue(ranked, "ranking should still offer candidates")

    def test_every_archetype_points_at_a_real_preset(self):
        for arch in tones.ARCHETYPES:
            self.assertTrue(tones.preset_path(arch).is_file(), arch.preset)


class PresetDocumentTest(unittest.TestCase):
    def test_tweaks_land_and_clamp(self):
        arch = next(a for a in tones.ARCHETYPES if a.key == "pop-punk")
        xml, changed = tones.xml_for(arch)
        self.assertIn("amp_bass", changed)
        before, after = changed["amp_bass"]
        self.assertAlmostEqual(after, tones.clamp(before - 0.6), places=6)
        self.assertIn(f'id="amp_bass" value="{after:.6f}"', xml)

    def test_tweaks_are_idempotent_because_they_come_from_the_saved_preset(self):
        arch = next(a for a in tones.ARCHETYPES if a.key == "pop-punk")
        first, _ = tones.xml_for(arch)
        second, _ = tones.xml_for(arch)
        self.assertEqual(first, second)

    def test_root_reports_the_capture_and_ir_by_name(self):
        arch = next(a for a in tones.ARCHETYPES if a.key == "clean")
        root = tones.root_of(arch)
        self.assertTrue(root["amp"].endswith(".nam"))
        self.assertTrue(root["ir"].endswith((".wav", ".wmir")))


if __name__ == "__main__":
    unittest.main()
