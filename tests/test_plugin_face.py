"""The face is the product now: one engine, real knobs, and words the backend actually says.

These tests read the shipped `desktop/plugin.js` as text. They are not a renderer; they pin the
promises that would otherwise rot silently: that the face asks only for routes the dashboard
serves, that every one of those asks names the engine it means, that a player who has no amp
running is offered a SET UP they can see, that the tone is three knobs and two files, and that
every colour comes from the app's theme tokens instead of a hardcoded chassis.
"""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from amphead import verdict as verdicts  # noqa: E402

PLUGIN = ROOT / "desktop" / "plugin.js"


def face() -> str:
    return PLUGIN.read_text(encoding="utf-8")


def verdict_table(source: str) -> str:
    start = source.index("const VERDICT_COLOR = {")
    end = source.index("}", start)
    return source[start:end]


def served_paths() -> set[str]:
    """Read the routes out of the dashboard source.

    Loading `dashboard.plugin_api` would drag the whole app in, and another test module in this
    suite has already replaced its router object by the time this runs. The decorators are the
    contract, so read the contract.
    """
    source = (ROOT / "dashboard" / "plugin_api.py").read_text(encoding="utf-8")
    return set(re.findall(r"@router\.(?:get|post|put|delete)\(\s*['\"](/[^'\"]*)['\"]", source))


def asked_paths(source: str) -> set[str]:
    """Every path the face asks for, whether through `act(...)` or a `rest(...)` query."""
    found = set(re.findall(r"act\(\s*['\"]([^'\"]+)['\"]", source))
    found |= set(re.findall(r"rest\(\s*['\"]([^'\"?]+)", source))
    return {path.split("?")[0] for path in found}


class TestFaceMatchesVerdicts(unittest.TestCase):
    def test_every_word_the_engine_can_say_has_a_colour(self):
        """A verdict the face cannot paint is a verdict the player cannot read."""
        table = verdict_table(face())
        words = [
            value
            for name, value in vars(verdicts).items()
            if name.isupper() and isinstance(value, str) and value == value.upper()
        ]
        self.assertGreater(len(words), 10, "verdict.py stopped exporting its words")
        missing = [
            word for word in words
            if not re.search(rf"^\s*['\"]?{re.escape(word)}['\"]?:", table, re.M)
        ]
        self.assertEqual([], missing, f"the face has no colour for: {missing}")

    def test_the_face_paints_no_host_words(self):
        table = verdict_table(face())
        for word in ("BRIDGE GONE", "NO REAPER INSTALL", "RIG TAB CLOSED"):
            self.assertNotIn(word, table, f"the face still paints {word}")


class TestTheFaceAsksTheBackend(unittest.TestCase):
    def test_every_path_the_face_asks_for_is_a_route_that_exists(self):
        served = served_paths()
        missing = sorted(path for path in asked_paths(face()) if path not in served)
        self.assertEqual([], missing, f"the face asks for routes the dashboard does not serve: {missing}")

    def test_every_request_names_the_engine_it_means(self):
        """`?engine=` is read per request, so the face must never lean on the process default."""
        source = face()
        self.assertIn("engine=${engine}", source)
        self.assertIn("engineContext(ctx, 'amphead2')", source)

    def test_the_engine_is_the_only_backend_on_screen(self):
        source = face()
        self.assertNotIn("REAPER", source.upper(), "the face still talks about the DAW")
        self.assertEqual(1, len(re.findall(r"^\s*id: 'amphead',", source, re.M)), "one plugin, one id")

    def test_a_failure_caused_by_yesterdays_gateway_says_so(self):
        """The 404 that cost an evening: the face must name the cause instead of going quiet."""
        self.assertIn("this face is newer than the gateway: restart Hermes once to load the new routes", face())


class TestSetupIsVisible(unittest.TestCase):
    def test_the_button_reads_set_up_before_the_engine_runs(self):
        source = face()
        power = source[source.index("function PowerButton("):source.index("function AmpFace(")]
        self.assertIn("setup: { text: 'SET UP'", power)
        self.assertIn("standby: { text: 'GO LIVE'", power)
        self.assertIn("onSetup: () => act('/setup', {})", source)

    def test_an_unanswered_state_leaves_the_face_offering_set_up(self):
        """The press a player cannot find must be the press they get when nothing answers.

        `s.running !== false` treated a missing state as a running engine, so a failed route drew
        `GO LIVE` over an engine that was never opened: the one control on the face did nothing and
        nothing on the face said why.
        """
        self.assertIn("const running = s.running === true", face())
        self.assertNotIn("s.running !== false", face())

    def test_the_face_says_how_to_start_when_the_engine_is_off(self):
        self.assertIn("press SET UP to open the engine", face())

    def test_an_open_engine_can_be_put_back_down(self):
        """Setup opens a device and away it stays; without a way down the face can only ever be
        pressed once. The row says which state the switch is in, in words, and closes it."""
        source = face()
        self.assertIn("act('/teardown', {})", source)
        self.assertIn("engine open · silent", source)
        self.assertIn("'close engine'", source)


class TestToneIsThreeKnobs(unittest.TestCase):
    def setUp(self):
        self.source = face()

    def test_the_knobs_are_bass_mid_and_treble(self):
        self.assertIn("const TONE_NAMES = ['bass', 'mid', 'treble']", self.source)

    def test_a_turn_names_the_tone_stage_and_the_track(self):
        turn = self.source[self.source.index("async function turnKnob("):]
        turn = turn[:turn.index("\n  }")]
        self.assertIn("param: index", turn)
        self.assertIn("fx: 1", turn, "the engine's tone stack is the first stage")
        self.assertIn("track: 1", turn)
        self.assertIn("mode: 'norm'", turn)

    def test_the_knob_degrades_into_a_slider_a_keyboard_can_drive(self):
        knob = self.source[self.source.index("function ChickenKnob("):self.source.index("function Plate(")]
        for bit in ("role: 'slider'", "tabIndex: 0", "onKeyDown", "'aria-valuenow'", "'aria-valuetext'"):
            self.assertIn(bit, knob, f"the knob is missing {bit}")

    def test_the_knob_follows_the_engine_but_not_under_a_finger(self):
        knob = self.source[self.source.index("function ChickenKnob("):self.source.index("function Plate(")]
        self.assertIn("if (dragging || Date.now() < settled.current) return", knob)

    def test_the_gain_knobs_sit_either_side_of_the_amp(self):
        """In drives the capture, out balances the cabinet. Both are on the plate, in order."""
        plate = self.source[self.source.index("the knobs, on one plate"):]
        plate = plate[:plate.index("jsx(Meter")]
        self.assertLess(plate.index("label: 'IN'"), plate.index("...TONE_NAMES.map"),
                        "the input knob must come before the tone the amp makes")
        self.assertLess(plate.index("...TONE_NAMES.map"), plate.index("label: 'OUT'"),
                        "the output knob must come after the tone")
        self.assertIn("act('/input_gain', { db })", plate)
        self.assertIn("act('/output_gain', { db })", plate)
        self.assertIn("const GAIN_LOW = -24", self.source)
        self.assertIn("const GAIN_HIGH = 24", self.source)

    def test_the_master_has_the_engines_headroom_not_a_daws_zero(self):
        self.assertIn("const MASTER_HIGH = 12", self.source)
        self.assertIn("const MASTER_LOW = -60", self.source)

    def test_the_tone_is_named_and_the_two_files_are_shown(self):
        self.assertIn("s.tone_name", self.source)
        for label in ("'amp · capture'", "'cabinet · impulse'"):
            self.assertIn(label, self.source)

    def test_choosing_a_file_posts_the_path(self):
        self.assertIn("await act('/capture', { path })", self.source)
        self.assertIn("await act('/cab', { path })", self.source)

    def test_panic_is_on_the_face(self):
        self.assertIn("act('/panic', {})", self.source)


class TestTheChassisWearsTheTheme(unittest.TestCase):
    """A real amp head, but one that belongs in whatever theme the app is wearing."""

    def test_no_colour_is_hardcoded_except_the_warning_red_and_its_ink(self):
        """Red is the one colour a theme cannot supply: a clip lamp and the switch that stops it.
        Everything else is a token, so a light theme gets a pale amp and a dark one a dark amp."""
        literals = set(re.findall(r"#[0-9a-fA-F]{3,8}\b", face()))
        self.assertEqual({"#e5533d", "#fff"}, literals, f"hardcoded colours: {sorted(literals)}")

    def test_the_panels_and_ink_come_from_app_tokens(self):
        source = face()
        self.assertGreaterEqual(source.count("var(--ui-"), 30, "the face stopped using theme tokens")
        for token in ("var(--ui-bg-primary)", "var(--ui-bg-secondary)", "var(--ui-text-primary)",
                      "var(--ui-stroke-primary)", "var(--ui-accent)"):
            self.assertIn(token, source)

    def test_the_face_does_not_paint_its_own_background(self):
        self.assertNotRegex(face(), r"background(Color)?:\s*'(white|black|#[0-9a-fA-F]{6})'")

class TestTheFaceLooksLikeAnAmp(unittest.TestCase):
    """A head with a badge and valves, not a form with knobs stuck to it.

    The tubes are the part a screenshot cannot check for you, so the tests pin the two rules that
    make them honest: six of them, and lit only when the engine is both open and armed.
    """

    def test_there_is_a_grille_with_the_name_on_it(self):
        source = face()
        self.assertIn("function Grille(", source)
        self.assertIn("function Valve(", source)
        self.assertIn("children: 'Amphead'", source)
        self.assertIn("valve · 100", source)

    def test_six_valves_stand_on_the_floor_of_the_grille(self):
        self.assertIn("[0, 1, 2, 3, 4, 5].map", face())

    def test_a_valve_lights_from_the_meter_and_not_from_the_switch(self):
        """Heat is the meter, squashed over the top 40 dB, so quiet playing still shows."""
        source = face()
        self.assertIn("const heat = Math.max(0, Math.min(1, (toDb(peak) + 40) / 40))", source)
        self.assertIn("const warm = heat > 0", source)

    def test_the_valves_are_dark_unless_the_engine_is_open_and_armed(self):
        source = face()
        self.assertIn("const lit = running && armed", source)
        self.assertIn("jsx(Grille, { lit, heat })", source)
