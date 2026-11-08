"""Which block size can this chain actually carry?

A block size is three things at once: how long the callback has to finish (its budget),
how much delay it adds on top of the driver buffer (its cost), and how much room is left
over for the machine to do something else for a moment (its headroom). Picking one by
ear means picking it once, on the quietest day the machine has.

So the rig measures itself instead. The music is a full-scale sine plus a little noise so
the capture runs its loud path rather than a near-silent one, and the answer is the
smallest block whose median render still fits in half the budget. Half, not all: a
callback that uses its whole block every block is one late page fault from a gap, and the
gap is what a player hears as "the amp hiccupped".
"""

from __future__ import annotations

import time

CANDIDATES = (64, 128, 256, 512, 1024)
WARMUP = 5
MEASURED = 40
# A block has to be filled with the time it takes to fill the *next* one, so half the
# budget is the line between "it fits" and "it fits until something else happens".
HEADROOM = 0.5
# The smallest block that survives a quiet soak is not the smallest block a rig can run.
# A soak sees this process and the driver; it cannot see the browser, the recording app or
# the update check that lands two minutes into a take, and every one of those is a missed
# deadline at 64 frames while 256 frames swallows it whole. So the floor is the size a
# player can leave set all day: 5.8 ms a block at 44.1 kHz, still well under the 40 ms
# where playing starts to feel late. Smaller sizes stay reachable by asking for one.
SAFE_FLOOR = 256


def _signal(blocksize: int):
    import numpy as np

    n = blocksize * (WARMUP + MEASURED)
    t = np.arange(n, dtype=np.float32) / 44100.0
    # 110 Hz at -3 dBFS: loud enough to keep the capture and the cabinet off their
    # zero paths, quiet enough that a tanh does not saturate and flatter the timing.
    wave = 0.7 * np.sin(2.0 * np.pi * 110.0 * t)
    rng = np.random.default_rng(7)
    return (wave + 0.01 * rng.standard_normal(n)).astype(np.float32)


def measure(chain, samplerate: int, candidates=CANDIDATES, warmup: int = WARMUP,
            measured: int = MEASURED) -> list[dict]:
    """Time the real chain at each block size. Nothing about the device is involved."""
    from .engine2 import process_chain

    rows = []
    for blocksize in candidates:
        budget_ms = 1000.0 * blocksize / float(samplerate)
        x = _signal(blocksize)
        # The first block at a new size rebuilds the cabinet's partitions and touches fresh
        # pages. That cost is real but it is paid once, so it is warmup, not measurement.
        for i in range(warmup):
            process_chain(x[i * blocksize:(i + 1) * blocksize], chain)
        seen = []
        for i in range(warmup, warmup + measured):
            piece = x[i * blocksize:(i + 1) * blocksize]
            started = time.perf_counter()
            process_chain(piece, chain)
            seen.append((time.perf_counter() - started) * 1000.0)
        seen.sort()
        median = seen[len(seen) // 2]
        rows.append({
            "blocksize": blocksize,
            "block_ms": round(budget_ms, 2),
            "render_ms": round(median, 2),
            "headroom_ms": round(budget_ms - median, 2),
            "realtime": round(median / budget_ms, 3),
            "fits": median <= budget_ms * HEADROOM,
        })
    return rows


def choose(rows: list[dict], floor: int = SAFE_FLOOR) -> int | None:
    """The smallest block that fits with room to spare, and the fallback if none does.

    `floor` is the smallest size the answer may name when a bigger one also fits: a rig that
    can technically run 64 frames should still be left at 256, because the measurement was
    taken while the machine was doing nothing else.
    """
    floor = int(floor or 0)
    for row in sorted(rows, key=lambda r: r["blocksize"]):
        if row["fits"] and row["blocksize"] >= floor:
            return int(row["blocksize"])
    # Nothing at or above the floor fits: name the safest size that still runs in time, so the
    # answer is the least bad one rather than the smallest.
    fitting = [r for r in rows if r["render_ms"] <= r["block_ms"] and r["blocksize"] >= floor]
    if fitting:
        return int(min(fitting, key=lambda r: r["render_ms"] / r["block_ms"])["blocksize"])
    fitting = [r for r in rows if r["render_ms"] <= r["block_ms"]]
    if fitting:
        return int(max(fitting, key=lambda r: r["blocksize"])["blocksize"])
    return None


def verdict(rows: list[dict], picked: int | None) -> str:
    """One line a player can read, in the words the rest of the plugin uses."""
    if picked is None:
        return "this chain cannot fill a block on this machine at any size"
    row = next(r for r in rows if r["blocksize"] == picked)
    spare = "with room to spare" if row["fits"] else "with nothing left over"
    return (f"{picked} frames, {row['block_ms']:.1f} ms a block, "
            f"{row['render_ms']:.1f} ms of work, {spare}")


def soak(engine, blocksize: int, seconds: float = 2.0) -> dict:
    """Run the real device at one block size and count what went wrong.

    Render timing alone flatters small blocks: it measures the chain in this process with no
    driver deadline, so a size that looks safe on paper can drop blocks on the wire. The
    honest test is the wire. A quiet chain still processes every block, so a soak with no
    guitar plugged in still finds a size the box cannot feed.
    """
    engine.blocksize = int(blocksize)
    # Armed at the floor: silent to the speaker, but the whole chain still runs every block,
    # which is the only way a soak measures the capture and the cabinet at all.
    was = engine.chain.armed, engine.chain.master_db
    engine.update(armed=True, master_db=-60.0)
    if engine.running:
        engine.stop()
    engine.start()
    started = time.perf_counter()
    time.sleep(max(0.5, seconds))
    got = engine.counters
    mode = engine.mode
    elapsed = time.perf_counter() - started
    engine.stop()
    engine.update(armed=was[0], master_db=was[1])
    dropped = got.get("underflows", 0) + got.get("overflows", 0) + got.get("late", 0)
    return {
        "blocksize": int(blocksize),
        "block_ms": round(1000.0 * blocksize / float(engine.samplerate), 2),
        "seconds": round(elapsed, 2),
        "blocks": got.get("blocks", 0),
        "underflows": got.get("underflows", 0),
        "overflows": got.get("overflows", 0),
        "late": got.get("late", 0),
        "mode": mode,
        "ok": got.get("blocks", 0) > 0 and dropped == 0,
    }


def soak_all(engine, candidates=CANDIDATES, seconds: float = 2.0, progress=None) -> list[dict]:
    rows = []
    for index, blocksize in enumerate(candidates):
        if progress:
            progress(f"{blocksize} frames", index, len(candidates))
        rows.append(soak(engine, blocksize, seconds=seconds))
    return rows


def choose_soak(rows: list[dict], floor: int = SAFE_FLOOR) -> int | None:
    """The smallest block at or above the floor that ran clean, else the least bad one.

    Same rule as choose(): the soak shows what this machine can do while nothing else runs,
    and the size that survives a quiet two seconds is not the size to leave a guitar rig at.
    """
    floor = int(floor or 0)
    clean = [r for r in rows if r["ok"] and r["blocksize"] >= floor]
    if clean:
        return int(min(clean, key=lambda r: r["blocksize"])["blocksize"])
    live = [r for r in rows if r["blocks"] and r["blocksize"] >= floor]
    if not live:
        live = [r for r in rows if r["blocks"]]
    if not live:
        return None
    return int(max(live, key=lambda r: r["blocksize"])["blocksize"])


def soak_verdict(rows: list[dict], picked: int | None, floor: int = SAFE_FLOOR) -> str:
    if picked is None:
        return "the device would not open at any block size"
    row = next(r for r in rows if r["blocksize"] == picked)
    bad = row["underflows"] + row["overflows"] + row.get("late", 0)
    if row["ok"]:
        smaller = [r for r in rows if r["ok"] and r["blocksize"] < picked]
        if smaller:
            return (f"{picked} frames, {row['block_ms']:.1f} ms a block, {row['seconds']:.0f} s "
                    f"with nothing dropped. {smaller[0]['blocksize']} frames also ran clean, "
                    f"but leaves no room for another app to interrupt: ask for it by name if "
                    f"you want it")
        return (f"{picked} frames, {row['block_ms']:.1f} ms a block, "
                f"{row['seconds']:.0f} s with nothing dropped")
    return (f"{picked} frames, {row['block_ms']:.1f} ms a block, "
            f"{bad} dropped block{'s' if bad != 1 else ''}: this chain is too heavy for it")
