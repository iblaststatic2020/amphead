# Demo shotlist — AmpHead, under 90 seconds

Rules: no API keys, no network, nothing on screen the viewer cannot verify. Guitar audible in every shot after 0:11. **REAPER's UI appears once, in the first seven seconds, as the problem. After that the only evidence REAPER exists is its taskbar icon** — that is the whole claim of the plugin, so the video has to obey it.

| # | Time | Shot | Spoken / typed | What proves it |
|---|---|---|---|---|
| 1 | 0:00-0:07 | REAPER open, one armed track, amp sim window front, hands playing | "This is my rig. Every knob needs a mouse, and both my hands are on the guitar." | The problem, stated honestly. Only REAPER UI in the video |
| 2 | 0:07-0:11 | Quit REAPER for real, window gone, desktop bare | "So let's make it not be an app I have to drive." | Nothing is faked later: the host is genuinely not running |
| 3 | 0:11-0:19 | Hermes pane, banner `REAPER NOT RUNNING`. Type `set up a crunchy rhythm tone` | nothing spoken | Failure shown, not hidden |
| 4 | 0:19-0:28 | Taskbar icon appears, **no window ever opens**, caption lists what the agent did: started host, new tab, track, NAM Universal, input, master to -60 dB, **left in standby** (no arm, no monitor) | "It started REAPER itself and left it on the taskbar." | The no-UI claim, on camera |
| 5 | 0:28-0:40 | Pane fills: chain `NAM Universal -> Pro-Q 4`, real values, `11.6 ms out`, banner `STANDBY`. User clicks the pane's LIVE switch, master still at -60 dB, guitar silent | "It built the chain and stopped there. It will not arm itself, and the master is at minus sixty, so nothing can howl." | The one thing the user touches is a switch, not REAPER |
| 5a | (inside 5) | Pane slider raised one notch, `-60 -> -24 dB`, guitar audible, caption `level 1 of 5` | "Now I bring the level up myself, in steps it reads back to me." | User-owned gain, verified values |
| 6 | 0:40-0:50 | Type `more gain, less fizz`, values move, caption `Amp Input 4.2 -> 6.8 dB, Treble 5.0 -> 4.6, both verified` | "Every change is read back from the plugin, not assumed." | The differentiator |
| 7 | 0:50-1:00 | Ask for a value the plugin refuses; tool answers `plugin reported -inf`, nothing changes | "When the plugin disagrees, it refuses instead of lying." | Reliability on camera |
| 8 | 1:00-1:10 | `/ab clean` — exactly three diff lines, guitar A/B by ear | "One command, back to the clean take." | Snapshots and undo |
| 9 | 1:10-1:18 | Pull the guitar cable, pane reads `NO SIGNAL` within a second, plug back in, back to `READY` | "And it tells me when the cable is out, instead of me guessing." | Error state a player actually meets |
| 10 | 1:18-1:24 | Close on pane READY, meter moving, taskbar icon still there, hands still playing | "I never opened REAPER." | Close on the claim |

Hard cuts, no intro card, no logo, no music bed. Captions on every typed line and on every verdict word, because the demo gets watched muted first.

Fallback take: same script, pre-recorded, kept if the live capture has any audio dropout. Never ship a take where the meter does not move, and never ship a take where a REAPER window appears after 0:11.
