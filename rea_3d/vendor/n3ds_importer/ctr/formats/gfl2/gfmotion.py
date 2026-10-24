from __future__ import annotations

import math
from dataclasses import dataclass, field

from ...binstream import BinStream

SECT_SUBHEADER = 0
SECT_SKELETAL = 1
SECT_MATERIAL = 3
SECT_VISIBILITY = 6

_ELEMENT_CONSTANT = 3
_ELEMENT_KEYFRAMES = (4, 5)
_WIDE_FRAME_THRESHOLD = 0xFF

@dataclass(frozen=True)
class KeyFrame:
    frame: int
    value: float
    slope: float

@dataclass
class Track:
    keys: list = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.keys)

    def sample(self, frame: float) -> float:
        keys = self.keys
        if not keys:
            return 0.0
        if len(keys) == 1:
            return keys[0].value
        if frame <= keys[0].frame:
            return keys[0].value
        if frame >= keys[-1].frame:
            return keys[-1].value

        lhs = keys[0]
        rhs = keys[-1]
        for i in range(len(keys) - 1):
            if keys[i].frame <= frame <= keys[i + 1].frame:
                lhs, rhs = keys[i], keys[i + 1]
                break

        if rhs.frame == lhs.frame:
            return lhs.value

        diff = frame - lhs.frame
        weight = diff / (rhs.frame - lhs.frame)
        result = lhs.value + (lhs.value - rhs.value) * (2 * weight - 3) * weight * weight
        result += (diff * (weight - 1)) * (lhs.slope * (weight - 1) + rhs.slope * weight)
        return result

@dataclass
class BoneTrack:
    name: str
    is_axis_angle: bool = False
    scale: tuple = ()
    rotation: tuple = ()
    translation: tuple = ()

    @property
    def animated(self) -> bool:
        return any(t for t in self.scale + self.rotation + self.translation)

@dataclass
class Motion:
    index: int = 0
    frame_count: int = 0
    looping: bool = False
    name: str = ""
    bones: list = field(default_factory=list)

def _read_element(stream: BinStream, flags: int, frame_count: int) -> Track:
    kind = flags & 7
    track = Track()

    if kind == _ELEMENT_CONSTANT:
        track.keys.append(KeyFrame(0, stream.f32(), 0.0))
        return track

    if kind not in _ELEMENT_KEYFRAMES:
        return track

    count = stream.u32()
    wide = frame_count > _WIDE_FRAME_THRESHOLD
    frames = [stream.u16() if wide else stream.u8() for _ in range(count)]
    stream.align(4)

    if flags & 1:
        for frame in frames:
            track.keys.append(KeyFrame(frame, stream.f32(), stream.f32()))
    else:
        value_scale = stream.f32()
        value_offset = stream.f32()
        slope_scale = stream.f32()
        slope_offset = stream.f32()
        for frame in frames:
            value = stream.u16() / 65535.0 * value_scale + value_offset
            slope = stream.u16() / 65535.0 * slope_scale + slope_offset
            track.keys.append(KeyFrame(frame, value, slope))

    return track

def _read_bone_track(stream: BinStream, name: str, frame_count: int) -> BoneTrack:
    flags = stream.u32()
    stream.u32()

    bone = BoneTrack(name=name, is_axis_angle=(flags >> 31) == 0)

    elements = []
    for _ in range(9):
        elements.append(_read_element(stream, flags, frame_count))
        flags >>= 3

    bone.scale = tuple(elements[0:3])
    bone.rotation = tuple(elements[3:6])
    bone.translation = tuple(elements[6:9])
    return bone

def read_motion(stream: BinStream, index: int = 0) -> Motion:
    base = stream.tell()
    stream.u32()
    section_count = stream.u32()

    sections = []
    for _ in range(section_count):
        sections.append((stream.u32(), stream.u32(), stream.u32()))

    motion = Motion(index=index)

    kind, _length, address = sections[0]
    if kind != SECT_SUBHEADER:
        raise ValueError(f"first motion section is {kind}, expected a subheader")
    stream.seek(base + address)
    motion.frame_count = stream.u32()
    motion.looping = (stream.u16() & 1) != 0
    stream.u16()
    stream.vec(3)
    stream.vec(3)
    stream.u32()

    for kind, _length, address in sections[1:]:
        if kind != SECT_SKELETAL:
            continue
        stream.seek(base + address)
        motion.bones = _read_skeletal(stream, motion.frame_count)

    return motion

def _read_skeletal(stream: BinStream, frame_count: int) -> list:
    name_count = stream.i32()
    name_block_length = stream.u32()
    block_start = stream.tell()

    names = [stream.bstring() for _ in range(name_count)]
    stream.seek(block_start + name_block_length)

    return [_read_bone_track(stream, name, frame_count) for name in names]

def sample_bone(bone: BoneTrack, frame: float, rest) -> tuple:
    scale = tuple(
        track.sample(frame) if track else rest.scale[i]
        for i, track in enumerate(bone.scale)
    )
    translation = tuple(
        track.sample(frame) if track else rest.translation[i]
        for i, track in enumerate(bone.translation)
    )
    rotation = tuple(
        track.sample(frame) if track else rest.rotation[i]
        for i, track in enumerate(bone.rotation)
    )

    if bone.is_axis_angle:
        rotation = _axis_angle_to_euler(rotation)

    return scale, rotation, translation

def _axis_angle_to_euler(vector: tuple) -> tuple:
    length = math.sqrt(sum(c * c for c in vector))
    if length <= 0.0:
        return (0.0, 0.0, 0.0)

    angle = length * 2.0
    axis = [c / length for c in vector]
    half = angle * 0.5
    s = math.sin(half)
    x, y, z, w = axis[0] * s, axis[1] * s, axis[2] * s, math.cos(half)

    sinr_cosp = 2 * (w * x + y * z)
    cosr_cosp = 1 - 2 * (x * x + y * y)
    roll = math.atan2(sinr_cosp, cosr_cosp)

    sinp = 2 * (w * y - z * x)
    pitch = math.copysign(math.pi / 2, sinp) if abs(sinp) >= 1 else math.asin(sinp)

    siny_cosp = 2 * (w * z + x * y)
    cosy_cosp = 1 - 2 * (y * y + z * z)
    yaw = math.atan2(siny_cosp, cosy_cosp)

    return (roll, pitch, yaw)
