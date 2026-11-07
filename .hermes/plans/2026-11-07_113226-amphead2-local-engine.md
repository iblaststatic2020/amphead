# AmpHead 2 Local Engine: Implementation Plan

> **For Hermes:** execute with subagent-driven-development, one task per subagent, gate reviews between phases.

**Goal:** make the `amphead2` rig real: a local engine process that hosts the NAM plugin, owns the audio device, and answers the same 31 verbs the REAPER rig answers, so the Amp 2 pane plays guitar with no DAW installed.

**Architecture:** a separate Python 3.11 process (`amphead2d`) in its own uv venv, hosting `NAM Universal.vst3` through `pedalboard`, running the audio in pedalboard's C++ callback (`AudioStream`), and serving a small JSON protocol on `127.0.0.1`. `amphead/local_port.py` becomes an HTTP client plus a launcher, exactly the shape `reaper_port.py` already has over the Lua bridge. Nothing above `port.py` changes: tools, presets, tones, snapshots, verdict and both panes keep asking the same verbs.

**Tech stack:** Python 3.11 (uv), `pedalboard 0.9.26`, `numpy`, `httpx` (client), stdlib `http.server` (engine side, no framework), `amphead.namstate` for the plugin state codec.

---

## 1. Current context, verified today

| Fact | Evidence |
|---|---|
| The seam exists and is enforced | `amphead/port.py` `Rig` protocol, 31 verbs; `tools.py` and `dashboard/plugin_api.py` never name `reaper` or `bridge` |
| `amphead2` is a shape, not an engine | `amphead/local_port.py` raises `NotBuilt` from every verb |
| NAM hosts headless and cheap | `NO_REAPER.md`: `load_plugin(NAM Universal.vst3)` works, 33 params, 0.43% of a 2.67 ms block, `AudioStream` duplex |
| Capture selection is the one unknown | `raw_state` is `VC2! <len> <xml>`; codec unopened (`NO_REAPER.md` §"Where it breaks") |
| The codec is probably already cracked | `amphead/namstate.py` decodes exactly that `VC2! … <PARAMETERS>` shape and repairs the length fields, for REAPER chunks |
| Host Python cannot host the engine | this box: `py -3` is 3.14.7, no `pedalboard` wheel story there; `uv 0.12.6` is installed, 3.11 is not |
| Vendors present | `C:\Program Files\Common Files\VST3\NAM Universal.vst3`, `NAM Rig.vst3` (does not scan), 34 `.nam` captures under `Documents\Guitar Amp Sims` and `Downloads` |
| `fx_params` shape the pane needs | `[{i, name, fmt, norm, raw}]` (`dashboard/plugin_api.py::_knobs`) |
| `state()` shape the judge needs | `bridge="live"`, `tracks>=1`, `project`, `device{buffer,rate,latency_out_ms,closed,inputs}` and `track_info` keys `armed/monitor/mute/recinput` |
| Verdict vocabulary is REAPER-specific | `verdict.py`: `REAPER NOT RUNNING`, `NO REAPER INSTALL`, `BRIDGE GONE`, `TWO REAPER`, `RIG TAB CLOSED` |

## 2. The decision this plan makes

**Engine = separate process, HTTP on loopback, pedalboard owns audio.**

Rejected alternatives and why:

- *In-process inside the Hermes plugin host*: host python is 3.14, pedalboard is not there; a crash in an audio callback would take the app with it; and the plugin process is not where a 3.11 venv lives.
- *Torch reference implementation (`neural-amp-modeler`) doing inference in a Python callback*: no plugin dependency, but Python-callback jitter with a GIL is the one thing guaranteed to glitch. Kept as **contingency C** below, not the plan.
- *Native NAM core host (C++)*: best runtime, 3-6 weeks. Contingency B, and the right v2 if the codec never opens.
- *Drive a DAW*: that is the build we already have.

## 3. Hole audit (do this before task 1)

| Hole | Why it bites | Closed by |
|---|---|---|
| Capture cannot be chosen headless | the amp is whatever the plugin had last; "make it sound like X" is a GUI action | T0.2, T2.3 |
| `verdict.py` speaks REAPER | a working local engine would be judged "NO REAPER INSTALL" and the pane would be red while audio plays | T1.6 |
| `local_port` has no transport | every verb raises; pane paints a stub | T1.2-T1.5 |
| Who starts and stops the engine | an orphaned engine holds ASIO and the next REAPER session gets no device | T1.4, T3.2 |
| ASIO is exclusive | REAPER and the engine cannot both hold the interface; the Amp pane must lose audio while Amp 2 plays | T3.1 (and the plan says so instead of pretending) |
| Boot safety is only enforced in `tools.py` | a second engine starting from a saved loud state would come up loud | T1.2, T3.3 |
| Master/track gain live in two places | `set_master` and `set_track_level` are separate verbs; the engine must have both or PANIC silently half-works | T1.2 |
| Undo has no owner | `tools.undo` calls `RIG.undo`, REAPER's stack is going away | T2.5 |
| Mute/recinput track fields | the judge reads `mute` and `recinput`; an engine that omits them changes verdicts by accident | T1.5 (explicit dict) |
| Chat is a second front end for tone | `handle_tone` already exists and the user expects "give me a JCM800" to work, but it applies through `tones.apply_to_live`, which builds a REAPER project | T2.4, T2.7 |
| Chat tools cannot target the local rig | `_engine_from_query` reads the pane's `?engine=` only; an `amphead_*` tool call always lands on the default rig | T1.8 |
| `tools.py` sentences name REAPER | "not applied: REAPER is not running" would be said to a player with a live local engine | T1.6 |
| Packaging | the engine needs 3.11 + wheels; the plugin cannot pip into the host | T4.1 |

**First-step audit (what every later step pays for):** the protocol in T1.1 is the expensive contract. Every verb `local_port` implements must exist there with the *same* argument names `port.Rig` uses, or later tasks will invent a second vocabulary. T1.1 must land before any engine code.

## 4. Gates

- **G0 (end of Phase 0, 1 day):** guitar, plugged into the interface, is audible through NAM Universal with REAPER closed, and a script prints the capture name out of `raw_state`. If either half fails, stop and pick contingency B or C before writing engine code.
- **G1 (end of Phase 1, +2-3 days):** the Amp 2 pane alone, REAPER closed: verdict goes `STANDBY` → `READY`, master parked at -60 dB until the ramp is moved, meter moves, LIVE/DISARM/PANIC all work.
- **G2 (end of Phase 2, +3-4 days):** changing the capture from the pane changes the amp; undo restores the previous amp and knobs; the three presets apply in one action.
- **G3 (end of Phase 3, +1-2 days):** engine crash, device busy, and double-launch all produce their own verdict words instead of a hang.

Gates are demo-capable states, not a freeze. The REAPER build stays shippable at every commit; `AMPHEAD_ENGINE` stays defaulted to `reaper`.

**Budget:** 9-12 working days to G2, 11-14 to G3. Deferrable without breaking G1: T2.4 capture library, T2.6 presets, all of Phase 4.

---

## Phase 0: Spike (1 day, decides everything)

### Task 0.1: engine venv

**Files:** create `.engine/.gitignore`, `scripts/engine_env.ps1`

**Step 1** run:
```
uv python install 3.11
uv venv .engine --python 3.11
.engine/Scripts/python -m pip install "pedalboard==0.9.26" numpy httpx
```
**Step 2** verify, expected versions printed:
```
.engine/Scripts/python -c "import pedalboard, numpy, httpx, sys; print(sys.version.split()[0], pedalboard.__version__, numpy.__version__)"
```
**Step 3** commit `.engine` ignored, `scripts/engine_env.ps1` committed.

### Task 0.2: prove the capture is readable

**Files:** create `scripts/probe_vst_state.py`, `tests/test_vst_state.py`

**Step 1** failing test (skipped when the VST is absent):
```python
def test_capture_name_comes_out_of_raw_state():
    state = probe.read_state()          # helper in scripts/probe_vst_state.py
    assert probe.capture_name(state)    # a str, e.g. "Twin Reverb"
```
**Step 2** run `py -3 -m pytest tests/test_vst_state.py -q` → FAIL (no module).
**Step 3** implement: load the plugin, take `raw_state`, run `amphead.namstate.decode` over it, find `<PARAMETERS>`, and print every element whose tag or attribute mentions `model`/`capture`/`ir`. The point is a report, not a guess.
**Step 4** run `py -3 scripts/probe_vst_state.py` and paste the report into `NO_REAPER.md` under a new "Codec" heading.
**Done when:** the report names the element that holds the capture path, or proves it is not in the XML (then try the `IComponent` blob with `namstate.replace_xml` semantics).

### Task 0.3: audible spike

**Files:** create `scripts/spike_play.py`

**Step 1** open `AudioStream(input_device_name=..., output_device_name=..., buffer_size=256, plugins=[plugin])`, read the list from `pedalboard.io.AudioStream.input_device_names`.
**Step 2** start the stream, play for 20 s, then print `stream.dropped_input_frame_count`, the peak of the last second, and `plugin.reported_latency_samples`.
**Done when:** the user hears the amp, `dropped_input_frame_count == 0`, latency printed in ms.

**G0 decision point.** If Task 0.2 fails: write one paragraph in `NO_REAPER.md` with the two contingencies (B: native `NeuralAmpModelerCore` helper, 2-4 weeks; C: torch reference in a worker thread with a ring buffer, 1 week + glitch risk) and stop for a decision.

---

## Phase 1: Engine process and the protocol (2-3 days)

### Task 1.1: the protocol is one file

**Files:** create `amphead2_engine/__init__.py`, `amphead2_engine/protocol.py`

Single source of truth for verb names and payload keys, imported by both the engine and `local_port`. No dict literals duplicated on either side.

```python
VERSION = 1

#: Every verb the engine answers, with the keys its reply guarantees.
VERBS = {
    "ping":     ("ok", "version", "pid"),
    "status":   ("running", "armed", "device", "chain", "master_db", "tracks"),
    "state":    ("bridge", "tracks", "project", "device", "reason"),
    "params":   ("params",),
    "set":      ("ok", "i", "value"),
    "master":   ("db",),
    "set_master": ("db",),
    "meter":    ("l", "r", "clip"),
    "inputs":   ("count", "index", "name", "track"),
    "arm":      ("armed", "monitor"),
    "disarm":   ("armed", "monitor"),
    "bypass":   ("ok", "i", "on"),
    "capture":  ("ok", "name", "path"),
    "snapshot": ("name", "at"),
    "restore":  ("ok", "name"),
    "undo":     ("ok", "restored"),
    "quit":     ("ok",),
}
```
**Test:** `tests/test_protocol.py::test_every_rig_verb_has_a_wire_name`, assert every non-deferred method on `port.Rig` maps to an entry in `VERBS`, with a `DEFERRED = {"fx_focus", "open_rig", "close_rig", "build", "teardown", "install", "record"}` set carrying the reason string.
**Run:** `py -3 -m pytest tests/test_protocol.py -q` → FAIL then PASS.
**Commit.**

### Task 1.2: session core, safety enforced at the source

**Files:** create `amphead2_engine/session.py`, `tests/test_engine_session.py`

Session owns: the plugin instance, `master_db`, `armed`, `monitor`, the param cache, the meter tap, the capture path, the undo stack. Two rules encoded in `__init__`, not in a caller:

```python
self.master_db = port_safe.SAFE_MASTER_DB   # -60.0, imported from amphead.port
self.armed = False
```
`arm()` refuses to leave the master above `SAFE_MASTER_DB` unless `allow_loud=True` is passed explicitly, and returns the same dict shape `reaper_port.arm` returns.

**Tests:** `test_a_fresh_session_is_parked_and_disarmed`, `test_a_fresh_engine_has_no_capture`, `test_the_first_sound_requires_a_capture`, `test_arm_without_allow_loud_keeps_the_master_at_the_floor`, `test_set_master_clamps_to_minus_sixty_zero`.
**Run:** `py -3 -m pytest tests/test_engine_session.py -q` with `pedalboard` absent: the session takes a `plugin_factory` injected, fake in tests.
**Commit.**

### Task 1.3: HTTP server

**Files:** create `amphead2_engine/server.py`, `tests/test_engine_server.py`

- binds `127.0.0.1:0` (random free port), prints `{"port":..,"token":..,"pid":..}` as its first stdout line
- every request carries `X-AmpHead2-Token`; a bad token is `403`, never a hint
- one request per verb in `protocol.VERBS`; unknown verb → `{"ok": false, "error": "no verb called X"}` with 200, so the client reports it as a sentence
- writes `%LOCALAPPDATA%\amphead2\engine.json` (pid, port, token, started_at) on start, removes it on `quit` and on `atexit`
- `/meter` reads the tap without locking the audio thread (single float pair written by the callback, `GIL`-safe assignment)

**Tests (stdlib `http.client` against a live server on a thread):** `test_bad_token_is_refused`, `test_unknown_verb_answers_in_words`, `test_engine_json_has_port_and_pid`.
**Commit.**

### Task 1.4: launcher

**Files:** create `amphead2_engine/launch.py`, `tests/test_engine_launch.py`

`ensure()`: read `engine.json`; if the pid is alive and `/ping` answers, return it. Otherwise spawn `.engine/Scripts/python -m amphead2_engine.server` detached, poll `/ping` up to 10 s, return the handle. `stop()`: `POST /quit`, wait for pid exit, delete `engine.json`. `host_alive()`: pid alive **and** `/ping` answers; those are different facts and the pane needs both.

**Tests:** `test_ensure_reuses_a_live_engine`, `test_ensure_replaces_a_stale_engine_json`, `test_stop_removes_engine_json`.
**Run:** `py -3 -m pytest tests/test_engine_launch.py -q`.
**Commit.**

### Task 1.5: `local_port` becomes a client

**Files:** rewrite `amphead/local_port.py`, create `tests/test_local_port.py`

Mirror `bridge.py`'s transport (retry, timeout, error mapping into `port.NoRig`/`RigTimeout`/`RigSaidNo`). Every verb returns the shapes the pane already consumes:

```python
def state(self) -> dict:
    return self._call("state")          # {"bridge": "live", "tracks": 1, "project": "amphead2 rig", ...}

def track_info(self, track: int = 1, **kw) -> dict:
    return {"armed": self._s.armed, "monitor": self._s.monitor, "mute": False, "recinput": 0}
```
`recinput` must be present and `>= 0` when armed: omitting it silently removes the NO_INPUT rule.

Keep `NotBuilt` only for the deferred set, with the same honest sentence style.

**Tests:** `test_rig_state_reports_live_and_one_track`, `test_track_info_carries_armed_monitor_mute_recinput`, `test_no_engine_raises_no_rig_with_a_sentence`.
**Run:** `py -3 -m pytest tests/test_local_port.py tests/test_plugin_api.py -q`.
**Commit.**

### Task 1.6: set the judge free of REAPER

**Files:** modify `amphead/verdict.py`, `amphead/reaper_port.py` (`state()` adds `"host": "reaper"`), `amphead2_engine/server.py` (`state()` adds `"host": "amphead2"`), `desktop/plugin.js` (`VERDICT_COLOR`)

Add engine words beside the DAW ones and pick them by `state["host"]`:
```python
ENGINE_NOT_RUNNING = "ENGINE NOT RUNNING"
ENGINE_GONE = "ENGINE GONE"
ENGINE_BUSY = "ENGINE BUSY"
DEVICE_BUSY = "DEVICE BUSY"
```
Mapping rule: `no-install` → `NO REAPER INSTALL` when host is `reaper`, `ENGINE NOT RUNNING` when `amphead2`; same for `gone`/`down`. Every word gets a colour in the pane table, because an unpainted verdict is unreadable.

Also sweep `amphead/tools.py` for hardcoded host sentences ("REAPER is not running, call amphead_setup first"). They become `RIG.host_name()`: "the amp engine is not running, call amphead_setup first" on `amphead2`, unchanged on `reaper`.

**Tests:** `tests/test_verdict_local.py::test_a_missing_engine_says_engine_not_running`, `::test_reaper_words_are_unchanged`, `tests/test_tools_words.py::test_not_live_sentences_name_the_rig_that_is_actual`, and update `test_plugin_face.py` for the new colour keys.
**Run:** `py -3 -m pytest tests -q` → all green (REAPER tests must not change behaviour).
**Commit.**

### Task 1.7: wire the pane

**Files:** modify `desktop/plugin.js` (Amp 2 registration: `ensure` before arm, `engine=amphead2`), `dashboard/plugin_api.py` (`/state` fallback text uses `appdecl` only for the reaper rig)

The Amp 2 LIVE button calls `/live`, which calls `tools.handle_live`, which must call `RIG.ensure()` first. Do not add a second path: one button, one chain.

**Verify:** harness at `desktop/preview/index.html` shows Amp 2 `STANDBY` with no engine, then `READY` after a real engine starts, master silent, ramp works.
### Task 1.8: the chat tools can pick a rig

**Files:** modify `dashboard/plugin_api.py` (`_engine_from_query`, the tool route), `desktop/plugin.js` (tool schema), create `tests/test_tool_engine.py`

Every `amphead_*` tool gains an optional `engine` argument (`amphead2` or blank for the default). The value is set into `_ENGINE` before `tools.handle_*` runs, so a chat sentence like "build a JCM800 tone on Amp 2" reaches the local engine. With no argument, keep today's behaviour.

**Rule:** a reply must say which rig it touched, because two rigs can both be up. `handle_*` already prints `bridge`/`host`; assert the local path prints `rig: amphead2`.

**Tests:** `test_a_tool_call_with_engine_amphead2_reaches_the_local_rig`, `test_a_tool_call_without_engine_keeps_the_reaper_default`.
**Run:** `py -3 -m pytest tests/test_tool_engine.py -q`.
**Commit.**

**G1 check, real run:** REAPER closed, `.engine` engine up, guitar in, pane READY, volume ramp audible, chat `amphead_status` says `rig: amphead2`.
**Commit and write the wiki log line.**

---

## Phase 2: Knobs, captures, undo, chat tone (3-4 days)

### Task 2.1: parameters in the pane's dialect
`server.py /params` returns `[{i, name, fmt, norm, raw}]` off `plugin.parameters` using pedalboard's `raw_value`/`__str__`. Test: names include `amplifier_input_gain`, count matches `len(plugin.parameters)`.

### Task 2.2: knob writes
`/set {i, norm}` sets `plugin.parameters[i].raw_value = denormalized`, returns the reading the plugin reports. Test: round-trip a 0.5 write, assert the returned `fmt` changed and a second read agrees.

### Task 2.3: capture load (the codec lands here)
**Files:** create `amphead2_engine/nam.py`, `tests/test_nam.py`.
Reuse `namstate.replace_xml` on `plugin.raw_state`, with the element found in T0.2, then assign `plugin.raw_state = patched`. Test with a saved `state.bin` fixture captured in T0.2 and two real `.nam` paths from `Documents\Guitar Amp Sims`: after load, `capture_name` reads back the new file's name, and `raw_state` round-trips through `namstate.decode` without `BadState`.
**Verify on hardware:** load `Chug.nam` then `01 Morning Wood.nam`, hear the change, confirm no click beyond one block.

### Task 2.4: the tone library can land on either rig

**Files:** modify `amphead/tones.py`, `amphead2_engine/server.py` (`/capture`, `/preset`), create `tests/test_tones_local.py`

`tones.apply_to_live` currently builds a REAPER project from the preset document. Split it: `tones.apply_to_live` keeps returning the same `Applied` dataclass, but the work moves behind two functions, `apply_to_reaper(arch)` (today's body) and `apply_to_engine(arch)` (new), chosen from `RIG.name`. `apply_to_engine` loads the archetype's `.nam` into the engine and sets `arch.tweaks` in one undo group, returning `amp_now` read back from the engine.

The archetype rows must also resolve to files that exist here: add a `.nam` path lookup with the existing `Documents\Guitar Amp Sims` root, and report a missing capture by name rather than skipping it.

**Tests:** `test_engine_apply_loads_the_capture_and_the_tweaks`, `test_engine_apply_reads_the_capture_back_by_name`, `test_a_missing_capture_is_named_not_skipped`.
**Verify on hardware:** `amphead_tone("sounds like blink182", apply=true, engine="amphead2")` in chat changes the sound, and the reply names the capture.

### Task 2.5: undo and snapshots
**Files:** create `amphead2_engine/stack.py`, `tests/test_stack.py`.
Snapshot = `{capture, params[], master_db}`; `undo` pops and applies in one pass. Tests: `test_undo_restores_the_capture_too`, `test_undo_on_empty_stack_says_nothing_to_undo`.

### Task 2.6: the three presets
**Files:** modify `amphead/presets.py` to resolve rows per rig; `server.py /preset`.
Test: applying `crunch` changes input gain, bass, mid, treble and returns one sentence naming what changed.

### Task 2.7: chat builds a tone end to end

**Files:** create `tests/test_chat_tone.py`

One test drives the whole path with the engine stubbed: `amphead_tone({"query": "an amp that sounds like blink182", "apply": True, "engine": "amphead2"})` must end with a `capture_now` that equals the archetype's capture, a knob list that is not empty, and no REAPER sentence anywhere in the reply. A second test asks for a sound the library does not carry and asserts the reply names the closest four archetypes instead of inventing one.

**G2 check:** from the pane alone, swap amp, A/B it, undo it, apply the three preset buttons. Then from chat, with the pane closed: `amphead_tone` on amphead2 lands the capture and the knobs, and the pane shows the same capture when reopened.

---

## Phase 3: Safety and hostile reality (1-2 days)

### Task 3.1: device exclusivity, said out loud
Engine start with the interface busy → `DEVICE BUSY`, reason sentence naming REAPER and the fix ("close REAPER, or use the Amp pane"). Test with the device name stubbed busy.

### Task 3.2: crash and recovery
`host_alive()` false while the pane is open → verdict `ENGINE GONE`; LIVE restarts it. Test: kill the engine pid mid-session, assert the next `/state` says ENGINE GONE within one poll.

### Task 3.3: boot safety under every entry point
Tests: `test_second_engine_does_not_come_up_loud` (engine.json has a saved master of -6 from a previous session; a fresh engine must still start at -60), `test_arm_refuses_loud_without_the_flag`.

### Task 3.4: honest numbers
Report `buffer`, `rate`, `latency_out_ms` from pedalboard's stream, not from a table. Test: the pane's DEVICE row equals what the engine reports.

---

## Phase 4: Packaging (2 days, after G2)

### Task 4.1: install script
`scripts/install_engine.ps1`: create `%LOCALAPPDATA%\amphead2\engine` venv with uv, install pinned wheels, copy `amphead2_engine`, write version file. Test: run on a clean folder, `python -m amphead2_engine.server --selftest` prints its version and exits 0.

### Task 4.2: the plugin declares the dependency
`plugin.yaml` gains an optional `engine` block (version + path), README gains a "No DAW" section. Test: `tests/test_plugin_yaml.py` asserts the block parses and the version matches `protocol.VERSION`.

### Task 4.3: macOS/Linux
Out of scope for this plan. Note in `NO_REAPER.md`: pedalboard AudioStream device naming differs, CoreAudio aggregates, ALSA/PipeWire buffer sizes; plan a second spike there, not a port-by-hope.

---

## Files likely to change

Create:
- `amphead2_engine/{__init__,protocol,session,server,launch,nam,stack}.py`
- `scripts/{engine_env.ps1,probe_vst_state.py,spike_play.py,install_engine.ps1}`
- `tests/{test_protocol,test_engine_session,test_engine_server,test_engine_launch,test_local_port,test_verdict_local,test_nam,test_stack}.py`

Modify:
- `amphead/local_port.py` (rewrite), `amphead/verdict.py`, `amphead/presets.py`, `amphead/tones.py`, `amphead/reaper_port.py` (add `host` to state)
- `dashboard/plugin_api.py` (fallback text, ensure-before-arm), `desktop/plugin.js` (verdict colours, Amp 2 live path)
- `plugin.yaml`, `README.md`, `NO_REAPER.md` (codec findings)

Unchanged on purpose: `bridge.py`, `launch.py`, `reaper/`, `namstate.py`, both panes' layout, `AMPHEAD_ENGINE` default `reaper`.

## Validation

```
uv run --python 3.11 -- .engine/Scripts/python -m pytest tests -q      # full suite, every phase
py -3 -m pytest tests -q                                              # host env, REAPER paths
```
Manual per gate: G0 hear it, G1 pane alone, G2 amp change + undo + one chat tone, G3 pull the USB cable. Each gate ends with a wiki log line.

## Risks and open questions

| Risk | Likelihood | Mitigation |
|---|---|---|
| Capture codec does not open | medium | T0.2 first, one day, before any engine code; contingencies B/C named above |
| pedalboard drops blocks under a load spike | low-medium | it owns the callback in C++; measure `dropped_input_frame_count` at 256 and 512 and print it in the pane |
| ASIO exclusivity makes the two panes fight | certain | T3.1 words + README: one device, one engine at a time |
| `raw_state` write is unsupported by this VST3 build | low | T2.3 test assigns and reads back; if the setter is refused, fall back to `load_preset` on a generated `.vstpreset` |
| Latency reported as 0 with no model, then not updated | medium | read `reported_latency_samples` after a capture load, not before |
| Python 3.14 host vs 3.11 engine drift | low | the wire protocol version is the only coupling; T1.1 owns it |

## Decisions settled

1. **Device sharing:** one device, one engine. Closing REAPER to play through the local engine is fine. No virtual-device path.
2. **Capture on boot:** none. A fresh engine loads no capture, so it is silent until one is picked. A capture is the `.nam` file: the profile of one real amp, and it is what makes the rig a Twin instead of a Plexi. Silence at boot means a wrong capture can never be the first thing through the speakers. Captures change two ways: the pane picks one, or the Hermes chat builds a tone and applies it.
3. **Recording:** out of scope, permanently. The product is an amp, not a recorder. `record` stays in `DEFERRED` with the reason "an amp does not record".
