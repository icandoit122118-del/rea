from __future__ import annotations

import struct
from dataclasses import dataclass

from ...pica import commands as reg
from ...pica.texcodec import decode
from ...pica.texformat import TextureFormat, data_size
from .tree import read_cstring

TEXTURE_SIZE = 0x28
COMMANDS_AT = 0x00
FORMAT_AT = 0x18
MIPMAP_AT = 0x19
NAME_AT = 0x1C

TEXUNIT0_DIM = 0x0082
TEXUNIT0_ADDR1 = 0x0085
TEXUNIT0_TYPE = 0x008E


@dataclass
class Texture:
    name: str = ""
    width: int = 0
    height: int = 0
    fmt: TextureFormat = TextureFormat.RGBA8
    mipmap_size: int = 0
    raw: bytes = b""

    def to_rgba(self) -> bytearray:
        return decode(self.fmt, self.raw, self.width, self.height)


def read_texture(data, at: int) -> Texture:
    commands_at, commands_count = struct.unpack_from("<II", data, at + COMMANDS_AT)
    raw_format = data[at + FORMAT_AT]
    mipmap = data[at + MIPMAP_AT]
    (name_at,) = struct.unpack_from("<I", data, at + NAME_AT)

    texture = Texture(name=read_cstring(data, name_at), mipmap_size=mipmap)
    try:
        texture.fmt = TextureFormat(raw_format)
    except ValueError:
        raise ValueError("unknown texture format %#04x" % raw_format)

    address = 0
    for cmd in reg.decode_commands(_words(data, commands_at, commands_count)):
        if cmd.register == TEXUNIT0_DIM:
            texture.height = cmd.param & 0x7FF
            texture.width = (cmd.param >> 16) & 0x7FF
        elif cmd.register == TEXUNIT0_ADDR1 and not address:
            address = cmd.param

    if not (texture.width and texture.height):
        raise ValueError("%s: no dimensions in the texture commands" % texture.name)

    needed = data_size(texture.fmt, texture.width, texture.height)
    if address and address + needed <= len(data):
        texture.raw = bytes(data[address:address + needed])
    else:
        raise ValueError(
            "%s: %s %dx%d needs %d bytes at %#x, outside the file"
            % (texture.name, texture.fmt.name, texture.width, texture.height,
               needed, address))

    return texture


def _words(data, at: int, count: int) -> list:
    if not at or count <= 0 or at + count * 4 > len(data):
        return []
    return list(struct.unpack_from("<%dI" % count, data, at))


def read_textures(data, contents) -> list:
    entry = contents.textures
    textures = []
    for index in range(entry.count):
        pointer_at = entry.values + index * 4
        if pointer_at + 4 > len(data):
            break
        (texture_at,) = struct.unpack_from("<I", data, pointer_at)
        if not texture_at or texture_at + TEXTURE_SIZE > len(data):
            continue
        try:
            texture = read_texture(data, texture_at)
        except Exception:
            continue
        if not texture.name and index < len(entry.entries):
            texture.name = entry.entries[index]
        textures.append(texture)
    return textures
