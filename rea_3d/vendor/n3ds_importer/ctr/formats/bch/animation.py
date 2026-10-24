from __future__ import annotations

import math
import struct

from ..gfl2.gfmotion import BoneTrack, KeyFrame, Motion, Track
from .curve import constant, read_group
from .tree import read_cstring

ANIMATION_SIZE = 0x18
NAME_AT = 0x00
FLAGS_AT = 0x04
TYPE_AT = 0x05
FRAMES_AT = 0x08
ELEMENTS_AT = 0x0C

ELEMENT_SIZE = 0x08
ELEMENT_NAME_AT = 0x00
ELEMENT_TARGET_AT = 0x04
ELEMENT_PRIMITIVE_AT = 0x06
ELEMENT_CONTENT_AT = 0x08

TARGET_BONE = 0

PRIMITIVE_TRANSFORM = 4
PRIMITIVE_QUAT_TRANSFORM = 7

TYPE_SKELETAL = 0
LOOPING = 1

SCALE_X_CONSTANT = 1 << 6
SCALE_X_INEXISTENT = 1 << 16
ROTATION_W_CONSTANT = 1 << 12

QUAT_SCALE_CONSTANT = 1 << 2
QUAT_SCALE_INEXISTENT = 1 << 5

MAX_ELEMENTS = 4096
MAX_SAMPLES = 0x4000


def read_animations(data, contents, version: int) -> list:
    entry = contents.skeletal_animations
    motions = []
    for index in range(entry.count):
        pointer_at = entry.values + index * 4
        if pointer_at + 4 > len(data):
            break
        (animation_at,) = struct.unpack_from("<I", data, pointer_at)
        if not animation_at or animation_at + ANIMATION_SIZE > len(data):
            continue
        try:
            motion = read_animation(data, animation_at, version, index)
        except Exception:
            continue
        if not motion.name and index < len(entry.entries):
            motion.name = entry.entries[index]
        if motion.bones:
            motions.append(motion)
    return motions


def read_animation(data, at: int, version: int, index: int = 0) -> Motion:
    (name_at,) = struct.unpack_from("<I", data, at + NAME_AT)
    flags = data[at + FLAGS_AT]
    kind = data[at + TYPE_AT]
    (frames,) = struct.unpack_from("<f", data, at + FRAMES_AT)
    elements_at, element_count = struct.unpack_from("<II", data, at + ELEMENTS_AT)

    if kind != TYPE_SKELETAL:
        raise ValueError("animation type %d is not skeletal" % kind)
    if element_count > MAX_ELEMENTS:
        raise ValueError("implausible element count %d" % element_count)
    if not (0.0 <= frames < 100000.0):
        raise ValueError("implausible frame count %r" % frames)

    motion = Motion(index=index)
    motion.name = read_cstring(data, name_at)
    motion.frame_count = int(frames) + 1
    motion.looping = bool(flags & LOOPING)

    for slot in range(element_count):
        pointer_at = elements_at + slot * 4
        if pointer_at + 4 > len(data):
            break
        (element_at,) = struct.unpack_from("<I", data, pointer_at)
        if not element_at or element_at + ELEMENT_CONTENT_AT > len(data):
            continue
        bone = _read_element(data, element_at, version)
        if bone is not None:
            motion.bones.append(bone)

    return motion


def _read_element(data, at: int, version: int):
    (name_at,) = struct.unpack_from("<I", data, at + ELEMENT_NAME_AT)
    target, primitive = struct.unpack_from("<HH", data, at + ELEMENT_TARGET_AT)
    if target != TARGET_BONE:
        return None

    name = read_cstring(data, name_at)
    if not name:
        return None

    content = at + ELEMENT_CONTENT_AT
    if primitive == PRIMITIVE_TRANSFORM:
        tracks = _read_transform(data, content, version)
    elif primitive == PRIMITIVE_QUAT_TRANSFORM:
        tracks = _read_quat_transform(data, content)
    else:
        return None

    if tracks is None:
        return None

    bone = BoneTrack(name=name, is_axis_angle=False)
    bone.scale = tuple(tracks[0:3])
    bone.rotation = tuple(tracks[3:6])
    bone.translation = tuple(tracks[6:9])
    return bone if bone.animated else None


def _read_transform(data, at: int, version: int):
    if at + 4 + 9 * 4 > len(data):
        return None
    (flags,) = struct.unpack_from("<I", data, at)

    tracks = []
    constant_mask = SCALE_X_CONSTANT
    inexistent_mask = SCALE_X_INEXISTENT

    for slot in range(9):
        offset = at + 4 + slot * 4
        track = Track()

        if not flags & inexistent_mask:
            if flags & constant_mask:
                (value,) = struct.unpack_from("<f", data, offset)
                track = constant(value)
            else:
                (group_at,) = struct.unpack_from("<I", data, offset)
                if group_at and group_at + 0x10 <= len(data):
                    try:
                        track = read_group(data, group_at, version)
                    except Exception:
                        track = Track()

        tracks.append(track)
        constant_mask <<= 1
        inexistent_mask <<= 1
        if constant_mask == ROTATION_W_CONSTANT:
            constant_mask <<= 1

    return tracks


def _read_quat_transform(data, at: int):
    if at + 0x10 > len(data):
        return None
    (flags,) = struct.unpack_from("<I", data, at)
    addresses = struct.unpack_from("<3I", data, at + 4)

    scales, rotations, translations = [], [], []
    constant_mask = QUAT_SCALE_CONSTANT
    inexistent_mask = QUAT_SCALE_INEXISTENT

    for slot in range(3):
        if not flags & inexistent_mask:
            width = 4 if slot == 1 else 3
            values = _read_samples(
                data, addresses[slot], width, bool(flags & constant_mask))
            if slot == 0:
                scales = values
            elif slot == 1:
                rotations = values
            else:
                translations = values
        constant_mask >>= 1
        inexistent_mask >>= 1

    tracks = []
    tracks.extend(_axis_tracks(scales, 3))
    tracks.extend(_axis_tracks([_quat_to_euler(q) for q in rotations], 3))
    tracks.extend(_axis_tracks(translations, 3))
    return tracks


def _read_samples(data, at: int, width: int, is_constant: bool) -> list:
    if not at:
        return []

    address, count = at, 1
    if not is_constant:
        if at + 0x14 > len(data):
            return []
        address, count = struct.unpack_from("<II", data, at + 0x0C)
        if count > MAX_SAMPLES:
            return []

    size = width * 4
    if not address or address + count * size > len(data):
        return []
    return [struct.unpack_from("<%df" % width, data, address + i * size)
            for i in range(count)]


def _axis_tracks(samples: list, width: int) -> list:
    tracks = [Track() for _ in range(width)]
    for frame, sample in enumerate(samples):
        for axis in range(width):
            tracks[axis].keys.append(
                KeyFrame(frame=frame, value=sample[axis], slope=0.0))
    return tracks


def _quat_to_euler(quat: tuple) -> tuple:
    x, y, z, w = quat
    m00 = 1.0 - 2.0 * (y * y + z * z)
    m10 = 2.0 * (x * y + z * w)
    m20 = 2.0 * (x * z - y * w)
    m21 = 2.0 * (y * z + x * w)
    m22 = 1.0 - 2.0 * (x * x + y * y)
    m11 = 1.0 - 2.0 * (x * x + z * z)
    m12 = 2.0 * (y * z - x * w)

    cy = math.sqrt(m00 * m00 + m10 * m10)
    if cy > 1e-6:
        return (math.atan2(m21, m22), math.atan2(-m20, cy), math.atan2(m10, m00))
    return (math.atan2(-m12, m11), math.atan2(-m20, cy), 0.0)
