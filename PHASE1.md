# Phase 1 — plugin skeleton, resolver, verified writes, build_rig

Goal: from a cold Hermes, `rig_setup("Shoeless")` produces an armed, monitoring guitar rig in its own REAPER tab in under three seconds, with every parameter write proven by a read-back, and the user's song file provably untouched.

Gate (all four must be true at once):

1. `hermes plugins validate C:/Users/Chris/Projects/hermes-reaper-amp --install-deps` exits 0 **with REAPER closed**.
2. `python -m unittest discover -s tests -v` green with REAPER closed (protocol v2, resolver, writes, rig identity).
3. Live: from REAPER closed, `rig_setup("Shoeless")` starts it minimized, builds the rig, and `rig_teardown()` removes it, leaving the tab list as it started and no project file outside the rig path touched.
4. `log.jsonl` holds one entry per write, each with requested value, mode, read-back, and accept/refuse reason.

---

## Step 1.0 — Bridge protocol v2 (everything else depends on this)

Do this first. It is cheap now and expensive after three other steps are built on v1.

The current wire format is `id|cmd|a|b|c`, split on `|`, three arguments max, no escaping, no grouping, no identity guard. That is not enough for what Phase 1 needs.

**Wire change.** `request.txt` stays a single line, but every argument becomes a percent-encoded `key=value` pair after the command:

```
4f2a9c|FX_SET|track=1|fx=2|param=0.72|value=0.72|mode=norm|expect=%2Fhome%2Frig.rpp
```

Percent-encode everything outside `[A-Za-z0-9._-]` and `/` so `|` and `=` can never appear inside a value. Decoding in Lua is three `gsub` calls, no JSON parser needed in ReaScript.

**1.0.1 Version the protocol.** `PING` returns `proto: 2` and `cmds: [...]` (the full command list). The Python client refuses to run against `proto` other than 2, and names the installed bridge path so the user knows what to reinstall. Without this, a stale installed bridge answers half the commands and looks like a bug in the plugin.

**1.0.2 Identity guard.** Every mutating command takes `expect=<full project path>`. The Lua resolves the active project's path and refuses with `{"ok":false,"error":"wrong project","active":"..."}` if it differs. This is the fix for the class of accident that already happened once on this project, where a test track landed in The_riff. Read-only commands skip the guard. Guard failure is a hard refuse, never a warning.

**1.0.3 Undo grouping equals one call.** Attempted as `TX_BEGIN`/`TX_END` across two calls and measured dead on REAPER 7.77: an undo block opened in one script invocation and closed in the next produces **no undo point at all**. The writes stayed applied and the next `UNDO` reverted the previous point instead, which on the live probe deleted the FX the rig was built on. The commands now refuse with "REAPER cannot hold an undo block between script runs; send the writes as one BATCH", and `PING` lists them under `removed`. Grouped writes have one form: `BATCH`, one round trip, one invocation, one undo point. Verified live: three writes in one batch, one `UNDO` put the value back and left both plugins in the chain.

**1.0.4 Normalized read-back in `FX_INFO`.** Return `norm`, `raw`, `min`, `max`, `fmt`, `name`, `steps`. Verification compares numbers (`abs(read_norm - wanted_norm) <= 0.005`), never parsed localized strings. This removes the whole `-inf` parsing problem from the accept path while keeping `fmt` for what the user sees.

**1.0.5 Destructive and identity commands.** `FX_DEL`, `FX_MOVE`, `PROJ_SAVE_AS` (path arg, `expect` guard, refuses to overwrite an existing file unless `overwrite=1`), `PROJ_TAB_CLOSE` (guard: refuses if the active project is not the saved rig path). Verify each against a throwaway tab, then verify the guard refuses when the active project is The_riff.

**1.0.6 Batch.** `BATCH` takes `ops` as `;`-separated `CMD:argkey=val,argkey=val` entries, runs them in order inside one transaction, stops at the first failure, and returns `results: [...]` plus the index that failed. `build_rig` becomes one round trip instead of nine, which is what makes the three-second budget reachable.

**1.0.6a Arming pulls the master down first.** `ARM` reads the master fader; if it is above `-60 dB` it writes `10^(-60/20) = 0.001`, verifies the read-back sits at or below the floor, and only then arms and monitors. If the master will not go down the command refuses and the track stays unarmed. `allow_loud=1` is the explicit opt-out, and the payload always carries `master_db_before`, `master_db_after`, `lowered`. Both the master move and the arm happen in one script run, so no dialog can slip between them. Verify: master at 0 dB, one `ARM` -> `-60.0 dB`, `lowered: true`, `armed: true`; repeat with `allow_loud=1` and the master stays at 0 dB.

**1.0.6b The rig boots dead.** A track Hermes creates is born unarmed with monitoring off, and nothing in the build path may arm it: `ARM` refuses without `confirm=1`, which only the pane's LIVE switch sends. The master stays at the -60 dB floor across the whole build, so the moment anything does go live the user is already safe. `DISARM` puts a track back to standby. Live-verified: after track + FX + input the track reads `armed=False monitor=False`, a builder-style `ARM` is refused with `arming needs confirm=1`, LIVE arms at `-60.0 -> -60.0 dB`, STANDBY disarms. Found on the way: **REAPER's new track arrives with `I_RECMON=1`**, so `TRACK_ADD` now writes arm 0 and monitor 0 explicitly instead of trusting defaults.

**1.0.7 Inputs and levels.** `INPUTS` returns device input count and, where the API exists, channel names (probe `GetInputChannelName`, report `null` if absent). `SET_INPUT` writes `I_RECINPUT` and reports the resulting `I_RECMODE`/`I_RECINPUT` values back. Do not hardcode 4096 again: the earlier probe set it blind. Determine mono vs stereo encoding empirically on this device (try 0, 1, 1024, 4096, read back what REAPER reports and whether `METER` moves) and record the mapping in the wiki.

**Done when:** `python -m unittest discover -s tests -v` passes 30+ tests covering v1 rejection, guard refusal, batch undo, batch stop-on-failure, rig-tab identity and the input mapping, with REAPER closed. Then one live run of `PING`, `BATCH` + single `UNDO`, `FX_MOVE`/`FX_BYPASS`/`FX_DEL`, `SET_INPUT` and `PROJ_TAB_CLOSE` with the guard armed. Status: 58 tests green, live close-out 19/19 (see `wiki/amphead.md`).

**Trap:** `fake_reaper.py` must be rewritten in the same commit. A double that still speaks v1 lets the whole suite pass while the installed bridge is broken.

---

## Step 1.1 — Manifest and validate gate

Repo layout, because `validate` is given the directory:

```
hermes-reaper-amp/
  plugin.yaml
  __init__.py          # def register(ctx)
  amphead/             # bridge.py, resolver.py, rig.py, fake_reaper.py
  tests/
  presets/             # later phases
```

**1.1.1** `plugin.yaml` with `name`, `version`, `description`, `author`. Match the bundled shape exactly (`plugins/disk-cleanup/plugin.yaml`); no speculative keys, since an unknown key may fail validation.

**1.1.2** `__init__.py` with `register(ctx)` calling `ctx.register_tool(name=..., toolset="amphead", schema=..., handler=..., check_fn=..., emoji=...)`. Grounded signature: `register_tool(name, toolset, schema, handler, check_fn=None, requires_env=None, is_async=False, description="", emoji="", override=False)`. Do not use `override=True`.

**1.1.3** Import safety. `register()` must not touch REAPER, open sockets, or read the resource path at import time: validate runs with REAPER closed and a raise here fails the gate. Bridge contact happens lazily in `check_fn` and in handlers.

**1.1.4** Tool names: `amphead_ping`, `amphead_setup`, `amphead_teardown`, plus read-only `amphead_status`. Underscored prefix, no collision with built-ins.

**Done when:** `hermes plugins validate C:/Users/Chris/Projects/hermes-reaper-amp --install-deps` exits 0, `--json` shows the tools, and it still exits 0 with REAPER closed.

**Status: DONE 2026-11-05.** `plugin.yaml` (name, version, description, `author: "@static2020"`) plus `provides_tools`; root `__init__.py` registers four tools through a lazy import; handlers live in `amphead/tools.py`. Validation passes all 16 checks with REAPER closed; 86 tests green.

What the gate caught, all of it worth keeping:

- `provides_tools` is **not optional**. Undeclared registrations fail `declared tools`, so the manifest and `TOOLS` must be kept in lockstep (a test now asserts it).
- `check_fn` must **not** require a live host. Gating on liveness would hide `amphead_setup`, the one tool whose job is to start REAPER. It checks for an install instead.
- `launch.state()` used to **raise** on a stale heartbeat, so the pane could not draw "REAPER NOT RUNNING". A missing host is now a returned state (`bridge: not-running | down`) with a reason.
- A bridge heartbeat **outlives the process** by seconds. `BridgeBlocked` guesses "a dialog probably has it"; we only say that when `reaper.exe` actually exists, otherwise it is simply not running.
- The project guard applies to **our own rig tab**. After `PROJ_OPEN` every write still needs `expect=<rig path>`, which is the behaviour we want: a write must name the tab it intends to touch.
- `TABS` ships its array as a JSON string under `tabs` with a sibling `count`. Added `bridge.tabs_list()`; setup now reports `tabs=1->1` instead of `0->0`.

Measured cold `amphead_setup` with REAPER closed: **5.03 s** to a minimized host, rig tab open, device open, master at -60.0 dB, `armed=False`.

---

### Step 1.2 — Client: locking, identity, typed errors — DONE 2026-11-05

Three additions, each closing a way the client could lie:

1. **Lock** — one request slot, `O_CREAT|O_EXCL`, pid inside, stale reclaim. `LOCK_TIMEOUT` (wait) and `LOCK_STALE` (reclaim) are now separate numbers, and the holder refreshes the lock's mtime once a second while it waits on the answer. Without the touch, any command slower than the stale window (a long `BATCH`) could have its own lock taken away mid-flight.
2. **Response identity** — the bridge now echoes the request id in the payload *and* publishes the response by rename (`resp_<id>.json.tmp` → final). Before this, a client could read a half-written file and report it as "not JSON". Client-side: `_parse_response()` refuses empty, truncated (no closing brace), non-JSON, non-object, and foreign-id payloads, in that order, so each failure names what actually happened.
3. **Liveness** — one `bridge.liveness()` returning `live | not-loaded | blocked | not-running` plus a human sentence, used by the client and available to the pane. The process decides first: a heartbeat outlives `reaper.exe` by seconds, so a fresh heartbeat with no process is `not-running`, not `blocked`. No heartbeat with a live process is `not-loaded` and names the script directory.

Verify:
```powershell
python -m unittest discover -s tests          # 103 tests, 17 new in tests/test_client.py
python scratch/live_12.py                     # against real REAPER 7.77
```

Live on REAPER 7.77 (cold host, ASIO AudioBox, 512 @ 44100):
```
launch: started=True ui=minimized waited=8.15s
ping:   proto=2 version=7.77/x64 id=b069f08da665      (id echo works)
4 concurrent PINGs: 4/4 answered, 0 errors, 0.12s
stale lock from pid 99999 reclaimed, lock released
truncated payload refused as "truncated", not "bad JSON"
liveness up:    live
liveness after quit: not-running | reaper.exe is gone; the last heartbeat was 1s ago
```

Hole found live: `liveness()` used to return `live` from a fresh heartbeat alone, and the client then said "bridge is answering; no answer within 0.4s" — a sentence that contradicts itself. Fixed by ordering the checks process-first.

Hole not yet covered: `_process_running()` shells out to PowerShell (~0.3 s). It only runs on the failure path, but the pane must not call `liveness()` per frame. Pane verdicts come from the last call's outcome plus a cached process check.

Most of this exists (`amphead/bridge.py`, protocol v2, 29 tests). Three additions.

**1.2.1** Inter-process lock around the request/response pair. `request.txt` is a single global slot; two Hermes sessions or one background tick calling concurrently silently eat each other's requests. Take a lock file with `O_CREAT|O_EXCL` and a stale-lock timeout of 5 s, hold it from write through read.

**1.2.2** Response identity check. Parse the filename's id and compare against the sent id; a mismatch or a truncated file is `BadResponse`, not a successful parse of the wrong answer.

**1.2.3** Distinguish "REAPER not running" from "REAPER is blocked". The `alive` heartbeat file decides: no heartbeat in 5 s and no process means `NoReaper`; fresh heartbeat but no response means `BridgeBlocked` (a modal dialog owns the main thread). Two different user-facing sentences.

**Done when:** tests cover lock contention (two threads, one request each, both answered), stale lock recovery, id mismatch, and both liveness verdicts.

---

## Step 1.3 — Resolver

**1.3.1** `resolve_fx(track, query, chain)` returns `Resolved(index, name)` or `Ambiguous(candidates)` or `NotFound(suggestions)`. Ranking: exact case-insensitive, exact on the name with the `VST3:`/`VST:`/`JS:` prefix stripped, then token-set containment, then subsequence. Never return the first fuzzy hit.

**1.3.2** `resolve_param(fx_name, query, params)` uses the same ranking over parameter names, and returns `Ambiguous` when two params score equal, which is common (`Gain` vs `Output Gain`). The candidate list is what the refusal sentence says out loud.

**1.3.3** Index is a hint, name is the truth. At write time, re-read the chain and confirm the FX at that index still has the resolved name; if not, re-resolve. FX indices shift the moment anything is added or removed, and silently writing to index 2 because it used to be Shoeless is exactly the kind of wrong that sounds plausible.

**Done when:** fixture tests cover `"shoeless"` resolving to the full VST3 name, `"drive"` returning ambiguous with three candidates, `"pro-q"` to Pro-Q 4, `"gain"` ambiguous on a two-gain fixture, and a stale-index re-resolve.

---

## Step 1.4 — Verified writes

`rig.set_param(track, fx, param, value, *, mode="auto", unit=None)`

Sequence, in this order:

1. `FX_INFO` for the param: `min`, `max`, `norm`, `raw`, `fmt`, `name`.
2. Choose mode. `auto` means normalized for anything the user said as a name or percentage, raw only when the user gave a number with the plugin's own unit. Normalized is the default because raw units differ per plugin.
3. Clamp to `[min, max]`, and report the clamp in the result rather than silently moving a different value than asked.
4. `BATCH` of three writes, then one `UNDO` (the only way to get one undo point).
5. Re-read `FX_INFO` and accept only if `abs(norm_after - norm_wanted) <= 0.005` **and** `fmt` is not `-inf`, `nan`, `?`, or empty.
6. On refusal, retry once in the other mode. If the retry also fails, refuse loudly with both read-backs shown.
7. Append to `log.jsonl`: timestamp, track, fx, param, requested, mode, read-back norm, read-back fmt, verdict, reason.

**Done when:** tests replay the real failure seen on this machine (ReaEQ gain at 0.35 normalized and ReaFir output gain at 1.44 raw both reading back `-inf`), assert the retry happens, assert the second failure refuses, assert `log.jsonl` has both attempts, and assert a clamp is reported as a clamp.

---

## Step 1.5 — Rig identity, build, teardown

**1.5.1** Rig identity before building anything. A new tab from action 40859 is unnamed, and `TABS` reports its name as `""`. So: create tab, `PROJ_SAVE_AS` to `<plugin data>/rig/rig.rpp`, then treat that absolute path as the rig's identity for the rest of its life. Every mutating call carries `expect=<that path>`. This is what makes "the user's song is never touched" a checked fact instead of a promise.

**1.5.2** `build_rig(amp, input, track_name="Gtr")` as one `BATCH`, in this order: new tab, save-as, rename track 1 or add track, `FX_ADD` amp, `FX_BYPASS` nothing, `SET_INPUT`, arm, monitor, `TRACK_VOL` to a starting level, snapshot pre-state. Report the FX index that actually came back, since the default project template can pre-load FX (it did: a new track arrived with ReaFir and RigSculpt, and the requested plugin landed at index 2).

**1.5.3** Template hazard. If the tab's track already carries FX, say so in the result and leave them in place rather than deleting something the user configured. `build_rig` never deletes FX it did not add.

**1.5.4** `teardown(rig_path)`: verify identity, close the tab, delete `rig.rpp` only if we created it, and report the remaining tab list. Never close a tab whose path is not the rig path.

**1.5.5** The song is a fixture, but it is not the live copy. REAPER no longer opens on The_riff (the user closed it; the old restore-on-open came from their own script, not from us), so nothing may assume it is a tab. Before and after every integration test, assert the tab list is exactly what we expect and that no `.rpp` outside the rig path changed mtime. Use a saved copy of The_riff as the hostile fixture when a populated project is needed.

**Done when:** live build under 3 s measured three times with REAPER already running, `rig_ready`-style assertions hold (tab name, chain, `I_RECARM=1`, `I_RECMON=1`, input set), teardown restores the tab list, and no project file outside the rig path had its mtime touched.

---

## Step 1.6 — Deployment

**1.6.1** The bridge is the one piece that cannot live only in the repo: REAPER loads it from `%APPDATA%\REAPER\Scripts\hermes_bridge\`. Add `amphead/install_bridge.py` that copies `reaper/bridge.lua` from the repo into the resource path, writes the `__startup.lua` line idempotently, and then requires `PING` to report `proto: 2` before claiming success. Repo copy is the source of truth; the installed copy is a build artifact.

**1.6.2** `__startup.lua` autoloads the bridge on every REAPER launch, which means it runs even when the plugin is disabled. Keep it, but make the bridge inert and cheap when idle (it already is: one missing-file check per defer tick), and have it publish `proto` and its own path so a stale copy is diagnosable.

**1.6.3** Uninstall removes the startup line and the script folder. There is no open-transaction state to guard: a batch either completes or refuses.

**Done when:** install → restart REAPER → `PING` says `proto 2` from the repo-supplied file, md5 matches the repo copy, and uninstall leaves REAPER starting clean.

---

## Step 1.7 — Cold start with no UI

**1.7.1** `rig_setup` calls `launch.ensure_running()` first. If the bridge is dead it starts REAPER, waits for the bridge, minimizes the window, and only then builds the rig. The tool result reports `started`, `waited`, `ui`, and the device line, so a slow cold start is visible rather than mysterious.

**1.7.2** Focus test, because "minimized" is not the same as "not in the way": record the foreground window handle and title before launch, launch, wait 1 s, assert both unchanged. If REAPER steals focus even briefly, that is a bug to fix in the launcher, not a caveat for the README.

**1.7.3** No command may require the window. Add a `ui: minimized` variant to the integration pass: full build, tweak, meter read, panic, snapshot, `/ab` all with the window iconified. Meter is the important one, because a minimized host that stops processing audio would make the whole plugin pointless and would otherwise only show up during the demo.

**1.7.4** A wedged driver is repaired, not reported. Kill REAPER by force, relaunch, confirm `DEVINFO.device_closed` is true, confirm `ensure_audio()` reopens it, and confirm the pane would say `REOPENED DEVICE` rather than `NO SIGNAL`.

**Done when:** from REAPER fully closed, one tool call produces `ui: "minimized"`, focus untouched, device open, and a rig that plays guitar, in under eight seconds cold.

---

## Holes found, ranked

| # | Hole | Why it bites | Closed by |
|---|---|---|---|
| 1 | No project guard on mutating commands | Already happened once: a test track landed in The_riff | 1.0.2 |
| 2 | Wire format has 3 unescaped args, no batch | Paths with `|` break; `build_rig` costs 9 round trips | 1.0.6, protocol v2 |
| 3 | Undo blocks are per-command | "One undo reverts a tweak" is false the moment a preset touches four params | 1.0.3 |
| 4 | `FX_INFO` has no normalized value | Verification ends up parsing localized `fmt` strings, which is how `-inf` became a silent accept | 1.0.4 |
| 5 | No tab save, close, FX delete or move | Rig tab is unnamed, so it has no identity to guard against | 1.0.5, 1.5.1 |
| 6 | Bridge lives outside the repo and autoloads | Installed copy drifts from source; plugin appears broken with no diagnosable cause | 1.0.1, 1.6.1 |
| 7 | FX index staleness | Writing to index 2 after a delete hits a different plugin, and the number still looks right | 1.3.3 |
| 8 | Single global request slot | Concurrent sessions eat each other's requests and both see a timeout | 1.2.1 |
| 9 | No launch path, so the plugin is useless with REAPER closed | The demo and every first-run judge hits this | 1.7.1 (done: `amphead/launch.py`) |
| 10 | A launched REAPER window steals focus | The plugin becomes "another app that pops up", which is the thing it exists to avoid | 1.7.2 |
| 11 | Tests assumed The_riff is the open project | It is not; REAPER opens blank now, so the fixture has to be explicit | 1.5.5 |
| 9 | Some VSTs expose 0 params until the UI is opened | Resolver reports "not found" on a plugin that is loaded and working | 1.4 addition: if `FX_INFO` returns 0 params, `TrackFX_Show` then re-read once |
| 10 | `I_RECINPUT` encoding was guessed | Armed and monitoring can be true while nothing is actually on the input | 1.0.7, empirical mapping |
| 11 | `TrackFX_AddByName` matches partially | "Shoeless" loading something else, or returning -1 on a name that exists | 1.3.1 resolves first, `FX_ADD` confirms the returned name |
| 12 | `register()` must survive REAPER being closed | Validate gate fails on import, which reads as a plugin bug | 1.1.3 |
| 13 | Default project template pre-loads FX | Chain order is not what the plan assumed | 1.5.2, 1.5.3 |
| 14 | Device round-trip latency is not plugin latency | Reporting 578 ms as "rig latency" is misleading; it is input+output buffering | Label it `device_latency` everywhere |
| 15 | `out/` grows without bound | Thousands of response files from meter polls | Client prunes older than 1 h |
| 16 | `fake_reaper` can drift from the real bridge | Suite green, product broken | Same-commit rule in 1.0 |

## Decisions this needs

1. **Protocol v2 now.** It touches `bridge.lua`, `bridge.py`, `fake_reaper.py`, and every test. Tonight it is one hour; after Steps 1.3 to 1.5 exist it is a day.
2. **Rig lives in a saved file.** Needed for identity, and it means an unsaved scratch tab is no longer acceptable as the rig. The alternative is guarding on track names, which cannot distinguish two tabs.
3. **`-inf` is always a refusal**, even when the plugin means it (a muted output gain is legitimately `-inf`). Cost: some honest writes get refused. Benefit: no silent wrong value ever ships.

## Time budget (Phase 1 = Friday morning)

| Step | Hours |
|---|---|
| 1.0 bridge v2 | 2.0 |
| 1.1 manifest + validate | 0.5 |
| 1.2 client additions | 0.5 |
| 1.3 resolver | 1.0 |
| 1.4 verified writes | 1.5 |
| 1.5 rig identity/build/teardown | 1.5 |
| 1.6 deployment | 0.75 |
| 1.7 cold start, no UI | 0.5 (launcher written; focus test and minimized-pass remain) |
| **Total** | **8.25** |

That is more than one morning. 1.0 and 1.7.1 are already done, so the remainder is about 6.5 h. Split: 1.1 Friday morning, 1.2 to 1.4 Friday late morning, 1.5, 1.6 and 1.7.2-1.7.4 Friday afternoon, with Phase 2 sliding to Friday evening. If the split is refused, drop 1.6 to Phase 4 (install) and keep 1.0, because an uninstallable-but-correct plugin beats an installed plugin that can write into the song.
