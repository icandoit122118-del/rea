from __future__ import annotations

import struct

from ..gfl2.gfmotion import KeyFrame, Track

HERMITE128 = 0
HERMITE64 = 1
HERMITE48 = 2
UNIFIED_HERMITE96 = 3
UNIFIED_HERMITE48 = 4
UNIFIED_HERMITE32 = 5
STEP_LINEAR64 = 6
STEP_LINEAR32 = 7

KEY_SIZE = {
    HERMITE128: 16,
    HERMITE64: 8,
    HERMITE48: 6,
    UNIFIED_HERMITE96: 12,
    UNIFIED_HERMITE48: 6,
    UNIFIED_HERMITE32: 4,
    STEP_LINEAR64: 8,
    STEP_LINEAR32: 4,
}

FP_1_7_8 = 1.0 / 256.0
FP_1_6_5 = 1.0 / 32.0
FP_1_10_5 = 1.0 / 32.0

MODERN_VERSION = 0x20
MAX_KEYS = 0x4000


def _signed(value: int, bits: int) -> int:
    sign = 1 << (bits - 1)
    return (value & (sign - 1)) - (value & sign)


def _u24(data, at: int) -> int:
    return data[at] | (data[at + 1] << 8) | (data[at + 2] << 16)


def _key(data, at: int, quantization: int) -> tuple:
    if quantization == HERMITE128:
        frame, value, in_slope, out_slope = struct.unpack_from("<4f", data, at)
        return frame, value, in_slope, out_slope

    if quantization == HERMITE64:
        packed, in_slope, out_slope = struct.unpack_from("<Ihh", data, at)
        return (packed & 0xFFF, (packed >> 12) & 0xFFFFF,
                in_slope * FP_1_7_8, out_slope * FP_1_7_8)

    if quantization == HERMITE48:
        frame = data[at]
        (value,) = struct.unpack_from("<H", data, at + 1)
        slopes = _u24(data, at + 3)
        return (frame, value,
                _signed(slopes, 12) * FP_1_6_5,
                _signed(slopes >> 12, 12) * FP_1_6_5)

    if quantization == UNIFIED_HERMITE96:
        frame, value, slope = struct.unpack_from("<3f", data, at)
        return frame, value, slope, slope

    if quantization == UNIFIED_HERMITE48:
        frame, value, slope = struct.unpack_from("<HHh", data, at)
        slope *= FP_1_7_8
        return frame * FP_1_10_5, value, slope, slope

    if quantization == UNIFIED_HERMITE32:
        frame = data[at]
        packed = _u24(data, at + 1)
        slope = _signed(packed >> 12, 12) * FP_1_6_5
        return frame, packed & 0xFFF, slope, slope

    if quantization == STEP_LINEAR64:
        frame, value = struct.unpack_from("<2f", data, at)
        return frame, value, 0.0, 0.0

    if quantization == STEP_LINEAR32:
        (packed,) = struct.unpack_from("<I", data, at)
        return (packed & 0xFFF), ((packed >> 12) & 0xFFFFF), 0.0, 0.0

    raise ValueError("unknown keyframe quantization %d" % quantization)


def read_group(data, at: int, version: int) -> Track:
    if version < MODERN_VERSION:
        (inner,) = struct.unpack_from("<I", data, at + 0x0C)
        if inner + 0x20 > len(data):
            raise ValueError("curve header at %#x runs past the file" % inner)
        interpolation = data[inner + 0x08]
        quantization = data[inner + 0x09]
        (count,) = struct.unpack_from("<H", data, inner + 0x0A)
        value_scale, value_offset, frame_scale = struct.unpack_from(
            "<3f", data, inner + 0x10)
        (keys_at,) = struct.unpack_from("<I", data, inner + 0x1C)
    else:
        if at + 0x24 > len(data):
            raise ValueError("curve header at %#x runs past the file" % at)
        interpolation = data[at + 0x0C]
        quantization = data[at + 0x0D]
        (count,) = struct.unpack_from("<H", data, at + 0x0E)
        value_scale, value_offset, frame_scale = struct.unpack_from(
            "<3f", data, at + 0x10)
        (keys_at,) = struct.unpack_from("<I", data, at + 0x20)

    if quantization not in KEY_SIZE:
        raise ValueError("unknown keyframe quantization %d" % quantization)
    if count > MAX_KEYS:
        raise ValueError("implausible key count %d" % count)

    size = KEY_SIZE[quantization]
    if keys_at + count * size > len(data):
        raise ValueError("%d key(s) at %#x run past the file" % (count, keys_at))

    track = Track()
    for index in range(count):
        frame, value, in_slope, out_slope = _key(
            data, keys_at + index * size, quantization)
        track.keys.append(KeyFrame(
            frame=frame * frame_scale,
            value=value * value_scale + value_offset,
            slope=0.0 if interpolation == 0 else (in_slope + out_slope) * 0.5,
        ))
    return track


def constant(value: float) -> Track:
    track = Track()
    track.keys.append(KeyFrame(frame=0, value=value, slope=0.0))
    return track
