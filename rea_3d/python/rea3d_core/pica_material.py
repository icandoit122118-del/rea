"""Source-backed BCH 0x21 PICA material/state inventory (no rendering).

SPDX-License-Identifier: GPL-3.0-or-later
Layout/bitfields independently implemented from pinned gdkchan/SPICA sources;
see SOURCE_REFERENCE and the retained D: research/pica-material source manifest.
Byte offsets are relative to the supplied uncompressed BCH, never the outer ROM.
Unwritten register bits remain unknown; this module does not invent GPU defaults.
"""
from __future__ import annotations

import hashlib
import math
import struct

from ctr.binstream import BinStream
from ctr.formats.bch.header import read_header, relocate

SOURCE_REFERENCE = "https://github.com/gdkchan/SPICA"
MAX_BCH = 128 * 1024**2
MAX_COMMAND_WORDS = 65536
SOURCES = {0: "PrimaryColor", 1: "FragmentPrimaryColor", 2: "FragmentSecondaryColor",
           3: "Texture0", 4: "Texture1", 5: "Texture2", 6: "Texture3",
           13: "PreviousBuffer", 14: "Constant", 15: "Previous"}
COLOR_OPERANDS = {0: "Color", 1: "OneMinusColor", 2: "Alpha", 3: "OneMinusAlpha",
                  4: "Red", 5: "OneMinusRed", 8: "Green", 9: "OneMinusGreen",
                  12: "Blue", 13: "OneMinusBlue"}
ALPHA_OPERANDS = dict(enumerate(("Alpha", "OneMinusAlpha", "Red", "OneMinusRed",
                                "Green", "OneMinusGreen", "Blue", "OneMinusBlue")))
COMBINERS = dict(enumerate(("Replace", "Modulate", "Add", "AddSigned", "Interpolate",
                           "Subtract", "DotProduct3Rgb", "DotProduct3Rgba", "MultAdd", "AddMult")))
TEST_FUNCS = dict(enumerate(("Never", "Always", "Equal", "Notequal", "Less", "Lequal", "Greater", "Gequal")))
BLEND_FUNCS = dict(enumerate(("Zero", "One", "SourceColor", "OneMinusSourceColor", "DestinationColor",
                            "OneMinusDestinationColor", "SourceAlpha", "OneMinusSourceAlpha", "DestinationAlpha",
                            "OneMinusDestinationAlpha", "ConstantColor", "OneMinusConstantColor", "ConstantAlpha",
                            "OneMinusConstantAlpha", "SourceAlphaSaturate")))
BLEND_EQUATIONS = dict(enumerate(("FuncAdd", "FuncSubtract", "FuncReverseSubtract", "Min", "Max")))
LUT_INPUTS = dict(enumerate(("CosNormalHalf", "CosViewHalf", "CosNormalView", "CosLightNormal", "CosLightSpot", "CosPhi")))
LUT_SCALES = {0: 1.0, 1: 2.0, 2: 4.0, 3: 8.0, 6: 0.25, 7: 0.5}
LUT_NAMES = ("dist0", "dist1", "specular", "fresnel", "reflect_r", "reflect_g", "reflect_b")
STAGE_BASES = (0xC0, 0xC8, 0xD0, 0xD8, 0xF0, 0xF8)
COLOR_NAMES = ("emission", "ambient", "diffuse", "specular0", "specular1", "constant0", "constant1",
               "constant2", "constant3", "constant4", "constant5", "blend")


class PicaMaterialError(ValueError):
    pass


def _need(condition, message):
    if not condition:
        raise PicaMaterialError(message)


def _bounds(blob, offset, size, label):
    _need(type(offset) is int and type(size) is int and offset >= 0 and size >= 0
          and offset <= len(blob) and size <= len(blob)-offset, f"out-of-bounds {label}")


def _u32(blob, offset):
    _bounds(blob, offset, 4, "u32")
    return struct.unpack_from("<I", blob, offset)[0]


def _floats(blob, offset, count):
    _bounds(blob, offset, count*4, "float field")
    values = list(struct.unpack_from("<"+"f"*count, blob, offset))
    _need(all(math.isfinite(v) for v in values), "nonfinite material float")
    return values


def _rgba(value):
    return [((value >> (i*8)) & 255)/255.0 for i in range(4)]


def _enum(code, choices, unsupported, label):
    if code is None:
        return None
    if code not in choices:
        unsupported.append({"code": "UNKNOWN_ENUM", "field": label, "value": code})
        return f"Unknown({code})"
    return choices[code]


def _float24(raw):
    if raw & 0x7FFFFF:
        word = ((raw & 0xFFFF) << 7) | ((((raw >> 16) & 0x7F)+64) << 23) | ((raw >> 23) << 31)
    else:
        word = (raw & 0x800000) << 8
    return struct.unpack("<f", struct.pack("<I", word))[0]


def decode_pica_commands(blob, offset, word_count, *, original_blob=None):
    """Strict GPU command decoder with register masks and per-word provenance."""
    _need(type(word_count) is int and 0 <= word_count <= MAX_COMMAND_WORDS, "PICA command word limit exceeded")
    _bounds(blob, offset, word_count*4, "PICA command stream")
    original = blob if original_blob is None else original_blob
    _bounds(original, offset, word_count*4, "original PICA stream")
    words = struct.unpack_from("<"+"I"*word_count, blob, offset)
    writes, registers, uniforms, uniform_sources = [], {}, {}, {}
    cursor, uniform_index, uniform32, uniform_known = 0, 0, False, False
    packed, packed_known = [0, 0, 0], [False, False, False]
    while cursor < word_count:
        _need(cursor+2 <= word_count, "truncated PICA command header")
        start = cursor
        first, command = words[cursor:cursor+2]
        extra = (command >> 20) & 0x7FF
        register, mask = command & 0xFFFF, (command >> 16) & 15
        consecutive = bool(command >> 31)
        _need(cursor+2+extra <= word_count, "truncated PICA extra parameters")
        parameters = [(first, start)] + [(words[start+1+j], start+1+j) for j in range(1, extra+1)]
        for step, (value, position) in enumerate(parameters):
            r = register+step if consecutive else register
            _need(r <= 0xFFFF, "consecutive PICA register overflow")
            absolute = offset+position*4
            bitmask = sum(0xFF << (i*8) for i in range(4) if mask >> i & 1)
            previous = registers.get(r, {"value": 0, "known_mask": 0, "source_offsets": []})
            offsets = previous["source_offsets"]
            offsets.append(absolute)
            state = {"value": (previous["value"] & ~bitmask) | (value & bitmask),
                     "known_mask": previous["known_mask"] | bitmask,
                     "source_offsets": offsets}
            registers[r] = state
            writes.append({"register": r, "value": value, "raw_value": _u32(original, absolute),
                           "byte_mask": mask, "source_offset": absolute,
                           "command_header_offset": offset+(start+1)*4})
            if r == 0x2C0:
                index_state = state
                uniform_known = index_state["known_mask"] & 0x800000FF == 0x800000FF
                uniform_index = (index_state["value"] & 255)*4
                uniform32 = bool(index_state["value"] >> 31)
                packed_known = [False, False, False]
            elif 0x2C1 <= r <= 0x2C8 and uniform_known:
                index, component = (uniform_index >> 2) & 0x5F, uniform_index & 3
                if uniform32:
                    vector = uniforms.setdefault(index, [None]*4)
                    vector[3-component] = struct.unpack("<f", struct.pack("<I", value))[0] if mask == 15 else None
                else:
                    if component < 3:
                        packed[component], packed_known[component] = value, mask == 15
                    if component == 2:
                        if all(packed_known):
                            bits = (packed[0] << 64) | (packed[1] << 32) | packed[2]
                            uniforms[index] = [_float24((bits >> (i*24)) & 0xFFFFFF) for i in range(4)]
                        else:
                            uniforms[index] = [None]*4
                        uniform_index += 1
                uniform_sources.setdefault(index, []).append(absolute)
                uniform_index += 1
        cursor += 2+extra
        if cursor & 1:
            _need(cursor < word_count, "missing PICA command alignment word")
            cursor += 1
    for vector in uniforms.values():
        _need(all(value is None or math.isfinite(value) for value in vector), "nonfinite shader uniform")
    return {"source_offset": offset, "size": word_count*4, "word_count": word_count,
            "sha256": hashlib.sha256(bytes(original[offset:offset+word_count*4])).hexdigest(),
            "writes": writes, "registers": {str(k): v for k, v in registers.items()},
            "vertex_uniforms": {str(k): v for k, v in uniforms.items()},
            "vertex_uniform_source_offsets": {str(k): v for k, v in uniform_sources.items()}}


def _reg(stream, register, mask=0xFFFFFFFF):
    item = stream["registers"].get(str(register))
    if not item or item["known_mask"] & mask != mask:
        return None
    return item["value"]


def _field(stream, register, shift, width):
    value = _reg(stream, register, ((1 << width)-1) << shift)
    return None if value is None else (value >> shift) & ((1 << width)-1)


def _bool(stream, register, bit):
    value = _field(stream, register, bit, 1)
    return None if value is None else bool(value)


def texture_transform(scale, rotation, translation, transform_type):
    """SPICA TextureTransform.GetTransform as affine rows for column (u,v,1)."""
    sx, sy = scale
    tx, ty = translation
    ca, sa = math.cos(rotation), math.sin(rotation)
    if transform_type == 0:
        ox, oy = sx*((0.5*sa-0.5*ca)+0.5-tx), sy*((-0.5*sa-0.5*ca)+0.5-ty)
    elif transform_type == 1:
        ox, oy = sx*(-ca*tx-sa*ty), sy*(sa*tx-ca*ty)
    elif transform_type == 2:
        ox = sx*ca*(-tx-0.5)-sx*sa*(ty-0.5)+0.5
        oy = sy*sa*(-tx-0.5)+sy*ca*(ty-0.5)+0.5
    else:
        return None
    # Matrix3x4 M11/M21 and M12/M22 are the source U/V rows in SPICA.
    return [[sx*ca, -sx*sa, ox], [sy*sa, sy*ca, oy]]


class _Bch:
    def __init__(self, blob):
        _need(isinstance(blob, (bytes, bytearray, memoryview)) and 68 <= len(blob) <= MAX_BCH, "BCH input size is outside byte budget")
        self.total_command_words = 0
        self.total_names = 0
        self.total_materials = 0
        self.total_lut_samplers = 0
        self.original = bytes(blob)
        self.header = read_header(BinStream(self.original))
        _need(self.header.backward == 0x21, "material layout supports BCH backward version 0x21 only")
        for name in ("contents", "strings", "commands", "raw_data", "raw_ext", "relocation"):
            offset, size = getattr(self.header, name+"_address"), getattr(self.header, name+"_length")
            _bounds(self.original, offset, size, name+" section")
        _need(self.header.relocation_length % 4 == 0, "unaligned relocation table")
        self.data = relocate(self.original, self.header)

    def region(self, offset, size, name="contents"):
        base, extent = getattr(self.header, name+"_address"), getattr(self.header, name+"_length")
        _need(base <= offset and size >= 0 and offset-base <= extent and size <= extent-(offset-base), f"{name} section bounds violated")

    def string(self, pointer):
        if not pointer:
            return ""
        self.region(pointer, 1, "strings")
        end = self.data.find(b"\0", pointer, min(pointer+4096, self.header.strings_address+self.header.strings_length))
        _need(end >= 0, "unterminated BCH material string")
        return self.data[pointer:end].decode("utf-8", errors="strict")

    def names(self, pointer, count):
        _need(0 <= count <= 65535, "Patricia name count limit")
        self.total_names += count
        _need(self.total_names <= 65536, "total name budget exceeded")
        if not count:
            return []
        self.region(pointer, (count+1)*12)
        return [self.string(_u32(self.data, pointer+i*12+8)) for i in range(1, count+1)]

    def dictionary(self, index):
        at = self.header.contents_address + index*12
        self.region(at, 12)
        values, count, tree = struct.unpack_from("<III", self.data, at)
        _need(count <= 1024, "BCH dictionary count limit")
        if count:
            self.region(values, count*4)
        return values, count, self.names(tree, count) if tree and count else []

    def commands(self, offset, count):
        self.total_command_words += count
        _need(self.total_command_words <= 2_000_000, "total PICA command budget exceeded")
        self.region(offset, count*4, "commands")
        return decode_pica_commands(self.data, offset, count, original_blob=self.original)


def _stages(stream, constant_assignment, colors, unsupported):
    stages = []
    update = _reg(stream, 0xE0, 0xFF00)
    for index, base in enumerate(STAGE_BASES):
        source, operand, combiner, color, scale = (_reg(stream, base+i, mask) for i, mask in enumerate(
            (0x0FFF0FFF, 0x00777FFF, 0x000F000F, 0xFFFFFFFF, 0x00030003)))
        cs = [None if source is None else (source >> (i*4)) & 15 for i in range(3)]
        als = [None if source is None else (source >> (16+i*4)) & 15 for i in range(3)]
        co = [None if operand is None else (operand >> (i*4)) & 15 for i in range(3)]
        ao = [None if operand is None else (operand >> (12+i*4)) & 7 for i in range(3)]
        cc, ac = (None, None) if combiner is None else (combiner & 15, (combiner >> 16) & 15)
        sc, sa = (None, None) if scale is None else (scale & 3, (scale >> 16) & 3)
        assignment = (constant_assignment >> (index*4)) & 15
        stage = {"stage": index, "color_sources": [_enum(v, SOURCES, unsupported, "color source") for v in cs],
                 "alpha_sources": [_enum(v, SOURCES, unsupported, "alpha source") for v in als],
                 "color_source_codes": cs, "alpha_source_codes": als,
                 "color_operands": [_enum(v, COLOR_OPERANDS, unsupported, "color operand") for v in co],
                 "alpha_operands": [_enum(v, ALPHA_OPERANDS, unsupported, "alpha operand") for v in ao],
                 "color_operand_codes": co, "alpha_operand_codes": ao,
                 "color_combiner": _enum(cc, COMBINERS, unsupported, "color combiner"),
                 "alpha_combiner": _enum(ac, COMBINERS, unsupported, "alpha combiner"),
                 "color_combiner_code": cc, "alpha_combiner_code": ac,
                 "color_scale": None if sc is None or sc == 3 else 1 << sc,
                 "alpha_scale": None if sa is None or sa == 3 else 1 << sa,
                 "color_scale_code": sc, "alpha_scale_code": sa,
                 "update_color_buffer": False if index in (0, 5) else None if update is None else bool(update & (1 << (index+7))),
                 "update_alpha_buffer": False if index in (0, 5) else None if update is None else bool(update & (1 << (index+11))),
                 "constant_index": assignment, "constant_color": None if color is None else _rgba(color),
                 "source_offsets": {str(base+i): stream["registers"].get(str(base+i), {}).get("source_offsets", []) for i in range(5)}}
        if any(value is None for value in (source, operand, combiner, color, scale)):
            unsupported.append({"code": "INCOMPLETE_TEXENV_STATE", "stage": index})
        if assignment > 5 or sc == 3 or sa == 3:
            unsupported.append({"code": "UNSUPPORTED_TEXENV_ASSIGNMENT_OR_SCALE", "stage": index})
        stages.append(stage)
    return stages


def _pipeline(stream, unsupported):
    alpha_func = _field(stream, 0x104, 4, 3)
    depth_func = _field(stream, 0x107, 4, 3)
    culling = _field(stream, 0x40, 0, 2)
    blend = {key: _enum(_field(stream, 0x101, shift, width), choices, unsupported, key)
             for key, shift, width, choices in (
                 ("color_equation", 0, 3, BLEND_EQUATIONS), ("alpha_equation", 8, 3, BLEND_EQUATIONS),
                 ("color_src", 16, 4, BLEND_FUNCS), ("color_dst", 20, 4, BLEND_FUNCS),
                 ("alpha_src", 24, 4, BLEND_FUNCS), ("alpha_dst", 28, 4, BLEND_FUNCS))}
    blend.update(enabled=_bool(stream, 0x100, 8), color_operation_raw=_reg(stream, 0x100, 0x103),
                 raw=_reg(stream, 0x101), logical_operation=_field(stream, 0x102, 0, 4))
    return {"alpha_test": {"enabled": _bool(stream, 0x104, 0), "function": _enum(alpha_func, TEST_FUNCS, unsupported, "alpha function"),
                           "function_code": alpha_func, "reference": _field(stream, 0x104, 8, 8)},
            "blend": blend,
            "depth_color_mask": {"enabled": _bool(stream, 0x107, 0), "depth_function": _enum(depth_func, TEST_FUNCS, unsupported, "depth function"),
                                 "depth_function_code": depth_func, "depth_write": _bool(stream, 0x107, 12),
                                 "color_write": [_bool(stream, 0x107, bit) for bit in range(8, 12)]},
            "face_culling": _enum(culling, {0: "Never", 1: "FrontFace", 2: "BackFace"}, unsupported, "face culling"),
            "face_culling_code": culling,
            "stencil_test_raw": _reg(stream, 0x105), "stencil_operation_raw": _reg(stream, 0x106),
            "buffer_access": {"color_read": _field(stream, 0x112, 0, 4), "color_write": _field(stream, 0x113, 0, 4),
                              "depth_read": _bool(stream, 0x114, 1), "depth_write": _bool(stream, 0x115, 1),
                              "stencil_read": _bool(stream, 0x114, 0), "stencil_write": _bool(stream, 0x115, 0)}}


def _material(bch, at, index):
    bch.region(at, 44)
    data, unsupported = bch.data, []
    params = _u32(data, at)
    bch.region(params, 0x110)
    flags = struct.unpack_from("<H", data, params+4)[0]
    fragment_flags = data[params+6]
    color_values = {name: _rgba(_u32(data, params+0x58+i*4)) for i, name in enumerate(COLOR_NAMES)}
    coords = []
    for unit in range(3):
        coordinate_at = params+12+unit*24
        flags_tc, transform, mapping, camera = struct.unpack_from("<3Bb", data, coordinate_at)
        scale_x, scale_y, rotation, tx, ty = _floats(data, coordinate_at+4, 5)
        coords.append({"source_offset": coordinate_at, "flags": flags_tc, "transform_type": transform,
                       "mapping_type": mapping, "reference_camera_index": camera,
                       "scale": [scale_x, scale_y], "rotation": rotation, "translation": [tx, ty],
                       "affine_rows": texture_transform((scale_x, scale_y), rotation, (tx, ty), transform)})
        if transform > 2 or mapping != 0:
            unsupported.append({"code": "NON_UV_TEXTURE_MAPPING_OR_TRANSFORM", "unit": unit, "mapping": mapping, "transform": transform})
    mapper_at = _u32(data, at+0x18)
    bch.region(mapper_at, 48)
    mappers = []
    for unit in range(3):
        offset = mapper_at+16*unit
        sampler, wrap_u, wrap_v, mag, min_filter, min_lod = struct.unpack_from("<6B", data, offset)
        mappers.append({"source_offset": offset, "sampler_type": sampler, "wrap_u": wrap_u, "wrap_v": wrap_v,
                        "mag_filter": mag, "min_filter": min_filter, "min_lod": min_lod,
                        "lod_bias": _floats(data, offset+8, 1)[0], "border_color": _rgba(_u32(data, offset+12))})
        if wrap_u > 3 or wrap_v > 3 or mag > 1 or min_filter > 5:
            unsupported.append({"code": "UNKNOWN_TEXTURE_MAPPER_ENUM", "unit": unit})
    texture_commands = bch.commands(_u32(data, at+0x10), _u32(data, at+0x14))
    lut_commands = decode_pica_commands(data, params+0xA8, 6, original_blob=bch.original)
    fragment_commands = bch.commands(_u32(data, params+0xC8), _u32(data, params+0xCC))
    _need(_u32(data, params+0xD0) == _u32(data, params+0xC8), "fragment repeat pointer differs")
    texture_config = _reg(texture_commands, 0x80, 0x407)
    texenv_stages = _stages(fragment_commands, _u32(data, params+0xC0), color_values, unsupported)
    lut_config = {}
    for index_lut, label in enumerate(LUT_NAMES):
        absolute = _field(lut_commands, 0x1D0, index_lut*4+1, 1)
        input_code = _field(lut_commands, 0x1D1, index_lut*4, 3)
        scale_code = _field(lut_commands, 0x1D2, index_lut*4, 3)
        lut_config[label] = {"absolute": None if absolute is None else not bool(absolute),
                             "input": _enum(input_code, LUT_INPUTS, unsupported, "LUT input"), "input_code": input_code,
                             "scale": _enum(scale_code, LUT_SCALES, unsupported, "LUT scale"), "scale_code": scale_code}
    references = ("dist0", "dist1", "fresnel", "reflect_r", "reflect_g", "reflect_b")
    lut_references = {label: {"table": bch.string(_u32(data, params+0xD4+i*4)),
                              "sampler": bch.string(_u32(data, params+0xEC+i*4)),
                              "table_pointer_source_offset": params+0xD4+i*4,
                              "sampler_pointer_source_offset": params+0xEC+i*4}
                      for i, label in enumerate(references)}
    lighting = {"material_flags": flags, "fragment_flags": fragment_flags,
                "source_offset": params+4,
                "fragment_enabled": bool(flags & 1), "vertex_enabled": bool(flags & 2),
                "hemisphere_enabled": bool(flags & 4), "hemisphere_occlusion": bool(flags & 8),
                "fog_enabled": bool(flags & 16), "polygon_offset_enabled": bool(flags & 32),
                "bump_renormalize": bool(fragment_flags & 1), "clamp_highlight": bool(fragment_flags & 2),
                "lut_dist0_enabled": bool(fragment_flags & 4), "lut_dist1_enabled": bool(fragment_flags & 8),
                "lut_reflection_enabled": bool(fragment_flags & 16),
                "geometry_factor0_enabled": bool(fragment_flags & 32), "geometry_factor1_enabled": bool(fragment_flags & 64),
                "light_set_index": struct.unpack_from("<H", data, params+0x54)[0],
                "fog_index": struct.unpack_from("<H", data, params+0x56)[0],
                "fresnel_selector": data[params+0xA5], "bump_mode": data[params+0xA6], "bump_texture": data[params+0xA7]}
    texture_sources = fragment_commands["vertex_uniforms"].get("10", [None]*4)
    if texture_config is None or any(value is None for value in texture_sources):
        unsupported.append({"code": "INCOMPLETE_TEXTURE_ENABLE_OR_SOURCE_UNIFORM"})
    pipeline = _pipeline(fragment_commands, unsupported)
    required_pipeline = {"alpha_enabled": pipeline["alpha_test"]["enabled"],
                         "alpha_function": pipeline["alpha_test"]["function"],
                         "alpha_reference": pipeline["alpha_test"]["reference"],
                         "depth_enabled": pipeline["depth_color_mask"]["enabled"],
                         "depth_function": pipeline["depth_color_mask"]["depth_function"],
                         "depth_write": pipeline["depth_color_mask"]["depth_write"],
                         "face_culling": pipeline["face_culling"],
                         **{"blend_"+key: pipeline["blend"][key] for key in (
                             "enabled", "color_equation", "alpha_equation", "color_src", "color_dst", "alpha_src", "alpha_dst")}}
    missing_pipeline = [key for key, value in required_pipeline.items() if value is None]
    if any(value is None for value in pipeline["depth_color_mask"]["color_write"]):
        missing_pipeline.append("color_write")
    if missing_pipeline:
        unsupported.append({"code": "INCOMPLETE_PIPELINE_STATE", "fields": missing_pipeline})
    return {"index": index, "name": bch.string(_u32(data, at+0x28)),
            "texture_names": [bch.string(_u32(data, at+0x1C+i*4)) for i in range(3)],
            "tex_coords": coords, "texture_mappers": mappers,
            "enabled_textures": [None if texture_config is None else bool(texture_config & (1 << i)) for i in (0, 1, 2, 10)],
            "colors": color_values, "color_scale": _floats(data, params+0x88, 1)[0],
            "texenv_stages": texenv_stages, "texenv_buffer_color": None if _reg(fragment_commands, 0xFD) is None else _rgba(_reg(fragment_commands, 0xFD)),
            **pipeline, "lighting": lighting, "lut_config": lut_config,
            "lut_references": lut_references, "layer_config": data[params+0xA4],
            "translucency_kind": data[params+0xA4] & 15, "tex_coord_config": data[params+0xA4] >> 4,
            "polygon_offset_unit": _floats(data, params+0xC4, 1)[0],
            "shader_reference": bch.string(_u32(data, params+0x104)),
            "model_reference": bch.string(_u32(data, params+0x108)),
            "source_offsets": {"material": at, "material_size": 44, "params": params, "params_size": 0x110,
                               "colors": params+0x58, "texture_mappers": mapper_at,
                               "material_flags": params+4, "fragment_flags": params+6,
                               "texture_coords": params+12, "light_set_index": params+0x54, "fog_index": params+0x56,
                               "color_scale": params+0x88, "layer_config": params+0xA4, "fresnel_selector": params+0xA5,
                               "bump_mode": params+0xA6, "bump_texture": params+0xA7, "constant_assignment": params+0xC0,
                               "polygon_offset": params+0xC4},
            "params_sha256": hashlib.sha256(bch.original[params:params+0x110]).hexdigest(),
            "pica": {"texture_commands": texture_commands, "lut_config_commands": lut_commands, "fragment_commands": fragment_commands},
            "texture_sources": texture_sources,
            "unsupported": unsupported, "static_state_decoded": not unsupported, "visual_parity_verified": False}


def _visibility(bch, model_at, meshes_at, mesh_count):
    data = bch.data
    pointer, count = struct.unpack_from("<II", data, model_at+0x7C)
    names_count, tree = struct.unpack_from("<II", data, model_at+0x88)
    _need(count <= 65535 and names_count <= 65535 and mesh_count <= 4096, "visibility count limit")
    names = bch.names(tree, names_count) if tree and names_count else []
    extent = ((count+31)//32)*4
    if extent:
        bch.region(pointer, extent)
    bits = int.from_bytes(data[pointer:pointer+extent], "little")
    bch.region(meshes_at, mesh_count*56)
    meshes = []
    for index in range(mesh_count):
        at = meshes_at+index*56
        material, flags, node = struct.unpack_from("<3H", data, at)
        meshes.append({"mesh_index": index, "source_offset": at, "material_index": material,
                       "node_index": node, "node_name": names[node] if node < len(names) else None,
                       "default_visible": bool(bits >> node & 1) if node < count else None,
                       "flags": flags})
    return {"source_offset": pointer, "size": extent, "bool_count": count, "node_names": names,
            "default_bits": list(data[pointer:pointer+extent]), "default_bits_hex": data[pointer:pointer+extent].hex(),
            "by_node_name": {name: bool(bits >> i & 1) if i < count else None for i, name in enumerate(names)}, "meshes": meshes}


def _luts(bch):
    values, count, _names = bch.dictionary(4)
    result = []
    for index in range(count):
        at = _u32(bch.data, values+index*4)
        bch.region(at, 12)
        sampler_at, sampler_count, name_at = struct.unpack_from("<III", bch.data, at)
        _need(sampler_count <= 256, "LUT sampler limit")
        bch.total_lut_samplers += sampler_count
        _need(bch.total_lut_samplers <= 256, "total LUT sampler budget exceeded")
        bch.region(sampler_at, sampler_count*16)
        samplers = []
        for i in range(sampler_count):
            offset = sampler_at+i*16
            flags, commands, words, sampler_name = struct.unpack_from("<4I", bch.data, offset)
            stream = bch.commands(commands, words)
            table, differences, source_offsets = [None]*256, [None]*256, [None]*256
            current = 0  # H3DLUTSampler starts each sampler's command block at index zero.
            for write in stream["writes"]:
                r, value = write["register"], write["value"]
                if r == 0x1C5:
                    _need(write["byte_mask"] & 1, "LUT index byte was not specified")
                    current = value & 255
                elif 0x1C8 <= r <= 0x1CF:
                    _need(current < 256 and write["byte_mask"] & 3 == 3, "LUT data index/mask invalid")
                    table[current] = (value & 0xFFF)/4095.0
                    differences[current] = (value >> 12) & 0xFFF
                    source_offsets[current] = write["source_offset"]
                    current += 1
            samplers.append({"name": bch.string(sampler_name), "source_offset": offset, "flags": flags,
                             "absolute": bool(flags & 1), "decoded": table, "difference_codes": differences,
                             "value_source_offsets": source_offsets, "commands_source_offset": commands,
                             "commands_size": words*4, "complete": all(v is not None for v in table),
                             "difference_interpretation": "retained_raw; SPICA sampler decodes base 12-bit values only"})
        result.append({"name": bch.string(name_at), "source_offset": at, "samplers": samplers})
    return result


def inspect_bch_materials(blob):
    """Return all model static materials, mesh-node visibility and LUT samplers."""
    bch = _Bch(blob)
    values, count, names = bch.dictionary(0)
    _need(count <= 128, "model count budget exceeded")
    models = []
    for index in range(count):
        at = _u32(bch.data, values+index*4)
        bch.region(at, 0x90)
        materials_at, materials_count = struct.unpack_from("<II", bch.data, at+0x34)
        meshes_at, meshes_count = struct.unpack_from("<II", bch.data, at+0x40)
        _need(materials_count <= 256, "material count limit")
        bch.total_materials += materials_count
        _need(bch.total_materials <= 1024, "total material budget exceeded")
        bch.region(materials_at, materials_count*44)
        models.append({"index": index, "name": names[index] if index < len(names) else "",
                       "source_offset": at, "mesh_visibility": _visibility(bch, at, meshes_at, meshes_count),
                       "materials": [_material(bch, materials_at+i*44, i) for i in range(materials_count)]})
    return {"schema": "rea3d.bch.pica_material.v1", "bch_version": bch.header.backward,
            "source_sha256": hashlib.sha256(bytes(blob)).hexdigest(), "offset_space": "uncompressed_BCH_relative_bytes",
            "models": models, "luts": _luts(bch), "original_shader_bytecode_executed": False,
            "visual_parity_verified": False, "limitations": [
                "GPU material state and LUTs are decoded; this does not establish original-game rendering parity.",
                "Scene lights, fog, original shader program execution and draw ordering are not inferred from material state.",
                "LUT base values follow SPICA; packed difference codes are retained without claiming hardware interpolation parity."]}


def read_bch_material_configs(blob):
    """Flat material list; model_index/model_name retain the owning model."""
    report = inspect_bch_materials(blob)
    return [{"model_index": model["index"], "model_name": model["name"], **material}
            for model in report["models"] for material in model["materials"]]
