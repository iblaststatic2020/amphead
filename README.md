# AmpHead

Hermes as the guitar amp. Not a remote control for one: the plugin starts its own audio engine, the
engine owns the interface, and the capture, tone stack, cabinet, trims and master all run there. You
sit down, say "set up my crunch tone", and Hermes is the amp. Nothing else needs to be open.

## What it does

- Starts a low-latency audio engine of its own: one duplex stream on the interface, 512-frame blocks,
  44.1 kHz, about 10 ms round trip on the kernel-streaming driver this was measured against.
- Loads a NAM capture (a real amp, captured) and a cabinet IR (a real speaker, measured), with the IR
  resampled to the engine's rate so a 44.1 kHz IR does not play 8.8% sharp at 48 kHz.
- Gives you the knobs a head has: bass, mid, treble, plus input gain (how hard the capture is driven)
  and output gain (how the cabinet is balanced against the room), and a master on top.
- Parks the master at -60 dB before anything is armed. The chain is born disarmed; only an explicit
  confirm arms it, and arming uses -6 dB, measured on a -18 dBFS DI so it peaks near -3 dBFS.
- Answers in plain language and reports what it read back: `amphead_ready` says `READY`, `STANDBY`,
  `NO SIGNAL`, `NO CHAIN`, `CLIPPING`, `DEVICE CLOSED` or `ENGINE OFF`, with the reason.
- Has a dead man's switch. `amphead_panic` parks the master, disarms and bypasses every stage, then
  proves the input is still arriving while nothing reaches the speakers.

## The amp face

`desktop/plugin.js` is the pane, drawn as an amp head: a chicken-head switch, the tone plate with the
capture and cabinet named on it, the knobs in a row, a meter, and the files. It talks to
`dashboard/plugin_api.py`, which talks to the same engine process chat talks to.

| row | what it answers |
|---|---|
| verdict | one word, plus a sentence naming the thing to fix |
| the switch | `SET UP` opens the engine silent, `STANDBY` is open and parked, `GO LIVE` arms at -6 dB, `CLOSE ENGINE` releases the device |
| the plate | tone named from the two files on the chain, with rate, block size and round trip under it |
| files | capture and cabinet pickers, listing what is actually in the folders |
| the knobs | `IN`, `BASS`, `MID`, `TREBLE`, `OUT`, `MASTER`, each labelled in place |

Routes: `GET /state`, `GET /meter`, `GET /library`, `GET /input`, `POST /setup`, `/live`, `/teardown`,
`/master`, `/input_gain`, `/output_gain`, `/knob`, `/tweak`, `/input`, `/capture`, `/cab`, `/preset`,
`/panic`. The pane polls `/state` at 1.5 s and `/meter` at 150 ms.

Every write route calls the same handler chat calls, so a knob drag and the sentence "more low end"
cannot do different things, and `/state` runs the same `verdict.judge` as `amphead_ready`.

## The knobs, in order

```
guitar -> input gain -> capture (NAM) -> bass/mid/treble -> cabinet (IR) -> output gain -> master
```

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
    "blocksize": 512,
    "latency_ms": 5.0
  }
}
```

Nothing has to be set. An empty file means the usual folders (the Neural Amp Modeler convention under
Documents, plus the IR library folders), the first input channel, 44.1 kHz, 512 frames, and the 5 ms
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

An agent can do the hunting. `amphead_settings action="find"` walks Documents, Downloads, Desktop,
Music and the cloud folders inside them, four levels down, and lists the folders that actually contain
`.nam` or `.wav` files with counts. `action="set"` writes your choice and keeps everything else.
`action="show"` reports what is set, whether each folder exists, and where each value came from.
Read-only unless you say `set`.

## What this touches

The catalog asks for an explicit disclosure, so this is the whole list, not the flattering part.

AmpHead runs one Python process of its own, the engine, in `.engine` beside the plugin. That process
opens the audio interface (exclusive kernel streaming on Windows, CoreAudio on macOS, ALSA on Linux)
and keeps it open while the amp is on. It reads `.nam` captures and `.wav` cabinets from the folders
named in a settings file (`%APPDATA%\amphead\settings.json` and the macOS/Linux equivalents), and
writes that file only when you or your agent call `amphead_settings action=set`. `action="find"` walks
Documents, Downloads, Desktop, Music and the cloud folders inside them, four levels deep, reading file
names only. It remembers the last capture and cabinet in `~/.amphead2.json`. It makes no network
calls, sends no telemetry, stores no credentials, and does not launch, steer or read any other
application: there is no other application.

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
- **Latency is measured, not promised.** The number comes from the driver's own report plus what the
  stream callback sees; 10 ms round trip is what this interface does, not a floor for every machine.

## Sound and measurements

`ENGINE_MEASUREMENTS.md` holds the numbers: block budget, convolution cost per block for a 16 384-tap
and a 262 144-tap cabinet, the 117%-of-realtime bug that made a 4x12 cut in and out, and the duplex
stream that took the round trip from about 20 ms to about 10 ms. The short version: a cabinet longer
than one block is convolved in partitions, so a 262 144-tap 4x12 costs 1.83 ms of an 11.6 ms block
instead of 13.6 ms, and one duplex stream replaced the two-stream queue whose cushion was the latency.

## Layout

```
amphead/engine2.py       the audio engine: device, stream, captures, cabinets, tone, master
amphead/engine_main.py   JSON-line server so another process can drive the engine
amphead/local_port.py    the phone line to that engine
amphead/port.py          the tiny vocabulary the tools, routes and tests share
amphead/verdict.py       the one judge the tool, the pane and the tests all call
amphead/library.py       find captures and cabinets, name the tone from the files
amphead/settings.py      the settings file and its environment overrides
amphead/nam.py           NAM WaveNet in numpy, parity-tested against PyTorch
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
.engine/Scripts/python -m unittest discover -s tests -q
hermes plugins validate .
```

Both interpreters get through the suite: the engine interpreter runs the DSP and device tests, the
plugin interpreter runs the dashboard tests, and each skips the handful of tests that need what the
other one has.

Install it into Hermes with `hermes plugins install iblaststatic2020/hermes-reaper-amp --enable`
while the repo is still private, or from the catalog once it is public.
