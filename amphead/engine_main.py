"""The engine process: one JSON object per line on stdin, one per line on stdout.

It runs out of process on purpose. The audio callback then shares nothing with the
agent, the pane, or a long tool call, and ``panic`` can stop a note even if the
caller is wedged. Everything it can do is a verb here, so the client in
``local_port.py`` is a thin pipe and the pane keeps talking to verbs it already
knows.

Run it with the interpreter that has numpy and sounddevice:

    python -m amphead.engine_main --samplerate 44100 --blocksize 512
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback

from .engine2 import ARM_MASTER_DB, SAFE_MASTER_DB, Engine


class Server:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.capture_name: str | None = None
        self.cab_name: str | None = None
        self.tone = {"bass": 0.0, "mid": 0.0, "treble": 0.0}

    # every verb returns plain json ----------------------------------------
    def status(self) -> dict:
        meter = self.engine.meter
        chain = self.engine.chain
        return {
            "engine": "amphead2",
            "running": self.engine.running,
            "mode": self.engine.mode,
            "samplerate": self.engine.samplerate,
            "blocksize": self.engine.blocksize,
            "latency_ms": self.engine.latency_ms,
            "armed": chain.armed,
            "master_db": chain.master_db,
            "input_gain_db": chain.input_gain_db,
            "output_gain_db": chain.output_gain_db,
            "capture": self.capture_name,
            "cab": self.cab_name,
            "tone": dict(self.tone),
            "meter": {
                "peak_in": meter.peak_in,
                "peak_out": meter.peak_out,
                "rms_out": meter.rms_out,
                "overflows": meter.overflows,
                "underflows": meter.underflows,
                "blocks": meter.blocks,
            },
            "last_error": self.engine.last_error,
        }

    def devices(self) -> dict:
        import sounddevice as sd

        rows = sd.query_devices()
        return {
            "default_input": sd.default.device[0],
            "default_output": sd.default.device[1],
            "inputs": [{"index": i, "name": r["name"]} for i, r in enumerate(rows) if r["max_input_channels"] > 0],
            "outputs": [{"index": i, "name": r["name"]} for i, r in enumerate(rows) if r["max_output_channels"] > 0],
        }

    def start(self, **kw) -> dict:
        engine = self.engine
        if engine.running:
            return {"mode": engine.mode, "already": True}
        for key in ("samplerate", "blocksize", "input_device", "output_device"):
            if kw.get(key) is not None:
                setattr(engine, key, int(kw[key]))
        mode = engine.start()
        # Started means an open device, not a live amp: the master stays at the floor
        # until arm() asks for a level, so "armed: false" is silent rather than quiet.
        engine.update(master_db=SAFE_MASTER_DB, armed=False)
        return {"mode": mode, "latency_ms": engine.latency_ms, "last_error": engine.last_error}

    def stop(self) -> dict:
        self.engine.stop()
        return {"running": False}

    def load_capture(self, path: str) -> dict:
        from .nam import WaveNet

        model = WaveNet.load(path)
        self.engine.update(amp=model)
        self.capture_name = str(path)
        return {"capture": self.capture_name, "receptive_field": model.receptive_field}

    def clear_capture(self) -> dict:
        self.engine.update(amp=None)
        self.capture_name = None
        return {"capture": None}

    def load_cab(self, path: str, gain_db: float | None = None) -> dict:
        from .cab import Cab

        cab = Cab.from_wav(
            path,
            gain_db=0.0 if gain_db is None else float(gain_db),
            rate=self.engine.samplerate,
        )
        self.engine.update(cab=cab)
        self.cab_name = str(path)
        return {
            "cab": self.cab_name,
            "taps": int(cab.length),
            "samplerate": cab.samplerate,
            "resampled": cab.samplerate != cab.file_rate,
            "file_rate": cab.file_rate,
        }

    def clear_cab(self) -> dict:
        self.engine.update(cab=None)
        self.cab_name = None
        return {"cab": None}

    def tone_set(self, **kw) -> dict:
        from .tone import ToneStack

        chain = self.engine.chain
        stack = chain.tone
        if stack is None:
            stack = ToneStack(self.engine.samplerate)
            self.engine.update(tone=stack)
        for name in ("bass", "mid", "treble"):
            if kw.get(name) is not None:
                stack.set(name, float(kw[name]))
                self.tone[name] = float(kw[name])
        return dict(self.tone)

    def gain(self, **kw) -> dict:
        for name in ("master_db", "input_gain_db", "output_gain_db"):
            if kw.get(name) is not None:
                self.engine.set_gain(name, float(kw[name]))
        chain = self.engine.chain
        return {
            "master_db": chain.master_db,
            "input_gain_db": chain.input_gain_db,
            "output_gain_db": chain.output_gain_db,
        }

    def arm(self, master_db: float | None = None) -> dict:
        self.engine.update(
            armed=True,
            master_db=ARM_MASTER_DB if master_db is None else float(master_db),
        )
        chain = self.engine.chain
        return {"armed": chain.armed, "master_db": chain.master_db}

    def disarm(self) -> dict:
        chain = self.engine.update(armed=False)
        return {"armed": chain.armed, "master_db": chain.master_db}

    def panic(self) -> dict:
        chain = self.engine.panic()
        return {"armed": chain.armed, "master_db": chain.master_db}

    def meter(self) -> dict:
        m = self.engine.meter
        return {
            "peak_in": m.peak_in,
            "peak_out": m.peak_out,
            "rms_out": m.rms_out,
            "overflows": m.overflows,
            "underflows": m.underflows,
            "blocks": m.blocks,
        }

    def render(self, source: str, target: str, block: int | None = None) -> dict:
        """Offline render through the live chain. Same code as the callback."""
        import numpy as np
        import wave

        with wave.open(source, "rb") as wav:
            channels = wav.getnchannels()
            width = wav.getsampwidth()
            rate = wav.getframerate()
            raw = wav.readframes(wav.getnframes())
        if width != 2:
            raise ValueError("render source must be a 16 bit wav")
        x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
        if channels > 1:
            x = x.reshape(-1, channels).mean(axis=1)
        y = self.engine.render(x, block)
        pcm = np.clip(y, -1.0, 1.0)
        pcm = (pcm * 32767.0).astype("<i2")
        with wave.open(target, "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(rate)
            out.writeframes(pcm.tobytes())
        return {
            "target": target,
            "samplerate": rate,
            "seconds": round(x.size / float(rate), 3),
            "peak_out": float(np.max(np.abs(pcm)) / 32768.0) if pcm.size else 0.0,
        }

    # dispatch -------------------------------------------------------------
    VERBS = {
        "status": "status",
        "devices": "devices",
        "start": "start",
        "stop": "stop",
        "load_capture": "load_capture",
        "clear_capture": "clear_capture",
        "load_cab": "load_cab",
        "clear_cab": "clear_cab",
        "tone": "tone_set",
        "gain": "gain",
        "arm": "arm",
        "disarm": "disarm",
        "panic": "panic",
        "meter": "meter",
        "render": "render",
    }

    def handle(self, request: dict) -> dict:
        verb = request.get("cmd") or request.get("verb")
        if verb in ("quit", "exit"):
            return {"ok": True, "stop": True}
        name = self.VERBS.get(verb or "")
        if name is None:
            return {"ok": False, "error": f"unknown command {verb!r}"}
        kwargs = {k: v for k, v in request.items() if k not in ("cmd", "verb")}
        try:
            return {"ok": True, "result": getattr(self, name)(**kwargs)}
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def serve(stdin=None, stdout=None) -> int:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    server = Server(Engine())
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            reply = {"ok": False, "error": f"bad json: {exc}"}
        else:
            try:
                reply = server.handle(request)
            except Exception as exc:                  # a verb must never kill the engine
                reply = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                traceback.print_exc(file=sys.stderr)
        stdout.write(json.dumps(reply) + "\n")
        stdout.flush()
        if reply.get("stop"):
            break
    server.engine.stop()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AmpHead 2 engine process")
    parser.add_argument("--samplerate", type=int, default=44100)
    parser.add_argument("--blocksize", type=int, default=512,
                        help="frames per callback (512 is the measured default: same round "
                             "trip as 128 on kernel streaming, less CPU, no starved blocks)")
    parser.add_argument("--input", type=int, default=None)
    parser.add_argument("--output", type=int, default=None)
    args = parser.parse_args(argv)
    server = Server(Engine(samplerate=args.samplerate, blocksize=args.blocksize,
                           input_device=args.input, output_device=args.output))
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            reply = {"ok": False, "error": f"bad json: {exc}"}
        else:
            try:
                reply = server.handle(request)
            except Exception as exc:
                reply = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        sys.stdout.write(json.dumps(reply) + "\n")
        sys.stdout.flush()
        if reply.get("stop"):
            break
    server.engine.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
