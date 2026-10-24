from __future__ import annotations

import struct

LZ10 = 0x10
LZ11 = 0x11

_MIN_PLAUSIBLE_SIZE = 4

class CompressionError(ValueError):
    pass

def _read_header(data: bytes) -> tuple[int, int, int]:
    if len(data) < 4:
        raise CompressionError("too short to hold an LZ header")
    type_byte = data[0]
    size = data[1] | (data[2] << 8) | (data[3] << 16)
    offset = 4
    if size == 0:
        if len(data) < 8:
            raise CompressionError("extended size header is truncated")
        size = struct.unpack_from("<I", data, 4)[0]
        offset = 8
    return type_byte, size, offset

def sniff_lz(head: bytes) -> bool:
    if len(head) < 4:
        return False
    if head[0] not in (LZ10, LZ11):
        return False
    size = head[1] | (head[2] << 8) | (head[3] << 16)
    return size == 0 or size >= _MIN_PLAUSIBLE_SIZE

def decompress_lz(data: bytes) -> bytes:
    type_byte, _, _ = _read_header(data)
    if type_byte == LZ10:
        return decompress_lz10(data)
    if type_byte == LZ11:
        return decompress_lz11(data)
    raise CompressionError(f"unsupported compression type {type_byte:#04x}")

def _copy_back(out: bytearray, disp: int, length: int) -> None:
    start = len(out) - disp
    if start < 0:
        raise CompressionError(
            f"back-reference of {disp} reaches before the output start "
            f"({len(out)} bytes written)"
        )
    for i in range(length):
        out.append(out[start + i])

def decompress_lz10(data: bytes) -> bytes:
    type_byte, size, pos = _read_header(data)
    if type_byte != LZ10:
        raise CompressionError(f"expected LZ10, got type {type_byte:#04x}")

    out = bytearray()
    n = len(data)
    while len(out) < size:
        if pos >= n:
            raise CompressionError(
                f"input exhausted after {len(out)} of {size} declared bytes"
            )
        flags = data[pos]
        pos += 1
        for bit in range(7, -1, -1):
            if len(out) >= size:
                break
            if pos >= n:
                raise CompressionError("input exhausted mid-group")
            if not (flags >> bit) & 1:
                out.append(data[pos])
                pos += 1
                continue
            if pos + 1 >= n:
                raise CompressionError("truncated back-reference")
            b1, b2 = data[pos], data[pos + 1]
            pos += 2
            length = (b1 >> 4) + 3
            disp = (((b1 & 0xF) << 8) | b2) + 1
            _copy_back(out, disp, length)

    return bytes(out[:size])

def decompress_lz11(data: bytes) -> bytes:
    type_byte, size, pos = _read_header(data)
    if type_byte != LZ11:
        raise CompressionError(f"expected LZ11, got type {type_byte:#04x}")

    out = bytearray()
    n = len(data)
    while len(out) < size:
        if pos >= n:
            raise CompressionError(
                f"input exhausted after {len(out)} of {size} declared bytes"
            )
        flags = data[pos]
        pos += 1
        for bit in range(7, -1, -1):
            if len(out) >= size:
                break
            if pos >= n:
                raise CompressionError("input exhausted mid-group")
            if not (flags >> bit) & 1:
                out.append(data[pos])
                pos += 1
                continue

            if pos + 1 >= n:
                raise CompressionError("truncated back-reference")
            b1 = data[pos]
            indicator = b1 >> 4

            if indicator == 0:
                if pos + 2 >= n:
                    raise CompressionError("truncated 3-byte back-reference")
                b2, b3 = data[pos + 1], data[pos + 2]
                length = (((b1 & 0xF) << 4) | (b2 >> 4)) + 0x11
                disp = (((b2 & 0xF) << 8) | b3) + 1
                pos += 3
            elif indicator == 1:
                if pos + 3 >= n:
                    raise CompressionError("truncated 4-byte back-reference")
                b2, b3, b4 = data[pos + 1], data[pos + 2], data[pos + 3]
                length = (((b1 & 0xF) << 12) | (b2 << 4) | (b3 >> 4)) + 0x111
                disp = (((b3 & 0xF) << 8) | b4) + 1
                pos += 4
            else:
                b2 = data[pos + 1]
                length = indicator + 1
                disp = (((b1 & 0xF) << 8) | b2) + 1
                pos += 2

            _copy_back(out, disp, length)

    return bytes(out[:size])
