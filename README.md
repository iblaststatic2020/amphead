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
- Refuses to guess which plugin or knob you meant. A name that matches several controls comes back
  with every candidate listed, not with the first fuzzy hit.
- Answers tone requests in plain language: `"an amp that sounds like blink182"` resolves to a saved NAM
  preset, reports the capture and IR it uses, applies the knob moves, and reads the plugin back.

## Tone requests

Two paths, and the tool picks the cheaper one. If what you asked for is a sound the rig's own presets
already carry, `amphead_tone` applies that preset: one undo group, every knob read back, a diff in the
plugin's own units.

```
amphead_tone query="make it crunchier"
  why: asked for crunch or drive
  preset=crunch | changed=9 | already there=1 | 1.06s | one undo point
  VST3: NAM Universal (WaveMind) | Pedal Input Gain: 0 -> 5.76
  VST3: NAM Universal (WaveMind) | Amplifier Input Gain: -3.84 -> 5.76
  ...
```

That mapping is a small cue table, not a list of magic words: "crunchier" and "cleaner" match on their
stems, "more gain" is checked before "gain", and a request that names no sound ("something darker") is
not guessed at. Those fall through to the second path, because amp choice is not a knob: NAM Universal
keeps the loaded amp inside its state chunk, so a different amp is a different saved preset.
`amphead/tones.py` holds nine archetypes (pop punk, pop punk hot, modern metal, AC/DC, hard rock,
lead, clean, edge of breakup, stoner fuzz, bass), each pointing at a preset in the library plus the
knob deltas that push it toward the request. Artist, song and amp words route: "sound like AC/DC",
"angus young", "back in black" and "plexi" all land on the JMP capture through a Greenback, while
"hard rock" and "guns n roses" stay on the generic rig.

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

## Naming a plugin or a knob

`amphead/resolver.py` answers two questions: which plugin on this track, and which knob on that plugin.
The ladder is exact name, exact name without the `VST3:`/`JS:` prefix, every word present, every word
present allowing one vendor misspelling, then the query as a subsequence. Only the best rung that hit
counts, and if two controls land on it the answer is a refusal that names them:

```
gain             -> 'gain' matches 8 parameters: #2 Input Gain, #3 Output Gain,
                    #6 Pedal Input Gain, #7 Pedal Ouput Gain, #15 Amplifier Input Gain,
                    #8 Instigator Input Gain, #16 Amplifier Output Gain, #9 Instigator Output Gain.
                    Name one of them exactly.
output gain      -> Output Gain (#3)
pedal output gain-> Pedal Ouput Gain (#7)      # the plugin misspells it, the request does not
presence         -> no parameter matches 'presence'
```

Before any write the chain is re-read and the name is confirmed against its index, because FX indices
shift the moment something is added or removed. Writing to index 2 because it used to be the amp is
exactly the kind of wrong that sounds plausible.

## Building a rig from nothing

```
amphead_build amp="NAM Universal"
  built=True | rig=...\amphead\rig\rig.rpp | track=1 Gtr | amp=VST3: NAM Universal (WaveMind) at fx 0
             | chain=1 | db=-6 | armed=False | monitor=False | input=untouched (default 0) | seconds=1.31
             | next=the player flips Live
```

The tab is opened from a minimal `rig.rpp` that the plugin writes itself, because a path on disk is
the only identity every later write can be guarded against. Then the whole build is one batch: add the
track, load the amp, set the input if one was asked for, set the starting level, and arm only if
`arm=true`. The track index is worked out before the batch runs (current count plus one), so the build
costs one round trip and still checks the index REAPER reports back.

Two rules keep it from surprising the player. It never arms on its own: `arm=true` exists, but the
default leaves the rig in standby, because going live is the switch the user flips. And it never
deletes FX it did not add; a project that arrives with a chain gets those plugins named in the result
and left exactly where they are. `teardown` will only close a tab whose path is the rig's, and only
deletes the file after REAPER confirms the close.

Live on this machine: 1.31 s unarmed, 1.21 s armed, chain and `I_RECARM`/`I_RECMON` read back from
REAPER, existing tabs untouched.

## Turning a knob, and proving it moved

`amphead/rig.py` writes one parameter at a time and does not take the plugin's word for it:

```
read FX_INFO  ->  pick a mode  ->  clamp, and say so  ->  write in one undo group
              ->  read the parameter back  ->  accept only if it moved AND is a real number
              ->  if not, retry once in the other mode  ->  else refuse, showing both tries
```

```
amphead_set fx="NAM Universal" param="IR Blend" value=0.52
  set=True | param=IR Blend | wrote=0.52 (norm) | reads_back=0.5200000 | clamped=False

amphead_set fx="Shoeless" param="Tone" value=0.9
  REFUSED: Tone on VST3: Shoeless Drive Pedal refused the write:
           norm 0.9000 read back 5.0 (norm 0.500) then raw 0.9000 read back 5.0 (norm 0.500);
           value did not move
```

Two real failures on this machine shaped that order. ReaEQ's gain at 0.35 normalized and ReaFir's
output gain at 1.44 raw both accept the write and then format the value as `-inf`; ReaFir lands on the
normalized retry, ReaEQ does not and comes back as a refusal. A plugin that silently ignores a write
is the third case and is refused the same way.

Details that matter in use: `mode=auto` writes the normalized 0..1 value unless you name the plugin's
own unit (`dB`, `Hz`), because raw units differ per plugin and `%` is just another way to say
normalized. Clamping is reported, never hidden. Every write carries the active project as `expect`,
because the bridge refuses an unguarded write, and that guard is what keeps a write from landing in
whichever tab happens to be in front. Each attempt is appended to
`%LOCALAPPDATA%\hermes\amphead\log.jsonl` so a tone change leaves a paper trail.

## A preset, and the knobs it says it moves

`amphead/presets.py` turns intent into knobs. A preset file is plain JSON naming the plugin and the
knobs in words, with the value in the plugin's own units where that is clearer:

```json
{
  "name": "crunch",
  "about": "pedal in front, amp pushed, treble pulled back",
  "track_db": -6.0,
  "targets": {
    "NAM Universal": {
      "pedal active": 1,
      "pedal input gain": 5.8,
      "amplifier input gain": 5.8,
      "amplifier treble": 5.2,
      "sparkle": 6.0
    }
  }
}
```

Names resolve through the same ladder `amphead_set` uses, so `sparkle`, `Sparkle` and `SPARKLE`
are one knob. The whole preset is one undo group: pick a preset at random, hit undo once, and you
are exactly where you were. Then every knob is read back and compared with what was asked for, so a
plugin with a broken automation binding shows up as a refusal instead of a lie. A knob already where
the preset wants it is left alone and says so, because writing it would only add noise to the diff.

```
amphead_preset name="crunch"
  preset=crunch | changed=8 | already there=2 | 1.07s | one undo point
  VST3: NAM Universal (WaveMind) | Pedal Active: Off -> On
  VST3: NAM Universal (WaveMind) | Pedal Input Gain: 0 -> 5.76
  VST3: NAM Universal (WaveMind) | Amplifier Input Gain: 0 -> 5.76
  VST3: NAM Universal (WaveMind) | Amplifier Treble: 7.74 -> 5.2
  VST3: NAM Universal (WaveMind) | IR Blend: 0.5 -> 0.5 (already there)
  ...
```

`amphead_preset` with no name lists what is on disk. Three ship: `clean`, `crunch`, `lead`, ordered
by gain, and the tests hold them to that order. A preset that names a plugin missing from the rig is
not a failure of the whole call: the plugins that are there still get applied, and the missing one is
named in the summary. If nothing on the preset is present, nothing is written at all.

## A/B, and proving undo really undid it

`amphead_ab` writes the whole rig down and then argues from the record. A snapshot is the chain in
real order, every knob in raw, normalized and formatted form, and the track level. Taking one is two
read-only calls per plugin, so it costs nothing and cannot add an undo point.

```
amphead_ab label="before"
  snapshot=before | plugins=1 | knobs=36 | db=-6.0 | ...\snapshots\20261106-074836-before.json

amphead_preset name="lead"
amphead_ab label="after"
amphead_ab action="compare" label="before" other="after"
  ab | before -> after | differences=11
  VST3: NAM Universal (WaveMind) | Amplifier Input Gain: 0.0000000 -> 9.5999985
  VST3: NAM Universal (WaveMind) | Pedal Active: Off -> On
  ...

undo once, snapshot "undone", compare "before" against it:
  ab | before -> undone | differences=0 | identical: nothing moved
```

That last line is the point: undo either put every knob back or the tool says which one it missed.

Two things the real plugin taught this. It exposes two parameters both called `Bypass`, so knobs are
compared by index and a duplicated name is printed with its index (`Bypass [#0]`) rather than matched
by name and compared against the wrong twin. And the bridge's `FX_PARAMS` sweep stops at 64
parameters by default while NAM Universal has 36 knobs and other plugins have hundreds, so a snapshot
asks for the lot and the sweep now carries raw and normalized values, not just formatted ones.

`amphead_ab action="list"` shows what is on disk; labels resolve to the newest snapshot with that
name, and an unknown label is refused by name instead of comparing against whatever came first.

## The amp face (Hermes side)

The pane is the interface. It is a desktop plugin half that ships in this package
(`desktop/plugin.js`) and talks to `dashboard/plugin_api.py`, which reads REAPER directly.

| row | what it answers |
|---|---|
| verdict | one word: `READY`, `STANDBY`, `NO SIGNAL`, `CLIPPING`, `DEVICE CLOSED`, `REAPER NOT RUNNING`, `REAPER BUSY`, plus the reason in a sentence |
| input meter | live peak in dB with a latching clip light |
| LIVE switch | one button: arm and monitor at -18 dB, or disarm and park the master at -60 dB |
| chain | the real plugin list on the guitar track, in order, marked on or bypassed |
| amp | eight knobs as sliders, sitting where the host says they sit, with the host's own read-back beside each one |
| tones | `clean`, `crunch`, `lead`, and a free-text box that takes "make it crunchier" |
| latency, master, project | the numbers REAPER would show, on screen while REAPER is minimized |

Routes: `GET /api/plugins/amphead/state`, `GET /api/plugins/amphead/meter` (150 ms poll),
`POST /live`, `POST /panic`, `POST /tweak`, `POST /preset`, `POST /focus`. The pane polls `/state`
at 1.5 s and `/meter` at 150 ms; the status bar chip polls `/meter` at 400 ms and `/state` at 4 s.

The face is not a second opinion. `/state` runs the same `verdict.judge` the `amphead_ready` tool
runs, and every write route calls the same handler chat calls (`tools.handle_set`,
`tools.handle_tone`), so a slider drag and the sentence "more gain" cannot do different things.

Dragging a slider holds the value your finger chose until you let go, then writes through `/tweak`
as a normalized value and shows the plugin's own read-back when the state poll comes back. If the
bridge cannot report a normalized value, the row shows the number instead of a slider, because a
slider sitting in the wrong place is worse than no slider.

A chain row brings that plugin forward in REAPER (`FX_FOCUS`, plus last-touched so the chain window
scrolls to it) but only while REAPER's window is really on screen. When it is minimized or on the
hidden desktop the row is not clickable and answers in place: "un-minimize REAPER first". Restoring
a window the player put away is a UI decision they did not ask for.

An armed rig whose master is parked at -60 dB says `STANDBY`, not `READY`. A rig that would be
silent is not ready to play, and that rule lives in the judge, so the tool, the pane and the status
bar chip always agree.

The app loads `%LOCALAPPDATA%\hermes\desktop-plugins\amphead\plugin.js` and refreshes that copy from
`plugins/amphead/desktop/plugin.js` on its own 5 s poll, so saving the source hot-reloads the UI.

Three doors to the same face: an **Amp** row in the sidebar (route `/amphead`, renders in the main
pane like a built-in view), the command palette entry **AmpHead: open the amp**, and a right-side
pane named `amp` for layouts that have a right zone. A dB chip also sits in the status bar.

## Going live

`amphead_live` is the switch that makes the rig audible from inside Hermes.

```
amphead_live on=true confirm=true master_db=-20
amphead_live on=true confirm=true master_db=-20 input="1"
amphead_live on=false
```

`on=true` opens the audio device if REAPER parked it closed, sets the master to the level you asked
for, then arms and monitors the track. `confirm=true` is required because this is the step that
makes noise. `on=false` disarms and parks the master back at -60 dB.

`input` defaults to `0`, which means leave the track's input exactly as REAPER has it; the reply
reports the input REAPER actually holds (`input=3 | input_changed=False`). Name an input only when
you want the tool to move it: `input="1"` for mono input 1, `input="1+2"` for a stereo pair,
`input="none"` to clear it.

## Is it playable?

`amphead_ready` is the one call that answers that, and the pane paints the same word because both
read from `amphead/verdict.py`.

```
amphead_ready
amphead_ready settle_ms=1500
```

It checks the whole path in order: REAPER alive, audio device open, track armed, input selected,
chain loaded, chain passing audio, signal actually arriving, latency short enough to play. The
answer is one word plus the problems behind it:

```
verdict=READY | reason=armed, monitoring, 11.61 ms out, 1 plugin live | problems=none
track=1 | armed=True | monitor=True | input=1 | chain=1 | live_fx=1 | peak_db=-14.0 | out_ms=11.61
```

The words are `READY`, `STANDBY` (not armed), `NO INPUT` (armed but pointed at nothing), `NO CHAIN`
(nothing loaded, or everything bypassed, which is what panic leaves behind), `NO SIGNAL` (armed and
monitoring but the meter never moved), `CLIPPING`, `DEVICE CLOSED`, `REAPER NOT RUNNING` and
`REAPER BUSY`. It listens for 600 ms by default before it calls an input silent, so one quiet frame
cannot report a live rig as dead. Nothing in it writes.

## Panic

`amphead_panic` is the dead man's switch, and it is the one button the pane paints red.

```
amphead_panic
amphead_panic restore=true master_db=-20
```

Panic parks the master at -60 dB first, because that is the control no plugin can refuse. Then it
disarms the track and bypasses every slot in the chain, reading each one back. The reply is a list of
facts, not a claim:

```
panicked=True | master_db=-60.0 | bypassed=1 | bypassed_names=VST3: NAM Universal (WaveMind)
armed=False | monitor=False | meter=still live: the input stays visible even with the speakers silent
problems=none
```

If a plugin ignores the bypass, the rest still close and the reply names the one that did not:
`still passing: Stubborn; refused the bypass: Stubborn (fx 0 ignored bypass)`. There is no mute
involved on purpose: muting would kill the meter, and the meter is how the face proves the guitar is
still arriving while nothing reaches the speakers. `restore=true` un-bypasses the chain and lifts the
master, and deliberately leaves the track disarmed, so the Live switch stays the only way back to
sound.

## Watch it without a rig

A judge, or anyone who has no DAW, no interface and no guitar, can still see the thing work. On the
verdicts where there is nothing to play with (`REAPER NOT RUNNING`, `NO REAPER INSTALL`, `BRIDGE
GONE`, `REAPER BUSY`, `TWO REAPER`, `RIG TAB CLOSED`) the pane offers **see it work**, which swaps
its source for a canned rig (`amphead/demo.py`): a made-up session, the plugin's own slider moves, a
meter that runs, a tone load, a panic and a restore. The row then reads **demo rig, not your
hardware** with a leave button, and the status chip says `demo` rather than printing a level it did
not measure. It answers through the same `verdict.judge` as the real path, so what a visitor sees is
the real rules applied to a rig that is not there. It never turns itself on.

## Honest limits

- **Windows only at 0.1.0.** The launcher, the window handling and the resource-path install are
  Windows specific. macOS and Linux are unbuilt, not broken.
- **It needs REAPER installed.** It can launch REAPER, it cannot install it, and nothing works while
  a modal dialog owns REAPER's main thread.
- **No DSP, no amp model, no tuner.** It does not process audio and does not try to sound like
  anything by itself. Tone comes from the NAM captures and IRs you already have.
- **Latency is REAPER's.** The number it reports is what REAPER says the device is doing, not a
  measurement this plugin makes.
- **It does not touch your songs.** Every write is guarded to the rig tab it created, inside its own
  undo block. The only saves are to that tab's own file, on teardown.
- **Silence on the meter is ambiguous.** It reads the rig track's own peaks, so a disarmed input, a
  muted track, a closed device and a guitar still hanging on its stand all read the same -60 dB. The
  verdict names the state instead of trusting the meter.

## Status

Working and verified against REAPER 7.77: the bridge, the launch and standby rules, the arm safety
floor, tone application, the live switch, the readiness verdict, panic, the amp face backend, the
pane with its sliders and status-bar chip, and deep links that only fire while REAPER is on screen.
426 unit tests, `hermes plugins validate` and `hermes plugins validate --install-deps` both pass.

The demo rig is verified the same way: a live pass through `dashboard.plugin_api` with a throwaway
home turns the demo on (READY, moving meter, knob tweak, tone load, PANIC to `NO CHAIN`, restore to
READY) and off again (`REAPER NOT RUNNING`), and the pane's own button was clicked in the preview
harness to prove the face flips.

Verified in a fresh Hermes session: `amphead_live` reported `live=True | armed=True | monitor=True |
master_db=-20.0 | input=1 | clips_at=11.61`, and `amphead_status` read back `device=open`,
`buffer=512`, `rate=44100`, `out_ms=11.61`.

Install it into Hermes with `hermes plugins install iblaststatic2020/hermes-reaper-amp --enable`.

Two host facts that cost real time and are worth knowing before touching the bridge:

- FX chunk payloads must be wrapped at 128 characters. A chunk with 78-character lines is accepted and
  then ignored, and the readback looks identical to the input.
- A plugin-written chunk can contain `=` padding in the middle, so strip every `=` before decoding and
  re-pad afterwards.

## Catalog status

`CATALOG_READINESS.md` audits this repo against what Hermes documents a plugin to be and what the
catalog requires: what the docs allow, why a plugin that fronts another application is a supported
category, and every gap that blocks or will be raised in review, each with its fix and acceptance
check. Read it before cutting the catalog PR.

Short version: the plugin qualifies on shape, surfaces and the contest's own wording. The repo stays
private on purpose until submission, so the remaining work is an application declaration for REAPER, a
fuller disclosure, an unattended-run test, and a fresh pin cut at the end.

## Layout

```
amphead/bridge.py     file-queue client: call, liveness, guards, batch
amphead/launch.py     find, start, stop, and health of the host
amphead/namstate.py   decode and rewrite NAM state chunks
amphead/tones.py      natural language to preset, with knob moves
amphead/presets.py    preset files: knobs by name, one undo group, read back
amphead/snapshots.py  whole-rig snapshots and the diff between two
amphead/verdict.py    the one judge the tool, the pane and the chip all call
amphead/tools.py      the plugin tools Hermes calls
dashboard/plugin_api.py  the routes the pane reads and writes
desktop/plugin.js     the amp face: pane, sidebar row, palette entry, status bar chip
reaper/bridge.lua     the script that runs inside REAPER
tests/                unit tests, no host required
```

## Development

```
python -m unittest discover -s tests -q
hermes plugins validate .
```

Tests run without REAPER. Only the integration paths in `tools.py` touch a live host.
