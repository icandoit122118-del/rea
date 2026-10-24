"""Original, synthetic binary fixtures; contains no Nintendo/Game Freak assets.

SPDX-License-Identifier: GPL-3.0-or-later
"""
from pathlib import Path
import struct


def _bstring(text):
    data = text.encode()
    return bytes([len(data)]) + data


def _hashname(text):
    return struct.pack("<I", 0) + _bstring(text)


def _section(name, body):
    return name.encode().ljust(8, b"\0") + struct.pack("<II", len(body), 0xFFFFFFFF) + body


def _table(names):
    return struct.pack("<I", len(names)) + b"".join(struct.pack("<I", 0) + n.encode().ljust(64, b"\0") for n in names)


def _command(register, value):
    return [value, register | (15 << 16)]


def _block(words, index):
    return struct.pack("<4I", len(words)*4, index, 3, 0) + struct.pack("<" + "I"*len(words), *words)


def model_bytes(root_rotation=(0, 0, 0)):
    """Two joints, one textured weighted triangle, parsed by the real GFModel reader."""
    bone = lambda n, p, t, r=(0, 0, 0): _bstring(n) + _bstring(p) + b"\0" + struct.pack("<9f", 1, 1, 1, *r, *t)
    body = _table([]) + _table(["synthetic_body"]) + _table(["body"]) + _table(["triangle"])
    body += struct.pack("<8f", 0, 0, 0, 1, 1, 1, 0, 1) + bytes(64)
    body += bytes(16) + struct.pack("<i", 2) + bytes(12)
    body += bone("Root", "", (0, 0, 0), root_rotation) + bone("Tip", "Root", (0, 1, 0))
    output = struct.pack("<II", 0x15122117, 1) + bytes(8) + _section("gfmodel", body)
    output += bytes((-len(output)) % 16)
    material = _hashname("body") + _hashname("synthetic_shader") + _hashname("") + _hashname("")
    material += bytes(16) + b"\xff" + bytes(7)
    material += bytes([255, 255, 255, 255]) * 12 + bytes(96) + struct.pack("<I", 1)
    material += _hashname("synthetic_body") + bytes(2) + struct.pack("<5f5I", 1, 1, 0, 0, 0, 2, 2, 0, 0, 0)
    output += _section("material", material)
    output += bytes((-len(output)) % 16)
    formats = 0xB | (0xB << 4) | (0x7 << 8) | (0xD << 12) | (0xF << 16)
    enable = []
    for register, value in [(0x201, formats), (0x202, 0), (0x204, 0x43210), (0x205, 52 << 16),
                            (0x242, 4), (0x2BB, 0x87410), (0x2BC, 0)]:
        enable.extend(_command(register, value))
    indices = _command(0x227, 0) + _command(0x228, 3) + _command(0x25E, 0)
    mesh = struct.pack("<I", 0) + b"triangle".ljust(64, b"\0") + bytes(4+32)
    mesh += struct.pack("<Ii", 1, 2) + bytes(16)
    mesh += _block(enable, 0) + _block([], 1) + _block(indices, 2)
    mesh += struct.pack("<Ii", 0, 8) + b"triangle" + bytes([2, 0, 1]) + bytes(29)
    mesh += struct.pack("<4i", 3, 3, 156, 3)
    for position, uv, joints, weights in [((0, 0, 0), (0, 0), (0, 255, 255, 255), (1, 0, 0, 0)),
                                          ((1, 0, 0), (1, 0), (1, 255, 255, 255), (1, 0, 0, 0)),
                                          ((0, 1, 0), (0, 1), (0, 1, 255, 255), (.5, .5, 0, 0))]:
        mesh += struct.pack("<8f4B4f", *position, 0, 0, 1, *uv, *joints, *weights)
    mesh += bytes([0, 1, 2])
    return output + _section("mesh", mesh)


def texture_bytes():
    # L8 texture with constant luminance 128, requiring genuine PICA texture decoding.
    raw = bytes([128])*64
    body = struct.pack("<I", len(raw)) + bytes(12) + b"synthetic_body".ljust(64, b"\0")
    body += struct.pack("<4H", 8, 8, 0x25, 0) + bytes(16) + raw
    return struct.pack("<II", 0x15041213, 1) + _section("texture", body)


def motion_bytes(name="Root", unsupported=False, skeletal=True):
    count = 1 + int(skeletal) + int(unsupported)
    data = bytearray(8 + 12*count)
    subheader = len(data)
    data += struct.pack("<IHH6fI", 3, 1, 0, 0, 0, 0, 2, 0, 0, 0)
    sections = [(0, len(data)-subheader, subheader)]
    if skeletal:
        start = len(data)
        encoded_name = _bstring(name)
        data += struct.pack("<iI", 1, len(encoded_name)) + encoded_name
        data += struct.pack("<II", 0x80000000 | 3 | (3 << 3) | (3 << 6) | (5 << 18), 0)
        data += struct.pack("<3fI", 1, 1, 1, 2) + bytes([0, 2])
        data += bytes((-len(data)) % 4)
        data += struct.pack("<4f", 0, 1, 2, 1)
        sections.append((1, len(data)-start, start))
    if unsupported:
        start = len(data)
        data += bytes(4)
        sections.append((3, 4, start))
    struct.pack_into("<II", data, 0, 0, len(sections))
    for index, section in enumerate(sections):
        struct.pack_into("<III", data, 8 + 12*index, *section)
    return bytes(data)


def sparse_axis_angle_motion_bytes(rotation_x=None):
    """Constant translation X=1, optionally axis-angle X; absent axes use rest pose."""
    data = bytearray(32)
    subheader = len(data)
    data += struct.pack("<IHH6fI", 2, 0, 0, 0, 0, 0, 2, 0, 0, 0)
    skeletal = len(data)
    name = _bstring("Root")
    data += struct.pack("<iI", 1, len(name)) + name
    flags = 3 << 18  # Constant translation X; high bit remains zero for axis-angle.
    if rotation_x is not None:
        flags |= 3 << 9
    data += struct.pack("<II", flags, 0)
    if rotation_x is not None:
        data += struct.pack("<f", rotation_x)
    data += struct.pack("<f", 1)
    struct.pack_into("<II", data, 0, 0, 2)
    struct.pack_into("<III", data, 8, 0, skeletal-subheader, subheader)
    struct.pack_into("<III", data, 20, 1, len(data)-skeletal, skeletal)
    return bytes(data)


def pc_bytes(*entries):
    offset = 4 + 4*(len(entries)+1)
    offsets = [offset]
    for entry in entries:
        offsets.append(offsets[-1]+len(entry))
    return b"PC" + struct.pack("<H", len(entries)) + struct.pack("<" + "I"*len(offsets), *offsets) + b"".join(entries)


def lz10_literal_bytes(data):
    if len(data) > 0xFFFFFF:
        raise ValueError("fixture is too large")
    output = bytes([0x10]) + len(data).to_bytes(3, "little")
    return output + b"".join(b"\0" + data[i:i+8] for i in range(0, len(data), 8))


def make_fixture(directory):
    path = Path(directory)
    path.mkdir(parents=True, exist_ok=True)
    model, motion = path / "synthetic.pc", path / "synthetic_motion.pc"
    model.write_bytes(pc_bytes(model_bytes(), texture_bytes()))
    motion.write_bytes(pc_bytes(motion_bytes()))
    return model, motion
