"""Can a real VST3 host (Carla) apply a patched NAM capture?

Carla's VST3 chunk is base64("VC2!" + int32 len + <PARAMETERS ...>), the exact
blob the plugin itself serialises. If we patch amp_profile inside that XML and
push it back, a host that keeps component state will return the new path.
"""
import base64, ctypes, os, pathlib, re, struct

CARLA = pathlib.Path(r"C:\Users\Chris\AppData\Local\Temp\carla\Carla-2.5.10-win64\Carla")
VST3 = r"C:\Program Files\Common Files\VST3\NAM Universal.vst3"
ALT = r"C:\Users\Chris\Documents\Guitar Amp Sims\NAM\Bugera 6262\Phillipe P Bugera6262-Lead-NoDrive-Cab-ESR0,009.nam"
BINARY_WIN64, PLUGIN_VST3, USE_CHUNKS = 4, 6, 0x008

os.add_dll_directory(str(CARLA))
lib = ctypes.CDLL(str(CARLA / "libcarla_standalone2.dll"))
P = ctypes.c_void_p
spec = [
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
]
for fn, args, res in spec:
    f = getattr(lib, fn)
    f.argtypes, f.restype = args, res

b64d = lambda t: base64.b64decode(t + "=" * (-len(t) % 4))
b64e = lambda b: base64.b64encode(b).decode()
carve = lambda b: b[8:8 + struct.unpack("<i", b[4:8])[0]]
prof = lambda x: (re.search(rb'amp_profile="([^"]*)"', x) or [None, b"?"])[1].decode()

h = lib.carla_standalone_host_init()
drivers = [lib.carla_get_engine_driver_name(i).decode() for i in range(lib.carla_get_engine_driver_count())]
driver = "DirectSound" if "DirectSound" in drivers else drivers[0]
print("engine_init:", lib.carla_engine_init(h, driver.encode(), b"probe"), drivers)
lib.carla_engine_idle(h)
print("add_plugin:", lib.carla_add_plugin(h, BINARY_WIN64, PLUGIN_VST3, VST3.encode(),
                                          b"NAM", None, 0, None, USE_CHUNKS))
print("param count:", lib.carla_get_parameter_count(h, 0))

raw = lib.carla_get_chunk_data(h, 0).decode()
blob = b64d(raw)
xml = carve(blob)
print("carla chunk:", len(raw), "decoded:", len(blob), "magic:", blob[:4], "xml:", len(xml))
print("initial profile:", prof(xml))
print("has PARAMETERS:", b"<PARAMETERS" in xml, "| tags:",
      sorted(set(m.decode() for m in re.findall(rb"<([A-Za-z_]+)", xml)))[:12])

print("alt exists:", os.path.exists(ALT))
print("XML head:", xml[:400])
print("XML tail:", xml[-200:])
patched = re.sub(rb'amp_profile="[^"]*"', lambda m: b'amp_profile="' + ALT.encode() + b'"', xml, count=1)
newblob = b"VC2!" + struct.pack("<i", len(patched)) + patched + b"\x00"
lib.carla_set_chunk_data(h, 0, b64e(newblob).encode())
lib.carla_engine_idle(h)
err = lib.carla_get_last_error(h)
print("set error:", err.decode(errors="replace") if err else "(none)")

back = lib.carla_get_chunk_data(h, 0).decode()
bx = carve(b64d(back))
print("readback chunk:", len(back), "xml:", len(bx))
print("readback profile:", prof(bx))
print("PATCH STUCK:", prof(bx) == ALT)
print("engine close:", lib.carla_engine_close(h))
