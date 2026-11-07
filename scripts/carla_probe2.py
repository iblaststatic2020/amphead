"""Does a real VST3 host apply NAM Universal's capture state from a chunk?

Carla stores VST3 state as base64("VC2!" + int32 len + <PARAMETERS ...>), the
same shape REAPER writes in an .rpp. So we can hand Carla REAPER's own chunk
and read back whether the capture survived, then patch the capture and see if
the host reports the new one.
"""
import base64, ctypes, os, pathlib, re, struct, sys

CARLA = pathlib.Path(r"C:\Users\Chris\AppData\Local\Temp\carla\Carla-2.5.10-win64\Carla")
VST3 = r"C:\Program Files\Common Files\VST3\NAM Universal.vst3"
RPP = r"C:\Users\Chris\Documents\REAPER Media\Originals\the_riff\The_riff.rpp"
ALT = r"C:\Users\Chris\Documents\Guitar Amp Sims\NAM\Bugera 6262\Phillipe P Bugera6262-Lead-NoDrive-Cab-ESR0,009.nam"

BINARY_WIN64, PLUGIN_VST3, USE_CHUNKS = 4, 6, 0x008
os.add_dll_directory(str(CARLA))
lib = ctypes.CDLL(str(CARLA / "libcarla_standalone2.dll"))
P = ctypes.c_void_p
for fn, args, res in [
    ("carla_standalone_host_init", [], P),
    ("carla_get_engine_driver_count", [], ctypes.c_uint),
    ("carla_get_engine_driver_name", [ctypes.c_uint], ctypes.c_char_p),
    ("carla_engine_init", [P, ctypes.c_char_p, ctypes.c_char_p], ctypes.c_bool),
    ("carla_engine_idle", [P], None),
    ("carla_engine_close", [P], ctypes.c_bool),
    ("carla_add_plugin", [P, ctypes.c_int, ctypes.c_int, ctypes.c_char_p, ctypes.c_char_p,
                          ctypes.c_char_p, ctypes.c_int64, P, ctypes.c_uint], ctypes.c_bool),
    ("carla_get_last_error", [P], ctypes.c_char_p),
    ("carla_get_chunk_data", [P, ctypes.c_uint], ctypes.c_char_p),
    ("carla_set_chunk_data", [P, ctypes.c_uint, ctypes.c_char_p], None),
    ("carla_get_parameter_count", [P, ctypes.c_uint], ctypes.c_uint),
    ("carla_get_parameter_text", [P, ctypes.c_uint, ctypes.c_uint], ctypes.c_char_p),

]:
    f = getattr(lib, fn)
    f.argtypes = args
    f.restype = res


def rpp_chunks():
    """Every plugin chunk in the project, keyed by the <VST line's index."""
    lines = open(RPP, encoding="utf-8", errors="replace").read().splitlines()
    out = []
    for i, l in enumerate(lines):
        if "<VST " not in l:
            continue
        j, buf = i + 1, []
        while j < len(lines) and re.fullmatch(r"[A-Za-z0-9+/=]+", lines[j].strip() or "x"):
            buf.append(lines[j].strip())
            j += 1
        if buf:
            out.append((" ".join(l.split('"')[1:2]), "".join(buf)))
    return out


def decode_chunk(txt):
    return base64.b64decode(txt + "=" * (-len(txt) % 4))


def encode_chunk(blob):
    return base64.b64encode(blob).decode()


def carve(blob):
    """blob is VC2! + int32 len + xml (+ NUL). Return (xml_start, xml_len, xml)."""
    assert blob[:4] == b"VC2!", blob[:4]
    n = struct.unpack("<i", blob[4:8])[0]
    return 8, n, blob[8:8 + n]


def rebuild(xml):
    return b"VC2!" + struct.pack("<i", len(xml)) + xml + b"\x00"


def amp_profile(xml):
    m = re.search(rb'amp_profile="([^"]*)"', xml)
    return m.group(1).decode() if m else None


def patch_profile(xml, new_path):
    m = re.search(rb'amp_profile="([^"]*)"', xml)
    if not m:
        raise ValueError("no amp_profile")
    return xml[:m.start(1)] + new_path.encode() + xml[m.end(1):]


chunks = rpp_chunks()
print(f"{len(chunks)} plugin chunks in rpp")
target = None
for name, txt in chunks:
    blob = decode_chunk(txt)
    if blob[:4] != b"VC2!":
        print(f"  {name[:40]:42s} not VC2, {len(blob)} bytes")
        continue
    _, n, xml = carve(blob)
    prof = amp_profile(xml)
    print(f"  {name[:40]:42s} xml={n:5d} profile={prof}")
    if prof and not target:
        target = (name, txt, blob, xml, prof)

if not target:
    sys.exit("no NAM chunk with amp_profile")

name, txt, blob, xml, prof = target
print(f"\ntarget: {name}\nprofile: {prof}")

h = lib.carla_standalone_host_init()
drivers = [lib.carla_get_engine_driver_name(i).decode() for i in range(lib.carla_get_engine_driver_count())]
driver = "DirectSound" if "DirectSound" in drivers else drivers[0]
print("engine:", lib.carla_engine_init(h, driver.encode(), b"probe"), drivers)
lib.carla_engine_idle(h)
print("add_plugin:", lib.carla_add_plugin(h, BINARY_WIN64, PLUGIN_VST3, VST3.encode(),
                                          b"NAM", None, 0, None, USE_CHUNKS))
print("params:", lib.carla_get_parameter_count(h, 0))
print("empty chunk:", lib.carla_get_chunk_data(h, 0))


def readback(tag):
    lib.carla_engine_idle(h)
    c = lib.carla_get_chunk_data(h, 0)
    b = decode_chunk(c.decode())
    _, n, x = carve(b)
    p = amp_profile(x)
    print(f"{tag:26s} chunk={len(c)} xml={n} profile={p}")
    return x


lib.carla_set_chunk_data(h, 0, txt.encode())
x1 = readback("after rpp chunk")
print("matches rpp profile:", amp_profile(x1) == prof)

print("\nalt exists:", os.path.exists(ALT))
patched = patch_profile(xml, ALT)
lib.carla_set_chunk_data(h, 0, encode_chunk(rebuild(patched)).encode())
x2 = readback("after patched chunk")
print("patched survived:", amp_profile(x2) == ALT)

print("\nparam names:", [lib.carla_get_parameter_name(h, 0, i).decode() for i in range(3)])
print("engine close:", lib.carla_engine_close(h))
