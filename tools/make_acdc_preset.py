"""Build NAMU/acdc_plexi.preset: a JMP 2203 rhythm capture through a Greenback 4x12, gain low.

The archetype table can only nudge knob values on a saved .preset, so a new sound needs a new
document. This one is derived from morningwood.preset (so every parameter the plugin expects is
present and sane) and then re-pointed at a Marshall capture and a Greenback cab, because "sound
like AC/DC" is not Morning Wood with the treble up: it is a master-volume Marshall kept clean on
the gain, lean in the bass, mid-forward, into a Greenback, with no pedal in front.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

NAMU = Path(r"C:\Users\Chris\WaveMind\NAMU")
SOURCE = NAMU / "morningwood.preset"
TARGET = NAMU / "acdc_plexi.preset"

AMP_FOLDER = r"Users\Chris\WaveMind\NAMU\Amps\Guitar\Marshall '77 2203 JMP Ground Zero MOAB"
AMP_FILE = "12b Marshall JMP77 GZA MOAB RAW Vintage M5.nam"
IR_FOLDER = r"Guitar\IR GREENBACK"
IR_FILE = r"Guitar\IR GREENBACK\Greenback.wav"

# The tonestack the amp sim shows, 0 to 10. Gain low on purpose: an AC/DC rhythm part is the power
# amp working, not a pedal, and every extra notch of input drive costs the string separation.
KNOBS = {
    "amp_pre_gain": 3.2,
    "amp_bass": 2.8,
    "amp_mid": 6.8,
    "amp_treble": 6.2,
    "amp_post_gain": 0.0,
    "oomph": 3.5,
    "sparkle": 4.5,
    "pedal_active": 0.0,
    "aggravator_active": 0.0,
    "instigator_active": 0.0,
    "cab1_active": 1.0,
    "cab2_active": 0.0,
    "impulseBlend": 0.0,
    "preGain": 0.0,
    "postGain": 0.0,
}


def main() -> int:
    tree = ET.parse(SOURCE)
    root = tree.getroot()
    root.set("presetName", "acdc_plexi")
    root.set("amp_profile_base", AMP_FOLDER)
    root.set("amp_profile", f"{AMP_FOLDER}\\{AMP_FILE}")
    root.set("impulse_base_folder1", IR_FOLDER)
    root.set("impulse_base_folder2", IR_FOLDER)
    root.set("impulse_file1", IR_FILE)
    root.set("impulse_file2", IR_FILE)

    params = {p.attrib["id"]: p for p in root.findall("PARAM")}
    for key, value in KNOBS.items():
        if key not in params:
            raise SystemExit(f"source preset has no {key!r} parameter, refusing to guess")
        params[key].set("value", f"{value:.6f}")

    ET.indent(tree, space="  ")
    tree.write(TARGET, encoding="UTF-8", xml_declaration=True)
    print(f"wrote {TARGET} ({TARGET.stat().st_size} bytes)")
    print(f"  amp: {AMP_FILE}")
    print(f"  ir:  {Path(IR_FILE).name}")
    print(f"  gain {KNOBS['amp_pre_gain']} bass {KNOBS['amp_bass']} "
          f"mid {KNOBS['amp_mid']} treble {KNOBS['amp_treble']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
