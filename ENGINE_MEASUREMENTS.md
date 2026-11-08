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

The driver buffer is fixed at about 10 ms either way on this interface, and the block
sits on top of it, so block size is *not* free latency-wise. The number a player feels
is the driver buffer plus one block, which is what the engine now reports as
`round_trip_ms`. Measuring only the driver figure is how a 512-frame build looked as
quick as a 256-frame one while sounding a block slower in the hands.

| frames | driver buffer | one block | felt round trip | median render* | headroom | starved or overrun |
|---|---|---|---|---|---|---|
| 512 | 9.98 ms | 11.61 ms | 21.6 ms | 3.06 ms | 8.55 ms | 0 in 5 s |
| 256 | 9.98 ms | 5.80 ms | **15.8 ms** | 2.47 ms | 3.33 ms | **0 in 60 s** |
| 128 | 9.98 ms | 2.90 ms | 12.9 ms | 2.19 ms | 0.71 ms | 17 overruns in 30 s |

*WaveNet capture plus the 262 144-tap cabinet, 44.1 kHz, same box. The per-call
overhead dominates at small blocks, which is why 128 has almost no room left: the
callback itself is 2.19 ms of a 2.90 ms block.

Default is 256. 128 is 3 ms quicker and audibly glitchy when anything else on the
machine looks at the disk, which is the trade a CPU-heavy amp cannot take.

## Gain staging

A synthetic DI at -18 dBFS peak through the capture and cabinet, engine armed:

| setting | out peak | out RMS | clipped samples |
|---|---|---|---|
| disarmed (master -60) | -180.0 dBFS | -180.0 dBFS | 0 |
| armed, master -6 (the old fixed level) | -2.9 dBFS | -14.4 dBFS | 0 |
| armed, master 0 | 0.0 dBFS | -8.5 dBFS | 920 |
| armed -6, bass +6 | -1.5 dBFS | -13.7 dBFS | 0 |
| armed -6, treble +6 | 0.0 dBFS | -11.9 dBFS | 6 |
| armed -6, mid -6 | -3.5 dBFS | -15.5 dBFS | 0 |

So the chain is about +15 dB at -6 dB and arming at 0 dB clips a normal DI. Nothing arms
at a fixed level any more: the switch opens at whatever the master knob shows, and a fresh
amp shows the floor, so the first press is silent rather than loud. -6 dB is the level to
start from once you are raising it by ear. A treble boost of +6 dB still reaches the
ceiling on a hot DI, which is a headroom question, not a bug: pull the master or the
interface gain, exactly as with a real amp.

## Cabinets longer than a block (the shaky-audio bug)

The first cabinet convolution transformed the whole IR once per block. That is fine
for a 2048-tap IR and fatal for a real close-mic'd 4x12:

| cabinet | taps | per 512-frame block | share of the 11.6 ms block |
|---|---|---|---|
| Vintage 30 4x12 close, whole-IR transform | 262 144 | **13.6 ms** | **117%** |

Partitioned, the same cabinet costs 1024 partitions of 256 frames and about 2.5 ms of
a 5.8 ms block alongside the capture, measured above.

At 117% the callback cannot finish one block before the next is due, so the queue
drains and the output starves: the "shaky, cutting in and out" a player hears is
literally dropped blocks. Length is the cause, not the file.

`amphead/cab.py` now uses partitioned overlap-save: the IR is cut into partitions of
one block, each transformed once at load time, so a block costs one forward FFT,
one multiply-accumulate across partitions and one inverse FFT. The same Vintage 30
IR:

| | per block | share of the 11.6 ms block |
|---|---|---|
| whole-IR transform | 13.6 ms | 117% |
| partitioned | **1.83 ms** | **16%** |

Cost per block is now flat in IR length. Exactness is pinned by tests against a full
convolution for IRs up to 8192 taps at 512 frames, and against 200 000 taps for the
wall-clock budget.

## Stream health

One duplex stream, 512 frames, chain armed with the 262 144-tap cabinet: ten seconds
per run, zero starved blocks and zero overflows at every latency setting tested.

The engine used to run two independent streams and hand blocks over a queue. That
queue sits in the signal path, so its depth was added latency: the six blocks of
cushion that cured the starved start were also 70 ms between the string and the
speaker, on top of the device's own 20 ms. A player hears that, and blames their
hands. A duplex stream reads and writes in one callback, so there is nowhere for a
queue to live and the mode name says `+queue` when the fallback is in use.

## Latency

The driver calls 10 ms per direction "low", which is 20 ms round trip. It honours a
smaller request and reports back the figure it took:

| asked per direction | reported round trip | blocks in 10 s (expect ~860) | starved |
|---|---|---|---|
| 3 ms | 6.0 ms | 242 | 0 |
| 4 ms | 8.0 ms | 564 | 0 |
| **5 ms** | **10.0 ms** | **850** | **0** |
| 8 ms | 16.0 ms | 855 | 0 |
| 10 ms | 20.0 ms | 860 | 0 |

3 and 4 ms open without complaint but only deliver a quarter and two thirds of the
blocks they should, i.e. the device is running behind without flagging a thing, so
they are not usable however quiet the xrun counter is. 5 ms per direction is the
tightest that holds realtime on this box and it is the default; `AMPHEAD_LATENCY_MS`
raises it for a machine that cannot. Asking for more than the device suggests is
always safe; asking for less is what has to be measured.

## The two trim knobs

In and out are digital trims either side of the capture, `-24 .. +24 dB`, and both
are stored separately from the master:

- **in** sits before the capture. It is how a quiet DI is made to drive an amp, so it
  changes tone as much as level, which is why it is not the master.
- **out** sits after the cabinet. It is how two captures are balanced against each
  other without moving the master off a level that is known to be safe.

Neither is allowed past 24 dB: beyond that the capture is already clipping whatever
the master says, so a wider range would be a distortion control wearing a level's
label. The master keeps its own `-60 .. +12 dB` range on top of both.
