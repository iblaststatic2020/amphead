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

## The amp face (Hermes side)

The pane is the interface. It is a desktop plugin half that ships in this package
(`desktop/plugin.js`) and talks to `dashboard/plugin_api.py`, which reads REAPER directly.

| row | what it answers |
|---|---|
| verdict | one word: `READY`, `STANDBY`, `NO SIGNAL`, `CLIPPING`, `DEVICE CLOSED`, `REAPER NOT RUNNING`, `REAPER BUSY`, plus the reason in a sentence |
| input meter | live peak in dB with a latching clip light |
| LIVE switch | one button: arm and monitor at -18 dB, or disarm and park the master at -60 dB |
| chain | the real plugin list on the guitar track, in order, marked on or bypassed |
| amp | the amp's own knob names with the values the host reports |
| tones | one button per archetype, so "blink182" is also a click |
| latency, master, project | the numbers REAPER would show, on screen while REAPER is minimized |

Routes: `GET /api/plugins/amphead/state`, `GET /api/plugins/amphead/meter` (150 ms poll),
`POST /live`, `POST /preset`.

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

## Status

Working and verified against REAPER 7.77: the bridge, the launch and standby rules, the arm safety
floor, tone application, the live switch, the readiness verdict, panic, and the amp face
backend. 169 unit tests, `hermes plugins validate` passes.

Verified in a fresh Hermes session: `amphead_live` reported `live=True | armed=True | monitor=True |
master_db=-20.0 | input=1 | clips_at=11.61`, and `amphead_status` read back `device=open`,
`buffer=512`, `rate=44100`, `out_ms=11.61`.

Install it into Hermes with `hermes plugins install iblaststatic2020/hermes-reaper-amp --enable`.

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
