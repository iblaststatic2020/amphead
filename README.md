# AmpHead

Hermes as the guitar amp. Not a remote control for one: the plugin starts its own audio engine, the
engine owns the interface, and the capture, tone stack, cabinet, trims and master all run there. You
sit down, say "set up my crunch tone", and Hermes is the amp. Nothing else needs to be open.

## What it does

- Starts a low-latency audio engine of its own: one duplex stream on the interface, 256-frame blocks,
  44.1 kHz, about 16 ms round trip on the kernel-streaming driver this was measured against (10 ms of
  driver buffer plus the 5.8 ms block the callback fills).
- Loads a capture of a real amp (a `.nam` file made with Neural Amp Modeler) and a cabinet IR (a real speaker, measured), with the IR
  resampled to the engine's rate so a 44.1 kHz IR does not play 8.8% sharp at 48 kHz.
- Gives you the knobs a head has: bass, mid, treble, plus input gain (how hard the capture is driven)
  and output gain (how the cabinet is balanced against the room), and a master on top.
- Parks the master at -60 dB before anything is armed. The chain is born disarmed; only an explicit
  confirm arms it, and arming uses the master the amp already has, so nothing arrives louder than the
  knob says.
- Puts a noise gate in front of the capture, off by default (the bottom of the knob is `off`), with a
  hold and a slow close so a decaying chord fades instead of being chopped.
- Parks itself. After 15 minutes with nothing above -50 dB on the input, the amp parks its own
  master and says `PARKED`, rather than listening to an empty room all night. The chain stays
  armed, so raising the master brings it straight back.
- Measures its own timing. `TUNE` soaks every block size on the real device, picks the smallest one
  that ran clean, confirms it on a longer pass, and writes it to the settings file.
- Answers in plain language and reports what it read back: `amphead_ready` says `READY`, `STANDBY`,
  `NO SIGNAL`, `NO CHAIN`, `CLIPPING`, `DEVICE CLOSED`, `PARKED` or `ENGINE OFF`, with the reason.
- Keeps sounds. A rig is the capture, the cabinet, the three tone knobs, both trims and the gate,
  saved under a name and recalled in one press from the pane. The same rig is also one line of text
  (`amphead:lead | capture=...`), so a sound can be pasted to someone who has the same captures.
  The master is deliberately not part of a rig: loading a sound must never turn the room up.
- Explains a file it would not load. A capture for the wrong architecture, a moved folder, a wav
  that is not PCM: the answer keeps the exception and adds the next step, in the same sentence
  the pane and the agent both show.
- Has a dead man's switch. `amphead_panic` parks the master, disarms and bypasses every stage, then
  proves the input is still arriving while nothing reaches the speakers.

## Install

```
hermes plugins install iblaststatic2020/amphead --enable
```

Accept the dependency prompt if Hermes asks: numpy and sounddevice come with the plugin, and the
engine runs in the interpreter Hermes installed them into. There is no second Python to set up, no
bootstrap script and no path to type.

From a source checkout the same install works directly, and the regression suite is fully headless,
so it runs on any machine with no audio device and no network:

```
pip install -e .
python -m unittest discover -s tests
```

It needs Hermes 0.22.0 or newer and Python 3.12 or newer, because the plugin runs on the interpreter
Hermes builds for itself. After the restart the installer asks for, open the AmpHead pane, press
SET UP, and pick a `.nam` capture and a `.wav` cabinet. `amphead_setup` says which piece is missing
rather than failing quietly.

## The amp face

`desktop/plugin.js` is the pane, drawn as an amp head: a chicken-head switch, the tone plate with the
capture and cabinet named on it, the knobs in a row, a meter, and the files. It talks to
`dashboard/plugin_api.py`, which talks to the same engine process chat talks to.

| row | what it answers |
|---|---|
| verdict | one word, plus a sentence naming the thing to fix |
| the switch | `SET UP` opens the engine silent, `STANDBY` is open and parked, `GO LIVE` arms at -6 dB, `CLOSE ENGINE` releases the device |
| the plate | tone named from the two files on the chain, with rate, block size, round trip and `TUNE` under it |
| files | capture and cabinet pickers, listing what is actually in the folders |
| the knobs | `GATE`, `IN`, `BASS`, `MID`, `TREBLE`, `OUT`, `MASTER`, each labelled in place |
| rigs | a chip per saved sound (press the name to load, press `del` twice to remove), a name field and `SAVE` |

Routes: `GET /state`, `GET /meter`, `GET /library`, `GET /input`, `POST /setup`, `/live`, `/teardown`,
`/master`, `/gate`, `/tune`, `/record`, `/input_gain`, `/output_gain`, `/knob`, `/tweak`, `/input`, `/capture`,
`/cab`, `/preset`, `/panic`, `/rigs`, `/rig`. The pane polls `/state` at 1.5 s and `/meter` at 150 ms.

Every write route calls the same handler chat calls, so a knob drag and the sentence "more low end"
cannot do different things, and `/state` runs the same `verdict.judge` as `amphead_ready`.

## The knobs, in order

```
guitar -> gate -> input gain -> capture (.nam) -> bass/mid/treble -> cabinet (IR) -> output gain -> master
```

The gate is first because it is the only stage that can hear the guitar before the capture makes the
hiss louder. It is off at the bottom of its travel: a gate that arrives switched on eats the quiet
part of a first take.

Input gain is a tone decision as much as a level one, so it is not the master. Output gain sits after
the cabinet so two captures can be balanced without moving the master off a level known to be safe.
Both trims are plus or minus 24 dB and clamped in the engine as well as the route: a number that
arrives from somewhere unexpected cannot push the DSP past what it can hold.

## Settings: the parts no code can guess

AmpHead works most of its own world out. It finds the interface by shape, remembers the last capture
and cabinet you played, and picks a device pair itself. Three things it genuinely cannot guess: where
you keep your files, which input on your interface is the guitar, and how hard your driver wants to be
pushed. Those live in one JSON file:

| Platform | File |
| --- | --- |
| Windows | `%APPDATA%\amphead\settings.json` |
| macOS | `~/Library/Application Support/amphead/settings.json` |
| Linux | `$XDG_CONFIG_HOME/amphead/settings.json` |

```json
{
  "capture_dirs": ["D:/Tones/NAM"],
  "cab_dirs": ["D:/Tones/IRs"],
  "engine_python": "",
  "audio": {
    "input_device": 37,
    "output_device": 38,
    "input_channel": 1,
    "samplerate": 44100,
    "blocksize": 256,
    "latency_ms": 5.0
  }
}
```

Nothing has to be set. An empty file means the usual folders (the Neural Amp Modeler convention under
Documents, plus the IR library folders), the first input channel, 44.1 kHz, 256 frames, and the 5 ms
per direction that held realtime on the machine this was measured on. The file exists so a guess can
be corrected without editing code.

Left to itself the engine picks the kernel-streaming pair by shape rather than by brand: one input
device and one stereo output belonging to the same interface, ignoring virtual cables and loopbacks
(VB-Audio, Steam streaming, anything called "cable"), and only falling back to the system default,
usually a shared WMME pair and roughly ten times slower, when no interface exposes that shape.
Changing any `audio` value takes effect the next time the engine opens.

`input_channel` counts from 1, the way an interface labels its sockets. The `AMPHEAD_CAPTURE_DIR`,
`AMPHEAD_CAB_DIR`, `AMPHEAD_ENGINE_PYTHON` and `AMPHEAD_LATENCY_MS` variables are still honoured and
win over the file.

The pane does the hunting, on a press. When a picker has nothing to show it names the folders it read
and offers one button; that button walks Documents, Downloads, Desktop, Music and the cloud folders
inside them, four levels down, lists the folders that actually contain `.nam` or `.wav` files with
counts, and writes the one you pick into the settings file, so the list fills without an environment
variable. Nothing is scanned before that press, in the pane or from chat. In chat the same walk is
`amphead_settings action="find"`, which runs only when you ask for it; `action="set"` writes your
choice and keeps everything else, and `action="show"` reports what is set, whether each folder exists,
and where each value came from. Read-only unless you say `set`.

## Asking for a sound

`amphead_aim` is for the thing an agent is actually good at: looking up how a record was made, then
finding out whether this machine can make it. It is not a button labelled with a band name, and it
never says it made you sound like one.

Ask in the ordinary way ("set me up with the AC/DC rhythm sound"), and the agent researches the rig
first: amplifier family, cabinet, speakers, microphone. It then calls:

```
amphead_aim artist=AC/DC amp=plexi cab=greenback
```

The tool ranks the captures and cabinets already on your disk by name and answers with the
strength of its claim:

```
AIMED AT AC/DC
amp=plexi | cab=greenback | confidence=family | matched=greenback | missing=amp:plexi
capture: no capture in the library answers that rig
cab: cab matched: Greenback
would_load_cab=Greenback
```

`confidence=exact` means every word of the researched rig was found in a file name.
`family` means part of it was, and `missing=` names the part that was not. That word is also drawn
on the amp face beside the sentence, so the pane cannot flatter the result.

`action="apply"` loads the pair it found and can set the trims and tone knobs in the same call:
`input_gain_db`, `output_gain_db`, `bass`, `mid`, `treble`.

There is a second action for when you have a recording and want to know how far off you are:

```
amphead_aim action=measure reference=<the record> take=<your render>
```

It compares the two in eight octave bands, prints the difference per band, the distance in dB, and
the curve that would close the gap. Measurement runs in the engine interpreter, because that is the
one with numpy; the plugin process stays dependency-free. Two guidelines worth knowing: compare
program material with program material (two raw impulse responses are not a useful comparison), and
treat the distance as a direction to move, not a verdict.

## Saved sounds

A rig is eight fields: capture, cabinet, bass, mid, treble, input gain, output gain, gate. A rig
saved on this machine also keeps the two file paths it was saved with. Loading tries those paths
first while the files still exist, then falls back to resolving the names against this machine's
library, so a capture imported from a folder outside the scanned library still loads after Setup.
A pasted rig carries only names and stays portable. Loading reports, stage by stage, anything it
could not find rather than refusing the whole sound.

```
amphead_rig action=save name=lead        # keep the sound on the chain right now
amphead_rig action=list                  # what is saved, plus the line for each
amphead_rig action=load name=lead        # put it back, knob by knob
amphead_rig action=forget name=lead      # delete a saved sound (action=delete is the same)
amphead_rig action=paste text="amphead:friend | capture=Thing;cab=Thing 4x12"
```

## A take

Press `rec` on the face, play, press `stop`. The amp writes what you hear, after the master, to a
24-bit wav in a `recordings` folder beside the settings file, named for the minute you played. The
strip under the knobs says the time as it runs and where the last take went, so the file is never
somewhere you have to go looking for. Once it is saved the strip offers two buttons: `▶` plays the
take in place through the app's own media scheme (the wav streams from disk with Range support, so
seeking works and nothing is uploaded), and `copy path` puts the full path on the clipboard, ready to
paste into chat, a DAW or a script. The strip also finds the newest take on disk after the engine has
been restarted, so a riff from yesterday is still one press from being heard.

```
amphead_record action=start name="verse riff"   # open a take now
amphead_record action=stop                      # close it, report path, length, peak
amphead_record action=state                     # is something being written?
```

Recording refuses when the amp is not live or the master is at the bottom, because a take after a
silent master is a wav full of nothing, and a player asking to record deserves the fix, not a file.

The audio callback never touches the disk: a block goes into a queue and a thread of its own writes
it, so a slow drive costs a counted dropped block instead of a hole in the take, and a full disk ends
the take with the error in the summary rather than a silently truncated file. Recording is only ever
the amp's own output. There is no second input, no monitoring path and no track to arm.

The wav is the handoff. Whatever comes next (a DAW, a clip sketcher, a song model) reads the file;
this plugin does not upload anything anywhere. See `ARCHITECTURE.md` for why that line is drawn
there.

## What this touches

The catalog asks for an explicit disclosure, so this is the whole list, not the flattering part.

AmpHead runs one Python process of its own, the engine, in the interpreter Hermes already runs the
plugin in. Its two dependencies, numpy and sounddevice, are declared in `pyproject.toml`, so a
normal `hermes plugins install` asks once and installs them: that is the only install step, and
there is no second interpreter to build or path to type. Hermes downloads those two pinned wheels
from PyPI, pinned as numpy 2.5.4 and sounddevice 0.5.6. That single download during install is the whole project's network access. The engine opens the audio interface (exclusive kernel streaming on Windows, CoreAudio on
macOS, ALSA on Linux) and keeps it open while the amp is on. It reads `.nam` captures and `.wav`
cabinets from the folders named in a settings file (`%APPDATA%\amphead\settings.json` and the
macOS/Linux equivalents), and writes that file only when you or your agent call
`amphead_settings action=set`. `action="find"` walks Documents, Downloads, Desktop, Music and the
cloud folders inside them, four levels deep, reading file names only, and runs only when a person
presses the search in the pane or asks for it in chat. It remembers the last capture and cabinet in
`~/.amphead2.json`, and writes `.wav` takes to a `recordings` folder beside its settings file when
you press record. Once running it makes no network calls, sends no telemetry, stores no credentials,
and does not launch, steer or read any other application: there is no other application.

`amphead_aim` reads the same file names, nothing else: no audio is opened unless you pass
`action="measure"`, which reads the two wav files you name and writes nothing. It stores one line of
its own answer in `~/.amphead2.json` so the face can show what was last asked for.

## Honest limits

- **Windows is the only platform it has been run on.** The macOS and Linux paths (device selection,
  settings location, engine interpreter discovery) have unit tests on this host and have not been run
  against a real install. The engine itself is portable Python plus PortAudio.
- **It needs an audio interface with a kernel-streaming or exclusive driver for low latency.** On a
  shared WMME pair it works but feels late; the verdict reports the round trip rather than hiding it.
- **Tone comes from what you own.** No amp is modelled in code: a capture and a cabinet are the sound.
  Without them it is a clean path with a tone stack.
- **Silence on the meter is ambiguous.** A guitar on its stand, a muted input and a closed device all
  read -60 dB, so the verdict names the state instead of trusting the meter.
- **Latency is measured, not promised.** The number is the driver's own report *plus one block*, because
  that block is also in the way; 16 ms round trip is what this interface does at 256 frames, not a floor
  for every machine. The face shows the sum, not the flattering half.
- **A saved device number is a slot, not a device.** Windows renumbers the PortAudio list when a
  USB interface is plugged in or removed, so the pair that was 37/38 can become 2/3. The engine
  checks the saved slots before opening, re-picks the interface pair when one is gone or the two
  endpoints speak different host APIs, and retries once if PortAudio still reports
  `Invalid device [PaErrorCode -9996]`; it does not hand a stale slot to the driver.
- **Nothing arms loud.** GO LIVE opens at whatever the master knob shows, and a fresh amp shows the
  floor, so the first press is silence you raise yourself rather than a fixed level.

## Where the maths comes from

`amphead/nam.py` is our own numpy implementation of the graphs a `.nam` file describes. The older
graph is one 1x1 rechannel, dilated convs with tanh, a 1x1 mix per layer, a summed head and a head
scale. The newer A2 graph adds a per-layer activation, a layer 1x1, and a causal head filter. No
upstream code is vendored and torch is not a dependency of anything that ships; the parity test
generates its golden files with the upstream `neural-amp-modeler` package and compares the streaming
path sample by sample, because an implementation that agrees with the reference is worth more than
one that looks like it. `SlimmableContainer` files also load: they hold complete WaveNet submodels,
so the engine runs the full-width one and leaves the quality dial to NAM Core. LSTM, gated
activations, FiLM conditioning and a separate output head are refused by name rather than guessed at.

Neural Amp Modeler and the `.nam` format belong to their authors (MIT). Nothing here is affiliated
with that project, and no capture or cabinet is redistributed with this repo.

## Sound and measurements

`ENGINE_MEASUREMENTS.md` holds the numbers: block budget, convolution cost per block for a 16 384-tap
and a 262 144-tap cabinet, the 117%-of-realtime bug that made a 4x12 cut in and out, and the duplex
stream that took the round trip from about 20 ms to about 10 ms. The short version: a cabinet longer
than one block is convolved in partitions, so a 262 144-tap 4x12 costs 1.83 ms of an 11.6 ms block
instead of 13.6 ms, one duplex stream replaced the two-stream queue whose cushion was the latency, and
the block went from 512 frames to 256 because the block, not the driver, was the part a player felt.

## Layout

```
amphead/engine2.py       the audio engine: device, stream, captures, cabinets, tone, master
amphead/engine_main.py   JSON-line server so another process can drive the engine
amphead/local_port.py    the phone line to that engine
amphead/port.py          the tiny vocabulary the tools, routes and tests share
amphead/verdict.py       the one judge the tool, the pane and the tests all call
amphead/library.py       find captures and cabinets, name the tone from the files
amphead/settings.py      the settings file and its environment overrides
amphead/nam.py           WaveNet (.nam) in numpy, parity-tested against PyTorch
amphead/cab.py           partitioned overlap-save FIR convolution, resampling
amphead/tone.py          bass/mid/treble biquads
amphead/tools.py         the plugin tools Hermes calls
amphead/play.py          play from a terminal with no window
dashboard/plugin_api.py  the routes the pane reads and writes
desktop/plugin.js        the amp face
tests/                   unit tests, no interface and no captures required
```

## Development

```
python -m unittest discover -s tests -q
hermes plugins validate .
```

Run the suite with an interpreter that has numpy and sounddevice: Hermes's own, or a venv you make
with `pip install numpy==2.5.4 sounddevice==0.5.6`. Without them the audio tests skip instead of
failing, so a thin interpreter gives a green run that proves less than it looks.
