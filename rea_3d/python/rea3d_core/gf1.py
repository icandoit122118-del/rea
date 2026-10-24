"""Bounded Game Freak generation-1 skeletal motion reader.

SPDX-License-Identifier: GPL-3.0-or-later
Format reference: gdkchan/SPICA, commit bd29a7828595d7839cda2ac61c76bb63f9071250
SPICA/Formats/GFL/{GF1MotionPack.cs,Motion/GF1MotBone.cs,Motion/GF1Motion.cs}.
SPICA's reference implementation is dedicated to the public domain (Unlicense).
This adapter is new and does not modify the vendored ctr readers.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import struct

from ctr.binstream import BinStream
from ctr.formats.gfl2.gfmotion import BoneTrack, KeyFrame, Motion, Track

MAX_MOTIONS = 512
MAX_BONES = 255
MAX_FRAMES = 10000
MAX_OCTALS = 4096
MAX_KEYS = 100000

# Native decoder/evaluator association is established for these exact selected
# ORAS payloads only. Unknown profiles retain the reference interpretation.
ORAS_NATIVE_CODE_SHA256 = "d587c98ac5c4dedacf9be4baf2d6b7d10169e6a63f8bc35b132002cc27bc1a37"
ORAS_NATIVE_GF1_SHA256 = {
    "edf9fb9b854db962a7fff5f0418c460ed528bce3479648213de40924e6217ef6": "PB",
    "744d2a33522e4d2f831818363bf224a687dbd6a0712c1016fbb68c503942e941": "PK",
}


@dataclass
class GF1Curve(Track):
    interpolation: str = "hermite"

    def sample(self, frame):
        if self.interpolation not in ("linear", "smoothstep") or len(self.keys) < 2:
            return super().sample(frame)
        if frame <= self.keys[0].frame:
            return self.keys[0].value
        if frame >= self.keys[-1].frame:
            return self.keys[-1].value
        for lhs, rhs in zip(self.keys, self.keys[1:]):
            if lhs.frame <= frame <= rhs.frame:
                weight = (frame-lhs.frame)/(rhs.frame-lhs.frame)
                if self.interpolation == "smoothstep":
                    weight = weight*weight*(3-2*weight)
                return lhs.value+(rhs.value-lhs.value)*weight
        raise ValueError("GF1 curve has an invalid frame interval")


def _finite(value):
    if not math.isfinite(value):
        raise ValueError("GF1 contains a non-finite numeric value")
    return value


def _cstring(stream, max_bytes=256):
    chars = bytearray()
    for _ in range(max_bytes):
        value = stream.u8()
        if value == 0:
            return chars.decode("utf-8", "strict")
        chars.append(value)
    raise ValueError("GF1 bone name exceeds 255 bytes")


def _skeleton(blob, start, end):
    stream = BinStream(blob[:end], start)
    count, first_bone_index = stream.u8(), stream.u8()
    if not 1 <= count <= MAX_BONES:
        raise ValueError("GF1 skeleton bone count lies outside 1..255")
    bones = [{"name": "Origin", "parent": -1, "flags": 0, "children": 0}]
    for index in range(1, count):
        parent, flags, children = stream.u8(), stream.u8(), stream.u8()
        if parent >= count or parent == index:
            raise ValueError("GF1 skeleton contains an invalid parent index")
        bones.append({"name": None, "parent": parent, "flags": flags, "children": children})
    for bone in bones[1:]:
        bone["name"] = _cstring(stream)
    if len({b["name"] for b in bones}) != count:
        raise ValueError("GF1 skeleton contains duplicate names")
    stream.align(4)
    for bone in bones:
        bone["translation"] = tuple(_finite(x) for x in stream.vec(3))
        bone["quaternion"] = tuple(_finite(x) for x in stream.vec(4))
        if sum(x*x for x in bone["quaternion"]) < 1e-12:
            raise ValueError("GF1 skeleton contains a zero quaternion")
    return bones, first_bone_index


def _motion(blob, start, end, bones, index, native_profile=False):
    stream = BinStream(blob[:end], start)
    octal_count, last_frame = stream.u16(), stream.u16()
    if not 2 <= octal_count <= MAX_OCTALS or last_frame > MAX_FRAMES:
        raise ValueError("GF1 motion exceeds octal/frame limits")
    octals = []
    for i in range(octal_count):
        if i % 8 == 0:
            current = int.from_bytes(stream.bytes(3), "little")
        octals.append(current & 7)
        current >>= 3
    if last_frame > 255 and stream.tell() & 1:
        stream.u8()
    key_tables = []
    keys_total = 0
    for opcode in octals:
        if opcode < 6:
            continue
        read_index = stream.u16 if last_frame > 255 else stream.u8
        count = read_index()
        keys_total += count+2
        if keys_total > MAX_KEYS:
            raise ValueError("GF1 key count exceeds the motion budget")
        frames = [0]+[read_index() for _ in range(count)]+[last_frame]
        if any(a >= b for a, b in zip(frames, frames[1:])):
            raise ValueError("GF1 key frames must increase strictly inside the declared range")
        key_tables.append(frames)
    stream.align(4)
    elements, elem, table = {}, 0, 0
    hermite_count = 0
    for opcode in octals[2:]:
        if elem >= len(bones)*9:
            raise ValueError("GF1 motion references an element outside its skeleton")
        if opcode == 1:
            if elem % 3:
                raise ValueError("GF1 vector skip is not aligned to a vector")
            elem += 3
            continue
        bone_index, slot = divmod(elem, 9)
        element = elements.setdefault(bone_index, [GF1Curve() for _ in range(9)])
        curve = element[slot]
        if opcode <= 5:
            value = {0: 0., 2: math.pi/2, 3: math.pi, 4: -math.pi/2}.get(opcode)
            if opcode == 5:
                value = _finite(stream.f32())
            curve.keys.append(KeyFrame(0, value, 0.))
        else:
            if table >= len(key_tables):
                raise ValueError("GF1 curve has no frame-index table")
            frames = key_tables[table]
            table += 1
            curve.interpolation = ("smoothstep" if native_profile else "linear") if opcode == 6 else "hermite"
            for frame in frames:
                value = _finite(stream.f32())
                slope = _finite(stream.f32())/30. if opcode == 7 else 0.
                curve.keys.append(KeyFrame(frame, value, slope))
            hermite_count += int(opcode == 7)
        elem += 1
    if elem != len(bones)*9:
        raise ValueError(f"GF1 decoded {elem} components for a {len(bones)*9}-component skeleton")
    if table != len(key_tables):
        raise ValueError("GF1 unused frame-index tables remain")
    motion = Motion(index=index, frame_count=last_frame+1, looping=False, name=f"GF1_Motion_{index}")
    for bone_index, channels in elements.items():
        bone = bones[bone_index]
        track = BoneTrack(name=bone["name"], is_axis_angle=False,
                          translation=tuple(channels[:3]), rotation=tuple(channels[3:6]),
                          scale=tuple(channels[6:9]))
        track.gf1_world_space = bool(bone["flags"] & 8)
        motion.bones.append(track)
    motion.gf1_header_octals = octals[:2]
    motion.gf1_hermite_curves = hermite_count
    return motion


def read_pack(blob):
    """Read only a caller-identified GF1 pack; it has no unique magic signature.

    Returns per-clip errors separately, never a guessed successful empty motion.
    World-space tracks are retained with a marker; the writer omits those until a
    verified world-to-local interpretation is implemented.
    """
    digest = hashlib.sha256(blob).hexdigest()
    native_profile = digest in ORAS_NATIVE_GF1_SHA256
    stream = BinStream(blob)
    count = stream.u32()
    if not 2 <= count <= MAX_MOTIONS+1 or 4+4*count > len(blob):
        raise ValueError("GF1 pack count/offset table is invalid")
    offsets = [stream.u32() for _ in range(count)]
    nonzero = [offset for offset in offsets if offset]
    if not offsets[0] or any(offset < 4+4*count or offset >= len(blob) for offset in nonzero):
        raise ValueError("GF1 pack offset lies outside its payload")
    if len(set(nonzero)) != len(nonzero):
        raise ValueError("GF1 nonzero pack offsets must not alias")
    ordered = sorted(nonzero)
    bounds = {offset: next((x for x in ordered if x > offset), len(blob)) for offset in ordered}
    bones, first = _skeleton(blob, offsets[0], bounds[offsets[0]])
    warnings = []
    unexpected = [{"name": b["name"], "flags": b["flags"]} for b in bones if b["flags"] & ~8]
    if unexpected:
        warnings.append({"code": "GF1_SKELETON_FLAGS_UNVERIFIED", "bones": unexpected,
                         "message": "Non-world-space GF1 skeleton flag semantics are not established."})
    motions, errors = [], []
    for index, offset in enumerate(offsets[1:]):
        if not offset:
            continue
        try:
            motion = _motion(blob, offset, bounds[offset], bones, index, native_profile)
            if not motion.bones:
                raise ValueError("GF1 clip contains no supported bone tracks")
            motions.append(motion)
            world = [b.name for b in motion.bones if b.gf1_world_space]
            if world:
                warnings.append({"code": "GF1_WORLD_SPACE_UNSUPPORTED", "motion_index": index,
                                 "bones": world, "message": "World-space GF1 tracks are marked and will be omitted, not exported as local tracks."})
            if motion.gf1_hermite_curves and not native_profile:
                warnings.append({"code": "GF1_SLOPE_SCALE_ASSUMPTION", "motion_index": index,
                                 "message": "Hermite slopes use the SPICA reference's experimental 1/30 scale; real-game agreement is not established."})
            if motion.gf1_header_octals != [0, 0]:
                warnings.append({"code": "GF1_HEADER_FLAGS_UNVERIFIED", "motion_index": index,
                                 "values": motion.gf1_header_octals,
                                 "message": "Two leading GF1 opcode fields are preserved but their semantics are unverified."})
        except Exception as exc:
            errors.append({"index": index, "offset": offset, "error": f"{type(exc).__name__}: {exc}"})
    result = {"motions": motions, "errors": errors, "warnings": warnings,
            "skeleton": bones, "first_bone_index": first, "declared_slots": count-1,
            "reference": "gdkchan/SPICA@bd29a7828595d7839cda2ac61c76bb63f9071250 (Unlicense)"}
    if native_profile:
        result["native_interpolation_profile"] = {
            "schema": "rea3d-oras-native-gf1-interpolation-v1", "package": ORAS_NATIVE_GF1_SHA256[digest],
            "payload_sha256": digest, "source_code_sha256": ORAS_NATIVE_CODE_SHA256,
            "source_decoder": "0x3af660 ->0x3af770",
            "source_evaluators": {"uint8_frames": "0x54ea14", "uint16_frames": "0x54edcc"},
            "opcode6": "zero-tangent Hermite / smoothstep; vL+(vR-vL)*(3*u*u-2*u*u*u)",
            "opcode7": "Hermite; original stored slopes multiply elapsed source frames/30",
            "native_slope_time_scale_hexword": "0x3d088889",
            "native_slope_time_scale_float32": struct.unpack("<f", bytes.fromhex("8988083d"))[0],
            "slope_unit_scale_verified_from_original_code": True,
            "numeric_implementation": "Python double evaluation with slope/30; native VFP float32 rounding remains distinct",
            "native_code_executed_by_parser": False, "original_game_visual_parity_verified": False,
        }
    return result
