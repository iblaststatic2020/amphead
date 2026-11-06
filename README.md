# AmpHead

Hermes as the amp head for a REAPER guitar rig. It starts the host, opens its own project tab, loads
the amp and drive, sets the knobs, and then stops in standby until the player switches the track live.

AmpHead never processes audio and never drives REAPER's UI. Everything it reads or writes goes through
a small file-queue bridge (`reaper/bridge.lua`) that runs inside REAPER, which means each write can be
read back and verified instead of assumed.

## What it does today

- Launches REAPER minimized and takes a rig tab it owns, leaving your song projects alone.
- Parks the master at -60 dB before arming anything. The track is born disarmed and unmonitored, and
  only an explicit confirm arms it.
- Builds and inspects a chain by name, with `expect` guards so a write cannot land in the wrong tab.
- Answers tone requests in plain language: `"an amp that sounds like blink182"` resolves to a saved NAM
  preset, reports the capture and IR it uses, applies the knob moves, and reads the plugin back.

## Tone requests

The NAM Universal plugin keeps the loaded amp inside its state chunk, not on a parameter, so amp choice
is a saved preset rather than a knob. `amphead/tones.py` holds eight archetypes (pop punk, modern metal,
hard rock, lead, clean, edge of breakup, stoner fuzz, bass), each pointing at a preset in the library
plus the knob deltas that push it toward the request.

```
python -m amphead.tones --list
python -m amphead.tones "an amp that sounds like blink182"
python -m amphead.tones "drop C metal chug" --apply
```

`--apply` needs an open project that already has a NAM Universal on a track. It pushes the preset state,
waits for the plugin to settle, then reports the capture name and knob values read back from the plugin.

Library layout it expects:

- captures: `%USERPROFILE%\WaveMind\NAMU\Amps\**\*.nam`
- presets: `%USERPROFILE%\WaveMind\NAMU\*.preset`

## Status

Working and verified against REAPER 7.77: the bridge, the launch and standby rules, the arm safety
floor, and tone application. 110 unit tests, `hermes plugins validate` passes. Not packaged for
`hermes plugins install` yet, which only accepts a git URL, so the CLI is the entry point for now.

Two host facts that cost real time and are worth knowing before touching the bridge:

- FX chunk payloads must be wrapped at 128 characters. A chunk with 78-character lines is accepted and
  then ignored, and the readback looks identical to the input.
- A plugin-written chunk can contain `=` padding in the middle, so strip every `=` before decoding and
  re-pad afterwards.

## Layout

```
amphead/bridge.py     file-queue client: call, liveness, guards, batch
amphead/launch.py     find, start, stop, and health of the host
amphead/namstate.py   decode and rewrite NAM state chunks
amphead/tones.py      natural language to preset, with knob moves
amphead/tools.py      the plugin tools Hermes calls
reaper/bridge.lua     the script that runs inside REAPER
tests/                unit tests, no host required
```

## Development

```
python -m unittest discover -s tests -q
hermes plugins validate .
```

Tests run without REAPER. Only the integration paths in `tools.py` touch a live host.
