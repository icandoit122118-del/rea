from __future__ import annotations

import struct
import zlib

SIGNATURE = b"\x89PNG\r\n\x1a\n"
COLOR_RGBA = 6
BIT_DEPTH = 8
FILTER_NONE = 0


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def encode_rgba(rgba: bytes, width: int, height: int, flip_y: bool = False) -> bytes:
    expected = width * height * 4
    if len(rgba) != expected:
        raise ValueError(f"expected {expected} bytes for {width}x{height} RGBA, got {len(rgba)}")
    if width <= 0 or height <= 0:
        raise ValueError(f"invalid size {width}x{height}")

    stride = width * 4
    rows = bytearray()
    order = range(height - 1, -1, -1) if flip_y else range(height)
    for y in order:
        rows.append(FILTER_NONE)
        rows += rgba[y * stride:(y + 1) * stride]

    header = struct.pack(">IIBBBBB", width, height, BIT_DEPTH, COLOR_RGBA, 0, 0, 0)
    return (
        SIGNATURE
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(bytes(rows), 6))
        + _chunk(b"IEND", b"")
    )


def write_rgba(path: str, rgba: bytes, width: int, height: int, flip_y: bool = False) -> str:
    with open(path, "wb") as handle:
        handle.write(encode_rgba(rgba, width, height, flip_y))
    return path
