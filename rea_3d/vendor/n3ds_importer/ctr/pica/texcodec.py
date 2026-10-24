from __future__ import annotations

from .etc1 import decode_etc1
from .swizzle import deswizzle
from .texformat import BITS_PER_PIXEL, TextureFormat, data_size

_ETC1_FORMATS = (TextureFormat.ETC1, TextureFormat.ETC1A4)

class UnsupportedFormat(ValueError):
    pass

def _expand(value: int, bits: int) -> int:
    if bits == 8:
        return value
    result = 0
    filled = 0
    while filled < 8:
        result = (result << bits) | value
        filled += bits
    return (result >> (filled - 8)) & 0xFF

def _texels_rgba8(data, count):
    return [
        (data[i * 4 + 3], data[i * 4 + 2], data[i * 4 + 1], data[i * 4])
        for i in range(count)
    ]

def _texels_rgb8(data, count):
    return [(data[i * 3 + 2], data[i * 3 + 1], data[i * 3], 0xFF) for i in range(count)]

def _texels_rgba5551(data, count):
    out = []
    for i in range(count):
        v = data[i * 2] | (data[i * 2 + 1] << 8)
        out.append((
            _expand((v >> 11) & 0x1F, 5),
            _expand((v >> 6) & 0x1F, 5),
            _expand((v >> 1) & 0x1F, 5),
            0xFF if v & 1 else 0x00,
        ))
    return out

def _texels_rgb565(data, count):
    out = []
    for i in range(count):
        v = data[i * 2] | (data[i * 2 + 1] << 8)
        out.append((
            _expand((v >> 11) & 0x1F, 5),
            _expand((v >> 5) & 0x3F, 6),
            _expand(v & 0x1F, 5),
            0xFF,
        ))
    return out

def _texels_rgba4(data, count):
    out = []
    for i in range(count):
        v = data[i * 2] | (data[i * 2 + 1] << 8)
        out.append((
            _expand((v >> 12) & 0xF, 4),
            _expand((v >> 8) & 0xF, 4),
            _expand((v >> 4) & 0xF, 4),
            _expand(v & 0xF, 4),
        ))
    return out

def _texels_la8(data, count):
    out = []
    for i in range(count):
        a, l = data[i * 2], data[i * 2 + 1]
        out.append((l, l, l, a))
    return out

def _texels_l8(data, count):
    return [(data[i], data[i], data[i], 0xFF) for i in range(count)]

def _texels_a8(data, count):
    return [(0xFF, 0xFF, 0xFF, data[i]) for i in range(count)]

def _texels_la4(data, count):
    out = []
    for i in range(count):
        v = data[i]
        l = _expand((v >> 4) & 0xF, 4)
        a = _expand(v & 0xF, 4)
        out.append((l, l, l, a))
    return out

def _nibbles(data, count):
    out = []
    for i in range(count):
        byte = data[i // 2]
        out.append(byte & 0xF if i % 2 == 0 else (byte >> 4) & 0xF)
    return out

def _texels_l4(data, count):
    return [(_expand(n, 4),) * 3 + (0xFF,) for n in _nibbles(data, count)]

def _texels_a4(data, count):
    return [(0xFF, 0xFF, 0xFF, _expand(n, 4)) for n in _nibbles(data, count)]

_DECODERS = {
    TextureFormat.RGBA8: _texels_rgba8,
    TextureFormat.RGB8: _texels_rgb8,
    TextureFormat.RGBA5551: _texels_rgba5551,
    TextureFormat.RGB565: _texels_rgb565,
    TextureFormat.RGBA4: _texels_rgba4,
    TextureFormat.LA8: _texels_la8,
    TextureFormat.L8: _texels_l8,
    TextureFormat.A8: _texels_a8,
    TextureFormat.LA4: _texels_la4,
    TextureFormat.L4: _texels_l4,
    TextureFormat.A4: _texels_a4,
}

def decode(fmt: TextureFormat, data: bytes, width: int, height: int) -> bytearray:
    needed = data_size(fmt, width, height)
    if len(data) < needed:
        raise ValueError(f"{fmt.name} {width}x{height} needs {needed} bytes, got {len(data)}")

    if fmt in _ETC1_FORMATS:
        return decode_etc1(data, width, height, has_alpha=fmt is TextureFormat.ETC1A4)

    decoder = _DECODERS.get(fmt)
    if decoder is None:
        raise UnsupportedFormat(
            f"{fmt.name} has no decoder yet (bpp={BITS_PER_PIXEL[fmt]})"
        )

    stored = decoder(data, width * height)
    linear = deswizzle(stored, width, height)

    out = bytearray(width * height * 4)
    for i, (r, g, b, a) in enumerate(linear):
        out[i * 4:i * 4 + 4] = bytes((r, g, b, a))
    return out
