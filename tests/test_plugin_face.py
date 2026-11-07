"""The face and the verdicts have to agree.

A verdict word is only useful if the pane can colour it, and a face that quietly falls back to cream
for a broken rig is worse than one that says nothing at all. This walks the words verdict.py can
produce and checks each one has an entry in the pane's table, so adding a state cannot ship
half-done.
"""

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

    def test_an_on_off_parameter_is_a_knob_with_two_ends(self):
        """The face draws the plugin's switches as knobs, and an On/Off knob snaps to one of its
        two ends so a drag can never leave it at a meaningless in-between."""
        source = face_source()
        knob = source[source.index("function Knob("):source.index("function KnobGrid(")]
        self.assertIn("const isBool = knob.bool === true", knob)
        self.assertIn("isBool ? (clamped >= 0.5 ? 1 : 0) : clamped", knob, "the turn must snap")
        self.assertIn("if (isBool && !moved.current)", knob, "a tap has to flip it")
        self.assertIn("move(isBool ? 1 : value + step, true)", knob, "the arrow keys snap too")

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
        # The dB number sits behind the live check however the ternary is nested, and the demo
        # comes first because a canned rig's level is not a measurement of this machine.
        self.assertIn("dB`", chip)
        self.assertLess(chip.index("const live"), chip.index("dB`"),
                        "the dB number must sit behind the live check, never print unconditionally")
        self.assertLess(chip.index("state.data.demo"), chip.index("dB`"),
                        "the demo has to claim the chip before a level does")
        self.assertLess(chip.index("const text"), chip.index("dB`"))

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
        # The split now comes from the backend's `section`, read off the plugin's own parameter
        # names, so the pane groups knobs the way the VST draws them.
        self.assertIn("function groups(knobs, plugin)", source)
        self.assertIn("pick('pedal')", source)

    def test_the_pane_names_the_plugin_it_is_showing(self):
        """A knob set is only checkable against a VST if the pane says which VST it is."""
        source = face_source()
        self.assertIn("const plugin = s.plugin || {}", source)
        self.assertIn("`the model in ${named}`", source)

class TestTheKnobKeepsUp(unittest.TestCase):
    """A knob has to stay where it was put, and say where that is in the plugin's own units."""

    def knob_source(self) -> str:
        source = face_source()
        start = source.index("function Knob({ knob, busy, onTurn, size }) {")
        return source[start:source.index("function KnobGrid(", start)]

    def test_the_reading_is_the_plugins_number_not_a_word(self):
        block = self.knob_source()
        self.assertNotIn("'turning'", block,
                         "the pane must keep showing the plugin's reading while a finger is down")
        self.assertIn("children: shown", block)

    def test_the_poll_cannot_yank_the_knob_back(self):
        """The sticky feel was the 1.5 s state poll overwriting the finger, mid-gesture."""
        block = self.knob_source()
        self.assertIn("if (dragging || Date.now() < settled.current) return", block)
        self.assertIn("settled.current = Date.now() + 1500", block)

    def test_a_turn_does_not_lock_the_face(self):
        """A knob turn must not go through `act`, which sets busy and refetches everything."""
        source = face_source()
        self.assertIn("onTurn: turnKnob", source)
        self.assertNotIn("act('/tweak'", source, "the knob still posts through the chat-shaped route")
        self.assertIn("rest('/knob'", source)
        start = source.index("async function turnKnob(")
        end = source.index("async function act(", start)
        self.assertNotIn("setBusy(true)", source[start:end])

    def test_shift_is_a_finer_turn_and_steps_stay_relative(self):
        block = self.knob_source()
        self.assertIn("event.shiftKey ? TRAVEL * 5 : TRAVEL", block,
                      "shift must travel five times further for the same movement")
        self.assertIn("const dy = finger.current - event.clientY", block,
                      "steps must come from the pixel delta, or shift mid-drag teleports the knob")

    def test_one_write_at_a_time_with_the_newest_position_winning(self):
        block = self.knob_source()
        self.assertIn("if (order.busy) {", block)
        self.assertIn("order.queued = { next, final }", block)


class TestTheDemoIsReachable(unittest.TestCase):
    """A visitor with no DAW must be able to see the plugin work, from inside the pane."""

    def test_the_face_offers_the_demo_on_a_dead_rig(self):
        source = face_source()
        offered = re.search(r"const DEMO_OFFER = new Set\(\[(.*?)\]\)", source, re.S)
        self.assertIsNotNone(offered, "the face has no list of verdicts that offer the demo")
        words = offered.group(1)
        for word in ("REAPER NOT RUNNING", "NO REAPER INSTALL", "BRIDGE GONE", "RIG TAB CLOSED"):
            self.assertIn(word, words, f"{word} does not offer the demo")
        self.assertIn("DemoRow", source)
        self.assertIn("'see it work'", source)

    def test_the_demo_says_out_loud_that_it_is_not_the_players_rig(self):
        source = face_source()
        self.assertIn("demo rig, not your hardware", source.lower())
        self.assertIn("'leave demo'", source)

    def test_the_demo_switch_is_a_route_the_face_calls(self):
        self.assertIn("act('/demo', { on: !s.demo })", face_source())
        api = (PLUGIN.parent.parent / "dashboard" / "plugin_api.py").read_text(encoding="utf-8")
        self.assertIn('@router.post("/demo")', api)
        self.assertIn("demo.set_enabled", api)

    def test_the_chip_says_demo_rather_than_a_level_it_did_not_measure(self):
        source = face_source()
        self.assertIn("state.data.demo", source)
        self.assertIn("? 'demo'", source)
