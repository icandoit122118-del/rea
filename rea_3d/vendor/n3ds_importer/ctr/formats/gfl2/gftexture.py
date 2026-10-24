from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from ...binstream import BinStream
from ...pica.texcodec import decode
from ...pica.texformat import TextureFormat, data_size
from .section import ALIGNMENT, read_section

MAGIC = 0x15041213
MAGIC_BYTES = bytes([0x13, 0x12, 0x04, 0x15])
SECTION_MAGIC = "texture"
NAME_FIELD = 0x40

class GFTextureFormat(IntEnum):
    RGB565 = 0x02
    RGB8 = 0x03
    RGBA8 = 0x04
    RGBA4 = 0x16
    RGBA5551 = 0x17
    LA8 = 0x23
    HiLo8 = 0x24
    L8 = 0x25
    A8 = 0x26
    LA4 = 0x27
    L4 = 0x28
    A4 = 0x29
    ETC1 = 0x2A
    ETC1A4 = 0x2B

_TO_PICA = {
    GFTextureFormat.RGB565: TextureFormat.RGB565,
    GFTextureFormat.RGB8: TextureFormat.RGB8,
    GFTextureFormat.RGBA8: TextureFormat.RGBA8,
    GFTextureFormat.RGBA4: TextureFormat.RGBA4,
    GFTextureFormat.RGBA5551: TextureFormat.RGBA5551,
    GFTextureFormat.LA8: TextureFormat.LA8,
    GFTextureFormat.HiLo8: TextureFormat.HiLo8,
    GFTextureFormat.L8: TextureFormat.L8,
    GFTextureFormat.A8: TextureFormat.A8,
    GFTextureFormat.LA4: TextureFormat.LA4,
    GFTextureFormat.L4: TextureFormat.L4,
    GFTextureFormat.A4: TextureFormat.A4,
    GFTextureFormat.ETC1: TextureFormat.ETC1,
    GFTextureFormat.ETC1A4: TextureFormat.ETC1A4,
}

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

SECTION_OFFSETS = (0x08, 0x10)

def sniff_gftexture(head: bytes) -> bool:
    if head[:4] != MAGIC_BYTES:
        return False
    return any(head[at:at + 7] == b"texture" for at in SECTION_OFFSETS)

def read_gftexture(stream: BinStream) -> Texture:
    base = stream.tell()
    magic = stream.u32()
    if magic != MAGIC:
        raise ValueError(f"not a GFTexture: magic {magic:#010x}")
    stream.u32()

    section = None
    for at in SECTION_OFFSETS:
        stream.seek(base + at)
        candidate = read_section(stream)
        if candidate.magic == SECTION_MAGIC:
            section = candidate
            break
    if section is None:
        raise ValueError("no texture section at offset 0x08 or 0x10")

    length = stream.u32()
    stream.skip(0xC)

    texture = Texture(name=stream.pstring(NAME_FIELD))
    texture.width = stream.u16()
    texture.height = stream.u16()
    raw_format = stream.u16()
    texture.mipmap_size = stream.u16()
    stream.skip(ALIGNMENT)

    try:
        texture.fmt = _TO_PICA[GFTextureFormat(raw_format)]
    except (ValueError, KeyError):
        raise ValueError(f"{texture.name}: unknown texture format {raw_format:#06x}")

    needed = data_size(texture.fmt, texture.width, texture.height)
    if length < needed:
        raise ValueError(
            f"{texture.name}: {texture.fmt.name} {texture.width}x{texture.height} "
            f"needs {needed} bytes, the section declares {length}"
        )

    texture.raw = stream.bytes(length)
    return texture
