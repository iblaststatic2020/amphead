"""Read and write a NAM Universal state blob.

A NAM plugin's model, IR and every knob live in its VST3 state, not in host parameters. REAPER
stores that state in the track chunk as base64. The decoded bytes are a short binary header, a
`VC2!` block whose body is a plain `<PARAMETERS ...>` XML document (the same schema as the
`.preset` files the plugin writes), and a JUCE tail.

To change the capture we rebuild the XML and repair the three length fields that describe the
region, then let the caller push the blob back with `FX_SETCHUNK`.
"""

from __future__ import annotations

import base64
import struct
import xml.etree.ElementTree as ET

XPATH_START = b"<?xml"
XPATH_END = b"</PARAMETERS>"
VC2 = b"VC2!"


class BadState(ValueError):
    """The blob does not look like a NAM state chunk."""


def decode(b64: str) -> bytes:
    # REAPER and the plugin both hand back the blob as one unbroken base64 string, and a blob
    # whose length is not a multiple of three arrives without its "=" padding.
    flat = "".join(b64.split()).replace("=", "")
    data = base64.b64decode(flat + "=" * (-len(flat) % 4))
    if XPATH_START not in data:
        raise BadState("no XML document in state blob")
    return data


def encode(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def span(data: bytes) -> tuple[int, int]:
    """Byte offsets of the XML document inside the blob, end exclusive."""
    start = data.find(XPATH_START)
    if start < 0:
        raise BadState("no XML document start")
    end = data.find(XPATH_END, start)
    if end < 0:
        raise BadState("no XML document end")
    return start, end + len(XPATH_END)


def _length_offsets(data: bytes, start: int, xml_len: int) -> list[int]:
    """Offsets of the 32-bit little-endian fields that describe the XML region.

    Three of them surround the document: the `VC2!` body length equals the XML length exactly,
    and two outer sizes are that length plus fixed padding. They are found by value in the 64
    bytes before the document so a format change shows up as BadState instead of silent rot.
    """
    window = range(max(0, start - 96), start - 3)
    found = []
    for pos in window:
        (value,) = struct.unpack("<I", data[pos:pos + 4])
        if 0 <= value - xml_len < 300:
            found.append((pos, value))
    lengths = [value for _, value in found]
    if xml_len not in lengths:
        raise BadState(f"the VC2 body length is missing; fields seen: {found}")
    if len(found) < 2:
        raise BadState(f"expected at least 2 length fields, found {found}")
    return [pos for pos, _ in found]


def replace_xml(data: bytes, new_xml: bytes) -> bytes:
    """Swap the state document and repair the length fields around it."""
    start, end = span(data)
    old_len = end - start
    delta = len(new_xml) - old_len
    out = bytearray(data[:start] + new_xml + data[end:])
    for pos in _length_offsets(data, start, old_len):
        (value,) = struct.unpack("<I", data[pos:pos + 4])
        struct.pack_into("<I", out, pos, value + delta)
    return bytes(out)


def xml_of(b64: str) -> str:
    data = decode(b64)
    start, end = span(data)
    return data[start:end].decode("utf-8")


def params_of(b64: str) -> dict[str, float]:
    root = ET.fromstring(xml_of(b64))
    return {p.get("id"): float(p.get("value")) for p in root.iter("PARAM")}


def attrs_of(b64: str) -> dict[str, str]:
    return dict(ET.fromstring(xml_of(b64)).attrib)


def preset_xml(path: str) -> str:
    """A saved `.preset` file is the same XML the plugin keeps in its state."""
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    start = text.find("<?xml")
    if start < 0:
        raise BadState(f"{path} is not a NAM preset")
    return text[start:].strip()


def retarget(xml: str, *, amp: str | None = None, cab1: str | None = None,
             cab2: str | None = None, attrs: dict[str, str] | None = None,
             params: dict[str, float] | None = None) -> str:
    """Point a state document at another capture / IR, optionally overriding values."""
    root = ET.fromstring(xml)
    header = root.attrib
    if amp is not None:
        header["amp_profile"] = amp
    if cab1 is not None:
        header["impulse_file1"] = cab1
    if cab2 is not None:
        header["impulse_file2"] = cab2
    for key, value in (attrs or {}).items():
        header[key] = value
    if params:
        for node in root.iter("PARAM"):
            key = node.get("id")
            if key in params:
                node.set("value", repr(float(params[key])))
    return ET.tostring(root, encoding="unicode")
