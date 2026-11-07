# Side by side: one core, two rigs

Written 2026-11-07. "Side by side" here means two audio backends behind one
product, not two products.

## Why this is cheap

Measured on the current tree:

| Fact | Number |
|---|---|
| Modules that call `bridge.*` | 8 of 13 |
| Total `bridge.*` references | 141, mostly in `tools.py` (73) and `rig.py` (27) |
| `verdict.py` bridge references | 0 (pure) |
| `snapshots.py` / `presets.py` bridge references | 4 / 6 |
| Distinct REAPER commands used | about 20 |

`verdict.py` is already pure. `rig.py` is already the layer that hides FX
plumbing. `fake_reaper.py` is already a second implementation that the tests
run against, which proves the seam exists and is exercised. The work is
naming it, not inventing it.

## The seam

`amphead/port.py` defines what a rig must do. `amphead/reaper_port.py` wraps
the bridge. `amphead/local_port.py` wraps a NAM host we ship. `tools.py` talks
to the port and never to `bridge` directly.

| Operation | REAPER adapter | Local adapter |
|---|---|---|
| `rig_info()` | project tab, sample rate, block | our engine, sample rate, block |
| `tracks()` / `fx_list()` | `TRACKS`, `FX_LIST` | our chain JSON |
| `params(fx)` | `FX_PARAMS` (name, norm, raw, index) | plugin or NAM core params |
| `set(fx, index, value)` | `rig.set_param` | direct write, no undo group |
| `master()` / `set_master(db)` | `MASTER_VOL` | our output gain |
| `meter()` | `METER` input and output | RMS of the audio callback |
| `snapshot()` / `restore(blob)` | `FX_CHUNK` per FX | JSON we own |
| `undo(steps)` | REAPER undo | our snapshot stack |
| `record(seconds)` | REAPER record | our WAV writer |
| `focus(fx)` | `FX_FOCUS` while visible | raises "no FX window", handled in the pane |
| `ready()` | verdict inputs incl. project tab | verdict inputs minus project tab |

Two operations are honestly different, not just renamed: `undo` and `ready`.
The local rig has no host undo, so `snapshots.py` becomes the primary store and
undo becomes "restore the previous snapshot". `verdict.py` already takes its
inputs as arguments, so the project-tab check becomes a rig-state check.

## What it costs

| Phase | Work | Time |
|---|---|---|
| P0, seam only | `port.py`, `reaper_port.py`, `tools.py` rewired, tests green, no behaviour change | 3 to 5 hours |
| P1, audio | `local_port.py` with pedalboard `AudioStream`, fixed amp, knobs, meter, verdict | 1 day |
| P2, amp choice | either crack the NAM Universal state codec or read `.nam` through NAM core | 2 to 5 days |
| P3, packaging | per-OS binary, device selection UI, installer | 3 to 5 days |

P0 is safe to do before submission because it changes no behaviour and the
tests already cover both sides of the seam.

## Two hard facts from the probes

1. pedalboard hosts NAM Universal fine: 33 params, 551x realtime, 0.43% of a
   128-sample budget per call, duplex `AudioStream` with device enumeration and
   a dropped-frame counter.
2. NAM Universal's state blob is opaque. It is deterministic (reading twice is
   byte-identical, setting a param to its current value changes nothing), but a
   real change rewrites about 1090 of 1595 bytes, which is compression or
   scrambling, not a plain parameter struct. `load_preset` rejects the plugin's
   own XML preset and `preset_data` is read-only.

Fact 2 means the local backend should not depend on hosting NAM Universal.
Either crack the codec (a day, unproven) or use NAM core, which is MIT with a
C API and reads `.nam` files directly. pedalboard stays useful for the audio
device and plugin hosting, but amp choice is the part that has to be ours.

## Recommended order

1. **P0 now.** It buys the option and risks nothing. After it, `tools.py` does
   not know what a REAPER is.
2. Ship the contest on the REAPER backend.
3. After submission, P1 with a fixed amp. If guitar in, speakers out, knobs and
   verdict all hold, the product thesis is proven even before amp switching.
4. Then P2. Prefer NAM core over codec archaeology: it is a dependency we
   control instead of a format we reverse.
5. P3 last, because packaging three OSes is where the schedule usually dies.

The one rule that keeps this honest: the pane and the chat tools never learn
which backend is running. If a feature needs backend-specific words, it belongs
in the port, not in the UI.
