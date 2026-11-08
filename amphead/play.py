"""Play through the engine with no window.

    python -m amphead.play --capture "model.nam" --cab "ir.wav" --arm

It opens the interface, loads the chain, prints the round trip, and runs the meters.
Nothing reaches the speakers unless you pass ``--arm``: a rig that arms itself over a
half-plugged cable is the one bug in this project that can actually hurt somebody.

``--check`` opens the device, listens two seconds, reports, and exits without arming.
That is the "is the cable live" question answered in numbers.

No prompts. This runs as a plain process, so it must never sit waiting for a keystroke
that a scheduled run will never send: ``--arm`` and ``--seconds`` say what to do,
Ctrl-C stops it, and stopping always parks the master at the safe floor first.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from . import lastchain

STATE = lastchain.STATE


def _last_chain() -> dict:
    return lastchain.load()


def _remember(**kw) -> None:
    lastchain.save(**kw)


def devices_name(index) -> str:
    import sounddevice as sd
    try:
        return str(sd.query_devices(int(index))["name"])
    except Exception:
        return str(index)


def _pick(devices: dict, wanted: str | None, kind: str) -> int | None:
    rows = devices.get(f"{kind}s") or []
    if wanted:
        low = wanted.lower()
        for row in rows:
            if low in str(row["name"]).lower() or str(row["index"]) == wanted:
                return int(row["index"])
        raise SystemExit(f"no {kind} device matches {wanted!r}; saw: "
                         + ", ".join(f"{r['index']}:{r['name']}" for r in rows))
    return None


def build_engine(args):
    import sounddevice as sd

    from .cab import Cab
    from .engine2 import Engine, choose_devices
    from .nam import WaveNet
    from .tone import ToneStack

    state = _last_chain()
    capture = args.capture or state.get("capture")
    cab = args.cab or state.get("cab")
    if not capture:
        raise SystemExit("no capture. Say --capture path/to/model.nam (a .nam file), "
                         "or run once with one and it will be remembered.")

    devices = {
        "inputs": [{"index": i, "name": r["name"]} for i, r in enumerate(sd.query_devices())
                   if r["max_input_channels"] > 0],
        "outputs": [{"index": i, "name": r["name"]} for i, r in enumerate(sd.query_devices())
                    if r["max_output_channels"] >= 2],
    }
    dev_in, dev_out, picked = choose_devices(sd)
    if args.input:
        dev_in = _pick(devices, args.input, "input")
    if args.output:
        dev_out = _pick(devices, args.output, "output")
    # Native rate by default: the AudioBox under WDM-KS only answers at 44100, and a
    # cabinet IR at its own rate then needs no resampling at all.
    rate = int(args.rate or sd.query_devices(dev_out)["default_samplerate"])

    engine = Engine(samplerate=rate, blocksize=args.block,
                    input_device=dev_in, output_device=dev_out)
    print(f"picked   : {devices_name(dev_in)} in -> {devices_name(dev_out)} out ({picked})")
    print(f"capture  : {capture}")
    engine.update(amp=WaveNet.load(capture))
    if cab:
        print(f"cabinet  : {cab}")
        engine.update(cab=Cab.from_wav(cab, rate=engine.samplerate))
    else:
        print("cabinet  : none (that will sound bright and buzzy on purpose)")
    engine.update(tone=ToneStack(engine.samplerate))

    _remember(capture=capture, cab=cab)
    return engine


def report(engine) -> None:
    print(f"device   : {devices_name(engine.input_device)} in -> "
          f"{devices_name(engine.output_device)} out, {engine.samplerate} Hz, "
          f"{engine.blocksize} frames, {engine.mode or '?'}")
    latency = engine.latency_ms
    if latency:
        print(f"round trip: {latency:.1f} ms")


def meters(engine, seconds: float, arm: bool) -> None:
    deadline = time.monotonic() + seconds
    peak_in = peak_out = 0.0
    while time.monotonic() < deadline:
        time.sleep(0.4)
        m = engine.meter
        peak_in = max(peak_in, m.peak_in)
        peak_out = max(peak_out, m.peak_out)
        bars = int(min(m.peak_out, 1.0) * 28)
        print(f"\r{'#' * bars:<28} out {m.peak_out:5.3f} in {m.peak_in:5.3f} "
              f"drop {m.overflows}/{m.underflows}   ", end="")
        sys.stdout.flush()
    m = engine.meter
    print(f"\npeak in {peak_in:.4f}, peak out {peak_out:.4f}, "
          f"overflows {m.overflows}, starved blocks {m.underflows}")
    if arm and peak_in < 0.002:
        print("the input heard nothing: cable, interface gain, or the wrong input device")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Play through the amp")
    parser.add_argument("--capture", help="a .nam capture file")
    parser.add_argument("--cab", help="a cabinet impulse response .wav")
    parser.add_argument("--input", help="input device name or index")
    parser.add_argument("--output", help="output device name or index")
    parser.add_argument("--rate", type=int, default=0,
                        help="sample rate (default: the output device's native rate)")
    parser.add_argument("--block", type=int, default=512,
                        help="frames per callback (default 512: same latency as 128 here, "
                             "less CPU, no starved blocks)")
    parser.add_argument("--master", type=float, default=None,
                        help="master in dB when armed (default: the measured -6)")
    parser.add_argument("--arm", action="store_true",
                        help="go live instead of metering silently")
    parser.add_argument("--seconds", type=float, default=None,
                        help="how long to run (default 2 with --check, 300 live, 10 silent)")
    parser.add_argument("--check", action="store_true",
                        help="open the device, listen two seconds, report, exit")
    args = parser.parse_args(argv)

    from .engine2 import ARM_MASTER_DB

    engine = build_engine(args)
    try:
        engine.start()
    except Exception as exc:
        print(f"could not open the device: {type(exc).__name__}: {exc}")
        return 2
    report(engine)

    seconds = args.seconds if args.seconds is not None else (
        2.0 if args.check else (300.0 if args.arm else 10.0))
    try:
        if args.arm:
            # Arming must also lift the master off the safe floor, or "live" is silence.
            engine.update(armed=True, master_db=float(args.master if args.master is not None
                                                      else ARM_MASTER_DB))
            print(f"LIVE, master {engine.chain.master_db:+.1f} dB. Ctrl-C to stop.")
        else:
            print("silent: metering the input with the master at the safe floor "
                  "(add --arm to play)")
        meters(engine, seconds, args.arm)
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        engine.panic()
        engine.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
