import base64, re, struct, hashlib, time
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
    off = 0
    while off + 4 <= len(blob):
        n = struct.unpack("<i", blob[off:off + 4])[0]
        if n < 0 or off + 4 + n > len(blob):
            break
        yield off, n, blob[off + 4:off + 4 + n]
        off += 4 + n


def swap(blob, old, new):
    old = old.encode() if isinstance(old, str) else old
    new = new.encode() if isinstance(new, str) else new
    i = blob.find(old)
    start = i - 4
    return blob[:start] + struct.pack("<i", len(new)) + new + blob[i + len(old):]


def render(blob, tag, wait=0.0):
    p = minihost.Plugin(V, sample_rate=48000)
    if blob is not None:
        p.set_state(wrap(blob))
    if wait:
        time.sleep(wait)
    block = 512
    rng = np.random.default_rng(7)
    inp = np.zeros((2, block), dtype=np.float32)
    out = np.zeros((2, block), dtype=np.float32)
    acc = []
    for _ in range(48000 // block):
        inp[0] = (rng.standard_normal(block) * 0.05).astype(np.float32)
        p.process(inp, out)
        acc.append(out[0].copy())
    y = np.concatenate(acc)
    print(f"{tag:30s} rms={np.sqrt((y**2).mean()):.6f} sha={hashlib.sha1(y.tobytes()).hexdigest()[:10]}")
    p.close()
    return y


ctrl = rpp_state()
peavey = next(s for o, n, s in strings(ctrl) if s.lower().endswith(b".nam"))
bugera = r"C:\Users\Chris\Documents\Guitar Amp Sims\NAM\Bugera 6262\Phillipe P Bugera6262-Lead-NoDrive-Cab-ESR0,009.nam"
missing = r"C:\Users\Chris\Documents\NOPE\does_not_exist.nam"

a = render(ctrl, "rpp state, no wait")
b = render(ctrl, "rpp state, wait 2s", wait=2.0)
c = render(swap(ctrl, peavey, bugera), "slot A = Bugera, wait 2s", wait=2.0)
d = render(swap(ctrl, peavey, missing), "slot A = missing, wait 2s", wait=2.0)
print("a vs b identical:", np.array_equal(a, b))
print("a vs c identical:", np.array_equal(a, c), "maxdiff", float(np.abs(a - c).max()))
print("a vs d identical:", np.array_equal(a, d))
