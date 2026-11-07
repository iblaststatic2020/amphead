# Engine measurements (Windows, AudioBox USB, no DAW)

Every number here came from a run on this machine, with
`amphead/engine2.py` and a real capture + cabinet
(Bugera 6262 lead, Aurora 212 IR). Nothing is estimated.

## Which audio path can actually play

The same hardware reports very different latency depending on which Windows audio
path opens it. Measured round trip (input stream + output stream, as reported by
PortAudio):

| path | round trip | verdict |
|---|---|---|
| WASAPI shared (device default) | 106-122 ms | unplayable, and it is the fallback nobody wants |
| WASAPI exclusive | refused | `Incompatible host API specific stream info` for every rate (44.1/48 k), channel count (1/2) and sample format (float32/int16/int24/int32) |
| WDM-KS (kernel streaming) | **20.0 ms** | what the engine picks |

The WASAPI endpoint on this device advertises `default_low_output_latency = 90 ms`,
and no block size moves it (64/128/256 frames all land at 91-105 ms), so the shared
path cannot be tuned into shape. Exclusive is refused by the driver outright rather
than by a format mismatch. WDM-KS gives 10 ms in and 10 ms out on the same box.

`choose_devices()` therefore prefers a WDM-KS pair, falls back to WASAPI, and is
silent about it above the seam: `mode` reports `wdm-ks`, `wasapi-exclusive` or
`wasapi-shared` so the pane and the agent see which one is running.

## Block size

Kernel-streaming latency comes from the driver buffer, not the block, so block size
here is free latency-wise and expensive elsewhere:

| frames | round trip | CPU, one core | starved output blocks in 60 s |
|---|---|---|---|
| 128 | 20.0 ms | 32% | 9 |
| 256 | 20.0 ms | 20% | 1 |
| 512 | 20.0 ms | 15% | 0 |

Default is 512. Smaller blocks buy no latency on this path and cost reliability,
because a Python callback that is late by one block is an audible gap.

## Gain staging

A synthetic DI at -18 dBFS peak through the capture and cabinet, engine armed:

| setting | out peak | out RMS | clipped samples |
|---|---|---|---|
| disarmed (master -60) | -180.0 dBFS | -180.0 dBFS | 0 |
| armed, master -6 (default) | -2.9 dBFS | -14.4 dBFS | 0 |
| armed, master 0 | 0.0 dBFS | -8.5 dBFS | 920 |
| armed -6, bass +6 | -1.5 dBFS | -13.7 dBFS | 0 |
| armed -6, treble +6 | 0.0 dBFS | -11.9 dBFS | 6 |
| armed -6, mid -6 | -3.5 dBFS | -15.5 dBFS | 0 |

So the chain is about +15 dB at the default and arming at 0 dB clips a normal DI.
-6 dB is the default for that reason. A treble boost of +6 dB still reaches the
ceiling on a hot DI, which is a headroom question, not a bug: pull the master or the
interface gain, exactly as with a real amp.

## Stream health

60 seconds live, 512 frames, chain armed, zero overflows and zero starved blocks.
The hand-off queue between the two streams starts primed with three blocks of
silence so the output side is never the one waiting.

One starved block at the very start of a run is normal: the output stream asks for
its first block before the input stream has produced one. It is silence at t=0,
not a glitch mid-play.
