# AmpHead without REAPER - feasibility study

Written 2026-11-07. Everything here was probed on this machine, not recalled.

## The question

Can AmpHead stop borrowing REAPER and just host the NAM plugin itself, so the
product is one app that runs on Windows, macOS and Linux?

## What was actually tested

Setup: `uv venv --python 3.11`, `pedalboard 0.9.26`, numpy. No REAPER in the loop.

| Probe | Result |
|---|---|
| `load_plugin("C:\Program Files\Common Files\VST3\NAM Universal.vst3")` | Loads. Type `VST3Plugin`. |
| Parameters exposed | 33, including `amplifier_input_gain`, `amplifier_bass`, `amplifier_mid`, `amplifier_treble`, `amplifier_output_gain`, `input_gain`, `output_gain`, `mono_input`, `hd_mode` |
| Process 5 s of 48 kHz audio | 9.1 ms, ~551x realtime (pass-through, no capture loaded) |
| Per 128-sample block call | 0.0113 ms, 0.43% of the 2.67 ms block budget |
| `reported_latency_samples` | 0 with no model loaded |
| `raw_state` | Readable and settable, 2240 bytes |
| `NeuralAmpModeler.vst3` | **Fails** to scan |
| `NAM Rig.vst3` | **Fails** to scan |
| `load_preset(our .preset XML)` | RuntimeError: "Plugin failed to load data from preset file" |
| `preset_data` | Read-only property, no setter |
| `save_preset` | AttributeError |
| `AudioStream` API | Duplex in/out, `buffer_size`, `input_device_names`, `dropped_input_frame_count`, `plugins.append(...)` runs the plugin inside the C++ callback |

Official surface, from the installed `pedalboard.load_plugin` docstring:
"VST3 format is supported on macOS, Windows, and Linux. Audio Units are
supported on macOS."

So: hosting a VST3 from Python works, costs almost nothing per block, and the
parameter names line up exactly with what the pane already reads from REAPER.
That is a real result, not a guess.

## Where it breaks

Loading a **capture** headless is the whole problem. The plugin state inside
`raw_state` looks like:

```
VC2! <u32 len> <?xml ...?><VST3PluginState><IComponent>1595.VMjLgXeA...==</IComponent></VST3PluginState>
```

The `IComponent` payload decodes only after mapping `.` to `/`, and the 1599
bytes it yields are not zlib, not raw deflate, not gzip. The codec is still
unknown. `load_preset` wants a binary `.vstpreset`, not the plugin's own XML
preset, and the plugin writes no accessible preset on disk. The settings file
`%APPDATA%/NAM Universal/NAM Universal.settings` holds only window position,
audio setup and a `filterState` blob, no capture name.

Consequence: with pedalboard alone, **amp choice is a GUI action**. That is a
dead end for an agent-driven product until the codec is cracked or we stop
going through the plugin.

Note this is the same class of problem `amphead/namstate.py` already solved for
REAPER's chunk, so it is a day of work, not a wall. But "a day" is a guess until
someone opens the blob.

Second, quieter risk: two of the three NAM front-ends on this machine refuse to
scan in pedalboard. VST3 hosting is plugin-by-plugin, so a shipped product would
inherit "works with NAM Universal, maybe not with the next build".

## Four ways to be REAPER-free

| Path | What it is | Cost | Risk |
|---|---|---|---|
| 1. REAPER everywhere | Keep the bridge, port `launch.py` off PowerShell, resolve REAPER paths per OS | 1-2 days | Low. REAPER ships Windows, macOS and Linux, and projects/RPP behave the same |
| 2. Python host | `pedalboard` VST3 + `AudioStream`, our own knob/undo/verdict layer | 1-2 weeks | Medium. State codec + VST3 scan variance + shipping Python to three OSes |
| 3. Native engine | NAM core (MIT, C API) in a small C++/Rust host, our own audio device layer | 3-6 weeks | Medium. No plugin dependency at all, full control, hardest to build |
| 4. Borrow a host | Drive Carla (or libpanel) headless over OSC | 1 week | Medium-high. Extra runtime dependency, OSC glue, its own bugs |

Path 2's per-block cost is not the risk (0.43% of budget). The risks are the
state codec, VST3 variance, and the fact that AmpHead's realtime behaviour would
be ours to get right on three platforms instead of REAPER's.

Path 3 has one strong argument: the model loading problem disappears, because we
read `.nam` files directly, which we already understand well enough to rewrite
state chunks.

## What actually dies if we leave REAPER

| Today | After |
|---|---|
| Undo groups via REAPER | Our own snapshot stack, `snapshots.py` becomes the primary store |
| A/B compare by snapshot diff | Trivial, we own both states |
| READY judge incl. project tab and master | Our meter and output gain, no project concept |
| `FX_FOCUS` deep link into the FX window | Gone, which is fine |
| Project file `amphead.rpp` | Our rig JSON |
| Two-take recording | Our WAV writer |
| Latency reported by REAPER | Our buffer size, reported honestly |

Roughly 60% of the codebase survives: `verdict.py`, `presets.py`, `tones.py`,
`snapshots.py`, `namstate.py`, the tool schemas, the pane. `bridge.py`,
`launch.py` and `reaper/` become obsolete.

## Recommendation

1. **Do not pivot before the contest.** Shipping is Wednesday. Path 1 is the
   cheap insurance and path 2 cannot be trusted until the state codec is open.
2. After submission, run a **one-day spike**: `AudioStream` duplex, NAM Universal,
   guitar to speakers, with our verdict and knob layer on top. If that holds,
   spend the next day on the state codec. That answers the whole question with
   real audio instead of a design document.
3. If the spike holds and the codec opens, v1.0 is a REAPER-free app. If either
   fails, port `launch.py` to macOS/Linux and be done: REAPER already runs on all
   three, so "OS agnostic" costs two days, not two months.

The honest summary: hosting NAM outside REAPER is proven cheap. Controlling it
headless is unproven by exactly one unknown, the plugin state codec.
