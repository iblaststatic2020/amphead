# How AmpHead is put together

Written down because the plugin now has enough parts that "where does this go" is a real
question. The rule underneath everything: **dependencies point down this list.** A module may
import anything above it and nothing below it. The bottom layer is pure numbers and pure numpy,
so it can be tested without an audio device, an engine, or a pane.

## Layers, top to bottom

| Layer | Files | Owns |
| --- | --- | --- |
| Faces | `desktop/plugin.js`, `dashboard/plugin_api.py`, `amphead/tools.py` | How a person or an agent drives the amp, and the words it answers in |
| Answer shape | `amphead/answers.py` | Turning a handler's one-line answer into the host's JSON |
| Wire | `amphead/local_port.py`, `amphead/port.py` | The phone line to the engine, and the vocabulary both ends share |
| Process | `amphead/engine_main.py` | The engine process: start, stop, commands, status |
| Engine | `amphead/engine2.py` | The audio device, the blocks, the chain state, the counters |
| Measurement | `amphead/tune.py`, `amphead/analyse.py` | What the rig does when asked to prove it (soak, latency, spectrum) |
| Rig facts | `amphead/library.py`, `amphead/settings.py`, `amphead/lastchain.py`, `amphead/target.py`, `amphead/verdict.py` | Files on disk, the settings file, what was last played, which capture a word means, how to describe the rig's state |
| DSP | `amphead/nam.py`, `amphead/cab.py`, `amphead/tone.py`, `amphead/gate.py` | Sample math only: captures, cabinets, tone stack, gate |
| Numbers | `amphead/limits.py` | The ranges every layer has to agree on |

## Rules that keep it honest

1. **One number, one home.** A range belongs in `limits.py`. The engine, the route, the tool and
   the knob all ask it. A knob that draws `-24..24` while the engine clamps `-12..12` is a bug
   waiting for a player to find.
2. **DSP never opens a device and never formats a sentence.** `nam.py` turns samples into
   samples. That is why the whole DSP layer runs under plain `unittest` with no interface
   plugged in, and why the golden capture fixture is a text file.
3. **Handlers decide, `answers.py` speaks.** A handler returns one line of prose (`key=value`
   fields, a verdict word first). The JSON the host sees is derived from that line, so the pane,
   the test and the agent can never disagree about what happened.
4. **Refusals are sentences, not exceptions that escape.** A handler that cannot do the thing
   answers `REFUSED: ...` with the reason a player can act on. Only the wire raises.
5. **The engine owns state, the faces own none.** The pane polls status; it does not remember
   whether the amp is armed. Reloading the face must not change the sound.

## Where the next things go

* **Saved rigs** (a named capture + cabinet + tone + trims): `amphead/rigs.py`, a rig-fact
  module with no device access, storing beside the settings file. Routes and tools call it;
  the face lists it.
* **Auto-park** (silence for N minutes parks the master): `engine2.py`, because it watches the
  meter the engine already has. The threshold is a setting, not a constant.
* **Better errors** (device held by another app, capture moved): `verdict.py`, so the pane, the
  status tool and the setup tool all say the same sentence with the same fix.

## Tests map the same way

`tests/` mirrors the layers: DSP tests need numpy only, rig-fact tests touch a temp folder, wire
tests stub the engine, face tests assert on the config the pane is built from.
