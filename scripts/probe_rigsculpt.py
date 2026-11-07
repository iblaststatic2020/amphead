import base64, re, struct, hashlib, sys
import numpy as np
import minihost

V = r"C:\Program Files\Common Files\VST3\RigSculpt.vst3"
RPP = r"C:\Users\Chris\Documents\REAPER Media\Originals\the_riff\The_riff.rpp"


def rpp_state():
    raw = open(RPP, encoding="utf-8", errors="replace").read().splitlines()
    n = next(i for i, l in enumerate(raw) if '<VST "VST3: RigSculpt' in l)
    j = n + 1
    while j < len(raw) and re.fullmatch(r"[A-Za-z0-9+/=]+", raw[j].strip() or "x"):
        j += 1
    buf = "".join(x.strip() for x in raw[n + 1:j])
    chunk = base64.b64decode(buf + "=" * (-len(buf) % 4))
    return chunk[68:]


def wrap(comp):
    enc = base64.b64encode(comp).decode().replace("/", ".")
    xml = ('<?xml version="1.0" encoding="UTF-8"?> <VST3PluginState><IComponent>%d.%s'
           '</IComponent><IEditController>0.</IEditController></VST3PluginState>'
           % (len(comp), enc)).encode()
    return b"VC2!" + struct.pack("<i", len(xml)) + xml + b"\x00"


def strings(blob):
    """Yield (offset, length, text) for the length-prefixed string table."""
    off = 0
    while off + 4 <= len(blob):
        n = struct.unpack("<i", blob[off:off + 4])[0]
        if n < 0 or off + 4 + n > len(blob):
            break
        s = blob[off + 4:off + 4 + n]
        yield off, n, s
        off += 4 + n


def swap_path(blob, old, new):
    old = old.encode() if isinstance(old, str) else old
    new = new.encode() if isinstance(new, str) else new
    i = blob.find(old)
    if i < 0:
        raise ValueError("path not found")
    start = i - 4
    assert struct.unpack("<i", blob[start:i])[0] == len(old), "length prefix mismatch"
    return blob[:start] + struct.pack("<i", len(new)) + new + blob[i + len(old):]


def render(blob, tag):
    p = minihost.Plugin(V, sample_rate=48000)
    if blob is not None:
        p.set_state(wrap(blob))
    block = min(p.max_block_size, 512)
    rng = np.random.default_rng(7)
    n = 48000
    x = (rng.standard_normal(block) * 0.05).astype(np.float32)
    inp = np.zeros((2, block), dtype=np.float32)
    inp[0] = x
    out = np.zeros((2, block), dtype=np.float32)
    acc = []
    for _ in range(n // block):
        p.process(inp, out)
        acc.append(out[0].copy())
    y = np.concatenate(acc)
    print(f"{tag:28s} rms={np.sqrt((y**2).mean()):.6f} peak={np.abs(y).max():.4f} "
          f"sha={hashlib.sha1(y.tobytes()).hexdigest()[:10]} lat={p.latency_samples}")
    p.close()
    return y


ctrl = rpp_state()
print("state strings:")
for off, n, s in strings(ctrl):
    print(f"  {off:5d} {n:5d} {s[:90]!r}")

a = render(ctrl, "original (rpp)")
alt = r"C:\Users\Chris\Documents\Guitar Amp Sims\NAM\Bugera 6262\Phillipe P Bug6262-Lead-NoDrive-Cab-ESR0,009.nam"
print("alt exists:", __import__("os").path.exists(alt))
first = next(s for o, n, s in strings(ctrl) if s.lower().endswith(b".nam"))
swapped = swap_path(ctrl, first, alt)
b = render(swapped, "swapped capture")

import os
print("identical audio:", np.array_equal(a, b))
print("max abs diff:", float(np.abs(a - b).max()))
os.makedirs("out", exist_ok=True)
np.save("out/a.npy", a)
np.save("out/b.npy", b)
