"""In-process fake REAPER that speaks protocol v2.

The point of this double is to reproduce the failures the real bridge produces, not to be
a nice API. It models a project with a path, a track with a chain, and parameters whose
formatted read-back can lie:

* ``Shoeless`` amp with ``Gain``, ``Drive``, ``Tone``, ``Output Gain``
* setting ``Gain`` to 0.35 normalized reads back ``-inf`` (the real bug seen on this machine)
* ``Drive`` matches two parameters, so a resolver must refuse it as ambiguous
* ``Tone`` ignores writes entirely, so verification has to catch a silent no-op

Modes: ``ok`` (normal), ``silent`` (heartbeat, never answers: modal dialog),
``garbage`` (answers non-JSON), ``old`` (proto 1), ``closed`` (no thread at all).
"""

from __future__ import annotations

import json
import math
import threading
import time
from pathlib import Path
from urllib.parse import quote, unquote

PROTO = 2


def _enc(v: object) -> str:
    return quote(str(v), safe="")


def _dec(v: str) -> str:
    return unquote(v)


def _ok(**fields) -> dict:
    return {"ok": True, **fields}


def _err(msg: str, **fields) -> dict:
    return {"ok": False, "error": msg, **fields}


ARM_SAFE_MASTER_DB = -60.0


class FakeReaper:
    def __init__(self, root: str | Path, mode: str = "ok", latency: float = 0.0,
                 traps: bool = False):
        self.root = Path(root)
        self.mode = mode
        self.traps = traps
        self.latency = latency
        self.out = self.root / "out"
        self.req = self.root / "request.txt"
        self.alive = self.root / "alive"
        self.out.mkdir(parents=True, exist_ok=True)

        self.project = "rig.rpp"
        self.project_path = str(self.root / "rig.rpp")
        self.tempo = 120.0
        self.master_vol = 1.0
        self.tx_open = False
        self.device_closed = False
        self.undo_stack: list[dict] = []
        self.requests: list[str] = []
        # 0.0 means a quiet input. Set it to a real amplitude to model signal arriving, so the
        # verdict can be exercised over the wire instead of only through mocks.
        self.signal = 0.0

        self.tracks: list[dict] = [
            {
                "name": "Gtr",
                "armed": False,
                "monitor": False,
                "recinput": 0,
                "vol": 1.0,
                "fx": [
                    {
                        "name": "VST3: Shoeless Drive Pedal (WaveMind)",
                        "enabled": True,
                        "params": [
                            {"name": "Gain", "min": 0.0, "max": 1.0, "raw": 0.5, "fmt": "5.0"},
                            {"name": "Drive", "min": 0.0, "max": 1.0, "raw": 0.25, "fmt": "2.5"},
                            {"name": "Drive Boost", "min": 0.0, "max": 1.0, "raw": 0.1, "fmt": "1.0"},
                            {"name": "Tone", "min": 0.0, "max": 1.0, "raw": 0.5, "fmt": "5.0"},
                            {"name": "Output Gain", "min": 0.0, "max": 4.0, "raw": 1.0, "fmt": "0.0 dB"},
                        ],
                    },
                    {
                        "name": "VST3: RigSculpt (WaveMind)",
                        "enabled": True,
                        "params": [
                            {"name": "Presence", "min": 0.0, "max": 1.0, "raw": 0.5, "fmt": "5.0"},
                        ],
                    },
                ],
            },
        ]
        if traps:
            # Opt-in second track, because the guitar chain's shape is what most tests assert
            # against. ReaFir's Output Gain takes a raw write of 1.44 and formats it as "-inf",
            # while a normalized write of the same value lands: that is why the retry exists.
            self.tracks.append({
                "name": "Bus",
                "armed": False,
                "monitor": False,
                "recinput": 0,
                "vol": 1.0,
                "fx": [
                    {
                        "name": "VST3: ReaFir (Cockos)",
                        "enabled": True,
                        "params": [
                            {"name": "Output Gain", "min": 0.0, "max": 2.0, "raw": 1.0, "fmt": "0.0 dB"},
                        ],
                    },
                ],
            })

        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> "FakeReaper":
        if self.mode == "closed":
            return self
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def __enter__(self) -> "FakeReaper":
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()

    # -- protocol ----------------------------------------------------------

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                if self.req.exists():
                    line = self.req.read_text(encoding="utf-8", errors="replace").strip()
                    self.req.unlink(missing_ok=True)
                    if line:
                        self.requests.append(line)
                        self._answer(line)
                # Heartbeat always runs: it is what distinguishes "closed" from "blocked".
                self.alive.write_text(str(int(time.time())), encoding="utf-8")
            except Exception:
                pass
            time.sleep(0.005)

    def _answer(self, line: str) -> None:
        parts = line.split("|")
        rid, cmd = parts[0], parts[1].upper() if len(parts) > 1 else ""
        args: dict[str, str] = {}
        for chunk in parts[2:]:
            if "=" in chunk:
                k, v = chunk.split("=", 1)
                args[_dec(k)] = _dec(v)

        if self.mode == "silent":
            return
        if self.latency:
            time.sleep(self.latency)

        if self.mode == "garbage":
            (self.out / f"resp_{rid}.json").write_text("<html>nope</html>", encoding="utf-8")
            return

        payload = self._handle(cmd, args)
        if self.mode != "old":
            payload.setdefault("proto", PROTO)
        payload.setdefault("id", rid)
        # Publish by rename, like the real bridge: a half-written file must never be readable.
        final = self.out / f"resp_{rid}.json"
        tmp = self.out / f".resp_{rid}.tmp"
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        final.unlink(missing_ok=True)
        tmp.replace(final)

    # -- guard -------------------------------------------------------------

    def _guard(self, args: dict) -> dict | None:
        if args.get("new") == "1":
            return None
        if args.get("expect"):
            if self._norm(args["expect"]) != self._norm(self.project_path):
                return _err(
                    f"wrong project: active is {self.project_path}, expected {args['expect']}",
                    active=self.project_path,
                    expected=args["expect"],
                )
            return None
        if self.project_path:
            return _err("guard required", active=self.project_path)
        return _err("unnamed project: create the rig tab first and pass new=1")

    @staticmethod
    def _norm(p: str) -> str:
        return str(p).replace("\\", "/").lower()

    # -- commands ----------------------------------------------------------

    def _track(self, args: dict) -> dict | None:
        try:
            idx = int(args.get("track", 1)) - 1
        except (TypeError, ValueError):
            return None
        if 0 <= idx < len(self.tracks):
            return self.tracks[idx]
        return None

    def _handle(self, cmd: str, args: dict) -> dict:
        if cmd == "PING":
            return _ok(
                proto=1 if self.mode == "old" else PROTO,
                bridge="2.0.0-fake",
                version="7.77/x64",
                script=str(self.root),
                cmds=["PING", "BATCH", "PROJ_OPEN", "PROJ_TAB_CLOSE", "FX_INFO", "FX_SETV", "METER"],
            )
        if cmd == "STATUS":
            return _ok(
                project=self.project,
                path=self.project_path,
                path_method="path",
                tracks=len(self.tracks),
                tempo=self.tempo,
                tx_open=self.tx_open,
            )
        if cmd == "TABS":
            return _ok(
                count=1,
                tabs=[
                    {
                        "i": 0,
                        "project": self.project,
                        "path": self.project_path,
                        "tracks": len(self.tracks),
                        "active": True,
                    }
                ],
            )
        if cmd == "DEVINFO":
            return _ok(
                mode="ASIO",
                device_closed=self.device_closed,
                ident_in="AudioBox ASIO Driver",
                bsize="512",
                srate="44100",
                bps="32",
                device_latency_ms=578.0,
                output_latency_samples=512,
                output_latency_ms=11.61,
                audio_running=True,
                inputs=2,
                outputs=2,
            )
        if cmd == "DEV_REOPEN":
            self.device_closed = False
            return _ok(was_running=True, audio_running=True, mode="ASIO", inputs=2)
        if cmd == "METER":
            tr = self._track(args)
            if not tr:
                return _ok(track=None, master=0.0, clip=False, note="no track")
            return _ok(
                track=int(args.get("track", 1)),
                name=tr["name"],
                l=self.signal,
                r=self.signal,
                master=self.signal,
                clip=False,
                armed=tr["armed"],
                monitor=tr["monitor"],
            )
        if cmd == "TRACK_INFO":
            tr = self._track(args)
            if not tr:
                return _err("no track")
            return _ok(
                index=int(args.get("track", 1)),
                name=tr["name"],
                mute=False,
                solo=False,
                armed=tr["armed"],
                monitor=tr["monitor"],
                recinput=tr["recinput"],
                vol=tr["vol"],
                db=20 * math.log(tr["vol"], 10) if tr["vol"] > 0 else -150,
                pan=0.0,
                fx_count=len(tr["fx"]),
                chain=[{"i": i, "name": f["name"], "bypassed": not f["enabled"]} for i, f in enumerate(tr["fx"])],
            )
        if cmd == "TRACKS":
            return _ok(count=len(self.tracks), names=[t["name"] for t in self.tracks])
        if cmd == "TRACK_ADD":
            g = self._guard(args)
            if g:
                return g
            self.tracks.append(
                {"name": args.get("name", "Track"), "armed": False, "monitor": False,
                 "recinput": 0, "vol": 1.0, "fx": []}
            )
            t = self.tracks[-1]
            return _ok(index=len(self.tracks), name=t["name"],
                       armed=t["armed"], monitor=t["monitor"])
        if cmd == "TRACK_DEL":
            g = self._guard(args)
            if g:
                return g
            tr = self._track(args)
            if not tr:
                return _err("no track")
            self.tracks.remove(tr)
            return _ok(deleted=tr["name"], tracks=len(self.tracks))
        if cmd == "TRACK_VOL":
            g = self._guard(args)
            if g:
                return g
            tr = self._track(args)
            if not tr:
                return _err("no track")
            if "db" in args:
                tr["vol"] = 10 ** (float(args["db"]) / 20)
            elif "vol" in args:
                tr["vol"] = float(args["vol"])
            else:
                return _err("need db= or vol=")
            return _ok(vol=tr["vol"], db=20 * math.log(tr["vol"], 10) if tr["vol"] > 0 else -150)
        if cmd == "DISARM":
            g = self._guard(args)
            if g:
                return g
            tr = self._track(args)
            if not tr:
                return _err("no track")
            tr["armed"] = False
            tr["monitor"] = False
            return _ok(armed=False, monitor=False)

        if cmd == "ARM":
            g = self._guard(args)
            if g:
                return g
            if args.get("confirm") != "1":
                return _err("arming needs confirm=1: the Live switch is the user's, "
                            "not the builder's")
            tr = self._track(args)
            if not tr:
                return _err("no track")
            before_db = 20 * math.log(self.master_vol, 10) if self.master_vol > 0 else -150.0
            lowered = False
            if args.get("allow_loud") != "1" and before_db > ARM_SAFE_MASTER_DB:
                if args.get("stuck_master") == "1":
                    return _err(
                        f"refusing to arm: master is at {before_db:.1f} dB and would not go down "
                        f"(reads {before_db:.1f} dB)",
                        master_db_before=round(before_db, 2), master_db_after=round(before_db, 2))
                self.master_vol = 10 ** (ARM_SAFE_MASTER_DB / 20)
                lowered = True
            tr["armed"] = True
            tr["monitor"] = args.get("monitor") != "0"
            after_db = 20 * math.log(self.master_vol, 10) if self.master_vol > 0 else -150.0
            return _ok(armed=tr["armed"], monitor=tr["monitor"],
                       master_db_before=round(before_db, 2), master_db_after=round(after_db, 2),
                       master_safety="lowered", lowered=lowered)
        if cmd == "SET_INPUT":
            enc = int(args.get("input", -1))
            inputs = 2
            ok_enc = enc == 0 or 1 <= enc <= inputs or 1024 <= enc < 1024 + (inputs // 2)
            if not ok_enc:
                return _err(
                    f"input {enc} is not available on this device ({inputs} inputs): "
                    "use 0 for none, 1..N for a mono channel, 1024.. for a stereo pair",
                    requested=enc, inputs=inputs)
            g = self._guard(args)
            if g:
                return g
            tr = self._track(args)
            if not tr:
                return _err("no track")
            if "input" not in args:
                return _err("need input=")
            tr["recinput"] = int(args["input"])
            return _ok(recinput=str(tr["recinput"]), recmode="0", requested=args["input"], applied=True)
        if cmd == "INPUTS":
            return _ok(
                count=2,
                channels=[{"i": 0, "name": "Analog In 1"}, {"i": 1, "name": "Analog In 2"}],
                has_channel_names=True,
            )
        if cmd == "FX_LIST":
            tr = self._track(args)
            if not tr:
                return _err("no track")
            return _ok(
                count=len(tr["fx"]),
                fx=[{"i": i, "name": f["name"], "enabled": f["enabled"]} for i, f in enumerate(tr["fx"])],
            )
        if cmd == "FX_ADD":
            g = self._guard(args)
            if g:
                return g
            tr = self._track(args)
            if not tr:
                return _err("no track")
            want = args.get("name", "")
            if not want:
                return _err("need name=")
            if "rigsculpt" in want.lower():
                tr["fx"].append({"name": "VST3: RigSculpt (WaveMind)", "enabled": True,
                                 "params": [{"name": "Presence", "min": 0.0, "max": 1.0, "raw": 0.5, "fmt": "5.0"}]})
                return _ok(fx=len(tr["fx"]) - 1, name=tr["fx"][-1]["name"], chain_len=len(tr["fx"]))
            if "shoeless" in want.lower():
                return _err("fx already loaded", query=want)
            return _err("fx not found", query=want)
        if cmd == "FX_PARAMS":
            tr = self._track(args)
            if not tr:
                return _err("no track")
            fx = int(args.get("fx", 0))
            if not (0 <= fx < len(tr["fx"])):
                return _err("no fx")
            params = tr["fx"][fx]["params"]
            return _ok(
                count=len(params),
                fx=fx,
                fx_name=tr["fx"][fx]["name"],
                params=[{"i": i, "name": p["name"], "fmt": p["fmt"]} for i, p in enumerate(params)],
            )
        if cmd == "FX_INFO":
            tr = self._track(args)
            if not tr:
                return _err("no track")
            fx = int(args.get("fx", 0))
            if not (0 <= fx < len(tr["fx"])):
                return _err("no fx", count=0)
            params = tr["fx"][fx]["params"]
            p = int(args.get("param", 0))
            if not (0 <= p < len(params)):
                return _err(f"no param {p}", count=len(params))
            prm = params[p]
            mn, mx = prm["min"], prm["max"]
            norm = (prm["raw"] - mn) / (mx - mn) if mx > mn else 0.0
            return _ok(
                fx=fx,
                fx_name=tr["fx"][fx]["name"],
                param=p,
                name=prm["name"],
                norm=norm,
                raw=prm["raw"],
                min=mn,
                max=mx,
                fmt=prm["fmt"],
                count=len(params),
            )
        if cmd in {"FX_SETV", "FX_SET"}:
            g = self._guard(args)
            if g:
                return g
            tr = self._track(args)
            if not tr:
                return _err("no track")
            fx = int(args.get("fx", 0))
            if not (0 <= fx < len(tr["fx"])):
                return _err("no fx")
            params = tr["fx"][fx]["params"]
            p = int(args.get("param", 0))
            if not (0 <= p < len(params)):
                return _err("no param")
            if "value" not in args:
                return _err("need value=")
            v = float(args["value"])
            prm = params[p]
            mn, mx = prm["min"], prm["max"]
            if args.get("mode") == "norm" or cmd == "FX_SET":
                raw = mn + v * (mx - mn)
            else:
                raw = v
            # Trap: ReaFir Output Gain takes a raw write of 1.44 and reports -inf.
            if ("reafir" in tr["fx"][fx]["name"].lower() and prm["name"] == "Output Gain"
                    and args.get("mode") != "norm" and abs(raw - 1.44) < 0.01):
                prm["fmt"] = "-inf"     # the value the read-back will report
                return _ok(norm=(prm["raw"] - mn) / (mx - mn), raw=prm["raw"], fmt="-inf", mode="raw")
            # Trap: Tone ignores writes, so a write "succeeds" but nothing moves.
            if prm["name"] == "Tone":
                return _ok(norm=(prm["raw"] - mn) / (mx - mn), raw=prm["raw"], fmt=prm["fmt"], mode="norm")
            prm["raw"] = raw
            # Trap: Gain at 0.35 normalized formats as -inf on the real plugin.
            if prm["name"] == "Gain" and abs(((raw - mn) / (mx - mn)) - 0.35) < 0.01:
                prm["fmt"] = "-inf"
            elif prm["fmt"].endswith("dB"):
                prm["fmt"] = f"{raw:.1f} dB"
            else:
                prm["fmt"] = f"{raw * 10:.1f}"
            norm = (raw - mn) / (mx - mn) if mx > mn else 0.0
            return _ok(norm=norm, raw=raw, fmt=prm["fmt"], mode=args.get("mode", "raw"))
        if cmd == "FX_BYPASS":
            g = self._guard(args)
            if g:
                return g
            tr = self._track(args)
            if not tr:
                return _err("no track")
            fx = int(args.get("fx", 0))
            if not (0 <= fx < len(tr["fx"])):
                return _err("no fx")
            tr["fx"][fx]["enabled"] = args.get("on") != "1"
            return _ok(bypassed=not tr["fx"][fx]["enabled"])
        if cmd == "FX_DEL":
            g = self._guard(args)
            if g:
                return g
            tr = self._track(args)
            if not tr:
                return _err("no track")
            fx = int(args.get("fx", 0))
            if not (0 <= fx < len(tr["fx"])):
                return _err("no fx")
            name = tr["fx"].pop(fx)["name"]
            return _ok(deleted=name, chain_len=len(tr["fx"]))
        if cmd == "MASTER_VOL":
            if "db" not in args and "vol" not in args:
                return _ok(vol=self.master_vol, db=20 * math.log(self.master_vol, 10))
            g = self._guard(args)
            if g:
                return g
            if "db" in args:
                self.master_vol = 10 ** (float(args["db"]) / 20)
            else:
                self.master_vol = float(args["vol"])
            return _ok(vol=self.master_vol, db=20 * math.log(self.master_vol, 10))
        if cmd == "PROJ_OPEN":
            path = args.get("path")
            if not path:
                return _err("need path=")
            if args.get("missing") == "1":
                return _err("no such project file", path=path)
            self.project_path = path
            self.project = str(Path(path).name)
            return _ok(path=self.project_path, marked=True, tabs_before=1, tabs_after=2)
        if cmd == "PROJ_SAVE_AS":
            if args.get("silent_copy") == "1":
                # What REAPER actually does with Main_SaveProjectEx options 0: the file is
                # written, the tab stays unnamed. The bridge must refuse rather than mark it.
                return _err("save did not give the project this path",
                            requested=args.get("path", ""), project_name="", opt="0")
            g = self._guard(args)
            if g:
                return g
            path = args.get("path")
            if not path:
                return _err("need path=")
            self.project_path = path
            self.project = str(Path(path).name)
            return _ok(path=self.project_path, marked=True)
        if cmd == "PROJ_TAB_CLOSE":
            g = self._guard(args)
            if g:
                return g
            self.project = ""
            self.project_path = ""
            return _ok(closed=True, tabs_before=1, tabs_after=0)
        if cmd == "TX_BEGIN":
            # Real REAPER drops an undo block that spans two script runs, so the bridge
            # refuses rather than pretending a transaction opened.
            return _err("REAPER cannot hold an undo block between script runs; send the writes as one BATCH")
        if cmd == "TX_END":
            return _err("no transaction is open; grouped writes are one BATCH")
        if cmd == "UNDO":
            if self.tx_open:
                return _err("transaction open, close it first")
            popped = self.undo_stack.pop() if self.undo_stack else None
            return _ok(undone=True, label=(popped or {}).get("label", ""))
        if cmd == "BATCH":
            ops = args.get("ops", "")
            self.tx_open = True
            results, failed_at = [], None
            for i, chunk in enumerate([c for c in ops.split(";") if c], start=1):
                cname, _, rest = chunk.partition(":")
                sub: dict[str, str] = {}
                if rest:
                    for pair in rest.split(","):
                        k, _, v = pair.partition("=")
                        if k:
                            sub[_dec(k)] = _dec(v)
                if args.get("expect") and "expect" not in sub:
                    sub["expect"] = args["expect"]
                if args.get("new") and "new" not in sub:
                    sub["new"] = args["new"]
                resp = self._handle(cname.upper(), sub)
                results.append({"i": i, "cmd": cname.upper(), "ok": bool(resp.get("ok")), "resp": resp})
                if not resp.get("ok"):
                    failed_at = i
                    break
            self.tx_open = False
            if failed_at is None:
                # One BATCH is one undo point; this is what lets a test prove the grouping.
                self.undo_stack.append({"label": args.get("label", "")})
            return {
                "ok": failed_at is None,
                "proto": PROTO,
                "count": len(results),
                "failed_at": failed_at,
                "results": results,
                **({"error": f"batch failed at op {failed_at}"} if failed_at else {}),
            }
        return _err(f"unknown command {cmd}")
