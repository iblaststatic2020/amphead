"""Turn a plain-language tone request into a NAM preset and push it into the live plugin.

The library is whatever sits in WaveMind's NAMU folder: nine saved .preset documents over 2,287
captured amps. There is no audio matching here and there never will be from a filename, so the
mapping is a curated table of archetypes, each pointing at the preset in this library that carries
that sound, plus the knob moves that push it closer.
"""

from __future__ import annotations

import re
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from . import bridge, namstate

NAMU = Path(r"C:\Users\Chris\WaveMind\NAMU")
PRESET_GLOB = "*.preset"


@dataclass(frozen=True)
class Archetype:
    """One sound a guitarist asks for by name, and the library preset that delivers it."""

    key: str
    title: str
    preset: str
    keywords: tuple[str, ...]
    tweaks: dict[str, float] = field(default_factory=dict)
    why: str = ""


ARCHETYPES: tuple[Archetype, ...] = (
    Archetype(
        key="pop-punk",
        title="Pop punk (blink-182, Green Day, Sum 41, The Offspring)",
        preset="punk_tone.preset",
        keywords=("blink", "blink182", "blink-182", "pop punk", "pop-punk", "punk", "green day",
                  "sum 41", "sum41", "offspring", "skate punk", "enema", "dookie", "bright punk"),
        tweaks={"amp_bass": -0.6, "amp_mid": -0.3, "amp_treble": 0.7, "oomph": 1.2},
        why=("Soldano HR50X over a Vintage 30 close mic. Punk lives on the bridge pickup with the "
             "bass rolled back so the palm mutes stay tight, treble up so the chords cut, and a "
             "little Oomph for the low-mid punch the double tracks need."),
    ),
    Archetype(
        key="pop-punk-hot",
        title="Pop punk, hotter second flavour (blink-182 bite, Dude Ranch edge)",
        preset="maybe_punk_tone_2.preset",
        keywords=("another", "second", "one more", "hotter", "brighter", "hot", "dude ranch",
                  "cheshire cat", "enema of the state", "old blink", "bite", "raw punk",
                  "more gain", "angrier", "less polished", "blink", "blink182", "blink-182"),
        tweaks={"amp_bass": 0.5, "amp_treble": -0.6, "sparkle": -0.8, "oomph": 0.4},
        why=("The same Soldano HR50X capture as punk_tone, but the front end is already driven hard "
             "and the treble sits near the top of the dial, so this is the rawer, angrier take. Bass "
             "comes up to put body back under the extra gain, treble and sparkle come down so the fizz "
             "does not slice on doubled tracks, and Oomph returns the thump the palm mutes lose when "
             "the input is pushed this hard. The Blackstar 57 on a Vintage 30 puts it forward and "
             "meaner than the close-mic punk_tone."),
    ),
    Archetype(
        key="modern-metal",
        title="Modern metal, metalcore, djent (chugging, drop tunings)",
        preset="gainzilla_sexy.preset",
        keywords=("metal", "metalcore", "djent", "chug", "drop c", "drop d", "heavy", "brutal",
                  "killswitch", "periphery", "architects", "slipknot", "modern metal"),
        tweaks={"amp_mid": 0.8, "amp_bass": -0.3, "amp_treble": 0.4},
        why=("Gainzilla into a blended cab IR. Mids stay up because the guitars need to sit under "
             "the vocal, and the bass comes down so the low end belongs to the bass guitar."),
    ),
    Archetype(
        key="hard-rock",
        title="Hard rock and classic crunch (AC/DC, Guns N' Roses, 80s Marshall)",
        preset="morningwood.preset",
        keywords=("hard rock", "classic rock", "rock", "acdc", "ac/dc", "gnr", "guns", "marshall",
                  "slash", "80s", "crunch", "riff"),
        tweaks={"amp_mid": 0.5, "amp_treble": 0.2},
        why=("Morning Wood with a Vintage 30 behind it. Gain sits around 4 so the low strings stay "
             "articulate and the mids carry the riff instead of the distortion."),
    ),
    Archetype(
        key="lead",
        title="Saturated lead and solo voice",
        preset="is_this_the_one.preset",
        keywords=("lead", "solo", "singing", "sustain", "shred", "satriani", "vai", "petrucci",
                  "melodic lead", "neck pickup"),
        tweaks={"amp_mid": 0.6, "amp_treble": -0.4, "sparkle": 0.5},
        why=("Soldano with the pre gain at 20 and the Instigator pushing it. Treble comes down a "
             "touch because a solo tone gets harsh fast once it is doubled."),
    ),
    Archetype(
        key="clean",
        title="Clean, jazz, chorus, ambient (Roland JC, Fender sparkle)",
        preset="clean_ring.preset",
        keywords=("clean", "jazz", "jazz chorus", "jc", "chorus", "ambient", "sparkle", "fender",
                  "pedal platform", "shimmer", "ballad"),
        tweaks={"amp_treble": 0.4, "sparkle": 1.0},
        why=("Jazz Chorus channel 2 with a Bonsai set neutral. The amp is a pedal platform, so the "
             "gain stays out of the way and the sparkle carries the top end."),
    ),
    Archetype(
        key="edge-of-breakup",
        title="Blues, indie, edge of breakup",
        preset="mutt_clean.preset",
        keywords=("blues", "indie", "breakup", "edge of breakup", "crunchy clean", "country",
                  "sr", "strat", "light drive", "kemper blues"),
        tweaks={"amp_mid": 0.7, "amp_pre_gain": 0.8},
        why=("Framus Cobra clean pushed with a touch of pre gain. This is the sound that cleans up "
             "when you pick soft and crunches when you dig in."),
    ),
    Archetype(
        key="stoner-fuzz",
        title="Stoner, fuzz, RAT-style saturation (QOTSA, Kyuss)",
        preset="something.preset",
        keywords=("stoner", "fuzz", "rat", "qotsa", "queens of the stone age", "kyuss", "doom",
                  "sludge", "desert rock"),
        tweaks={"amp_mid": -0.5, "amp_bass": 0.8, "amp_treble": 0.5},
        why=("Soldano Turbo with the RAT in front. Bass up and mids scooped just enough to let the "
             "fuzz sit on top instead of turning into mush."),
    ),
    Archetype(
        key="bass",
        title="Bass guitar (Ampeg-style low end)",
        preset="clean_bass.preset",
        keywords=("bass", "bass guitar", "fingerstyle", "ampeg", "sansamp", "low end", "roundwound"),
        tweaks={"amp_mid": 0.6},
        why=("Orange AD200B capture with an 8x10 IR. The mids come up a little so the bass reads on "
             "a phone speaker, which is where most of these mixes get checked."),
    ),
)

# Words a riff on the archetype name: they narrow the search but are never required to match.
FILLER = {"sounds", "sound", "like", "give", "me", "an", "a", "the", "amp", "tone", "tune",
          "guitar", "please", "want", "something", "that", "in", "style", "of", "for"}


def normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", text.lower()).strip()


def tokens(query: str) -> set[str]:
    raw = normalise(query)
    glued = raw.replace(" ", "")
    words = {w for w in raw.split() if w and w not in FILLER}
    return words | {glued}


def score(arch: Archetype, query: str) -> int:
    """Phrases are searched in the whole sentence, single words against the tokens.

    The haystack has to be the sentence, not a set of tokens: joined from a set, two words that
    touch in the request ("dude ranch") can come out in any order and the phrase never matches.
    """
    raw = normalise(query)
    words = [w for w in raw.split() if w]
    hay = f"{raw} {raw.replace(' ', '')}"
    hits = 0
    for kw in arch.keywords:
        needle = normalise(kw)
        if not needle:
            continue
        if " " in needle:
            if needle in hay:
                hits += 3
        elif needle in words:
            hits += 2
        elif needle.replace(" ", "") in raw.replace(" ", ""):
            hits += 1
    return hits


def best(query: str) -> tuple[Archetype | None, list[tuple[int, Archetype]]]:
    ranked = sorted(((score(a, query), a) for a in ARCHETYPES), key=lambda p: -p[0])
    top = ranked[0][0]
    return (ranked[0][1] if top > 0 else None), ranked


def preset_path(arch: Archetype) -> Path:
    return NAMU / arch.preset


def clamp(value: float, low: float = 0.0, high: float = 10.0) -> float:
    return max(low, min(high, value))


def xml_for(arch: Archetype) -> tuple[str, dict[str, tuple[float, float]]]:
    """The preset document with this archetype's knob moves applied.

    Returns the XML plus the knob changes that actually landed, old value to new value.
    """
    root = ET.fromstring(namstate.preset_xml(str(preset_path(arch))))
    params = {p.attrib["id"]: p for p in root.findall("PARAM")}
    changed: dict[str, tuple[float, float]] = {}
    for knob, delta in arch.tweaks.items():
        node = params.get(knob)
        if node is None:
            continue
        before = float(node.attrib.get("value", "0") or 0)
        after = clamp(before + delta)
        if abs(after - before) < 1e-9:
            continue
        node.set("value", f"{after:.6f}")
        changed[knob] = (before, after)
    return ET.tostring(root, encoding="unicode"), changed


def root_of(arch: Archetype) -> dict[str, str]:
    """The capture and cab a preset points at, as file names, straight from the preset document."""
    attrs = dict(ET.fromstring(namstate.preset_xml(str(preset_path(arch)))).attrib)
    return {"amp": Path(attrs.get("amp_profile", "")).name,
            "ir": Path(attrs.get("impulse_file1", "")).name,
            "amp_path": attrs.get("amp_profile", "")}


def describe(arch: Archetype) -> str:
    root = root_of(arch)
    return f"{arch.title}\n  preset: {arch.preset}\n  amp:    {root['amp']}\n  IR:     {root['ir']}"


class NoRig(RuntimeError):
    """No open project carries a NAM Universal, so there is nowhere to load a tone."""


@dataclass
class Applied:
    project: str
    track: int
    fx: int
    amp: str
    knobs: dict[str, str]
    changed: dict[str, tuple[float, float]]
    expected_amp: str


def live_project_with_nam() -> tuple[str, int, int] | None:
    """Find an open tab that already has a NAM Universal on it, and its track and fx index."""
    for tab in bridge.tabs_list():
        path = tab.get("path") or ""
        if not path:
            continue
        try:
            count = int(bridge.call("TRACKS", expect=path)["count"])
        except (bridge.ReaperError, KeyError, ValueError):
            continue
        for index in range(1, count + 1):
            for fx in bridge.fx_list(track=index, expect=path):
                if "NAM" in (fx.get("name") or "").upper():
                    return path, index, int(fx["i"])
    return None


def apply_to_live(arch: Archetype) -> Applied:
    target = live_project_with_nam()
    if target is None:
        raise NoRig("no open project has a NAM Universal on a track")
    project, track, fx = target

    xml, changed = xml_for(arch)
    current = bridge.call("FX_CHUNK", track=track, fx=fx, expect=project)["chunk"]
    blob = namstate.replace_xml(namstate.decode(current), xml.encode("utf-8"))
    bridge.call("FX_SETCHUNK", track=track, fx=fx, chunk=namstate.encode(blob), expect=project)
    time.sleep(1.2)

    after = bridge.call("FX_CHUNK", track=track, fx=fx, expect=project)["chunk"]
    amp = Path(namstate.attrs_of(after).get("amp_profile", "")).name
    listing = bridge.fx_params(track=track, fx=fx, expect=project)
    knobs = {p["name"]: p["fmt"] for p in listing
             if p["name"] in ("Amplifier Bass", "Amplifier Mid", "Amplifier Treble", "Oomph",
                              "Sparkle", "Amplifier Input Gain")}
    expected = Path(ET.fromstring(namstate.preset_xml(str(preset_path(arch)))).attrib["amp_profile"]).name
    return Applied(project=project, track=track, fx=fx, amp=amp, knobs=knobs, changed=changed,
                   expected_amp=expected)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--list" in args:
        for a in ARCHETYPES:
            amp = Path(ET.fromstring(namstate.preset_xml(str(preset_path(a)))).attrib["amp_profile"]).name
            print(f"{a.key:16s} {a.preset:24s} {amp}")
        return 0
    apply = "--apply" in args
    args = [a for a in args if a != "--apply"]
    query = " ".join(args) or "blink-182"
    arch, ranked = best(query)
    if arch is None:
        print(f"No preset in the library matches {query!r}. Closest options:")
        for _, a in ranked[:4]:
            print(" -", a.title)
        return 2

    print(describe(arch))
    print(f"\nWhy: {arch.why}")
    if arch.tweaks:
        moves = ", ".join(f"{k} {v:+.1f}" for k, v in arch.tweaks.items())
        print(f"Knob moves from the saved preset: {moves}")
    if not apply:
        print("\nDry run. Add --apply to load it into the open NAM Universal.")
        return 0

    applied = apply_to_live(arch)
    print(f"\nLoaded into {Path(applied.project).name} track {applied.track + 1} fx {applied.fx}.")
    ok = applied.amp == applied.expected_amp
    print(f"Amp capture now: {applied.amp} ({'matches preset' if ok else 'MISMATCH, expected ' + applied.expected_amp})")
    print("Knobs now:", applied.knobs)
    if applied.changed:
        print("Tweaks applied:", {k: f"{a:.2f} to {b:.2f}" for k, (a, b) in applied.changed.items()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
