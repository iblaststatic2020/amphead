"""Drive Carla's host engine from Python via libcarla_standalone2.dll.

Measurement only: proves whether a real VST3 host keeps the plugin's
edit-controller state (the <PARAMETERS amp_profile=...> XML) that pedalboard
and minihost throw away.
"""
import ctypes, os, pathlib, sys

CARLA = pathlib.Path(r"C:\Users\Chris\AppData\Local\Temp\carla\Carla-2.5.10-win64\Carla")
VST3 = r"C:\Program Files\Common Files\VST3\NAM Universal.vst3"
RPP = r"C:\Users\Chris\Documents\REAPER Media\Originals\the_riff\The_riff.rpp"

BINARY_WIN64, PLUGIN_VST3, USE_CHUNKS = 4, 6, 0x008

os.add_dll_directory(str(CARLA))
lib = ctypes.CDLL(str(CARLA / "libcarla_standalone2.dll"))

lib.carla_standalone_host_init.restype = ctypes.c_void_p
lib.carla_get_engine_driver_count.restype = ctypes.c_uint
lib.carla_get_engine_driver_name.restype = ctypes.c_char_p
lib.carla_get_engine_driver_name.argtypes = [ctypes.c_uint]
lib.carla_engine_init.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p]
lib.carla_engine_init.restype = ctypes.c_bool
lib.carla_add_plugin.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                 ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p,
                                 ctypes.c_int64, ctypes.c_void_p, ctypes.c_uint]
lib.carla_add_plugin.restype = ctypes.c_bool
lib.carla_get_last_error.restype = ctypes.c_char_p
lib.carla_get_last_error.argtypes = [ctypes.c_void_p]
lib.carla_get_chunk_data.restype = ctypes.c_char_p
lib.carla_get_chunk_data.argtypes = [ctypes.c_void_p, ctypes.c_uint]
lib.carla_set_chunk_data.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_char_p]
lib.carla_get_parameter_count.restype = ctypes.c_uint
lib.carla_get_parameter_count.argtypes = [ctypes.c_void_p, ctypes.c_uint]
lib.carla_get_parameter_info.restype = ctypes.c_void_p
lib.carla_get_parameter_info.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint]
lib.carla_get_parameter_text.restype = ctypes.c_char_p
lib.carla_get_parameter_text.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint]
lib.carla_get_current_parameter_value.restype = ctypes.c_float
lib.carla_get_current_parameter_value.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint]


lib.carla_engine_idle.argtypes = [ctypes.c_void_p]
lib.carla_is_engine_running.argtypes = [ctypes.c_void_p]
lib.carla_is_engine_running.restype = ctypes.c_bool
lib.carla_engine_close.argtypes = [ctypes.c_void_p]
lib.carla_engine_close.restype = ctypes.c_bool
lib.carla_remove_all_plugins.argtypes = [ctypes.c_void_p]
lib.carla_remove_all_plugins.restype = ctypes.c_bool


def last(h):
    e = lib.carla_get_last_error(h)
    return e.decode(errors="replace") if e else ""


def rpp_chunk():
    import base64, re
    raw = open(RPP, encoding="utf-8", errors="replace").read().splitlines()
    n = next(i for i, l in enumerate(raw) if '<VST "VST3: NAM Universal' in l or "NAM Universal" in l and "<VST" in l)
    j = n + 1
    while j < len(raw) and re.fullmatch(r"[A-Za-z0-9+/=]+", raw[j].strip() or "x"):
        j += 1
    return "".join(x.strip() for x in raw[n + 1:j])


h = lib.carla_standalone_host_init()
print("host handle:", bool(h))

names = [lib.carla_get_engine_driver_name(i).decode() for i in range(lib.carla_get_engine_driver_count())]
print("drivers:", names)

driver = "DirectSound" if "DirectSound" in names else (names[0] if names else "")
ok = lib.carla_engine_init(h, driver.encode(), b"probe")
print("engine_init", driver, ok, last(h))
if not ok:
    sys.exit(1)
lib.carla_engine_idle(h)

ok = lib.carla_add_plugin(h, BINARY_WIN64, PLUGIN_VST3, VST3.encode(), b"NAM", None, 0, None, USE_CHUNKS)
print("add_plugin", ok, last(h))
if not ok:
    sys.exit(1)
lib.carla_engine_idle(h)

n = lib.carla_get_parameter_count(h, 0)
print("params:", n)
for i in range(n):
    t = lib.carla_get_parameter_text(h, 0, i)
    print(f"  {i:3d} {t.decode(errors='replace') if t else ''!r} {lib.carla_get_current_parameter_value(h, 0, i):.4f}")

chunk = lib.carla_get_chunk_data(h, 0)
print("carla chunk:", "none" if not chunk else f"{len(chunk)} bytes, head={chunk[:60]!r}")

reaper = rpp_chunk()
print("rpp chunk:", len(reaper), "head:", reaper[:60])

# Carla's VST3 chunk is base64 of a VC2! blob, same shape REAPER stores.
print("rpp is base64 of VC2:", reaper[:4], "->", __import__("base64").b64decode(reaper + "=" * (-len(reaper) % 4))[:20])

lib.carla_set_chunk_data(h, 0, reaper.encode())
lib.carla_engine_idle(h)
print("set_chunk error:", last(h))
after = lib.carla_get_chunk_data(h, 0)
print("chunk after:", "none" if not after else len(after))
if after:
    print("same as rpp:", after.decode() == reaper)
    import base64, re
    blob = base64.b64decode(after + "=" * (-len(after) % 4))
    print("blob len", len(blob), "head", blob[:24])
    m = re.search(rb"<PARAMETERS.*?</PARAMETERS>", blob, re.S)
    print("PARAMETERS:", (m.group(0)[:300] if m else None))
