# How AmpHead is put together

Written down because the plugin now has enough parts that "where does this go" is a real
question. The rule underneath everything: **dependencies point down this list.** A module may
import anything above it and nothing below it. The bottom layer is pure numbers and pure numpy,
so it can be tested without an audio device, an engine, or a pane.

## Layers, top to bottom

| Layer | Files | Owns |
| --- | --- | --- |
| Faces | `desktop/plugin.js`, `dashboard/plugin_api.py`, `amphead/tools.py` | How a person or an agent drives the amp, and the words it answers in |
| Answer shape | `amphead/answers.py`, `amphead/faults.py` | Turning a handler's one-line answer into the host's JSON, and turning a failed load into the next step |
| Wire | `amphead/local_port.py`, `amphead/port.py` | The phone line to the engine, and the vocabulary both ends share |
| Process | `amphead/engine_main.py` | The engine process: start, stop, commands, status |
| Engine | `amphead/engine2.py`, `amphead/park.py` | The audio device, the blocks, the chain state, the counters, and the timer that parks a master nobody is playing |
| Measurement | `amphead/tune.py`, `amphead/analyse.py` | What the rig does when asked to prove it (soak, latency, spectrum) |
| Rig facts | `amphead/library.py`, `amphead/settings.py`, `amphead/lastchain.py`, `amphead/target.py`, `amphead/verdict.py` | Files on disk, the settings file, what was last played, which capture a word means, how to describe the rig's state |
| DSP | `amphead/nam.py`, `amphead/cab.py`, `amphead/tone.py`, `amphead/gate.py` | Sample math only: captures, cabinets, tone stack, gate |
| Numbers | `amphead/limits.py` | The ranges every layer has to agree on |
| Rig storage | `amphead/rigs.py` | The shape of a saved sound, the file it lives in, the line it travels as |

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

These are now built, kept here as the worked examples of the rule:

* **Saved rigs**: `amphead/rigs.py` owns the shape and the file; `engine_main.rig()` lands one;
  the tool and the routes call the same handlers; the face draws a chip per rig.
* **Auto-park**: `amphead/park.py` is a pure timer over the input peak, and `engine2.py` acts on
  it by parking the master and leaving the chain armed. The verdict says `PARKED`, which is a
  different sentence from `STANDBY` because the fix is different.
* **Better errors**: `amphead/faults.py` turns the exceptions the loaders actually raise into
  one sentence with the next step, attached in the engine's dispatch so the tool, the pane and
  the rig report all get it without knowing the module exists.

## Tests map the same way

`tests/` mirrors the layers: DSP tests need numpy only, rig-fact tests touch a temp folder, wire
tests stub the engine, face tests assert on the config the pane is built from.
