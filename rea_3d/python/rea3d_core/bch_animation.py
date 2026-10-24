"""BCH material/visibility curves and bounded Pokemon PB/PK clip association.

SPDX-License-Identifier: GPL-3.0-or-later
Format reference: gdkchan/SPICA@bd29a7828595d7839cda2ac61c76bb63f9071250,
SPICA/Formats/CtrH3D/Animation and SPICA.Rendering/Animation (Unlicense).
PB/PK association is corroborated on the selected ORAS files by slot, clip name,
frame duration, dictionary kind and six-float bounding records. It is not claimed
as a universal rule for every game package.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import math
import struct

from ctr.binstream import BinStream
from ctr.compress.lz import decompress_lz, sniff_lz
from ctr.formats.bch import curve as source_curve
from ctr.formats.bch.header import read_header, relocate
from ctr.formats.bch.contents import read_contents
from ctr.formats.bch.tree import read_cstring

REFERENCE = "gdkchan/SPICA@bd29a7828595d7839cda2ac61c76bb63f9071250 (Unlicense)"
MAX_BYTES = 128*1024*1024
MAX_ELEMENTS = 4096
MAX_FRAMES = 100000
MAX_KEYS = 16384
# Selected ORAS PB slot 25 and PK slot 33 contain the same 136-byte motion.
# The identity gate keeps an observed auxiliary role from becoming a guessed
# rule for arbitrary out-of-primary slots in other Pokemon packages.
_ORAS_SHARED_AUX_SHA256 = "dba54618ad5b76df354325cf7ac7562a603bd46c909542cc4e6a2aec7fb29a3f"

TARGETS = ["bone"] + [f"material_constant{i}" for i in range(6)] + [
    "material_emission", "material_ambient", "material_diffuse", "material_specular0", "material_specular1",
    "material_mapper0_border_color", "material_mapper0_texture", "material_mapper1_border_color", "material_mapper1_texture",
    "material_mapper2_border_color", "material_mapper2_texture", "material_blend_color",
    "material_texcoord0_scale", "material_texcoord0_rotation", "material_texcoord0_translation",
    "material_texcoord1_scale", "material_texcoord1_rotation", "material_texcoord1_translation",
    "material_texcoord2_scale", "material_texcoord2_rotation", "material_texcoord2_translation",
    "light_transform", "light_ambient", "light_diffuse", "light_specular0", "light_specular1", "light_ground", "light_sky",
    "light_direction", "light_attenuation_start", "light_attenuation_end", "light_interpolation_factor", "light_enabled",
    "camera_transform", "camera_target_position", "camera_twist", "camera_up_vector", "camera_view_rotation",
    "camera_z_near", "camera_z_far", "camera_aspect_ratio", "camera_height", "mesh_node_visibility", "model_visibility", "fog_color"]
PRIMITIVES = {0: "float", 1: "integer", 2: "vector2", 3: "vector3", 4: "transform", 5: "rgba",
              6: "texture", 7: "quaternion_transform", 8: "boolean", 9: "matrix_transform"}
# The selected ORAS v0x21 visibility dictionary stores wire target 47. Resolve
# these in dictionary context instead of assuming the newer SPICA enum numbering
# of unrelated light/camera targets applies to this older shipped asset profile.
TARGETS[47] = "mesh_node_visibility"
TARGETS[48] = "model_visibility"


def _range(data, at, size):
    if not isinstance(at, int) or at < 0 or size < 0 or at+size > len(data):
        raise ValueError(f"range {at}+{size} exceeds {len(data)}-byte BCH payload")


def _unpack(fmt, data, at):
    _range(data, at, struct.calcsize(fmt))
    return struct.unpack_from(fmt, data, at)


def _finite(value):
    if not math.isfinite(value):
        raise ValueError("non-finite animation number")
    return value


@dataclass
class Key:
    frame: float
    value: float
    in_slope: float = 0.
    out_slope: float = 0.


@dataclass
class Curve:
    keys: list[Key] = field(default_factory=list)
    interpolation: str = "step"
    start_frame: float = 0.
    end_frame: float = 0.
    pre_repeat: int = 0
    post_repeat: int = 0
    quantization: int | None = None

    def sample(self, frame):
        _finite(float(frame))
        if not self.keys:
            return None
        if len(self.keys) == 1 or frame <= self.keys[0].frame:
            return self.keys[0].value
        if frame >= self.keys[-1].frame:
            return self.keys[-1].value
        for lhs, rhs in zip(self.keys, self.keys[1:]):
            if lhs.frame <= frame < rhs.frame:
                if self.interpolation == "step":
                    return lhs.value
                duration = rhs.frame-lhs.frame
                t = (frame-lhs.frame)/duration
                if self.interpolation == "linear":
                    return lhs.value+(rhs.value-lhs.value)*t
                if self.interpolation == "hermite":
                    return ((2*t*t*t-3*t*t+1)*lhs.value + (t*t*t-2*t*t+t)*duration*lhs.out_slope
                            + (-2*t*t*t+3*t*t)*rhs.value + (t*t*t-t*t)*duration*rhs.in_slope)
                raise ValueError(f"unsupported interpolation {self.interpolation}")
        raise ValueError("animation frame was not bracketed by keys")

    def to_dict(self):
        return {"kind": "float", "interpolation": self.interpolation, "start_frame": self.start_frame,
                "end_frame": self.end_frame, "pre_repeat": self.pre_repeat, "post_repeat": self.post_repeat,
                "quantization": self.quantization, "keys": [vars(key).copy() for key in self.keys]}


@dataclass
class BooleanCurve:
    values: list[bool] = field(default_factory=list)
    start_frame: float = 0.
    end_frame: float = 0.
    pre_repeat: int = 0
    post_repeat: int = 0

    def sample(self, frame):
        _finite(float(frame))
        if not self.values:
            return None
        return self.values[max(0, min(len(self.values)-1, int(frame)))]

    def to_dict(self):
        return {"kind": "boolean", "values": self.values.copy(), "start_frame": self.start_frame,
                "end_frame": self.end_frame, "pre_repeat": self.pre_repeat, "post_repeat": self.post_repeat}


@dataclass
class Element:
    name: str
    target_id: int
    primitive_id: int
    curves: list = field(default_factory=list)

    def to_dict(self):
        return {"name": self.name, "target_id": self.target_id,
                "target": TARGETS[self.target_id] if self.target_id < len(TARGETS) else f"unknown_{self.target_id}",
                "primitive_id": self.primitive_id, "primitive": PRIMITIVES.get(self.primitive_id, "unknown"),
                "curves": [curve.to_dict() if curve is not None else None for curve in self.curves]}


@dataclass
class Animation:
    name: str
    kind: str
    flags: int
    end_frame: float
    elements: list[Element] = field(default_factory=list)
    texture_names: list[str] = field(default_factory=list)
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    def to_dict(self):
        return {"name": self.name, "kind": self.kind, "flags": self.flags, "looping": bool(self.flags & 1),
                "end_frame": self.end_frame, "frame_count": int(self.end_frame)+1,
                "texture_names": self.texture_names.copy(), "elements": [element.to_dict() for element in self.elements],
                "errors": self.errors.copy(), "warnings": self.warnings.copy()}


def read_curve(data, at, version):
    start, end, pre, post, _index = _unpack("<ffBBH", data, at)
    _finite(start); _finite(end)
    if version < 0x20:
        inner, = _unpack("<I", data, at+12)
        _range(data, inner, 32)
        interpolation, quantization, count = _unpack("<BBH", data, inner+8)
        value_scale, value_offset, frame_scale, keys_at = _unpack("<fffI", data, inner+16)
    else:
        _range(data, at, 36)
        interpolation, quantization, count = _unpack("<BBH", data, at+12)
        value_scale, value_offset, frame_scale = _unpack("<3f", data, at+16)
        keys_at, = _unpack("<I", data, at+32)
    if interpolation not in (0, 1, 2) or quantization not in source_curve.KEY_SIZE or not 1 <= count <= MAX_KEYS:
        raise ValueError("unsupported interpolation, quantization, or key count")
    for value in (value_scale, value_offset, frame_scale):
        _finite(value)
    size = source_curve.KEY_SIZE[quantization]
    _range(data, keys_at, count*size)
    keys = []
    for index in range(count):
        frame, value, in_slope, out_slope = source_curve._key(data, keys_at+index*size, quantization)
        keys.append(Key(_finite(frame*frame_scale), _finite(value*value_scale+value_offset),
                        _finite(in_slope), _finite(out_slope)))
    if any(a.frame >= b.frame for a, b in zip(keys, keys[1:])):
        raise ValueError("curve frames do not increase strictly")
    return Curve(keys, ["step", "linear", "hermite"][interpolation], start, end, pre, post, quantization)


def _vector(data, at, width, version):
    flags, = _unpack("<I", data, at)
    if flags & ~0x000f0f0f:
        raise ValueError(f"unknown vector flags {flags:#x}")
    output = []
    for axis in range(width):
        slot = at+4+4*axis
        if flags & (1 << (8+axis)):
            output.append(None)
        elif flags & (1 << axis):
            value, = _unpack("<f", data, slot)
            output.append(Curve([Key(0., _finite(value))]))
        else:
            pointer, = _unpack("<I", data, slot)
            if not pointer:
                raise ValueError("nonconstant curve has a null pointer")
            output.append(read_curve(data, pointer, version))
    return output


def _boolean(data, at):
    start, end, pre, post, _index, values_at, count = _unpack("<ffBBHII", data, at)
    if not 1 <= count <= MAX_FRAMES:
        raise ValueError("boolean sample count is invalid")
    _finite(start); _finite(end)
    _range(data, values_at, ((count+31)//32)*4)
    return BooleanCurve([bool(data[values_at+i//8] & (1 << (i % 8))) for i in range(count)], start, end, pre, post)


def _animation(data, at, kind, version):
    name_at, flags, declared_kind, _curve_count, end, elements_at, count, _meta = _unpack("<IBBHfIII", data, at)
    if not 0 <= _finite(end) <= MAX_FRAMES or count > MAX_ELEMENTS:
        raise ValueError("animation frame/element count exceeds limits")
    if declared_kind != (1 if kind == "material" else 2):
        raise ValueError("animation dictionary and declared type disagree")
    animation = Animation(read_cstring(data, name_at), kind, flags, end)
    if flags & ~3:
        animation.warnings.append({"code": "UNKNOWN_ANIMATION_FLAGS", "flags": flags})
    if kind == "material":
        textures_at, texture_count = _unpack("<II", data, at+24)
        if texture_count > MAX_ELEMENTS:
            raise ValueError("texture-name table exceeds limits")
        _range(data, textures_at, texture_count*4)
        animation.texture_names = [read_cstring(data, _unpack("<I", data, textures_at+4*i)[0]) for i in range(texture_count)]
    _range(data, elements_at, count*4)
    for index in range(count):
        try:
            element_at, = _unpack("<I", data, elements_at+4*index)
            name_at, target, primitive = _unpack("<IHH", data, element_at)
            if kind == "material" and not 1 <= target <= 27:
                raise ValueError(f"target {target} is not a supported material target")
            if kind == "visibility" and target not in (47, 48):
                raise ValueError(f"target {target} is not mesh/model visibility")
            if kind == "visibility" and primitive != 8:
                raise ValueError("visibility target does not contain a boolean primitive")
            if kind == "material":
                expected = (6 if target in (13, 15, 17) else
                            0 if target in (20, 23, 26) else
                            2 if target in (19, 21, 22, 24, 25, 27) else 5)
                if primitive != expected:
                    raise ValueError(f"material target {target} requires primitive {expected}, got {primitive}")
            if primitive in (0, 6):
                curves = _vector(data, element_at+8, 1, version)
            elif primitive in (2, 3, 5):
                curves = _vector(data, element_at+8, {2: 2, 3: 3, 5: 4}[primitive], version)
            elif primitive == 8:
                curves = [_boolean(data, element_at+8)]
            else:
                raise ValueError(f"unsupported material/visibility primitive {primitive}")
            animation.elements.append(Element(read_cstring(data, name_at), target, primitive, curves))
            for curve in curves:
                if curve is not None and (curve.pre_repeat or curve.post_repeat):
                    animation.warnings.append({"code": "CURVE_REPEAT_NOT_APPLIED", "element_index": index,
                                               "message": "Curve repeat metadata is preserved; evaluation follows the SPICA clamped curve evaluator and optional animation looping."})
        except Exception as exc:
            animation.errors.append({"element_index": index, "error": f"{type(exc).__name__}: {exc}"})
    return animation


def parse_bch(blob):
    result = {"material": [], "visibility": [], "errors": [], "warnings": [], "reference": REFERENCE}
    try:
        if len(blob) > MAX_BYTES:
            raise ValueError("BCH file exceeds byte budget")
        header = read_header(BinStream(blob))
        for name in ("contents", "strings", "commands", "raw_data", "raw_ext", "relocation"):
            _range(blob, getattr(header, name+"_address"), getattr(header, name+"_length"))
        if header.relocation_length % 4:
            raise ValueError("relocation length is not word aligned")
        data = relocate(blob, header)
        contents = read_contents(data, header)
        for kind in ("material", "visibility"):
            entry = contents.dicts[kind+"_animations"]
            if entry.count > MAX_ELEMENTS:
                raise ValueError("animation dictionary exceeds element budget")
            _range(data, entry.values, 4*entry.count)
            for index in range(entry.count):
                try:
                    pointer, = _unpack("<I", data, entry.values+4*index)
                    animation = _animation(data, pointer, kind, header.backward)
                    if not animation.name and index < len(entry.entries):
                        animation.name = entry.entries[index]
                    result[kind].append(animation)
                except Exception as exc:
                    result["errors"].append({"kind": kind, "index": index, "error": f"{type(exc).__name__}: {exc}"})
        result["version"] = header.backward
    except Exception as exc:
        result["errors"].append({"error": f"{type(exc).__name__}: {exc}"})
    return result


def evaluate_animation(animation, frame, loop=False):
    frame = _finite(float(frame))
    if loop and animation.flags & 1 and animation.end_frame > 0:
        frame %= animation.end_frame
    output = []
    for element in animation.elements:
        item = {"name": element.name, "target_id": element.target_id,
                "target": TARGETS[element.target_id], "primitive": PRIMITIVES[element.primitive_id],
                "values": [curve.sample(frame) if curve is not None else None for curve in element.curves]}
        if element.primitive_id == 6 and item["values"][0] is not None:
            index = int(item["values"][0])
            if not 0 <= index < len(animation.texture_names):
                item["error"] = "texture-selection index lies outside the animation texture-name table"
            else:
                item["texture_name"] = animation.texture_names[index]
        output.append(item)
    return output


def _selected_oras_auxiliary_pose(blob, offsets, slot, record_count):
    """Return source masks for the corroborated shared sparse ORAS motion.

    The asset establishes a pose payload, not a gameplay trigger or a base-clip
    pairing. Its explicit constant channels matter just as much as its one
    varying channel. Consumer overlay playback must preserve absent channels.
    """
    if slot != record_count+7:
        return None
    start = offsets[slot+1]
    end = min((at for at in offsets if at > start), default=len(blob))
    payload = blob[start:end]
    digest = hashlib.sha256(payload).hexdigest()
    if len(payload) != 136 or digest != _ORAS_SHARED_AUX_SHA256:
        return None
    from .gf1 import read_pack
    pack = read_pack(blob)
    motion = next((motion for motion in pack["motions"] if motion.index == slot), None)
    if motion is None or motion.frame_count != 71 or pack["first_bone_index"] != 0:
        return None
    expected = ["LFoot", "RFoot", "Feeler", "Jaw"]
    if [bone.name for bone in motion.bones] != expected or any(bone.gf1_world_space for bone in motion.bones):
        return None
    masks, constants, variables = [], [], []
    for bone in motion.bones:
        for path in ("translation", "rotation", "scale"):
            curves = getattr(bone, path)
            axes = [axis for axis, curve in enumerate(curves) if curve.keys]
            if axes:
                masks.append({"bone": bone.name, "path": path, "axes": axes})
            varying_axes = []
            for axis in axes:
                curve = curves[axis]
                if all(key.value == curve.keys[0].value and key.slope == 0 for key in curve.keys):
                    constants.append({"bone": bone.name, "path": path, "axis": axis, "value": curve.keys[0].value})
                else:
                    varying_axes.append(axis)
            if varying_axes:
                variables.append({"bone": bone.name, "path": path, "axes": varying_axes})
    if (masks != [{"bone": name, "path": "rotation", "axes": [0, 1, 2]} for name in expected]
            or variables != [{"bone": "Jaw", "path": "rotation", "axes": [1]}]):
        return None
    return {"payload_sha256": digest, "source_entry": 0, "payload_offset": start,
            "payload_length": len(payload), "source_frame_count": motion.frame_count,
            "gf1_first_bone_index": pack["first_bone_index"], "channel_mask": masks,
            "constant_targets": constants, "variable_targets": variables,
            "automatic_base_association_verified": False,
            "source_rotation_representation": "absolute_local_euler_radians",
            "composite_playback_policy": "user_selected_base_then_override_present_source_channels",
            "classification_basis": "selected_oras_payload_identity_and_sparse_channel_mask"}


def parse_pokemon_pack(blob, label=""):
    result = {"schema": "rea3d-pokemon-visual-animation-v1", "label": label, "reference": REFERENCE,
              "kind": "", "record_count": 0, "bounds": [], "entries": {}, "mappings": [], "errors": [], "warnings": []}
    if sniff_lz(blob[:8]):
        size = int.from_bytes(blob[1:4], "little") or int.from_bytes(blob[4:8], "little")
        if size > MAX_BYTES:
            raise ValueError("compressed package exceeds byte budget")
        blob = decompress_lz(blob)
    if len(blob) > MAX_BYTES or blob[:2] not in (b"PB", b"PK"):
        raise ValueError("expected a bounded PB/PK package")
    count, = _unpack("<H", blob, 2)
    if not 2 <= count <= MAX_ELEMENTS:
        raise ValueError("PB/PK entry count is invalid")
    offsets = _unpack("<"+"I"*(count+1), blob, 4)
    if offsets[0] < 4+4*(count+1) or offsets[-1] > len(blob) or any(a > b for a, b in zip(offsets, offsets[1:])):
        raise ValueError("PB/PK offsets are invalid")
    entries = [blob[a:b] for a, b in zip(offsets, offsets[1:])]
    result["kind"] = blob[:2].decode()
    metadata = entries[1]
    if len(metadata) % 24 or not metadata:
        result["errors"].append({"entry": 1, "error": "six-float clip-bounds table has an invalid size"})
        return result
    n = len(metadata)//24
    if n > MAX_ELEMENTS or 2+3*n > count:
        raise ValueError("PB/PK does not contain the three bounded animation groups")
    result["record_count"] = n
    for index in range(n):
        values = _unpack("<6f", metadata, 24*index)
        if any(not math.isfinite(value) for value in values) or any(values[i] > values[i+3] for i in range(3)):
            raise ValueError("clip bounds contain non-finite or descending extents")
        result["bounds"].append({"source_slot": index, "minimum": list(values[:3]), "maximum": list(values[3:])})
    for index, entry in enumerate(entries):
        if entry[:4] == b"BCH\0":
            parsed = parse_bch(entry)
            result["entries"][str(index)] = parsed
            result["errors"].extend({"entry": index, **error} for error in parsed["errors"])
            result["warnings"].extend({"entry": index, **warning} for warning in parsed["warnings"])
            for kind in ("material", "visibility"):
                for animation in parsed[kind]:
                    result["errors"].extend({"entry": index, "animation": animation.name, **error} for error in animation.errors)
                    result["warnings"].extend({"entry": index, "animation": animation.name, **warning} for warning in animation.warnings)
    skeletal_count, = _unpack("<I", entries[0], 0)
    if not 2 <= skeletal_count <= 513:
        raise ValueError("GF1 skeletal-slot table is invalid")
    skeletal_offsets = _unpack("<"+"I"*skeletal_count, entries[0], 4)
    nonzero_offsets = [at for at in skeletal_offsets if at]
    if (not skeletal_offsets[0] or len(set(nonzero_offsets)) != len(nonzero_offsets)
            or any(at < 4+4*skeletal_count or at+4 > len(entries[0]) for at in nonzero_offsets)):
        raise ValueError("GF1 skeletal offsets are invalid")
    for slot, skeletal_at in enumerate(skeletal_offsets[1:]):
        if not skeletal_at:
            continue
        _octals, end_frame = _unpack("<HH", entries[0], skeletal_at)
        mapping = {"skeletal_slot": slot, "source_slot": slot, "source_end_frame": end_frame,
                   "visibility_entry": None, "material_entries": [], "visibility_animation": [],
                   "material_animations": [], "clip_name": None, "warnings": []}
        if slot < n:
            mapping["visibility_entry"] = 2+slot
            mapping["material_entries"] = [2+n+slot, 2+2*n+slot]
            for entry_index, kind in [(2+slot, "visibility"), (2+n+slot, "material"), (2+2*n+slot, "material")]:
                found = result["entries"].get(str(entry_index), {}).get(kind, [])
                mapping["visibility_animation" if kind == "visibility" else "material_animations"].extend(found)
            animations = mapping["visibility_animation"]+mapping["material_animations"]
            names = {animation.name for animation in animations if animation.name}
            mapping["clip_name"] = next(iter(names)) if len(names) == 1 else None
            if len(names) > 1 or any(abs(animation.end_frame-end_frame) > 1e-5 for animation in animations):
                mapping["warnings"].append({"code": "CLIP_ASSOCIATION_MISMATCH", "names": sorted(names),
                                            "message": "Slot-based clip association is not corroborated by name/duration."})
            mapping["association_verified"] = bool(animations) and len(names) == 1 and not mapping["warnings"]
        else:
            mapping["association_verified"] = False
            auxiliary = _selected_oras_auxiliary_pose(entries[0], skeletal_offsets, slot, n)
            if auxiliary is not None:
                mapping["role"] = "auxiliary_pose"
                mapping["association_kind"] = "shared_auxiliary_pose"
                mapping["auxiliary_pose"] = auxiliary
                mapping["warnings"].append({"code": "AUXILIARY_BASE_CLIP_SELECTION_UNVERIFIED",
                                            "message": "Shared sparse pose is identified. Original-game base selection and trigger are unverified; composite playback requires a user-selected base and preserves absent source channels."})
            else:
                mapping["warnings"].append({"code": "AUXILIARY_SKELETAL_SLOT_UNMAPPED",
                                            "message": "Skeletal slot lies outside the primary clip-bounds groups; no material/visibility association is invented."})
        result["mappings"].append(mapping)
    return result


def sample_pack_clip(pack, source_slot, frame, loop=False):
    mapping = next((item for item in pack["mappings"] if item["source_slot"] == source_slot), None)
    if mapping is None:
        raise ValueError("requested source skeletal slot is absent")
    return {"source_slot": source_slot, "clip_name": mapping["clip_name"],
            "materials": [track for animation in mapping["material_animations"] for track in evaluate_animation(animation, frame, loop)],
            "visibility": [track for animation in mapping["visibility_animation"] for track in evaluate_animation(animation, frame, loop)],
            "warnings": mapping["warnings"].copy()}


def serialize(value):
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if isinstance(value, dict):
        return {str(key): serialize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [serialize(item) for item in value]
    return value


def read_pokemon_animation_package(blob, label=""):
    return serialize(parse_pokemon_pack(blob, label))
