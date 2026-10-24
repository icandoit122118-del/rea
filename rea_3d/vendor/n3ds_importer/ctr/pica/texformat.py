from __future__ import annotations

from enum import IntEnum

class TextureFormat(IntEnum):
    RGBA8 = 0
    RGB8 = 1
    RGBA5551 = 2
    RGB565 = 3
    RGBA4 = 4
    LA8 = 5
    HiLo8 = 6
    L8 = 7
    A8 = 8
    LA4 = 9
    L4 = 10
    A4 = 11
    ETC1 = 12
    ETC1A4 = 13

BITS_PER_PIXEL = {
    TextureFormat.RGBA8: 32,
    TextureFormat.RGB8: 24,
    TextureFormat.RGBA5551: 16,
    TextureFormat.RGB565: 16,
    TextureFormat.RGBA4: 16,
    TextureFormat.LA8: 16,
    TextureFormat.HiLo8: 16,
    TextureFormat.L8: 8,
    TextureFormat.A8: 8,
    TextureFormat.LA4: 8,
    TextureFormat.L4: 4,
    TextureFormat.A4: 4,
    TextureFormat.ETC1: 4,
    TextureFormat.ETC1A4: 8,
}

def data_size(fmt: TextureFormat, width: int, height: int) -> int:
    return width * height * BITS_PER_PIXEL[fmt] // 8
