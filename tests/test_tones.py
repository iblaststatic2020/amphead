"""Tone routing is pure text work, so it is testable with the host closed and no audio device."""

from __future__ import annotations

import sys
import unittest
import xml.etree.ElementTree as ET
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
        self.check("acdc crunch", "acdc")
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

    def test_saying_the_band_name_alone_is_enough(self):
        self.check("I want to sound like AC/DC", "acdc")
        self.check("make it sound like ac dc", "acdc")

    def test_acdc_songs_and_players_reach_the_acdc_rig(self):
        for query in ("angus young", "back in black", "highway to hell", "let there be rock",
                      "malcolm young rhythm", "thunderstruck", "a 1979 marshall plexi",
                      "sg into a super lead", "greenback crunch"):
            self.check(query, "acdc")

    def test_generic_hard_rock_stays_on_the_generic_rig(self):
        self.check("hard rock", "hard-rock")
        self.check("guns n roses", "hard-rock")
        self.check("80s marshall", "hard-rock")

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

    def test_the_acdc_rig_actually_exists_on_disk(self):
        # A preset that names a missing capture or IR loads silently and sounds like nothing, so the
        # paths are checked here rather than discovered at the amp.
        arch = next(a for a in tones.ARCHETYPES if a.key == "acdc")
        root = ET.fromstring(tones.xml_for(arch)[0].encode("utf-8"))
        capture = Path("C:/" + root.attrib["amp_profile"].replace("\\", "/"))
        cab = tones.NAMU / "IR" / root.attrib["impulse_file1"].replace("\\", "/")
        self.assertTrue(capture.is_file(), capture)
        self.assertTrue(cab.is_file(), cab)

    def test_the_acdc_rig_is_a_marshall_with_the_gain_down(self):
        arch = next(a for a in tones.ARCHETYPES if a.key == "acdc")
        xml, changed = tones.xml_for(arch)
        self.assertEqual(changed, {}, "the acdc preset carries its own knobs, not deltas")
        root = ET.fromstring(xml.encode("utf-8"))
        self.assertIn("Marshall", root.attrib["amp_profile"])
        self.assertIn("Greenback", root.attrib["impulse_file1"])
        knobs = {p.attrib["id"]: float(p.attrib["value"]) for p in root.findall("PARAM")}
        self.assertLessEqual(knobs["amp_pre_gain"], 3.5)
        self.assertLess(knobs["amp_bass"], 3.5)
        self.assertGreater(knobs["amp_mid"], 6.0)
        self.assertEqual(knobs["pedal_active"], 0.0)


if __name__ == "__main__":
    unittest.main()
