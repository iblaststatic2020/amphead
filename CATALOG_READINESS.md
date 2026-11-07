# Catalog readiness: what the docs require and where we fall short

Audit date: 2026-11-07, against HEAD `37a66885f2f9a57ead1437ac5ed9c0386509694d`.

Verdict: **the plugin qualifies, the submission does not yet.** Nothing found here says AmpHead
is ineligible or that a plugin fronting another application is out of bounds. What is missing is
submission mechanics (public repo, correct pin) and three reviewer-facing behaviours (application
declaration, complete disclosure, unattended-run proof).

Sources, all local copies of the installed Hermes tree unless noted:

| Ref | File |
|---|---|
| P | `website/docs/developer-guide/plugins/index.md` |
| C | `website/docs/developer-guide/plugins/catalog-submission.md` |
| A | `website/docs/developer-guide/plugins/application-declarations.md` |
| D | `website/docs/developer-guide/desktop-plugin-sdk.md` |
| T | `website/docs/developer-guide/adding-tools.md` |

## 1. Why it counts as a plugin

The docs define a plugin by shape and surface, not by novelty: a directory with `plugin.yaml` and
at least one entrypoint Hermes loads (`__init__.py`, `desktop/plugin.js`, `plugin.json`,
`dashboard/manifest.json`), extended through `ctx.register_*` and the Desktop plugin SDK, never by
patching core (`C:24-30`).

Fronting another application is an explicitly supported category, not a grey area:

- `P` says plugins that "integrate someone else's product or project" belong in a standalone repo,
  and calls that "a coupling-and-maintenance decision ... not a quality bar".
- `A` is a whole page for plugins "whose MCP server fronts a desktop application", including
  per-OS presence checks and a `missing_app` result.

The contest's only relevance filter is a scoring preference, from the 2026-11-04 announcement:
"Every kind of plugin is eligible ... Judges score it on usefulness, reliability, polish and
creativity, and they favor plugins that do more than a prompt or a skill could." A prompt or a
skill cannot group ten parameter writes into one undo point, diff snapshots to prove a change, or
refuse to call a rig READY when the master is parked. That is the clause AmpHead clears.

Current pass state: `hermes plugins validate .` exits 0 on all 16 checks, including `loadable`,
`declared tools`, `security scan`, `no core override`, `desktop surface`. Manifest and catalog
`provides_tools` agree at 12 entries. `provides_hooks`, `provides_middleware`, `requires_env` are
correctly empty (`__init__.py` registers tools only).

## 2. Blockers

### B1. The repository is private

`C:21` is unambiguous: "A public repository. The `repo` URL is an `https://` URL anyone can
clone." `gh repo view` reports `PRIVATE` for `iblaststatic2020/hermes-reaper-amp`. The contest
requires a catalog PR, so a private repo fails before a judge sees anything.

Decision 2026-11-07: this stays private until the entry is ready, because it is a competition and
an early public flip hands the work to other entrants. Nothing else in this document is blocked by
that: the declaration, the disclosure, the unattended test and the meter work all land in the
private repo first.

Fix, at submission time only: sweep history once for secrets
(`git log -p --all | grep -in "token\|api_key\|password\|secret"`), flip the repo public, then
confirm `gh repo view --json visibility` reports `PUBLIC` and an anonymous clone works. Current
tree has no credentials; confirm history agrees. Keep `LICENSE` in place.

### B2. The pin and version are stale

`plugin-catalog/amphead.yaml` pins `f707b22e2389f55c161332fabf65557fa58da1f0`, which is several commits
behind HEAD. `C` requires "A loadable plugin at the commit you pin"; rules 1 and 2 require a full
40-character SHA and a version bump on every submission.

Fix: cut the PR from the final commit, not from today. Bump `version` in `plugin.yaml`, copy it into
the catalog entry, set `sha` to `git rev-parse HEAD`, then run
`hermes plugins validate . --install-deps` against that exact checkout.

## 3. Gaps a reviewer will raise

Ordered by how likely a reviewer is to ask about it.

### G1. No application declaration for REAPER

`A:1-10` describes the mechanism: "A plugin whose MCP server fronts a desktop application declares
which application that is and what the server needs of it. The core evaluates the declaration on the
host and gates the server's tools ... on the answer." Today the plugin has no declaration, so with
REAPER missing the tools attempt work and explain themselves in prose. `A` also spells out
`requires_apps:` skill gating, which we do not need yet because we ship no skill.

This is optional for catalog admission (CI does not require it), but it is the documented way to
name the app, and it gets `missing_app`, `version_too_old` and `unsupported_os` for free instead of
our hand-written sentences.

Fix: register one declaration for REAPER, Windows block only (`executable` check on
`reaper.exe` under the standard install roots, plus `HKLM`/registry fallback if the doc's roots
miss a portable install), `min_version` 7.0. Then have `amphead_setup` read the availability result
instead of hand-rolling `find_reaper()`.

**Done.** `amphead/appdecl.py` holds the declaration (Windows block, three install roots plus
`HKLM\SOFTWARE\REAPER` / `WOW6432Node`, `min_version` 7.0) and registers it from the plugin's
`__init__`, keeping the returned declaration so `amphead_setup`, `launch.py` and the dashboard's
`/setup` route read one answer instead of four hand-rolled probes. `state()` maps the host's answer
onto four words, `available` / `missing_app` / `version_too_old` / `unsupported_os` (plus `unknown`
when the declaration or its reader is not there), and `sentence()` turns each into the sentence a
user actually gets. The locator still has a fallback: if the host's reader is missing the plugin
searches the same roots itself rather than going dark.

One wrinkle worth naming in the PR: the developer-guide page documents
`hermes_platform.resolver.availability` as the reader, but that module is not in the published
list `hermes plugins validate` checks against (`hermes_cli/data/plugin_api.json`), so validation
warns "private core import" on the line that imports it. `hermes_platform.declaration` is on the
list and is imported plainly. The availability import sits inside the guard's `try` precisely
because of this: if a release moves it, the plugin degrades to `unknown` and falls back to its own
search instead of failing to load. That is a documented-module-not-yet-listed gap, and the
alternative, hand-rolling REAPER version detection out of the PE version resource, would be worse
than one warning.

### G2. Rule 13 disclosure is thin in three specific places

Rule 13 asks for "network calls to third-party services, reads outside the plugin's own data, shell
commands, long-running background processes, stored credentials". The catalog description already
covers the Lua bridge, `__startup.lua` append with backup and uninstall, arming, writes, master
fader, its own `.rpp`, no network, no audio, Windows only. Missing:

1. **PowerShell is not named at all.** `amphead/launch.py` runs `powershell -NoProfile
   -ExecutionPolicy Bypass -Command ...` from seven places: launching REAPER, either through the
   hidden launcher (`:80`, 60 s timeout) or `Start-Process` minimized (`:85`), minimizing the
   window once it exists (`:142`), the visibility probe that gates `/focus` (`:172`), the process
   alive check (`:312`), the process count (`:334`), and `Stop-Process` on teardown (`:380`).
   Undisclosed shell execution is the kind of thing a reviewer finds on their own and turns into a
   change request.
2. **The background channel is not described as long running.** Requests and responses are files
   under the Hermes data folder, polled by the Python side at 150 ms while a request is in flight
   (`amphead/bridge.py:593`), driven by a Lua loop inside REAPER. That is a persistent background
   process, which rule 13 names.
3. **Reads are understated.** The plugin reads track names, FX chain names, every parameter name
   and value the loaded plugin publishes, the master fader value and the open project's path, from
   a session it did not create. "Reads outside the plugin's own data" is exactly this.

Fix: one paragraph, pasted into both the catalog `description` and the README under a
`## What this touches` heading. Draft in section 5.

**Done.** The paragraph is in `plugin-catalog/amphead.yaml` as the second block of the `description`
and in `README.md` under `## What this touches`, naming all seven PowerShell call sites' work, the
`__startup.lua` append with its backup and uninstall, the long-lived file channel with its 150 ms
rate, the reads from a session the plugin did not create, and the four things it does not do.
`tests/test_disclosure.py` is the pin: it folds the YAML the way YAML folds, squashes whitespace so
wrapping is free to differ, and asserts the two copies are the same words. Six tests, and they
fail on either side being edited alone.

`plugin.yaml`'s own `description` stays a short pitch on purpose. The catalog entry is what a
reviewer and a user read, and a two-paragraph description in a `hermes plugins list` row is worse
for the reader, not better for the disclosure.

### G3. Unattended behaviour is unproven

Rule 12 forbids routing around approvals and requires that a plugin not stall when nobody is there.
AmpHead refuses `amphead_live` without `confirm=1` and no-ops `/focus` when REAPER is hidden, which
is the right design, but it has never been exercised from a gateway or cron run with the desktop
closed. The `_powershell` calls have 30 s and 15 s timeouts, but `minimize_and_wait` waits up to
`max_wait + 20` seconds, and nothing tests what a run from a headless gateway actually returns.

Fix: one integration test that runs `amphead_ready`, `amphead_status` and a refused `amphead_live`
with no desktop attached, asserting each returns within 5 s with a readable sentence and no
exception. That is the evidence to cite when asked "what happens if a cron job calls this?".

**Done.** `tests/test_unattended.py` runs each tool in its own process with stdin closed and an
engine that has no rig, so nothing launches and no fader moves. It asserts the answer is one line
with no traceback, that the line names a state a person can act on, that every call comes back
inside a 5 s wall-clock budget with the cold interpreter counted, and that a live request without
`confirm` is refused rather than attempted. That last one is deliberately not the only shape
asserted: with no rig open the refusal reads `NOT LIVE: no project is open, call amphead_setup
first`, and with a rig open it reads `REFUSED`, so the test pins "a named state plus the next step"
rather than one spelling. It also greps the package for a real `input(` call, so a handler added
later cannot reintroduce a prompt.

### G4. Tool handlers return prose, not JSON — **done**

`T:115` says "Handlers **MUST** return a JSON string (via `json.dumps()`), never raw dicts", and
the plugin guide's tool example does the same. `amphead/tools.py` has zero `json.dumps` calls and
returns formatted sentences (`changed=9 | already there=1 | 1.11s`). It works, the model reads it
fine, and the validator does not check it, so this is a convention gap rather than a rule breach.
Still, a reviewer who reads `T` will notice, and structured returns would let the pane and the agent
share one shape.

**Done, at the boundary.** `amphead/tools.py` now has `json_answer()`, `call_shape()` and
`json_tools()`; `__init__.py` registers `json_tools()`, so what the host and the model receive is one
JSON string per call:

```json
{"tool": "amphead_tone", "ok": true, "summary": "changed=9 | already there=1 | 1.11s",
 "fields": {"changed": "9", "already there": "1"}}
```

The handlers themselves still return the sentence. That is deliberate, not an oversight: the panes,
the log, the demo transcript and 80-odd assertions are written against that sentence, and rewriting
every one of them to reach into a dict to rebuild the same line before the freeze buys nothing a
player can see. The guide's rule is about what a tool call answers, and it now answers in JSON.

`tests/test_tool_json.py` pins it: the registered set is the same set of tools, `__init__.py` wires
`json_tools()` and not `TOOLS`, the wrapper passes arguments through untouched and does not alter the
sentence, a refusal comes back `ok: false`, a sentence with no numbers has no `fields`, and a cold
subprocess calling the registered `amphead_status` handler parses as JSON with a non-empty summary.

`ok` is a judgement on prose, so it is made on the opening clause (everything before the first
colon, where every handler puts its verdict): a leading `REFUSED`/`NOT`/`CANNOT`/`ERROR`/`NO `, or
`NOT` inside that clause, reads as `ok: false`. That covers `REAPER NOT RUNNING: installed at ...`,
which begins with a product name rather than a failure word, without letting a caveat later in a
passing sentence flip it. A handler that invents a new way to say no will read as `ok: true` until
the word joins `FAILURE_WORDS`; that list and `_ok()` are the one place to change.

### G5. Data paths are hand-rolled instead of `plugin_data_dir()` — **done**

`P:1160-1178` says runtime state must never go in the plugin directory and names the sanctioned
home: `plugin_data_dir("amphead")`, which "survives both and follows the active profile". We do not
write inside the plugin tree, so the hard rule is satisfied, but two locations are hand-built:
`rig.data_dir()` under `%LOCALAPPDATA%/hermes/amphead/`, and `demo.py` composing
`$HERMES_HOME/plugin-data/amphead/demo.json` by string, which ignores profiles for the same reason
`get_hermes_home()` is documented. Two profiles on one machine share the rig, the log and the
snapshots.

**Done.** `rig.data_dir()` asks `plugins.plugin_storage.plugin_data_dir("amphead")` first, which
resolves the active profile on each call, so two profiles on one machine now get two rigs, two logs
and two sets of snapshots. The import sits inside the function, so the package still imports with
the host closed (a test run, a checkout without Hermes); when it cannot be imported, or an older host
refuses the name, the old `%LOCALAPPDATA%/hermes/amphead` path answers instead and nothing raises.
`AMPHEAD_HOME` still wins outright, which is what the tests use.

A first run on an existing install copies the old folder into the new home once, and only when the
new home is empty. The old copy is left where it is: quietly deleting a rig someone has been playing
through is worse than a stale directory, and they may still open it with an older build.

`TheHostOwnsTheFolder` in `tests/test_rig_path.py` covers the seam, the copy, the do-not-overwrite
case and the env override, using a stand-in `plugins.plugin_storage` module so the test does not need
Hermes installed. The pre-existing default-path test now pins the fallback explicitly by patching
`_host_data_dir` to `None`, which is the case it was always describing.

### G6. The meter polls every 150 ms

`D:2466` says "Don't poll faster than a few seconds with `host.request`". That rule names
`host.request`; the meter uses `ctx.rest`, so this is not a documented violation, and the earlier
note claiming otherwise was wrong. It remains a real cost: 6.7 requests per second per open pane,
each one a bridge round trip into REAPER.

Fix, cheap: pause the meter when the pane is not visible (`document.visibilityState` plus the
existing pane mount state) and drop to 400 ms when the rig is in STANDBY, where the only thing
moving is a -60 dB floor. Document the 150 ms choice and its reason in the PR, so it reads as a
decision instead of an oversight.

**Done.** One policy in `desktop/plugin.js` covers both panes and the chip: `METER_MS = 150` while
the rig is armed and the pane is on screen, `METER_IDLE_MS = 400` in STANDBY, nothing at all when
`document.visibilityState` is `hidden` (a `visibilitychange` listener brings it back, which is why
the interval can safely be `false`). The `state` query is paused the same way. So an unwatched
pane costs nothing and a parked rig costs 2.5 requests a second instead of 6.7. The vendored face
keeps its layout verbatim and reads the same helper; the marker comment says so.

### G7. Windows only, with no unsupported-OS story

`platforms: [windows]` is honest. The gap is what a macOS or Linux user sees: a loadable plugin
whose tools all fail on a PowerShell call. Adding the G1 declaration gives `unsupported_os` for
free and makes the failure a sentence instead of a traceback.

## 4. Scoring gap, not a rule

The pane is the weakest part of the submission under the announced criteria, because mirroring
eight knobs is the one thing a reviewer can call a wrapper. The agent-side work is what wins: one
undo group for a ten-knob change in 1.11 s, a snapshot diff of 9 as proof, one undo returning to
identical, a parked master read as STANDBY, "make it crunchier" resolving to a preset instead of a
guess.

Demo order should be: chat request, real knob change read back, snapshot diff, one undo, verdict
flip, then five seconds of the pane as evidence that the same judge and the same handlers feed both
surfaces. Not the other way round. This belongs in the 4.6 shotlist as an explicit ordering note.

## 5. Disclosure paragraph to paste

> AmpHead touches the machine in four ways. It runs PowerShell (`-NoProfile
> -ExecutionPolicy Bypass`) to find REAPER, launch it minimized, steer its window, read whether it
> is visible, count running instances, and stop a REAPER it started. It installs a Lua bridge into the REAPER resource path, appending a line to
> `__startup.lua` after backing the file up, and removes both on uninstall. While connected it runs
> a background request channel over files in the Hermes data folder, polled at 150 ms, answered by
> a Lua loop inside REAPER. It reads track names, FX chain names, every parameter name and value the
> loaded plugin publishes, the master fader value and the project path of the session it is pointed
> at. It writes plugin parameters into its own rig tab as one undo group per action, lowers the
> master fader, and saves its rig to its own `.rpp` under the Hermes data folder. It never saves,
> renames or overwrites your song projects, never touches audio files, stores no credentials and
> makes no network calls.

## 6. Work order

Blocking, in this order. The public flip is deliberately last, as one step with the pin, so the
repo is only exposed at the moment of submission:

| # | Item | Done when |
|---|---|---|
| 1 | G1 application declaration | ~~REAPER missing returns `missing_app`; `amphead_setup` reads it; test covers both branches~~ **done** |
| 2 | G2 disclosure | ~~paragraph in catalog `description` and README `## What this touches`; byte-identical text in both~~ **done**, pinned by `tests/test_disclosure.py` |
| 3 | G3 unattended test | ~~three tools called with no desktop, each returns inside 5 s, no exception~~ **done**, plus the refused live |
| 4 | B2 pin and version | `plugin.yaml` version == catalog version; `sha` == `git rev-parse HEAD`; validate clean at that checkout |
| 5 | B1 flip and cut the PR | history sweep clean, visibility `PUBLIC`, anonymous clone works, catalog PR opened against that SHA |

Non-blocking, before or after the PR depending on the freeze:

| # | Item | Done when |
|---|---|---|
| 6 | G5 profile-aware data dir | ~~`rig.data_dir()` returns `plugin_data_dir("amphead")`; suite green; migration tested~~ **done** |
| 7 | G6 meter policy | ~~meter pauses when the pane is hidden; STANDBY interval 400 ms; reason documented~~ **done** |
| 8 | G4 JSON handlers | ~~registered handlers return `json.dumps` with `summary`; live tone proof still shows the same sentence~~ **done at the boundary** |
| 9 | G7 unsupported OS | covered by G1 on non-Windows hosts |

## 7. Deliberately out of scope for v0

- No MCP server. The plugin uses tool handlers plus desktop routes, which the docs allow; the
  application-declaration page describes the MCP variant, and adopting MCP only to use its
  declaration would add a protocol hop for no gain. Register the declaration directly.
- No skill. `requires_apps:` gating is only worth it once a skill exists.
- No macOS or Linux work. `platforms: [windows]` stays, and G1 makes the refusal readable.
- No DSP, tuner or amp modelling. Latency is REAPER's and is reported, not hidden.
