from __future__ import annotations

MODIFIER_TABLE = (
    (2, 8, -2, -8),
    (5, 17, -5, -17),
    (9, 29, -9, -29),
    (13, 42, -13, -42),
    (18, 60, -18, -60),
    (24, 80, -24, -80),
    (33, 106, -33, -106),
    (47, 183, -47, -183),
)

_BLOCK_ORIGINS = ((0, 0), (4, 0), (0, 4), (4, 4))

_BLOCK_BYTES = 8
_ALPHA_BYTES = 8

def _clamp(value: int) -> int:
    return 0 if value < 0 else 255 if value > 255 else value

def _clamp5(value: int) -> int:
    return 0 if value < 0 else 31 if value > 31 else value

def _signed3(value: int) -> int:
    return value - 8 if value >= 4 else value

def _rgb555_to_888(r: int, g: int, b: int) -> tuple:
    return ((r << 3) | (r >> 2), (g << 3) | (g >> 2), (b << 3) | (b >> 2))

def decode_block(block: bytes) -> list:
    if len(block) < _BLOCK_BYTES:
        raise ValueError(f"an ETC1 block is {_BLOCK_BYTES} bytes, got {len(block)}")

    lo = int.from_bytes(block[0:4], "big")
    hi = int.from_bytes(block[4:8], "big")

    flip = lo & 1
    diff = (lo >> 1) & 1
    table0 = MODIFIER_TABLE[(lo >> 5) & 7]
    table1 = MODIFIER_TABLE[(lo >> 2) & 7]

    if diff:
        r = (lo >> 27) & 0x1F
        g = (lo >> 19) & 0x1F
        b = (lo >> 11) & 0x1F
        base0 = _rgb555_to_888(r, g, b)
        base1 = _rgb555_to_888(
            _clamp5(r + _signed3((lo >> 24) & 7)),
            _clamp5(g + _signed3((lo >> 16) & 7)),
            _clamp5(b + _signed3((lo >> 8) & 7)),
        )
    else:
        base0 = (((lo >> 28) & 0xF) * 17, ((lo >> 20) & 0xF) * 17, ((lo >> 12) & 0xF) * 17)
        base1 = (((lo >> 24) & 0xF) * 17, ((lo >> 16) & 0xF) * 17, ((lo >> 8) & 0xF) * 17)

    pixels = [None] * 16
    for x in range(4):
        for y in range(4):
            i = x * 4 + y
            index = (((hi >> (i + 16)) & 1) << 1) | ((hi >> i) & 1)

            first_subblock = (y < 2) if flip else (x < 2)
            base = base0 if first_subblock else base1
            delta = (table0 if first_subblock else table1)[index]

            pixels[y * 4 + x] = (
                _clamp(base[0] + delta),
                _clamp(base[1] + delta),
                _clamp(base[2] + delta),
            )

    return pixels

def decode_etc1(data: bytes, width: int, height: int, has_alpha: bool) -> bytearray:
    stride = _BLOCK_BYTES + (_ALPHA_BYTES if has_alpha else 0)
    tiles_across = width // 8
    tiles_down = height // 8
    needed = tiles_across * tiles_down * len(_BLOCK_ORIGINS) * stride
    if len(data) < needed:
        raise ValueError(f"ETC1 {width}x{height} needs {needed} bytes, got {len(data)}")

    out = bytearray(width * height * 4)
    cursor = 0

    for tile_y in range(tiles_down):
        for tile_x in range(tiles_across):
            for origin_x, origin_y in _BLOCK_ORIGINS:
                if has_alpha:
                    alpha_bytes = data[cursor:cursor + _ALPHA_BYTES]
                    colour_bytes = data[cursor + _ALPHA_BYTES:cursor + stride]
                else:
                    alpha_bytes = None
                    colour_bytes = data[cursor:cursor + _BLOCK_BYTES]
                cursor += stride

                pixels = decode_block(colour_bytes)

                for x in range(4):
                    for y in range(4):
                        if alpha_bytes is None:
                            a = 0xFF
                        else:
                            i = x * 4 + y
                            packed = alpha_bytes[i // 2]
                            nibble = packed & 0xF if i % 2 == 0 else (packed >> 4) & 0xF
                            a = nibble * 17

                        px = tile_x * 8 + origin_x + x
                        py = tile_y * 8 + origin_y + y
                        r, g, b = pixels[y * 4 + x]
                        offset = (py * width + px) * 4
                        out[offset:offset + 4] = bytes((r, g, b, a))

    return out
