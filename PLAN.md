# AmpHead — Hermes as the amp head for a REAPER rig

> **For Hermes:** implement task-by-task with the `subagent-driven-development` skill; each task is TDD with the `fake_reaper` fixture so nothing here needs a live REAPER until the integration tasks.

**Goal:** Hermes becomes the amp you play through. You sit down with a guitar, tell Hermes what tone you want in plain words, and it builds and holds a REAPER rig that is armed, monitoring, and verified: correct input, correct plugin chain in the correct order, every parameter set and read back, a live meter in a Hermes pane, and a stated verdict of READY / NO SIGNAL / CLIPPING.

**Architecture:** four layers, each one testable alone.

1. **Bridge (REAPER side, Lua, already proven).** A file-queue command server inside REAPER. `request.txt` in, `out/resp_<id>.json` out. No network, no keys, no sockets. Autoloads from `Scripts/__startup.lua`.
2. **Rig daemon (Python, `plugin_api.py`).** Owns the rig model: device check, chain, parameter resolver, write-with-verification, snapshots, meter poll, undo grouping. Single source of truth, cached state, every write logged as evidence.
3. **Agent surface (tools + slash commands).** `rig_setup`, `rig_tweak`, `rig_ready`, `rig_preset`, `rig_snapshot`, `rig_ab`, `rig_panic`, plus `/amp`, `/tone`, `/ab`, `/panic`.
4. **Amp face (Hermes desktop pane).** Live input meter, chain in real order, the six controls for the current amp with real values and plain words, preset buttons, latency in ms, and a verdict banner. Not a REAPER clone: a status-and-proof surface that proves the amp is alive while you play.

**Stack:** Lua (REAPER 7.77 ReaScript), Python 3 stdlib only on the plugin side, one ESM file for the pane (`@hermes/plugin-sdk`), JSON on disk for presets and snapshots.

**Deadline:** hackathon entry due 2026-11-11 17:00 EST. Freeze code 2026-11-10 18:00 local.

---

## 1. Why this is a plugin and not a prompt or a skill

A skill can teach a model what REAPER's API looks like. It cannot arm a track, cannot read a VST's parameter list, cannot verify that a write landed, and cannot put a live meter on screen. The value lives in three runtime capabilities Hermes does not have:

- **Hands with read-back.** Verified writes, not "I set the gain to 6 dB" hopes. Every write reads the plugin's own formatted value back and refuses to claim success on nonsense (the `-inf` failure found in the probe is exactly this class of bug).
- **A held rig.** A stateful daemon that knows what the amp is right now, so "more gain" and "back to the clean one" are diffs against truth, not guesses.
- **A face.** A pane that shows signal, not prose.

## 2. Proven before planning

Verified live against REAPER 7.77 on this machine, real JSON out:

```
PING            {"ok":true,"version":"7.77/x64"}
STATUS          {"project":"The_riff.rpp","tracks":46,"tempo":192.0}
ACTION 40859    {"ok":true}                       # new project tab
TRACK_ADD       {"index":47,"name":"Gtr L"}
FX_ADD Shoeless {"fx":2,"name":"VST3: Shoeless Drive Pedal (WaveMind)"}
ARM             {"armed":true,"monitor":true}
TRACK_VOL -3dB  {"vol":0.7079,"db":-3.0}
```

Two traps found, both load-bearing for the design:

- **New tracks are not empty.** The user's project default FX chain inserted ReaFir and RigSculpt, so the requested plugin landed at index 2, not 0. Every command must return and reuse the real FX index.
- **Parameter writes lie.** ReaEQ gain at 0.35 normalized and ReaFir output gain at 1.44 raw both read back as `-inf`. Raw-vs-normalized semantics differ per plugin. Writes must be verified against formatted read-back, with a normalized fallback retry.

Installed guitar-relevant VSTs already present: **NAM Universal (WaveMind)**, NAM Rig (Nouratone), NeuralAmpModeler (Steven Atkinson), NadIR and Emissary (STL Tones), RigSculpt (Steven Converse), Shoeless Drive Pedal (WaveMind), FabFilter Pro-Q 4 / Pro-C 2 / Saturn 2, Hybrid. The demo uses plugins the user already owns.

**Default amp is NAM Universal, not Shoeless.** Shoeless is a drive pedal and does not sound like an amp. NAM Universal is a full amp suite and exposes every knob as a host parameter (36 of them: `Amplifier Input Gain`, `Amplifier Bass/Mid/Treble`, `Amplifier Output Gain`, `IR Blend`, `Gate Threshold`, `Oomph`, `Sparkle`, pedal and Instigator stages, `Tuner Active`). See "NAM findings" in the wiki.

**The capture is not a parameter.** None of the three NAM builds exposes the loaded `.nam` file as a host parameter, so "load this amp" cannot be a parameter write. It is a state change: one saved REAPER FX preset per capture, switched by name and verified. The pane's knobs are real params; the amp choice is a preset.

## 3. Design decisions

**The amp lives in its own project tab.** `rig_setup` creates a new project tab (`ACTION 40859`) and builds the rig there. The user's song is never touched, which removes the entire class of accident the probe hit. Closing the tab without saving is the exit path. This is the single most important safety decision in the plan.

**REAPER stays the audio host.** Hermes never touches samples in v0. It configures a host that already does low-latency monitoring well, and it proves that configuration. No DSP, no audio callbacks, no buffer rewriting.

**Latency is reported, not faked.** Bridge gains a `DEVINFO` command using `reaper.GetInputLatency()` / `reaper.GetOutputLatency()`, reported in ms with the number the user should beat (>15 ms is called out as "too slow to play through"). If REAPER reports zero, the pane says "unknown, set your buffer to 128 samples" instead of inventing a number.

**Ambiguity is refused.** If "gain" matches three parameters or "drive" matches two plugins, the tool returns the candidates and asks. It never picks the first match and hopes.

**REAPER never shows its face.** The plugin is the interface, so REAPER is launched by the plugin and dropped to the taskbar the moment its window exists, and on request it can run on a private Windows desktop with no window at all. A user who starts the plugin never gets a second application's UI shoved in front of them. Verified: `-WindowStyle Minimized` does not stick because REAPER restores its saved window placement, so `amphead/launch.py` waits for the handle and minimizes it, which does. Cold start to answering bridge with an open ASIO device: ~4 s, `ui: minimized`. Deep-linking a VST window is only offered while REAPER is visible; in minimized or hidden mode the amp face carries every number instead.

**Every write is evidence.** `rig/log.jsonl` appends `{ts, target, param, requested, formatted_after, accepted}`. `rig_ready` reads the same log to state what it verified. This is also the honest demo narration: the pane shows the last verified write.

## 4. Interfaces

### Agent tools

| tool | input | output |
|---|---|---|
| `rig_setup` | `amp` (plugin name or alias), `input` (default 1), `chain` (optional list) | tab created, chain built, `{ready, verdict, latency_ms, problems[]}` |
| `rig_tweak` | `changes: {param: value}` or free text | per-param `{requested, actual, accepted}` |
| `rig_preset` | preset name or `{...}` object | full diff applied, single undo group |
| `rig_ready` | none | `{verdict, meter, chain[], problems[]}` |
| `rig_snapshot` / `rig_ab` | label, or label + compare | snapshot id, diff lines |
| `rig_panic` | none | all FX bypassed, track muted, disarmed |

Verdicts are words, not booleans: `READY`, `NO SIGNAL`, `CLIPPING`, `NOT ARMED`, `REAPER NOT RUNNING`, `AMBIGUOUS`.

### Pane (amp face)

Row 1: verdict banner, big, colored, with the reason inline ("READY — armed, monitoring, 8.9 ms"). Row 2: input meter, live, real peak from REAPER, with a clip indicator that latches. Row 3: chain as an ordered list of real plugin names, tap to focus that FX window in REAPER. Row 4: six sliders for the current amp, each labeled with name, value, unit, and a plain word (`gain 6.2 dB, hot`). Row 5: preset buttons, latency, and an "open amp UI in REAPER" deep link. No tooltips anywhere; every number explains itself in place.

### Slash commands

`/amp crunch` apply a preset, `/tone more gain` free-text tweak, `/ab` snapshot and compare, `/panic` bypass and mute.

## 5. Files

```
C:\Users\Chris\Projects\hermes-reaper-amp\
  PLAN.md  PHASE1.md
  amphead\                      # package: importable with no REAPER running
    __init__.py
    bridge.py                   # protocol v2 client: typed failures, guard args, batch
    launch.py                   # finds REAPER, starts it minimized/hidden, DEV_REOPEN, state()
    fake_reaper.py              # in-process REAPER stand-in, modes ok/silent/garbage/old/closed
    resolver.py                 # Phase 1: plugin + param resolution, ambiguity refusal
    rig.py                      # Phase 1: rig model, write-with-verify, snapshots, undo groups
    presets.py                  # Phase 2: preset load/apply, alias map
    plugin_api.py               # Phase 3: /api/plugins/amphead state, meter, tweak, presets
    tools.py                    # Phase 1-2: agent tools + slash commands
  reaper\
    bridge.lua                  # protocol v2 command server (guard, undo tx, batch, DEVINFO)
    __startup.lua               # loader appended into the user's startup script
    install.py                  # Phase 4: copies to the resource path, backs up startup
  scripts\
    reaper_hidden.ps1           # launch onto a private Windows desktop (no window at all)
  desktop\
    plugin.js                   # Phase 3: amp face pane + status bar item
  presets\
    clean.json  crunch.json  lead.json
  tests\
    test_bridge.py              # 29 protocol tests
    test_launch.py              # 8 launcher tests
    test_resolver.py  test_rig.py  test_presets.py  test_errors.py   # Phase 1-4
  demo\
    shotlist.md  demo.mp4
```

Runtime install targets: `C:\Users\Chris\AppData\Roaming\REAPER\Scripts\hermes_bridge\` and `C:\Users\Chris\AppData\Local\hermes\desktop-plugins\amphead\plugin.js`.

## 6. Build plan

Seven phases, compressed so a demo-capable build exists Saturday afternoon. Saturday is a target, not a freeze: if testing shows the idea needs an overhaul, the overhaul happens and the submission date is what does not move. Each gate is a command or an observable, not a feeling.

| Phase | Window | Work | Gate (proof it is done) |
|---|---|---|---|
| 0 | Thu Nov 5 evening, 2 h | bridge v2 (guard, undo tx, batch, DEVINFO/METER), `fake_reaper`, launcher | `python -m unittest discover -s tests` green with REAPER closed **(PASSED 2026-11-05, 37 tests, v2 live-verified)** |
| 1 | Fri Nov 6 morning | manifest, resolver, verified writes, `build_rig` | `rig_setup` builds an armed, monitoring rig in a new tab **after starting REAPER itself; `ui` comes back `minimized`**; user's project untouched |
| 2 | Fri Nov 6 afternoon | `rig_ready`, `rig_tweak`, two presets, panic | "make it crunchier" applies with real numbers; panic silences the rig |
| 3 | Fri Nov 6 evening + Sat morning | `plugin_api.py`, pane, meter, chain, sliders | play guitar, meter moves, "more gain" moves slider and sound together, **with REAPER on the taskbar and the pane carrying every number on its own** |
| 4 | Sat Nov 7 by 14:00 | demo take 1, README, first catalog draft, validate green | **DEMO-CAPABLE, not frozen.** Two takes recorded, validate exits 0, overhaul still allowed, **take shows the taskbar icon and no REAPER window after the cold open** |
| 5 | Sat 14:00 to Tue 18:00 | error states, `/ab`, deep links, status bar, re-takes, overhauls if the idea needs one | Every error shows a word; the shipped demo beats take 1 |
| 6 | Wed Nov 11 before 14:00 EST | PR, X post, Typeform | One-file PR, post live, confirmation captured |

**Critical path:** bridge -> verified writes -> `rig` -> pane. The pane is not on the critical path: it can be built Friday from noon against a hardcoded `/state` payload and swapped to the live daemon when `plugin_api.py` lands. Nothing waits on the pane, and the pane waits on nothing.

**Saturday build minimum (what "amp" means this week):** setup from one sentence, verified tweak by name or free text, ready verdict, live meter, real chain, six sliders, panic. Preset library beyond two, A/B diffs, deep links and the status-bar meter are improvements, not build.


### Phase 0 — Thu Nov 5 evening. Bridge completeness and test double

**Task 0.1** Add `DEVINFO` (`GetInputLatency`, `GetOutputLatency`, sample rate if available) and `METER` (track peak L/R via `Track_GetPeakInfo`, master peak, latched clip flag the bridge clears on read) to `reaper/bridge.lua`. Verify: `python -c "from bridge import call; print(call('DEVINFO'))"` returns ms values; `METER` returns a float that moves when you play.

**Task 0.2** Add `FX_BYPASS`, `FX_DEL`, `FX_MOVE`, `PROJ_TAB_CLOSE`, `PROJ_SAVE_AS` to the bridge. Verify each against the scratch tab.

**Task 0.3** Write `amphead/fake_reaper.py`: answers from a scripted table and can be told to return malformed JSON, time out, run protocol v1, or come up with the audio device closed. It must reproduce the three real traps: a param whose formatted read-back is `-inf`, a param that silently ignores writes, and two params with the same name. Verify: `python -m unittest discover -s tests` passes with no REAPER running.

**Task 0.4** Write `amphead/launch.py`: find REAPER (`REAPER_EXE`, install paths, PATH), start it, drop the window to the taskbar, reopen a wedged ASIO device, report `ui` as `minimized`/`hidden`/`visible`/`already-running`. **Done 2026-11-05**, 8 tests, live cold start 4.05 s with device open. Note for anyone re-running it: `-WindowStyle Minimized` does not stick, REAPER re-shows its saved placement, so the launcher minimizes after the handle appears.

### Phase 1 — Fri Nov 6. The plugin skeleton and verified writes

Detailed step plan: [PHASE1.md](PHASE1.md) — seven steps, each with its own verify command, plus a ranked hole list. Read it before writing code; the bridge needed a protocol change (percent-encoded args, project guard, normalized read-back, batch) before Steps 1.3–1.5 were worth building. Undo grouping is per call, not per session: see the note on `TX_BEGIN` in PHASE1.md.

**Task 1.1** `plugin.yaml` + `__init__.py`: manifest with id `amphead`, empty pip deps, one tool `rig_ping` returning bridge version. Verify: `hermes plugins validate C:/Users/Chris/Projects/hermes-reaper-amp --install-deps` exits 0 with REAPER closed.

**Task 1.2** `bridge.py` client. **Done in Phase 0**: percent-encoded wire args, request lock, `proto` handshake, `BridgeBlocked` liveness verdict, and six typed errors (`NoReaper`, `BridgeBlocked`, `OldBridge`, `BridgeTimeout`, `BadResponse`, `CommandFailed`), 29 tests.

**Task 1.3** `resolver.py`: `resolve_fx(query)` and `resolve_param(query)` with exact → substring → token scoring, returning `Resolved` or `Ambiguous(candidates)`. Verify: tests for "shoeless" → full name, "drive" → ambiguous on a 3-param fixture, "pro-q" → Pro-Q 4.

**Task 1.4** `rig.py` `set_param(track, fx, param, value)`: `FX_INFO` → choose raw vs normalized → write inside an undo group → read back → accept only if the formatted value is not `-inf`/`nan`/`?` and within tolerance; one retry in the other mode; log to `log.jsonl`. Verify: tests replaying the real `-inf` failure and asserting the retry path and the rejection.

**Task 1.5** `rig.py` `build_rig(amp, input)`: new tab, add track, add amp FX, arm, select input, monitor on, set a sane starting level, snapshot pre-state. Verify: integration test against live REAPER on the scratch tab; assert tab name, chain, `I_RECARM=1`, `I_RECMON=1`.

**Acceptance:** from a cold Hermes with REAPER closed, `rig_setup("NAM Universal")` starts REAPER, leaves it minimized, and builds a working guitar rig in a new tab in under eight seconds total, with the user's song file untouched. The three seconds is the rig itself; the cold-start launch budget is separate and measured.

### Phase 2 — Fri Nov 6 afternoon. Ready check, tweak, two presets, panic

**Task 2.1** `rig_ready`: check REAPER alive, tab is a rig tab, track armed, monitor on, input set, chain non-empty, no FX bypassed by surprise, meter above floor for 500 ms, latency under threshold. Returns verdict word plus problems. Verify: five tests, one per failure verdict, using `fake_reaper` scenarios.

**Task 2.2** `presets.py` + `presets/clean.json`, `crunch.json`, `lead.json`: semantic targets (`drive: 0.72`) resolved at apply time against the live param map. Verify: applying `crunch` on the live rig changes the expected params, one undo group, diff printed.

**Task 2.3** Snapshots and `/ab`: `snapshot(label)` captures chain + every param raw and formatted; `compare(a, b)` returns human diff lines. Verify: apply preset, snapshot, undo, compare shows the inverse diff.

**Task 2.4** `rig_panic`: bypass all FX, mute track, disarm. Verify: meter still moves, audio dead, one call to restore.

**Acceptance:** "make it crunchier" as free text maps to a preset or a param delta, applies in one undo group, and reports what actually changed with real numbers.

### Phase 3 — Fri evening and Sat morning. The amp face

**Task 3.1** `plugin_api.py`: `GET /api/plugins/amphead/state` (verdict, chain, params, latency), `GET /meter` (cheap, 50 ms poll), `POST /tweak`, `POST /preset`. Verify: `curl` against a running gateway returns JSON with the real chain names.

**Task 3.2** `desktop/plugin.js`: pane registering the five rows, `useValue` over a poll of `/meter`, native UI kit components only. Verify: pane appears in Capabilities → Plugins, meter tracks a real guitar, chain names match REAPER.

**Task 3.3** Status bar item: tiny always-on meter plus verdict dot, so the amp state is visible from any pane.

**Task 3.4** Deep links: chain row click sends a bridge `FX_FOCUS` command that opens that plugin's window in REAPER. **Only while `ui == visible`.** When REAPER is minimized or on the hidden desktop, the row shows the value instead of a dead button, because restoring a hidden window is a UI decision the user did not ask for.

**Acceptance:** play guitar, watch the pane bar move, say "more gain" in chat, watch the slider and the sound change together. That is the demo.

### Phase 4 — Sat Nov 7 by 14:00. Demo-capable build (not a freeze)

**Task 4.1** Install and uninstall paths: `reaper/install.py` copies `bridge.lua` + `__startup.lua` into the resource path from `reaper.GetResourcePath()`, backing up any existing `__startup.lua` and appending instead of overwriting. A `--uninstall` flag restores the backup. Verify: install on a clean profile, PING works, uninstall, PING fails with `NoReaper`, no stray files.

**Task 4.2** Error states end to end, one test each, asserted on the pane and in the tool output: REAPER not running and unlaunchable, REAPER on a modal dialog (`BridgeBlocked`), bridge file deleted mid-run, two REAPER instances, rig tab closed by the user, track muted by the user, audio device unplugged (meter flat while armed), and **device came up closed after a killed REAPER (auto `DEV_REOPEN`, pane says `REOPENED DEVICE` rather than `NO SIGNAL`)**. Verify: `python -m unittest discover -s tests` plus one manual pass per state.

**Task 4.3** Demo fixture on the user's real guitar: scratch tab, DI input, Shoeless into RigSculpt with a cab, three presets (`clean`, `crunch`, `lead`) tuned by ear, and one deliberately hostile case: a parameter that reads back `-inf` so the tool's refusal is on camera.

**Task 4.4** Record the under-90s demo per `demo/shotlist.md`. Two takes. Keep the second as the fallback. No keys, no network, nothing typed that a viewer cannot see happen.

**Task 4.5** README: what it is in one sentence, install in three commands, the four layers, honest limits (no DSP, latency is REAPER's, Windows only in v0, no tuner), the safety model (own tab, undo groups, panic), and the demo video link.

**Task 4.6** `demo/shotlist.md` finalized with timestamps and the exact words spoken, so the take is reproducible.

### Phase 5 — Sat 14:00 to Tue 18:00. Testing, improvements, overhauls allowed

**Task 5.1** `plugin-catalog/amphead.yaml` in the same shape as `git-autosave.yaml`: `name`, `title`, `repo`, pinned 40-char `sha`, long `description` with the full disclosure paragraph (what it writes, what it reads, that it never saves or modifies the user's project without consent, that it needs REAPER running), `maintainer: "@static2020"`, `tier: community`, `category: tools`, `requires_hermes: ">=0.22.0"`, `docs_url`, `version: "0.1.0"`, `readme: true`, `platforms: [windows]`, `capabilities: {provides_tools: [...], provides_hooks: [], provides_middleware: [], requires_env: []}`.

**Task 5.2** `hermes plugins validate C:/Users/Chris/Projects/hermes-reaper-amp --install-deps` exits 0. Fix everything it reports. Re-run after each fix.

**Task 5.3** Install from the catalog entry form on a clean profile, not from the working tree: `hermes plugins install <path-or-owner/repo>`, then `hermes plugins list` shows it enabled, then tools appear in a session.

**Task 5.4** Fresh-eyes read of the repo as a stranger: no absolute paths of mine, no secrets, no scratch files, LICENSE, `.gitignore` for `runs/`, `log.jsonl`, `snapshots/`.

**Task 5.5** Freeze at 18:00 local. After that, only demo-video edits, no code.

### Phase 6 — Wed Nov 11, submit before 14:00 EST

**Task 6.1** Push the branch, pin the SHA, open the PR against `NousResearch/hermes-agent` adding only `plugin-catalog/amphead.yaml`. Verify the PR shows one changed file.

**Task 6.2** Post the demo on X, under 90s, tagging `@NousResearch`, with the repo link.

**Task 6.3** Submit the Typeform with the PR URL, the X post URL, and the description. Verify the confirmation.

**Task 6.4** Post-entry: nothing. If the deadline slips, the fallback is the recorded demo plus the PR, not a rushed live take.

## 7. Visualization: the recommendation and the reasoning

Yes, build a pane, and build exactly one. Three candidates were considered:

- **A clone of the amp's controls in Hermes.** Rejected. RigSculpt and Shoeless already have real interfaces with real graphics; a generic six-slider copy looks worse than the thing it copies, and duplicates maintenance.
- **A REAPER timeline or mixer view.** Rejected. Wide, fiddly, and it is not the amp.
- **A status-and-proof face.** Chosen. What you cannot see anywhere else is: is the rig actually armed and monitoring, is signal arriving right now, which plugins are in the chain in which order, what are the real values, what did the last verified write do, and what is the round-trip latency. That is the pane.

It is load-bearing for the product, not decoration. Playing guitar means hands are busy, so the screen has to answer "is it working" in one glance, and chat has to answer "change it" in one sentence. Together they are the amp head.

## 8. Tests and validation

- Unit/integration: `python -m unittest discover -s tests` — 37 tests against `fake_reaper` and mocked bridge calls, no REAPER, no audio device. (pytest is not in the Hermes runtime; unittest is.)
- Launcher: `amphead/launch.py` finds REAPER (`REAPER_EXE`, then standard install paths, then PATH), starts it minimized, reopens a wedged ASIO device, and reports `ui` as `minimized` / `hidden` / `visible` / `already-running`. Hidden-desktop launcher at `scripts/reaper_hidden.ps1`.
- Live rig: `python -m plugin.demo_check` builds a rig on a scratch tab and prints the verdict JSON.
- Plugin contract: `hermes plugins validate C:/Users/Chris/Projects/hermes-reaper-amp --install-deps`.
- Pane: load in Hermes Desktop with REAPER closed, expect `REAPER NOT RUNNING` in the banner and no crash; then with REAPER running and nothing armed, expect `NO SIGNAL`; then play, expect `READY`.
- Safety: assert `The_riff.rpp` mtime and track count unchanged after a full test pass.

## 9. Risks

| risk | likelihood | mitigation |
|---|---|---|
| Audio buffer too large for playing through | medium | report `GetInputLatency`/`GetOutputLatency`; state target 128 samples; do not pretend |
| VST parameter semantics differ per plugin (the `-inf` class) | high | verify every write against formatted read-back, retry once in the other mode, refuse and report |
| Bridge stalls if REAPER shows a modal dialog | medium | 2s timeout, typed `Timeout` error, pane shows `REAPER BUSY`, never blocks the agent turn |
| Pane polling load | low | 50 ms poll of a cached peak only; full state at 1 Hz |
| REAPER not installed on the judge's machine | certain | demo is a video; README states the requirement; `rig_ready` returns `REAPER NOT RUNNING` cleanly |
| REAPER window steals focus on a cold start | certain | launch minimized, minimize again after the handle exists, hidden-desktop option; never require the window for any command |
| Scope creep into recording, tuner, IRs | high | written cut list below |

## 10. Cut list (not in v0)

Recording takes, tuner, IR/cab browser, MIDI footswitch, multi-amp parallel rigs, voice control while playing, tone matching from a reference recording, Windows audio device switching, mobile surface. Voice is the obvious v2: hands are on the guitar, so `/tone` spoken aloud is the natural next step, but it is not on the critical path to a working amp.

## 11. No-UI and no-ears acceptance spec

The rule: **the plugin is the interface.** REAPER is a host on the taskbar, not a second app the user has to drive. Every item below is a gate, not a vibe, and each names the check.

| # | Requirement | Check | Phase |
|---|---|---|---|
| 1 | Plugin starts REAPER when it is not running | `launch.ensure_running()` returns `started: true` from a cold machine | 1 |
| 2 | REAPER ends up minimized, not in the way | `ui == "minimized"` and `IsIconic(hwnd) == true` after launch | 1 (done) |
| 3 | Focus is not stolen | foreground window handle and title unchanged 1 s after launch | 1 |
| 4 | Cold start under 8 s to a usable rig | measured `waited` + rig build, printed by `rig_setup` | 1 |
| 5 | No command needs REAPER's window | full rig build, tweak, meter, panic, snapshot all pass while minimized | 1-2 |
| 6 | A closed audio device is repaired, not blamed | `DEVINFO.device_closed` -> auto `DEV_REOPEN`, pane says `REOPENED DEVICE` | 2 |
| 7 | Pane carries every number REAPER would show | armed, monitoring, chain order, real formatted values, latency, last verified write, all visible with REAPER minimized | 3 |
| 8 | No dead buttons | deep link only rendered when `ui == "visible"`; otherwise the row shows the value | 3 |
| 9 | Failure looks like a sentence, in the pane | `REAPER NOT RUNNING`, `REAPER BUSY`, `NO SIGNAL`, `WRONG PROJECT`, `REFUSED: plugin reported -inf` | 3-4 |
| 10 | User's project never opens for our benefit | song path refused by the guard; rig lives in its own tab | 1 (done) |
| 11 | Demo never makes the viewer watch REAPER | REAPER on screen only in the first 8 s as the problem; after that it is a taskbar icon | 4 |
| 12 | Rig never boots live | `TRACK_ADD` writes arm 0 / monitor 0; `ARM` refuses without `confirm=1` (pane LIVE switch only); master parked at -60 dB through the build | 1 (done) |
| 13 | Arming can never blast the user's ears | `ARM` drops the master to -60 dB and verifies it before the track arms; refuses if it will not go down; `allow_loud` is explicit | 1 (done) |

Optional mode, not a gate: `hidden_desktop=True` via `scripts/reaper_hidden.ps1` runs REAPER on a private Windows desktop with no window and no taskbar entry. Verified working (bridge, ASIO, meter). Keep it for long unattended sessions; the default stays minimized because a taskbar icon is a useful sign that the host is alive.

What this rules out, so nobody adds it later thinking it is harmless: an "open REAPER" button on the pane, auto-restoring the window on error, any flow that requires the user to click something in REAPER (arming, choosing input, picking a device), and any demo shot that shows REAPER's mixer.

## 12. Open questions

1. Name: `AmpHead` / `amphead` as the plugin id, or `hermes-rig`?
2. Does the pane ship in the hackathon entry, or does it land in v1 while the entry is bridge + tools + demo? (Recommendation: ship it, it is the whole point. Settled by the no-UI rule: with REAPER on the taskbar, the pane is the only place the rig is visible, so it is not optional.)
3. Is the desktop app the surface the demo should show, or the web dashboard, for reach?
4. Should `rig_setup` default to the user's real project when they ask for it, or is the scratch-tab rule absolute? (Recommendation: absolute in v0, opt-in later.)
